"""Event-driven backtester.

Walks bar by bar across the whole universe simultaneously, so portfolio-level
constraints (gross leverage, position count, drawdown halts) bind exactly as
they will live. A per-symbol backtest would silently ignore all of them and
flatter the results.

Costs modelled: taker fees both ways, slippage, and funding on perps. These are
not a rounding error at the trade frequency this system runs at -- omitting
them is the single most common reason a backtest looks profitable and the live
account does not.

Fill conventions, chosen to be pessimistic:
  - signals computed on bar t are filled at bar t+1's open
  - if a bar's range touches both the stop and the target, the stop wins
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from .config import Config
from .portfolio import Portfolio
from .risk import RiskManager
from .strategies import Strategy

log = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    equity_curve: pd.Series
    trades: list
    stats: dict = field(default_factory=dict)

    def summary(self) -> str:
        lines = [f"{k:.<28} {v}" for k, v in self.stats.items()]
        return "\n".join(lines)


def _annualisation_factor(interval: str) -> float:
    from .marketdata import INTERVAL_MS

    bars_per_year = 365 * 24 * 3600 * 1000 / INTERVAL_MS[interval]
    return float(np.sqrt(bars_per_year))


def compute_stats(
    equity: pd.Series, trades: list, interval: str, starting_equity: float
) -> dict:
    if equity.empty:
        return {"error": "no equity curve"}

    returns = equity.pct_change().dropna()
    total_return = equity.iloc[-1] / starting_equity - 1.0
    days = max((equity.index[-1] - equity.index[0]).total_seconds() / 86400, 1e-9)
    cagr = (equity.iloc[-1] / starting_equity) ** (365 / days) - 1 if days > 0 else 0.0

    ann = _annualisation_factor(interval)
    sharpe = (returns.mean() / returns.std() * ann) if returns.std() > 0 else 0.0
    downside = returns[returns < 0].std()
    sortino = (returns.mean() / downside * ann) if downside and downside > 0 else 0.0

    running_max = equity.cummax()
    drawdown = 1 - equity / running_max
    max_dd = drawdown.max()

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)

    return {
        "start": str(equity.index[0].date()),
        "end": str(equity.index[-1].date()),
        "days": f"{days:.0f}",
        "starting equity": f"{starting_equity:,.0f}",
        "ending equity": f"{equity.iloc[-1]:,.0f}",
        "total return": f"{total_return:.1%}",
        "CAGR": f"{cagr:.1%}",
        "max drawdown": f"{max_dd:.1%}",
        "Sharpe": f"{sharpe:.2f}",
        "Sortino": f"{sortino:.2f}",
        "trades": str(len(trades)),
        "win rate": f"{len(wins) / len(trades):.1%}" if trades else "n/a",
        "profit factor": f"{gross_win / gross_loss:.2f}" if gross_loss > 0 else "n/a",
        "avg trade": f"{np.mean([t.pnl for t in trades]):,.2f}" if trades else "n/a",
        "total fees": f"{sum(t.fees for t in trades):,.2f}",
        "total funding": f"{sum(t.funding for t in trades):,.2f}",
    }


class Backtester:
    def __init__(self, cfg: Config, strategies: list[tuple[Strategy, float]]):
        self.cfg = cfg
        self.strategies = strategies
        self.slippage = cfg.risk.max_slippage / 4  # expected, not worst-case

    def run(
        self,
        frames: dict[str, pd.DataFrame],
        funding: dict[str, pd.Series] | None = None,
    ) -> BacktestResult:
        frames = {s: df for s, df in frames.items() if not df.empty}
        if not frames:
            raise ValueError("no data to backtest")

        # One shared clock across all symbols so portfolio limits bind properly.
        timeline = sorted(set().union(*(df.index for df in frames.values())))
        portfolio = Portfolio(self.cfg.starting_equity)
        risk = RiskManager(self.cfg.risk, taker_fee=self.cfg.taker_fee)

        warmup = max(s.warmup_bars for s, _ in self.strategies)
        if len(timeline) <= warmup + 2:
            raise ValueError(
                f"need more than {warmup + 2} bars to backtest; got {len(timeline)}"
            )

        # Signals decided on bar i are executed at bar i+1's open.
        pending: list = []
        # Strategies see exactly the same window here as the live runner gives
        # them, so an indicator computes to the same value in both. A backtest
        # that feeds strategies more history than live ever will is measuring
        # a system nobody is going to run.
        window = max(self.cfg.lookback_bars, warmup + 50)

        for i in range(warmup, len(timeline) - 1):
            now: datetime = timeline[i]
            next_bar: datetime = timeline[i + 1]

            bars = {
                symbol: df.loc[now]
                for symbol, df in frames.items()
                if now in df.index
            }
            prices = {s: float(b["close"]) for s, b in bars.items()}
            if not prices:
                continue

            self._settle_funding(portfolio, funding, now, prices)
            self._process_exits(portfolio, bars, now)

            equity = portfolio.mark(now, prices)
            halt = risk.check_halt(portfolio, equity)

            # Execute what the previous bar decided, at this bar's open.
            self._execute(portfolio, pending, bars, now)
            pending = []

            if halt.halted:
                if halt.hard:
                    self._flatten(portfolio, bars, now, "hard halt")
                continue

            # Hand strategies a bounded window, not the whole history. They
            # only ever look back `warmup_bars`, and recomputing indicators
            # over an ever-growing frame makes the backtest quadratic --
            # slow enough that nobody runs the parameter sweeps that actually
            # matter.
            history = {
                symbol: df.loc[:now].iloc[-window:]
                for symbol, df in frames.items()
                if now in df.index
            }
            pending = self._decide(history, portfolio, risk, equity, prices, funding, now)

        # Close out at the last available price so the curve is honest.
        final = timeline[-1]
        final_bars = {s: df.loc[final] for s, df in frames.items() if final in df.index}
        self._flatten(portfolio, final_bars, final, "end of backtest")
        final_prices = {s: float(b["close"]) for s, b in final_bars.items()}
        portfolio.mark(final, final_prices)

        curve = pd.Series(
            [e for _, e in portfolio.equity_curve],
            index=pd.DatetimeIndex([t for t, _ in portfolio.equity_curve]),
        )
        stats = compute_stats(
            curve, portfolio.trades, self.cfg.interval, self.cfg.starting_equity
        )
        return BacktestResult(equity_curve=curve, trades=portfolio.trades, stats=stats)

    # -- internals --------------------------------------------------------

    def _decide(
        self, history, portfolio, risk, equity, prices, funding, now
    ) -> list:
        # Cross-sectional strategies need the whole universe scored first.
        for strategy, _ in self.strategies:
            # hasattr, not isinstance: a strategy may be wrapped (e.g. by
            # Inverted), and the wrapper forwards these through.
            if hasattr(strategy, "rank_universe"):
                strategy.rank_universe(history)
            elif hasattr(strategy, "set_funding") and funding:
                strategy.set_funding(
                    {
                        s: float(series.asof(now))
                        for s, series in funding.items()
                        if not series.empty and not pd.isna(series.asof(now))
                    }
                )

        orders = []
        for symbol, df in history.items():
            if symbol in portfolio.positions:
                continue
            best = None
            best_weight = 0.0
            for strategy, weight in self.strategies:
                signal = strategy.generate(symbol, df)
                if signal is None or signal.is_flat:
                    continue
                score = weight * abs(signal.direction)
                if score > best_weight:
                    best, best_weight = (signal, strategy.name), score
            if best is None:
                continue
            signal, strategy_name = best
            order = risk.size_order(
                signal,
                prices[symbol],
                equity,
                portfolio,
                prices,
                strategy=strategy_name,
            )
            if order:
                orders.append(order)

        # Fit the strongest ideas first when leverage headroom is scarce.
        orders.sort(key=lambda o: o.notional, reverse=True)
        return orders

    def _execute(self, portfolio, orders, bars, when) -> None:
        for order in orders:
            bar = bars.get(order.symbol)
            if bar is None or order.symbol in portfolio.positions:
                continue
            side = 1 if order.size > 0 else -1
            fill_price = float(bar["open"]) * (1 + side * self.slippage)
            if fill_price <= 0:
                continue
            size = order.notional / fill_price * side
            fee = abs(size) * fill_price * self.cfg.taker_fee
            # Re-derive the stop from the actual fill, not the signal price.
            stop_distance = abs(order.reference_price - order.stop_price) / order.reference_price
            stop_price = fill_price * (1 - side * stop_distance)
            target_price = None
            if order.target_price:
                target_distance = (
                    abs(order.target_price - order.reference_price) / order.reference_price
                )
                target_price = fill_price * (1 + side * target_distance)

            portfolio.open_position(
                order.symbol,
                size,
                fill_price,
                stop_price,
                target_price,
                fee,
                when,
                order.strategy,
            )

    def _process_exits(self, portfolio, bars, when) -> None:
        for symbol in list(portfolio.positions):
            bar = bars.get(symbol)
            if bar is None:
                continue
            pos = portfolio.positions[symbol]
            low, high = float(bar["low"]), float(bar["high"])

            # Pessimistic: if both levels are inside the bar, assume the stop
            # went first. Intrabar order is unknowable from OHLC.
            if pos.stop_hit(low, high):
                price = pos.stop_price * (1 - pos.direction * self.slippage)
                fee = abs(pos.size) * price * self.cfg.taker_fee
                portfolio.close_position(symbol, price, fee, when, "stop")
            elif pos.target_hit(low, high):
                price = pos.target_price
                fee = abs(pos.size) * price * self.cfg.taker_fee
                portfolio.close_position(symbol, price, fee, when, "target")

    def _settle_funding(self, portfolio, funding, now, prices) -> None:
        """Charge one bar's worth of funding on every open perp position.

        Hyperliquid quotes funding hourly, so a bar longer than an hour owes
        proportionally more than a single period.
        """
        if not funding:
            return
        from .marketdata import INTERVAL_MS

        hours_per_bar = INTERVAL_MS[self.cfg.interval] / 3_600_000
        for symbol in list(portfolio.positions):
            series = funding.get(symbol)
            if series is None or series.empty or symbol not in prices:
                continue
            rate = series.asof(now)
            if pd.isna(rate):
                continue
            portfolio.apply_funding(symbol, float(rate) * hours_per_bar, prices[symbol])

    def _flatten(self, portfolio, bars, when, reason) -> None:
        for symbol in list(portfolio.positions):
            bar = bars.get(symbol)
            if bar is None:
                continue
            pos = portfolio.positions[symbol]
            price = float(bar["close"]) * (1 - pos.direction * self.slippage)
            fee = abs(pos.size) * price * self.cfg.taker_fee
            portfolio.close_position(symbol, price, fee, when, reason)
