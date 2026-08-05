import numpy as np
import pandas as pd
import pytest

from hltrader.indicators import (
    adx,
    atr,
    donchian,
    ema,
    realized_vol,
    rolling_return,
    rsi,
    true_range,
    zscore,
)


@pytest.fixture
def ohlcv():
    rng = np.random.default_rng(7)
    n = 400
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    high = close * (1 + abs(rng.normal(0, 0.004, n)))
    low = close * (1 - abs(rng.normal(0, 0.004, n)))
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": 1.0},
        index=idx,
    )


def test_ema_tracks_a_constant_series():
    series = pd.Series([5.0] * 50)
    assert ema(series, 10).iloc[-1] == pytest.approx(5.0)


def test_true_range_is_never_negative(ohlcv):
    tr = true_range(ohlcv).dropna()
    assert (tr >= 0).all()


def test_atr_is_positive_and_warms_up(ohlcv):
    result = atr(ohlcv, 14)
    assert result.iloc[:13].isna().all()
    assert (result.dropna() > 0).all()


def test_rsi_is_bounded(ohlcv):
    result = rsi(ohlcv["close"], 14).dropna()
    assert result.between(0, 100).all()


def test_rsi_is_100_when_price_only_rises():
    series = pd.Series(np.arange(1, 60, dtype=float))
    assert rsi(series, 14).iloc[-1] == pytest.approx(100.0)


def test_rsi_is_0_when_price_only_falls():
    series = pd.Series(np.arange(60, 1, -1, dtype=float))
    assert rsi(series, 14).iloc[-1] == pytest.approx(0.0)


def test_zscore_of_constant_series_is_undefined_not_infinite():
    series = pd.Series([3.0] * 40)
    assert zscore(series, 20).dropna().empty


def test_donchian_excludes_the_current_bar(ohlcv):
    """A breakout test must not compare a bar's high against itself."""
    upper, lower = donchian(ohlcv, 20)
    manual_upper = ohlcv["high"].iloc[-21:-1].max()
    assert upper.iloc[-1] == pytest.approx(manual_upper)
    assert lower.iloc[-1] == pytest.approx(ohlcv["low"].iloc[-21:-1].min())


def test_adx_is_bounded(ohlcv):
    result = adx(ohlcv, 14).dropna()
    assert result.between(0, 100).all()


def test_adx_is_high_in_a_clean_trend():
    n = 200
    close = pd.Series(np.linspace(100, 200, n))
    df = pd.DataFrame(
        {"open": close, "high": close * 1.001, "low": close * 0.999, "close": close}
    )
    assert adx(df, 14).iloc[-1] > 40


def test_realized_vol_matches_manual_stdev(ohlcv):
    result = realized_vol(ohlcv["close"], 24)
    manual = np.log(ohlcv["close"] / ohlcv["close"].shift(1)).iloc[-24:].std()
    assert result.iloc[-1] == pytest.approx(manual)


def test_rolling_return():
    series = pd.Series([100.0, 110.0, 121.0])
    assert rolling_return(series, 2).iloc[-1] == pytest.approx(0.21)


@pytest.mark.parametrize(
    "func", [lambda df: atr(df, 14), lambda df: adx(df, 14)]
)
def test_indicators_are_causal(ohlcv, func):
    """Appending a future bar must not change any earlier value.

    This is the single most important property in the whole system: an
    indicator that peeks makes every backtest above it meaningless.
    """
    truncated = func(ohlcv.iloc[:-1])
    full = func(ohlcv)
    pd.testing.assert_series_equal(truncated, full.iloc[:-1], check_freq=False)
