"""Out-of-sample validation.

The failure mode this module exists to prevent: sweep parameters over your
history, pick the best-looking set, and conclude you have an edge. You do not.
You have a number that describes the past. Any large enough grid contains a
configuration that looks excellent on any dataset, including pure noise.

Walk-forward answers a harder and more useful question: if I had tuned on data
available *at the time*, would the result have held up on the data that came
next? A strategy that survives that has a chance. One that does not, does not.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .backtest import Backtester
from .config import Config
from .strategies import build_strategy
from .strategies.funding_carry import FundingCarry

log = logging.getLogger(__name__)


@dataclass
class FoldResult:
    fold: int
    params: dict[str, Any]
    train_return: float
    train_sharpe: float
    test_return: float
    test_sharpe: float
    test_trades: int


@dataclass
class WalkForwardResult:
    folds: list[FoldResult]
    strategy: str

    @property
    def mean_test_return(self) -> float:
        return float(np.mean([f.test_return for f in self.folds])) if self.folds else 0.0

    @property
    def mean_train_return(self) -> float:
        return float(np.mean([f.train_return for f in self.folds])) if self.folds else 0.0

    @property
    def consistency(self) -> float:
        """Fraction of folds that were profitable out of sample."""
        if not self.folds:
            return 0.0
        return sum(1 for f in self.folds if f.test_return > 0) / len(self.folds)

    @property
    def overfit_gap(self) -> float:
        """How much better training looked than reality.

        A large positive gap is the signature of curve fitting: the parameters
        describe the training window, not the market.
        """
        return self.mean_train_return - self.mean_test_return

    def report(self) -> str:
        lines = [
            f"walk-forward: {self.strategy}",
            "",
            f"{'fold':<6}{'train ret':>12}{'test ret':>11}{'test SR':>10}{'trades':>9}  params",
            "-" * 88,
        ]
        for f in self.folds:
            params = ", ".join(f"{k}={v}" for k, v in sorted(f.params.items()))
            lines.append(
                f"{f.fold:<6}{f.train_return:>11.1%}{f.test_return:>11.1%}"
                f"{f.test_sharpe:>10.2f}{f.test_trades:>9}  {params}"
            )
        lines += [
            "-" * 88,
            f"mean out-of-sample return .... {self.mean_test_return:>8.1%}",
            f"profitable folds ............. {self.consistency:>8.0%}",
            f"overfit gap (train - test) ... {self.overfit_gap:>8.1%}",
            "",
            self.verdict(),
        ]
        return "\n".join(lines)

    def verdict(self) -> str:
        if not self.folds:
            return "No folds completed -- not enough history."
        if self.mean_test_return <= 0:
            return (
                "VERDICT: no edge. The configuration does not make money on data "
                "it was not tuned on. Do not trade this."
            )
        if self.consistency < 0.6:
            return (
                "VERDICT: inconsistent. Profitable on average but not reliably, "
                "which usually means one lucky fold is carrying the result. "
                "Treat as noise until more folds agree."
            )
        if self.overfit_gap > 0.15:
            return (
                "VERDICT: likely overfit. Out-of-sample results are far worse "
                "than in-sample, so the parameter search is fitting history."
            )
        return (
            "VERDICT: survived out-of-sample testing. This is the weakest claim "
            "worth acting on -- paper trade it before risking capital."
        )


def expand_grid(grid: dict[str, Iterable]) -> list[dict[str, Any]]:
    """Cartesian product of a parameter grid."""
    if not grid:
        return [{}]
    keys = list(grid)
    return [dict(zip(keys, combo)) for combo in itertools.product(*(grid[k] for k in keys))]


def _score(result) -> tuple[float, float, int]:
    curve = result.equity_curve
    if curve.empty or len(curve) < 2:
        return 0.0, 0.0, 0
    total_return = curve.iloc[-1] / curve.iloc[0] - 1.0
    returns = curve.pct_change().dropna()
    sharpe = float(returns.mean() / returns.std()) if returns.std() > 0 else 0.0
    return float(total_return), sharpe, len(result.trades)


def walk_forward(
    cfg: Config,
    frames: dict[str, pd.DataFrame],
    strategy_name: str,
    grid: dict[str, Iterable] | None = None,
    folds: int = 4,
    train_frac: float = 0.6,
    invert: bool = False,
) -> WalkForwardResult:
    """Tune on each fold's training window, then measure on the window after it.

    Folds are anchored and rolling forward, never overlapping train and test,
    so no fold is ever scored on data its parameters saw.
    """
    if strategy_name.removeprefix("inverted_") == FundingCarry.name:
        # Refuse rather than quietly report "no edge". This strategy trades on
        # funding, and Hyperliquid serves no per-asset historical funding
        # series -- so every bar would abstain and the run would look like a
        # tested failure instead of an untested strategy. A validator that
        # cannot tell those apart is worse than no validator.
        raise ValueError(
            "funding_carry cannot be validated out of sample: the venue serves "
            "no historical funding series, so there is nothing to backtest it "
            "against. Evaluate it by paper trading instead."
        )

    combos = expand_grid(grid or {})
    timeline = sorted(set().union(*(df.index for df in frames.values())))
    n = len(timeline)

    warmup = build_strategy(strategy_name, invert=invert).warmup_bars
    fold_size = n // folds
    train_size = int(fold_size * train_frac)
    test_size = fold_size - train_size

    if train_size <= warmup + 60 or test_size <= warmup + 60:
        raise ValueError(
            f"not enough history: each fold gives {train_size} train and "
            f"{test_size} test bars, but the strategy needs {warmup} to warm up. "
            f"Use fewer folds or a longer --days."
        )

    results: list[FoldResult] = []
    for fold in range(folds):
        start = fold * fold_size
        train_end = start + train_size
        test_end = min(train_end + test_size, n)
        train_span = (timeline[start], timeline[train_end - 1])
        test_span = (timeline[train_end], timeline[test_end - 1])

        best = None
        for params in combos:
            train_result = _run(cfg, frames, strategy_name, params, *train_span, invert)
            if train_result is None:
                continue
            ret, sharpe, _ = _score(train_result)
            # Rank on Sharpe, not raw return: a big number from one lucky
            # trade is not something to carry into the next window.
            if best is None or sharpe > best[1]:
                best = (params, sharpe, ret)

        if best is None:
            log.warning("fold %d: no configuration produced a result", fold)
            continue

        params, train_sharpe, train_return = best
        test_result = _run(cfg, frames, strategy_name, params, *test_span, invert)
        if test_result is None:
            continue
        test_return, test_sharpe, trades = _score(test_result)

        results.append(
            FoldResult(
                fold=fold,
                params=params,
                train_return=train_return,
                train_sharpe=train_sharpe,
                test_return=test_return,
                test_sharpe=test_sharpe,
                test_trades=trades,
            )
        )
        log.info(
            "fold %d: train %.1f%% -> test %.1f%% (%d trades) %s",
            fold,
            train_return * 100,
            test_return * 100,
            trades,
            params,
        )

    label = f"inverted_{strategy_name}" if invert else strategy_name
    return WalkForwardResult(folds=results, strategy=label)


def _run(cfg, frames, strategy_name, params, start, end, invert=False):
    window = {
        symbol: df.loc[start:end]
        for symbol, df in frames.items()
        if not df.loc[start:end].empty
    }
    if not window:
        return None
    strategy = build_strategy(strategy_name, params, invert=invert)
    try:
        return Backtester(cfg, [(strategy, 1.0)]).run(window)
    except ValueError:
        return None  # not enough bars in this slice; the caller skips it
