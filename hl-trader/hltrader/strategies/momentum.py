"""Cross-sectional momentum, ranked across the whole universe.

Unlike the other strategies this one is inherently relative: it needs every
symbol's returns before it can score any single one. `rank_universe` does the
cross-sectional pass and caches the scores, and `generate` then reads them.
The runner calls `rank_universe` once per cycle before generating signals.
"""

from __future__ import annotations

import pandas as pd

from ..indicators import atr, realized_vol, rolling_return
from .base import Signal, Strategy


class CrossSectionalMomentum(Strategy):
    name = "momentum"

    def __init__(
        self,
        lookback: int = 168,
        skip_recent: int = 12,
        vol_window: int = 48,
        top_fraction: float = 0.2,
        atr_window: int = 14,
        atr_stop_mult: float = 3.0,
        min_symbols: int = 8,
        allow_short: bool = True,
    ):
        super().__init__(
            lookback=lookback,
            skip_recent=skip_recent,
            vol_window=vol_window,
            top_fraction=top_fraction,
            atr_window=atr_window,
            atr_stop_mult=atr_stop_mult,
            min_symbols=min_symbols,
            allow_short=allow_short,
        )
        self.warmup_bars = lookback + skip_recent + atr_window + 5
        self._scores: dict[str, float] = {}
        self._longs: set[str] = set()
        self._shorts: set[str] = set()

    def _score(self, df: pd.DataFrame) -> float | None:
        """Risk-adjusted momentum, skipping the most recent bars.

        The skip avoids buying a spike that is about to mean-revert -- the
        short-horizon reversal effect that eats naive momentum.
        """
        if not self.ready(df):
            return None
        close = df["close"]
        if self.skip_recent > 0:
            close = close.iloc[: -self.skip_recent]
        ret = rolling_return(close, self.lookback).iloc[-1]
        vol = realized_vol(close, self.vol_window).iloc[-1]
        if pd.isna(ret) or pd.isna(vol) or vol <= 0:
            return None
        return float(ret / vol)

    def rank_universe(self, frames: dict[str, pd.DataFrame]) -> dict[str, float]:
        """Score every symbol and pick the long and short buckets."""
        scores: dict[str, float] = {}
        for symbol, df in frames.items():
            score = self._score(df)
            if score is not None:
                scores[symbol] = score

        self._scores = scores
        self._longs = set()
        self._shorts = set()
        if len(scores) < self.min_symbols:
            return scores

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        bucket = max(1, int(len(ranked) * self.top_fraction))
        self._longs = {s for s, score in ranked[:bucket] if score > 0}
        if self.allow_short:
            self._shorts = {s for s, score in ranked[-bucket:] if score < 0}
        return scores

    def generate(self, symbol: str, df: pd.DataFrame) -> Signal | None:
        if not self.ready(df) or symbol not in self._scores:
            return None

        atr_now = atr(df, self.atr_window).iloc[-1]
        px = df["close"].iloc[-1]
        if pd.isna(atr_now) or atr_now <= 0:
            return None
        stop_distance = self.atr_stop_mult * atr_now / px
        score = self._scores[symbol]

        if symbol in self._longs:
            direction = 1.0
        elif symbol in self._shorts:
            direction = -1.0
        else:
            # Ranked but not selected: this strategy wants no exposure here.
            direction = 0.0

        return Signal(
            symbol=symbol,
            direction=direction,
            stop_distance=stop_distance,
            reason=f"xsec momentum score={score:.2f}",
            meta={"score": score},
        )
