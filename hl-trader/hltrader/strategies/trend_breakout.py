"""Donchian breakout with a trend filter and an ATR stop.

The classic crypto workhorse: crypto trends persist, and most of the annual
return arrives in a handful of long moves. The trend filter and the ADX gate
exist to keep it out of the chop between those moves, which is where naive
breakout systems give it all back.
"""

from __future__ import annotations

import pandas as pd

from ..indicators import adx, atr, donchian, ema
from .base import Signal, Strategy


class TrendBreakout(Strategy):
    name = "trend_breakout"

    def __init__(
        self,
        entry_window: int = 40,
        exit_window: int = 20,
        trend_fast: int = 50,
        trend_slow: int = 200,
        atr_window: int = 14,
        atr_stop_mult: float = 2.5,
        atr_target_mult: float = 6.0,
        min_adx: float = 20.0,
        allow_short: bool = True,
    ):
        super().__init__(
            entry_window=entry_window,
            exit_window=exit_window,
            trend_fast=trend_fast,
            trend_slow=trend_slow,
            atr_window=atr_window,
            atr_stop_mult=atr_stop_mult,
            atr_target_mult=atr_target_mult,
            min_adx=min_adx,
            allow_short=allow_short,
        )
        self.warmup_bars = max(trend_slow, entry_window) + atr_window + 5

    def generate(self, symbol: str, df: pd.DataFrame) -> Signal | None:
        if not self.ready(df):
            return None

        upper, lower = donchian(df, self.entry_window)
        exit_upper, exit_lower = donchian(df, self.exit_window)
        fast = ema(df["close"], self.trend_fast)
        slow = ema(df["close"], self.trend_slow)
        atr_series = atr(df, self.atr_window)
        adx_series = adx(df, self.atr_window)

        close = df["close"].iloc[-1]
        a = atr_series.iloc[-1]
        if pd.isna(a) or a <= 0 or pd.isna(slow.iloc[-1]) or pd.isna(upper.iloc[-1]):
            return None

        stop_distance = self.atr_stop_mult * a / close
        target_distance = self.atr_target_mult * a / close
        trending = not pd.isna(adx_series.iloc[-1]) and adx_series.iloc[-1] >= self.min_adx
        uptrend = fast.iloc[-1] > slow.iloc[-1]

        # Breakout above the channel, with the trend, in a trending tape.
        if close > upper.iloc[-1] and uptrend and trending:
            return Signal(
                symbol=symbol,
                direction=1.0,
                stop_distance=stop_distance,
                target_distance=target_distance,
                reason=f"break {self.entry_window}-bar high in uptrend",
                meta={"atr": float(a), "adx": float(adx_series.iloc[-1])},
            )

        if self.allow_short and close < lower.iloc[-1] and not uptrend and trending:
            return Signal(
                symbol=symbol,
                direction=-1.0,
                stop_distance=stop_distance,
                target_distance=target_distance,
                reason=f"break {self.entry_window}-bar low in downtrend",
                meta={"atr": float(a), "adx": float(adx_series.iloc[-1])},
            )

        # Not a fresh entry. Emit a flat signal when price has fallen back
        # through the shorter exit channel, so the runner knows to stand down
        # rather than simply holding on stale conviction.
        if close < exit_lower.iloc[-1] or close > exit_upper.iloc[-1]:
            return Signal(
                symbol=symbol,
                direction=0.0,
                stop_distance=stop_distance,
                reason="exit channel crossed",
            )
        return None
