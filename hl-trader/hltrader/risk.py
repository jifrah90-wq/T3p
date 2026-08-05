"""Risk management: position sizing and the limits that stop the bleeding.

Every order in the system passes through here. A strategy can only ever ask for
a direction; this module decides whether the trade happens at all and how big
it is. The halt checks are deliberately blunt -- when an account is down, the
correct action is to stop, not to size cleverly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import RiskConfig
from .portfolio import Portfolio
from .strategies.base import Signal

log = logging.getLogger(__name__)


@dataclass
class SizedOrder:
    symbol: str
    # Signed target size in base units: positive long, negative short.
    size: float
    notional: float
    stop_price: float
    target_price: float | None
    reference_price: float
    strategy: str
    reason: str


@dataclass
class HaltState:
    halted: bool
    reason: str = ""
    # A daily halt lifts at the next UTC day; a hard halt needs a human.
    hard: bool = False


class RiskManager:
    def __init__(self, cfg: RiskConfig, taker_fee: float = 0.00045):
        self.cfg = cfg
        self.taker_fee = taker_fee
        self._hard_halt_reason = ""

    # -- circuit breakers -------------------------------------------------

    def check_halt(self, portfolio: Portfolio, equity: float) -> HaltState:
        """Decide whether trading is allowed at all right now."""
        if self._hard_halt_reason:
            return HaltState(True, self._hard_halt_reason, hard=True)

        drawdown = portfolio.drawdown_pct(equity)
        if drawdown >= self.cfg.max_drawdown_limit:
            self._hard_halt_reason = (
                f"max drawdown breached: {drawdown:.1%} from high-water mark "
                f"{portfolio.high_water_mark:,.0f} (limit {self.cfg.max_drawdown_limit:.0%})"
            )
            log.error("HARD HALT -- %s", self._hard_halt_reason)
            return HaltState(True, self._hard_halt_reason, hard=True)

        daily = portfolio.daily_pnl_pct(equity)
        if daily <= -self.cfg.daily_loss_limit:
            reason = (
                f"daily loss limit hit: {daily:.1%} today "
                f"(limit {self.cfg.daily_loss_limit:.0%})"
            )
            log.warning("DAILY HALT -- %s", reason)
            return HaltState(True, reason, hard=False)

        if equity <= 0:
            self._hard_halt_reason = "equity is zero or negative"
            return HaltState(True, self._hard_halt_reason, hard=True)

        return HaltState(False)

    def reset_hard_halt(self) -> None:
        """Clear a hard halt. Deliberately manual -- a human must look first."""
        log.warning("hard halt cleared manually (was: %s)", self._hard_halt_reason)
        self._hard_halt_reason = ""

    # -- sizing -----------------------------------------------------------

    def size_order(
        self,
        signal: Signal,
        price: float,
        equity: float,
        portfolio: Portfolio,
        prices: dict[str, float],
        strategy: str = "",
        max_venue_leverage: int = 50,
    ) -> SizedOrder | None:
        """Turn a signal into a concrete size, or None if it should not trade.

        Sizing is risk-first: the position is whatever size makes a stop-out
        cost exactly `risk_per_trade` of equity. That keeps the loss per trade
        constant across assets no matter how volatile each one is, which is the
        whole point -- a wide-stop altcoin gets a small position, not a big one.
        """
        if signal.is_flat or price <= 0 or equity <= 0:
            return None
        if signal.symbol in portfolio.positions:
            return None  # already positioned; the runner handles exits

        if len(portfolio.positions) >= self.cfg.max_concurrent_positions:
            log.debug("skip %s: at max concurrent positions", signal.symbol)
            return None

        # Round-trip fees widen the effective loss on a stop-out, so charge
        # them against the risk budget rather than discovering them later.
        effective_stop = signal.stop_distance + 2 * self.taker_fee
        if effective_stop <= 0:
            return None

        risk_budget = equity * self.cfg.risk_per_trade * abs(signal.direction)
        notional = risk_budget / effective_stop

        # Cap 1: no single position may exceed the per-position leverage cap.
        notional = min(notional, equity * self.cfg.max_position_leverage)
        # Cap 2: the venue's own leverage limit for this asset.
        notional = min(notional, equity * max_venue_leverage)

        # Cap 3: whatever gross leverage headroom is left.
        gross = portfolio.gross_notional(prices)
        gross_room = max(0.0, equity * self.cfg.max_gross_leverage - gross)
        notional = min(notional, gross_room)

        # Cap 4: net directional exposure, which is the risk that actually
        # hurts when the whole crypto complex moves together.
        # Adding `n` of exposure must keep |net| within the limit: going long
        # eats headroom above, going short eats headroom below.
        net = portfolio.net_notional(prices)
        net_limit = equity * self.cfg.max_net_leverage
        net_room = net_limit - net if signal.direction > 0 else net_limit + net
        notional = min(notional, max(0.0, net_room))

        if notional < self.cfg.min_order_notional:
            log.debug(
                "skip %s: sized notional %.2f below minimum %.2f",
                signal.symbol,
                notional,
                self.cfg.min_order_notional,
            )
            return None

        size = notional / price
        direction = 1 if signal.direction > 0 else -1
        stop_price = price * (1 - direction * signal.stop_distance)
        target_price = (
            price * (1 + direction * signal.target_distance)
            if signal.target_distance
            else None
        )

        return SizedOrder(
            symbol=signal.symbol,
            size=size * direction,
            notional=notional,
            stop_price=stop_price,
            target_price=target_price,
            reference_price=price,
            strategy=strategy,
            reason=signal.reason,
        )
