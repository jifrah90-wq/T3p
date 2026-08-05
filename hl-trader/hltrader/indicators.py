"""Indicator primitives.

Everything here takes and returns pandas Series so strategies stay declarative.
All functions are causal: the value at bar i uses only bars <= i.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """Wilder's average true range."""
    tr = true_range(df)
    return tr.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    # An all-gain window has zero average loss, which divides to NaN above.
    # RSI is pinned at 100 there, not undefined.
    return out.mask(avg_loss.notna() & (avg_loss == 0), 100.0)


def realized_vol(series: pd.Series, window: int = 24) -> pd.Series:
    """Stdev of log returns over `window` bars, in per-bar units."""
    returns = np.log(series / series.shift(1))
    return returns.rolling(window, min_periods=window).std()


def zscore(series: pd.Series, window: int) -> pd.Series:
    mean = series.rolling(window, min_periods=window).mean()
    std = series.rolling(window, min_periods=window).std()
    return (series - mean) / std.replace(0, np.nan)


def donchian(df: pd.DataFrame, window: int) -> tuple[pd.Series, pd.Series]:
    """Highest high and lowest low of the `window` bars *before* the current one.

    Shifted by one so a breakout test against these levels never peeks at the
    bar it is evaluating.
    """
    upper = df["high"].rolling(window, min_periods=window).max().shift(1)
    lower = df["low"].rolling(window, min_periods=window).min().shift(1)
    return upper, lower


def adx(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """Average directional index -- how trending (vs chopping) the tape is."""
    up_move = df["high"].diff()
    down_move = -df["low"].diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    alpha = 1 / window
    tr_smooth = true_range(df).ewm(alpha=alpha, adjust=False, min_periods=window).mean()
    plus_smooth = pd.Series(plus_dm, index=df.index).ewm(
        alpha=alpha, adjust=False, min_periods=window
    ).mean()
    minus_smooth = pd.Series(minus_dm, index=df.index).ewm(
        alpha=alpha, adjust=False, min_periods=window
    ).mean()

    tr_smooth = tr_smooth.replace(0, np.nan)
    plus_di = 100 * plus_smooth / tr_smooth
    minus_di = 100 * minus_smooth / tr_smooth
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=alpha, adjust=False, min_periods=window).mean()


def rolling_return(series: pd.Series, window: int) -> pd.Series:
    return series / series.shift(window) - 1.0
