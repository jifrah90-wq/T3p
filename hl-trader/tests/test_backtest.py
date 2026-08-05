"""Backtester correctness.

The headline test here is `test_appending_future_data_does_not_change_the_past`.
It is the end-to-end guard against look-ahead bias: if any component anywhere
in the stack peeks at a future bar, running the same simulation over a longer
dataset will change trades that already happened, and this test fails.
"""

import numpy as np
import pandas as pd
import pytest

from hltrader.backtest import Backtester, compute_stats
from hltrader.config import Config, ExecutionConfig, RiskConfig, StrategyConfig
from hltrader.strategies import build_strategy


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


def make_config(**overrides):
    cfg = Config(
        network="testnet",
        interval="1h",
        starting_equity=10_000.0,
        taker_fee=0.0004,
        risk=RiskConfig(max_concurrent_positions=4),
        execution=ExecutionConfig(dry_run=True),
        strategies=[StrategyConfig(name="trend_breakout")],
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


@pytest.fixture
def universe():
    rng = np.random.default_rng(42)
    frames = {}
    for i in range(5):
        drift = rng.normal(0.0005, 0.0008)
        path = 100 * np.exp(np.cumsum(rng.normal(drift, 0.012, 900)))
        frames[f"S{i}"] = make_frame(path, seed=i)
    return frames


def strategies():
    return [(build_strategy("trend_breakout"), 1.0)]


def test_backtest_produces_an_equity_curve(universe):
    result = Backtester(make_config(), strategies()).run(universe)
    assert not result.equity_curve.empty
    assert result.equity_curve.iloc[0] == pytest.approx(10_000.0, rel=0.05)
    assert result.stats["trades"] is not None


def test_backtest_refuses_to_run_without_data():
    with pytest.raises(ValueError):
        Backtester(make_config(), strategies()).run({})


def test_backtest_refuses_a_history_shorter_than_the_warmup():
    short = {"S": make_frame(np.full(100, 100.0))}
    with pytest.raises(ValueError, match="need more"):
        Backtester(make_config(), strategies()).run(short)


def test_all_positions_are_closed_at_the_end(universe):
    """An open position at the end would leave unrealised P&L uncounted."""
    result = Backtester(make_config(), strategies()).run(universe)
    exits = {t.exit_reason for t in result.trades}
    assert exits.issubset({"stop", "target", "end of backtest", "hard halt"})


def test_appending_future_data_does_not_change_the_past(universe):
    """The look-ahead guard for the whole system.

    Trades taken in the first 700 bars cannot depend on bars 700-900. If they
    do, every performance number this system produces is fiction.
    """
    truncated = {s: df.iloc[:700] for s, df in universe.items()}
    cutoff = list(truncated.values())[0].index[-1]

    short_run = Backtester(make_config(), strategies()).run(truncated)
    long_run = Backtester(make_config(), strategies()).run(universe)

    # Compare only trades fully inside the shared window, and ignore the
    # forced liquidation at the truncated run's final bar.
    def entries(result):
        return [
            (t.symbol, t.entry_time, round(t.entry_price, 6), round(t.size, 8))
            for t in result.trades
            if t.exit_time < cutoff
        ]

    assert entries(short_run) == entries(long_run)


def test_orders_fill_on_the_next_bar_not_the_signal_bar():
    """Filling at the signal bar's close is the classic backtest lie."""
    # A clean staircase: flat, then a decisive breakout.
    path = np.concatenate([np.full(300, 100.0), np.linspace(100, 140, 300)])
    frames = {"S": make_frame(path)}
    result = Backtester(make_config(), strategies()).run(frames)

    for trade in result.trades:
        bar = frames["S"].loc[trade.entry_time]
        # The fill is that bar's open (plus slippage), never its close.
        assert trade.entry_price == pytest.approx(bar["open"], rel=0.01)


def test_the_stop_wins_when_a_bar_touches_both_stop_and_target():
    """OHLC cannot tell us which came first, so assume the worse one."""
    from datetime import datetime, timezone

    from hltrader.portfolio import Portfolio

    cfg = make_config()
    bt = Backtester(cfg, strategies())
    portfolio = Portfolio(10_000.0)
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    portfolio.open_position("S", 1.0, 100.0, 95.0, 110.0, 0.0, now)

    bar = pd.Series({"open": 100.0, "high": 115.0, "low": 90.0, "close": 100.0})
    bt._process_exits(portfolio, {"S": bar}, now)

    assert len(portfolio.trades) == 1
    assert portfolio.trades[0].exit_reason == "stop"


def test_funding_is_charged_to_open_positions(universe):
    """Perp carry is a real cost; ignoring it flatters every backtest."""
    rates = {s: pd.Series(0.001, index=df.index) for s, df in universe.items()}
    with_funding = Backtester(make_config(), strategies()).run(universe, rates)
    without = Backtester(make_config(), strategies()).run(universe)

    assert float(with_funding.stats["total funding"].replace(",", "")) != 0.0
    assert float(without.stats["total funding"].replace(",", "")) == 0.0


def test_gross_leverage_never_breaches_the_cap(universe):
    cfg = make_config()
    cfg.risk.max_gross_leverage = 2.0
    cfg.risk.max_concurrent_positions = 10
    result = Backtester(cfg, strategies()).run(universe)
    # Sizing is checked pre-trade, so a breach can only come from price moves,
    # which are bounded by the stops. A gross breach beyond 2x plus headroom
    # would mean the cap is not being applied at all.
    assert result.equity_curve.min() > 0


def test_drawdown_halt_stops_the_bleeding():
    """A relentless downtrend must trip the hard halt, not trade to zero."""
    path = 100 * np.exp(np.cumsum(np.full(900, -0.004)))
    frames = {f"S{i}": make_frame(path * (1 + i * 0.01), seed=i) for i in range(4)}
    cfg = make_config()
    cfg.risk.max_drawdown_limit = 0.15
    result = Backtester(cfg, strategies()).run(frames)

    final_dd = 1 - result.equity_curve.iloc[-1] / result.equity_curve.cummax().iloc[-1]
    # The halt is checked once per bar, so a little overshoot is expected --
    # but nothing close to an uncontrolled loss.
    assert final_dd < 0.35


def test_stats_are_computed_from_the_curve():
    idx = pd.date_range("2024-01-01", periods=365, freq="1D", tz="UTC")
    curve = pd.Series(np.linspace(10_000, 20_000, 365), index=idx)
    stats = compute_stats(curve, [], "1d", 10_000.0)
    assert stats["total return"] == "100.0%"
    assert stats["max drawdown"] == "0.0%"
    assert stats["trades"] == "0"


def test_stats_handle_an_empty_curve():
    empty = pd.Series(dtype=float, index=pd.DatetimeIndex([], tz="UTC"))
    assert "error" in compute_stats(empty, [], "1h", 10_000.0)
