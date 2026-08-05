"""Funding-rate carry on perpetuals.

When funding is strongly positive, longs pay shorts every hour; being short
collects that carry. This only works when the underlying trend is not running
against you, so the position is gated on price sitting on the right side of a
slow moving average. Funding is an edge in calm markets and a rounding error
in violent ones -- the trend gate is what stops it being the latter.
"""

from __future__ import annotations

import pandas as pd

from ..indicators import atr, ema
from .base import Signal, Strategy


class FundingCarry(Strategy):
    name = "funding_carry"

    def __init__(
        self,
        # Hourly funding rate that makes the carry worth the risk.
        # 0.0002/hr is roughly 175% annualised.
        min_hourly_funding: float = 0.00015,
        trend_window: int = 100,
        atr_window: int = 14,
        atr_stop_mult: float = 2.5,
        allow_short: bool = True,
    ):
        super().__init__(
            min_hourly_funding=min_hourly_funding,
            trend_window=trend_window,
            atr_window=atr_window,
            atr_stop_mult=atr_stop_mult,
            allow_short=allow_short,
        )
        self.warmup_bars = trend_window + atr_window + 5
        self._funding: dict[str, float] = {}

    def set_funding(self, funding: dict[str, float]) -> None:
        """Injected by the runner each cycle; carry is not in the candles."""
        self._funding = funding

    def generate(self, symbol: str, df: pd.DataFrame) -> Signal | None:
        if not self.ready(df):
            return None
        rate = self._funding.get(symbol)
        if rate is None:
            return None

        px = df["close"].iloc[-1]
        atr_now = atr(df, self.atr_window).iloc[-1]
        trend = ema(df["close"], self.trend_window).iloc[-1]
        if pd.isna(atr_now) or atr_now <= 0 or pd.isna(trend):
            return None

        stop_distance = self.atr_stop_mult * atr_now / px
        annualised = rate * 24 * 365

        # Longs are paying: collect by being short, but only if price is also
        # below trend so the carry is not fighting a rally.
        if self.allow_short and rate >= self.min_hourly_funding and px < trend:
            return Signal(
                symbol=symbol,
                direction=-1.0,
                stop_distance=stop_distance,
                reason=f"collect funding {annualised:.0%} ann.",
                meta={"funding": rate, "annualised": annualised},
            )

        # Shorts are paying: collect by being long, with price above trend.
        if rate <= -self.min_hourly_funding and px > trend:
            return Signal(
                symbol=symbol,
                direction=1.0,
                stop_distance=stop_distance,
                reason=f"collect funding {-annualised:.0%} ann.",
                meta={"funding": rate, "annualised": annualised},
            )

        if abs(rate) < self.min_hourly_funding * 0.5:
            return Signal(
                symbol=symbol,
                direction=0.0,
                stop_distance=stop_distance,
                reason="carry decayed",
            )
        return None
