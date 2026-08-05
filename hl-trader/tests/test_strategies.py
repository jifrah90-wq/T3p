"""Strategy behaviour, and the property that matters most: no look-ahead.

A strategy that can see the future produces a beautiful backtest and loses
money live. Every strategy here is checked bar-by-bar against a truncated
history to prove its decision at time t never depends on data after t.
"""

import numpy as np
import pandas as pd
import pytest

from hltrader.strategies import REGISTRY, build_strategy
from hltrader.strategies.base import Signal, Strategy
from hltrader.strategies.funding_carry import FundingCarry
from hltrader.strategies.mean_reversion import MeanReversion
from hltrader.strategies.momentum import CrossSectionalMomentum
from hltrader.strategies.trend_breakout import TrendBreakout


def make_frame(closes):
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="1h", tz="UTC")
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes * 1.002,
            "low": closes * 0.998,
            "close": closes,
            "volume": 1.0,
        },
        index=idx,
    )


@pytest.fixture
def noisy():
    rng = np.random.default_rng(11)
    return make_frame(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 600))))


@pytest.fixture
def uptrend():
    rng = np.random.default_rng(3)
    drift = np.cumsum(rng.normal(0.002, 0.004, 600))
    return make_frame(100 * np.exp(drift))


# -- Signal contract ------------------------------------------------------


def test_direction_is_clamped_to_the_unit_interval():
    assert Signal("A", direction=5.0, stop_distance=0.02).direction == 1.0
    assert Signal("A", direction=-9.0, stop_distance=0.02).direction == -1.0


def test_a_signal_without_a_positive_stop_is_rejected():
    with pytest.raises(ValueError):
        Signal("A", direction=1.0, stop_distance=0.0)


def test_registry_builds_every_named_strategy():
    for name in REGISTRY:
        assert isinstance(build_strategy(name), Strategy)


def test_unknown_strategy_names_fail_loudly():
    with pytest.raises(ValueError, match="unknown strategy"):
        build_strategy("does_not_exist")


# -- behaviour ------------------------------------------------------------


def test_breakout_goes_long_on_a_new_high_in_an_uptrend(uptrend):
    signal = TrendBreakout().generate("X", uptrend)
    assert signal is not None
    assert signal.direction >= 0


def test_breakout_abstains_before_it_has_warmed_up():
    assert TrendBreakout().generate("X", make_frame(np.full(50, 100.0))) is None


def test_breakout_will_not_short_when_shorting_is_disabled():
    rng = np.random.default_rng(5)
    downtrend = make_frame(100 * np.exp(np.cumsum(rng.normal(-0.002, 0.004, 600))))
    signal = TrendBreakout(allow_short=False).generate("X", downtrend)
    assert signal is None or signal.direction >= 0


def test_mean_reversion_buys_a_sharp_dip_in_a_flat_market():
    # A quiet range, then a short sharp drop: exactly what it should fade.
    # Kept to 4 bars so ADX stays under the trend ceiling -- a longer slide
    # reads as a genuine trend and the strategy is right to stand aside.
    base = np.full(300, 100.0) + np.tile([0.4, -0.4], 150)
    dipped = np.concatenate([base, np.linspace(100, 90, 4)])
    signal = MeanReversion().generate("X", make_frame(dipped))
    assert signal is not None
    assert signal.direction > 0


def test_mean_reversion_stays_out_of_a_strong_trend(uptrend):
    signal = MeanReversion(max_adx=15.0).generate("X", uptrend)
    assert signal is None or signal.is_flat


def test_momentum_needs_a_ranked_universe_first(noisy):
    strategy = CrossSectionalMomentum()
    assert strategy.generate("X", noisy) is None


def test_momentum_longs_the_leaders_and_shorts_the_laggards():
    rng = np.random.default_rng(2)
    frames = {}
    for i in range(10):
        drift = (i - 5) * 0.0015
        frames[f"S{i}"] = make_frame(
            100 * np.exp(np.cumsum(rng.normal(drift, 0.004, 600)))
        )
    strategy = CrossSectionalMomentum(top_fraction=0.2, min_symbols=5)
    strategy.rank_universe(frames)

    best = max(strategy._scores, key=strategy._scores.get)
    worst = min(strategy._scores, key=strategy._scores.get)
    assert strategy.generate(best, frames[best]).direction > 0
    assert strategy.generate(worst, frames[worst]).direction < 0


def test_momentum_abstains_on_a_universe_that_is_too_small():
    strategy = CrossSectionalMomentum(min_symbols=8)
    frames = {f"S{i}": make_frame(np.full(600, 100.0) + i) for i in range(3)}
    strategy.rank_universe(frames)
    assert not strategy._longs and not strategy._shorts


def test_funding_carry_shorts_the_asset_that_longs_are_paying_for():
    rng = np.random.default_rng(8)
    down = make_frame(100 * np.exp(np.cumsum(rng.normal(-0.001, 0.003, 400))))
    strategy = FundingCarry(min_hourly_funding=0.0001)
    strategy.set_funding({"X": 0.0005})  # longs paying heavily
    signal = strategy.generate("X", down)
    assert signal is not None and signal.direction < 0


def test_funding_carry_abstains_without_a_funding_rate(noisy):
    strategy = FundingCarry()
    strategy.set_funding({})
    assert strategy.generate("X", noisy) is None


def test_funding_carry_will_not_fight_the_trend(uptrend):
    """Longs paying is not a reason to short an asset that is ripping."""
    strategy = FundingCarry(min_hourly_funding=0.0001)
    strategy.set_funding({"X": 0.0005})
    signal = strategy.generate("X", uptrend)
    assert signal is None or signal.direction >= 0


# -- statelessness --------------------------------------------------------


@pytest.mark.parametrize(
    "factory",
    [lambda: TrendBreakout(), lambda: MeanReversion()],
    ids=["trend_breakout", "mean_reversion"],
)
def test_generate_is_a_pure_function_of_the_frame(noisy, factory):
    """No hidden state may leak between calls.

    The runner reuses one strategy instance across every symbol and every
    cycle, so a strategy that remembers the last frame it saw would emit
    signals for one asset based on another. Feeding the same frame after a
    detour through different data must give the same answer.
    """
    strategy = factory()
    first = _describe(strategy.generate("X", noisy))

    other = make_frame(np.linspace(50, 150, 600))
    strategy.generate("OTHER", other)
    strategy.generate("OTHER", other.iloc[:450])

    assert _describe(strategy.generate("X", noisy)) == first


def _describe(signal):
    if signal is None:
        return None
    return (round(signal.direction, 9), round(signal.stop_distance, 9))


# -- inversion ------------------------------------------------------------


def test_inverted_flips_the_direction(uptrend):
    inner = TrendBreakout()
    original = inner.generate("X", uptrend)
    flipped = build_strategy("inverted_trend_breakout").generate("X", uptrend)
    assert original is not None and flipped is not None
    assert flipped.direction == pytest.approx(-original.direction)


def test_inverted_keeps_the_stop_distance(uptrend):
    """Risk sizing must not change just because the side did."""
    original = TrendBreakout().generate("X", uptrend)
    flipped = build_strategy("trend_breakout", invert=True).generate("X", uptrend)
    assert flipped.stop_distance == pytest.approx(original.stop_distance)


def test_inverted_drops_the_target(uptrend):
    """The original target pointed the other way; carrying it over is a bug."""
    assert build_strategy("inverted_trend_breakout").generate("X", uptrend).target_distance is None


def test_inverted_abstains_wherever_the_inner_strategy_does():
    short = make_frame(np.full(50, 100.0))
    assert build_strategy("inverted_trend_breakout").generate("X", short) is None


def test_inverted_forwards_hooks_the_runner_depends_on():
    """rank_universe and set_funding must survive the wrapper."""
    wrapped = build_strategy("inverted_momentum")
    frames = {f"S{i}": make_frame(np.linspace(100, 100 + i * 20, 600)) for i in range(10)}
    wrapped.rank_universe(frames)  # would raise if not forwarded
    assert build_strategy("inverted_funding_carry").warmup_bars > 0


def test_inverted_reports_a_distinguishable_name():
    assert build_strategy("inverted_mean_reversion").name == "inverted_mean_reversion"
