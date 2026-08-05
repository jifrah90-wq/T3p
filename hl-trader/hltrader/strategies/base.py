"""Strategy interface.

A strategy is a pure function of history: given OHLCV for one symbol it emits a
`Signal` describing *what it wants*, never *how much to buy*. Sizing, leverage
and portfolio limits belong to the risk layer, so a strategy can never blow up
the account by itself.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class Signal:
    symbol: str
    # Desired exposure in [-1, 1]: -1 fully short, 0 flat, +1 fully long.
    # It expresses conviction, not size.
    direction: float
    # Distance from entry to the protective stop, as a fraction of price.
    # The risk layer turns this into a position size.
    stop_distance: float
    # Optional take-profit distance as a fraction of price.
    target_distance: float | None = None
    reason: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.direction = max(-1.0, min(1.0, float(self.direction)))
        if self.stop_distance <= 0:
            raise ValueError(f"{self.symbol}: stop_distance must be positive")

    @property
    def is_flat(self) -> bool:
        return abs(self.direction) < 1e-9


class Strategy(ABC):
    """Base class. Subclasses declare `name` and implement `generate`."""

    name: str = "unnamed"
    # Bars of history the strategy needs before its output is meaningful.
    warmup_bars: int = 200

    def __init__(self, **params: Any):
        self.params = params
        for key, value in params.items():
            setattr(self, key, value)

    @abstractmethod
    def generate(self, symbol: str, df: pd.DataFrame) -> Signal | None:
        """Return a signal for the *last* bar of `df`, or None to abstain.

        `df` is indexed by bar open time ascending. Implementations must not
        look ahead: only use rows up to and including the last one.
        """

    def ready(self, df: pd.DataFrame) -> bool:
        return len(df) >= self.warmup_bars

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.params})"
