"""Configuration loading and validation.

Config comes from three layers, later layers win:
  1. defaults defined here
  2. a YAML file (config/default.yaml)
  3. environment variables (secrets only -- never put keys in YAML)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, get_type_hints

import yaml

MAINNET_URL = "https://api.hyperliquid.xyz"
TESTNET_URL = "https://api.hyperliquid-testnet.xyz"


@dataclass
class RiskConfig:
    # Fraction of equity risked on a single trade if its stop is hit.
    risk_per_trade: float = 0.01
    # Hard cap on notional of one position as a multiple of equity.
    max_position_leverage: float = 3.0
    # Cap on the sum of absolute notionals across all positions.
    max_gross_leverage: float = 4.0
    # Cap on the net (long minus short) notional.
    max_net_leverage: float = 2.5
    max_concurrent_positions: int = 6
    # Per-venue leverage setting sent to Hyperliquid for each asset.
    venue_leverage: int = 5
    # Trading halts for the rest of the UTC day past this daily loss.
    daily_loss_limit: float = 0.06
    # Trading halts permanently (until manually reset) past this drawdown
    # from the high-water mark.
    max_drawdown_limit: float = 0.20
    # Positions smaller than this in USD are not worth the fees.
    min_order_notional: float = 12.0
    # Refuse to cross the book by more than this to get filled.
    max_slippage: float = 0.004


@dataclass
class UniverseConfig:
    # Only trade assets with at least this much 24h notional volume.
    min_daily_volume_usd: float = 5_000_000.0
    # Only trade assets with at least this much open interest (USD).
    min_open_interest_usd: float = 1_000_000.0
    max_symbols: int = 40
    # Never trade these, whatever the screen says.
    blacklist: list[str] = field(default_factory=list)
    # Always consider these, even if they fail the liquidity screen.
    whitelist: list[str] = field(default_factory=list)
    include_spot: bool = False


@dataclass
class ExecutionConfig:
    # "ioc" crosses the spread; "alo" posts and waits (maker rebate, may miss).
    order_style: str = "ioc"
    # Seconds to wait for a resting order before giving up on it.
    order_timeout_s: float = 20.0
    # Attach a reduce-only trigger order for the stop after entry.
    use_exchange_stops: bool = True
    dry_run: bool = True


@dataclass
class StrategyConfig:
    name: str
    weight: float = 1.0
    params: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


@dataclass
class Config:
    network: str = "testnet"
    account_address: str = ""
    # Bar size the strategies run on.
    interval: str = "1h"
    # How many bars of history to pull for each symbol.
    lookback_bars: int = 1200
    # Seconds between decision cycles.
    poll_seconds: float = 60.0
    state_dir: str = "state"
    log_level: str = "INFO"
    starting_equity: float = 10_000.0
    # Round-trip fee assumption used by the sizer and the backtester.
    taker_fee: float = 0.00045
    maker_fee: float = 0.00015
    risk: RiskConfig = field(default_factory=RiskConfig)
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    strategies: list[StrategyConfig] = field(default_factory=list)

    @property
    def base_url(self) -> str:
        if self.network == "mainnet":
            return MAINNET_URL
        return TESTNET_URL

    @property
    def secret_key(self) -> str:
        return os.environ.get("HL_SECRET_KEY", "")

    def validate(self) -> None:
        if self.network not in ("mainnet", "testnet"):
            raise ValueError(f"network must be mainnet or testnet, got {self.network!r}")
        r = self.risk
        if not 0 < r.risk_per_trade <= 0.25:
            raise ValueError("risk.risk_per_trade must be in (0, 0.25]")
        if r.max_position_leverage <= 0 or r.max_gross_leverage <= 0:
            raise ValueError("leverage caps must be positive")
        if r.max_position_leverage > r.max_gross_leverage:
            raise ValueError("max_position_leverage cannot exceed max_gross_leverage")
        if not 0 < r.daily_loss_limit < 1 or not 0 < r.max_drawdown_limit < 1:
            raise ValueError("loss limits must be fractions in (0, 1)")
        if self.execution.order_style not in ("ioc", "alo"):
            raise ValueError("execution.order_style must be 'ioc' or 'alo'")
        if not self.strategies:
            raise ValueError("at least one strategy must be configured")
        if all(not s.enabled for s in self.strategies):
            raise ValueError("all strategies are disabled")


def _build(cls: type, data: dict[str, Any]) -> Any:
    """Instantiate a dataclass from a dict, recursing into nested dataclasses."""
    # `from __future__ import annotations` makes field.type a string, so resolve
    # the real classes before checking for nested dataclasses.
    hints = get_type_hints(cls)
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"unknown config keys for {cls.__name__}: {sorted(unknown)}")
    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        ftype = hints[key]
        if is_dataclass(ftype) and isinstance(value, dict):
            kwargs[key] = _build(ftype, value)
        else:
            kwargs[key] = value
    return cls(**kwargs)


def load_config(path: str | Path) -> Config:
    path = Path(path)
    raw = yaml.safe_load(path.read_text()) or {}

    strategies = [_build(StrategyConfig, s) for s in raw.pop("strategies", [])]
    cfg = _build(Config, raw)
    cfg.strategies = strategies

    # Environment overrides for the handful of things that vary per deployment.
    if env_net := os.environ.get("HL_NETWORK"):
        cfg.network = env_net
    if env_addr := os.environ.get("HL_ACCOUNT_ADDRESS"):
        cfg.account_address = env_addr
    if os.environ.get("HL_LIVE") == "1":
        cfg.execution.dry_run = False

    cfg.validate()
    return cfg
