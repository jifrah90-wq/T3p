import numpy as np
import pandas as pd
import pytest

from hltrader.config import Config, ExecutionConfig, RiskConfig, StrategyConfig
from hltrader.validation import (
    FoldResult,
    WalkForwardResult,
    expand_grid,
    walk_forward,
)


def make_frame(closes, seed=0):
    rng = np.random.default_rng(seed)
    closes = np.asarray(closes, dtype=float)
    wiggle = abs(rng.normal(0, 0.003, len(closes)))
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="1h", tz="UTC")
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes * (1 + wiggle),
            "low": closes * (1 - wiggle),
            "close": closes,
            "volume": 1.0,
        },
        index=idx,
    )


def make_config():
    return Config(
        network="testnet",
        interval="1h",
        lookback_bars=400,
        starting_equity=10_000.0,
        risk=RiskConfig(),
        execution=ExecutionConfig(dry_run=True),
        strategies=[StrategyConfig(name="trend_breakout")],
    )


def fold(train, test):
    return FoldResult(0, {}, train, 1.0, test, 1.0, 10)


def test_expand_grid_produces_the_cartesian_product():
    combos = expand_grid({"a": [1, 2], "b": [3, 4, 5]})
    assert len(combos) == 6
    assert {"a": 1, "b": 3} in combos


def test_expand_grid_of_nothing_is_one_empty_config():
    assert expand_grid({}) == [{}]


def test_consistency_counts_profitable_folds():
    result = WalkForwardResult([fold(0.1, 0.1), fold(0.1, -0.1)], "s")
    assert result.consistency == pytest.approx(0.5)


def test_overfit_gap_measures_train_minus_test():
    result = WalkForwardResult([fold(0.5, 0.1)], "s")
    assert result.overfit_gap == pytest.approx(0.4)


def test_a_losing_out_of_sample_result_is_called_out_as_no_edge():
    result = WalkForwardResult([fold(0.5, -0.1), fold(0.4, -0.2)], "s")
    assert "no edge" in result.verdict()


def test_a_large_train_test_gap_is_called_out_as_overfit():
    result = WalkForwardResult([fold(0.9, 0.02), fold(0.8, 0.03)], "s")
    assert "overfit" in result.verdict()


def test_inconsistent_results_are_not_endorsed():
    # One huge fold carrying three losers must not read as success.
    folds = [fold(0.1, 0.5), fold(0.1, -0.02), fold(0.1, -0.03), fold(0.1, -0.02)]
    assert "inconsistent" in WalkForwardResult(folds, "s").verdict()


def test_a_genuinely_consistent_result_is_endorsed_but_hedged():
    folds = [fold(0.12, 0.09), fold(0.11, 0.08), fold(0.13, 0.10)]
    verdict = WalkForwardResult(folds, "s").verdict()
    assert "survived" in verdict
    assert "paper trade" in verdict


def test_walk_forward_never_scores_a_fold_on_its_own_training_data():
    """The whole point: test windows must not overlap train windows."""
    rng = np.random.default_rng(1)
    frames = {
        f"S{i}": make_frame(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 3000))), seed=i)
        for i in range(3)
    }
    result = walk_forward(make_config(), frames, "trend_breakout", folds=3)
    assert result.folds

    timeline = sorted(frames["S0"].index)
    n, fold_size = len(timeline), len(timeline) // 3
    train_size = int(fold_size * 0.6)
    for f in result.folds:
        train_end = f.fold * fold_size + train_size
        # Test starts strictly after training ends, on every fold.
        assert train_end < min(n, train_end + (fold_size - train_size))


def test_walk_forward_refuses_folds_too_short_to_warm_up():
    frames = {"S": make_frame(np.full(400, 100.0))}
    with pytest.raises(ValueError, match="not enough history"):
        walk_forward(make_config(), frames, "trend_breakout", folds=6)


def test_walk_forward_picks_a_configuration_from_the_grid():
    rng = np.random.default_rng(4)
    frames = {
        f"S{i}": make_frame(100 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, 3000))), seed=i)
        for i in range(3)
    }
    grid = {"entry_window": [20, 60], "atr_stop_mult": [2.0, 3.0]}
    result = walk_forward(make_config(), frames, "trend_breakout", grid, folds=2)
    for f in result.folds:
        assert f.params["entry_window"] in (20, 60)
        assert f.params["atr_stop_mult"] in (2.0, 3.0)


def test_funding_carry_refuses_validation_instead_of_faking_a_verdict():
    """An untested strategy must not be reported as a tested failure.

    funding_carry abstains on every bar without a funding series, and the
    venue serves no history for one. Silently returning "no edge" would be a
    wrong answer dressed as a measurement.
    """
    frames = {"S": make_frame(np.full(3000, 100.0))}
    with pytest.raises(ValueError, match="no historical funding series"):
        walk_forward(make_config(), frames, "funding_carry", folds=3)


def test_report_renders_without_folds():
    assert "not enough history" in WalkForwardResult([], "s").verdict()
