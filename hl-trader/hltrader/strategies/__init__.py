"""Strategy registry.

`build_strategy` is the only thing config touches, so adding a strategy means
adding one entry to REGISTRY and nothing else changes.
"""

from __future__ import annotations

from typing import Any

from .base import Inverted, Signal, Strategy
from .funding_carry import FundingCarry
from .mean_reversion import MeanReversion
from .momentum import CrossSectionalMomentum
from .trend_breakout import TrendBreakout

REGISTRY: dict[str, type[Strategy]] = {
    TrendBreakout.name: TrendBreakout,
    MeanReversion.name: MeanReversion,
    CrossSectionalMomentum.name: CrossSectionalMomentum,
    FundingCarry.name: FundingCarry,
}


def build_strategy(
    name: str, params: dict[str, Any] | None = None, invert: bool = False
) -> Strategy:
    """Build a strategy by name. An `inverted_` prefix flips its signals."""
    if name.startswith("inverted_"):
        name, invert = name[len("inverted_") :], True
    try:
        cls = REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"unknown strategy {name!r}; available: {sorted(REGISTRY)}"
        ) from None
    strategy = cls(**(params or {}))
    return Inverted(strategy) if invert else strategy


__all__ = [
    "REGISTRY",
    "Inverted",
    "Signal",
    "Strategy",
    "build_strategy",
    "TrendBreakout",
    "MeanReversion",
    "CrossSectionalMomentum",
    "FundingCarry",
]
