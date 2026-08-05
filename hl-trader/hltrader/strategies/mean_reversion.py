"""Fade stretched moves inside a range.

The mirror image of the breakout book: when ADX says there is no trend, sharp
excursions from the mean tend to snap back. The ADX ceiling is what keeps this
from standing in front of a real trend, which is how mean reversion dies.
"""

from __future__ import annotations

import pandas as pd

from ..indicators import adx, atr, ema, rsi, zscore
from .base import Signal, Strategy


class MeanReversion(Strategy):
    name = "mean_reversion"

    def __init__(
        self,
        z_window: int = 48,
        entry_z: float = 2.0,
        anchor_window: int = 100,
        rsi_window: int = 14,
        rsi_long_max: float = 30.0,
        rsi_short_min: float = 70.0,
        atr_window: int = 14,
        atr_stop_mult: float = 2.0,
        max_adx: float = 25.0,
        allow_short: bool = True,
    ):
        super().__init__(
            z_window=z_window,
            entry_z=entry_z,
            anchor_window=anchor_window,
            rsi_window=rsi_window,
            rsi_long_max=rsi_long_max,
            rsi_short_min=rsi_short_min,
            atr_window=atr_window,
            atr_stop_mult=atr_stop_mult,
            max_adx=max_adx,
            allow_short=allow_short,
        )
        self.warmup_bars = max(z_window, anchor_window) + atr_window + 5

    def generate(self, symbol: str, df: pd.DataFrame) -> Signal | None:
        if not self.ready(df):
            return None

        close = df["close"]
        z = zscore(close, self.z_window)
        anchor = ema(close, self.anchor_window)
        rsi_series = rsi(close, self.rsi_window)
        atr_series = atr(df, self.atr_window)
        adx_series = adx(df, self.atr_window)

        px = close.iloc[-1]
        a = atr_series.iloc[-1]
        z_now = z.iloc[-1]
        r_now = rsi_series.iloc[-1]
        if pd.isna(a) or a <= 0 or pd.isna(z_now) or pd.isna(r_now):
            return None

        # Only fade when the tape is genuinely rangebound.
        if pd.isna(adx_series.iloc[-1]) or adx_series.iloc[-1] > self.max_adx:
            return None

        stop_distance = self.atr_stop_mult * a / px
        # The target is the mean the trade is betting on reverting to.
        target_distance = abs(anchor.iloc[-1] - px) / px if not pd.isna(anchor.iloc[-1]) else None

        if z_now <= -self.entry_z and r_now <= self.rsi_long_max:
            return Signal(
                symbol=symbol,
                direction=1.0,
                stop_distance=stop_distance,
                target_distance=target_distance,
                reason=f"oversold z={z_now:.2f} rsi={r_now:.0f}",
                meta={"z": float(z_now), "rsi": float(r_now)},
            )

        if self.allow_short and z_now >= self.entry_z and r_now >= self.rsi_short_min:
            return Signal(
                symbol=symbol,
                direction=-1.0,
                stop_distance=stop_distance,
                target_distance=target_distance,
                reason=f"overbought z={z_now:.2f} rsi={r_now:.0f}",
                meta={"z": float(z_now), "rsi": float(r_now)},
            )

        # Reverted to the mean: the reason for the trade is gone.
        if abs(z_now) < 0.3:
            return Signal(
                symbol=symbol,
                direction=0.0,
                stop_distance=stop_distance,
                reason="reverted to mean",
            )
        return None
