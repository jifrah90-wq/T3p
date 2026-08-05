"""Broker abstraction.

Two implementations behind one interface:

  PaperBroker        -- fills against live mid prices with a slippage model.
                        No keys, no risk, identical decision path.
  HyperliquidBroker  -- real orders through the Hyperliquid exchange endpoint.

The runner only ever talks to this interface, so paper and live differ by one
line of config and nothing else. That is deliberate: the code that trades your
money should be the code you tested, not a variant of it.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from .marketdata import Instrument, MarketData

log = logging.getLogger(__name__)


@dataclass
class Fill:
    symbol: str
    size: float  # signed: positive bought, negative sold
    price: float
    fee: float
    order_id: str = ""
    is_reduce: bool = False


@dataclass
class OrderResult:
    ok: bool
    fill: Fill | None = None
    error: str = ""
    raw: dict | None = None


class Broker(ABC):
    @abstractmethod
    def market_order(
        self, instrument: Instrument, size: float, reduce_only: bool = False
    ) -> OrderResult:
        """Buy (size > 0) or sell (size < 0) `size` base units at market."""

    @abstractmethod
    def place_stop(
        self, instrument: Instrument, size: float, trigger_price: float
    ) -> OrderResult:
        """Rest a reduce-only stop that closes `size` when price trades through."""

    @abstractmethod
    def cancel_all(self, symbol: str) -> None: ...

    @abstractmethod
    def account_value(self) -> float: ...

    def set_leverage(self, instrument: Instrument, leverage: int) -> None:
        """Optional; venues that do not expose this can ignore it."""


class PaperBroker(Broker):
    """Simulated fills at the live mid, plus a spread and impact charge."""

    def __init__(
        self,
        market: MarketData,
        starting_equity: float,
        taker_fee: float = 0.00045,
        slippage: float = 0.0005,
    ):
        self.market = market
        self.equity = starting_equity
        self.taker_fee = taker_fee
        self.slippage = slippage
        self._counter = 0

    def _price(self, instrument: Instrument, side: int) -> float:
        mids = self.market.mid_prices()
        px = mids.get(instrument.name) or instrument.mark_px
        if not px:
            raise RuntimeError(f"no price available for {instrument.name}")
        # Cross the spread in the direction that hurts.
        return px * (1 + side * self.slippage)

    def market_order(
        self, instrument: Instrument, size: float, reduce_only: bool = False
    ) -> OrderResult:
        if size == 0:
            return OrderResult(ok=False, error="zero size")
        side = 1 if size > 0 else -1
        price = self._price(instrument, side)
        fee = abs(size) * price * self.taker_fee
        self._counter += 1
        log.info(
            "[paper] %s %s %.6f @ %.6f (fee %.4f)",
            "BUY" if side > 0 else "SELL",
            instrument.name,
            abs(size),
            price,
            fee,
        )
        return OrderResult(
            ok=True,
            fill=Fill(
                symbol=instrument.name,
                size=size,
                price=price,
                fee=fee,
                order_id=f"paper-{self._counter}",
                is_reduce=reduce_only,
            ),
        )

    def place_stop(
        self, instrument: Instrument, size: float, trigger_price: float
    ) -> OrderResult:
        # The paper runner enforces stops itself against live prices, so there
        # is nothing to rest on a venue here.
        return OrderResult(ok=True)

    def cancel_all(self, symbol: str) -> None:
        return None

    def account_value(self) -> float:
        return self.equity


class HyperliquidBroker(Broker):
    """Live trading against Hyperliquid.

    Requires HL_SECRET_KEY (an API wallet private key, not your seed phrase --
    generate one at app.hyperliquid.xyz under API, and never put a key with
    withdrawal rights in an env var).
    """

    def __init__(
        self,
        base_url: str,
        secret_key: str,
        account_address: str,
        market: MarketData,
        taker_fee: float = 0.00045,
        max_slippage: float = 0.004,
    ):
        if not secret_key:
            raise ValueError("HL_SECRET_KEY is not set; cannot trade live")

        # Imported lazily so paper mode never needs the signing stack.
        from eth_account import Account
        from hyperliquid.exchange import Exchange

        wallet = Account.from_key(secret_key)
        self.wallet_address = wallet.address
        self.account_address = account_address or wallet.address
        self.exchange = Exchange(
            wallet, base_url, account_address=self.account_address
        )
        self.info = market.info
        self.market = market
        self.taker_fee = taker_fee
        self.max_slippage = max_slippage
        log.info(
            "live broker ready: signer %s trading for %s",
            self.wallet_address,
            self.account_address,
        )

    def market_order(
        self, instrument: Instrument, size: float, reduce_only: bool = False
    ) -> OrderResult:
        size = instrument.round_size(size)
        if size == 0:
            return OrderResult(ok=False, error="size rounds to zero at venue precision")

        is_buy = size > 0
        try:
            if reduce_only:
                raw = self.exchange.market_close(
                    instrument.name, sz=abs(size), slippage=self.max_slippage
                )
            else:
                raw = self.exchange.market_open(
                    instrument.name,
                    is_buy,
                    abs(size),
                    slippage=self.max_slippage,
                )
        except Exception as exc:
            log.exception("order failed for %s", instrument.name)
            return OrderResult(ok=False, error=str(exc))

        return self._parse_fill(instrument, is_buy, raw, reduce_only)

    def _parse_fill(
        self, instrument: Instrument, is_buy: bool, raw: dict, reduce_only: bool
    ) -> OrderResult:
        if not isinstance(raw, dict) or raw.get("status") != "ok":
            return OrderResult(ok=False, error=str(raw), raw=raw)

        statuses = raw.get("response", {}).get("data", {}).get("statuses", [])
        filled_size = 0.0
        notional = 0.0
        order_id = ""
        for status in statuses:
            if "error" in status:
                return OrderResult(ok=False, error=status["error"], raw=raw)
            filled = status.get("filled")
            if filled:
                sz = float(filled["totalSz"])
                px = float(filled["avgPx"])
                filled_size += sz
                notional += sz * px
                order_id = str(filled.get("oid", ""))

        if filled_size == 0:
            # A resting order is not an error, but there is nothing to book yet.
            return OrderResult(ok=True, raw=raw)

        avg_px = notional / filled_size
        signed = filled_size if is_buy else -filled_size
        return OrderResult(
            ok=True,
            fill=Fill(
                symbol=instrument.name,
                size=signed,
                price=avg_px,
                fee=notional * self.taker_fee,
                order_id=order_id,
                is_reduce=reduce_only,
            ),
            raw=raw,
        )

    def place_stop(
        self, instrument: Instrument, size: float, trigger_price: float
    ) -> OrderResult:
        """Rest a reduce-only stop-market on the venue.

        `size` is the signed position being protected. This matters more than
        it looks: an exchange-side stop is the one protection that survives
        this process crashing, the network dropping, or the box rebooting.
        """
        qty = instrument.round_size(abs(size))
        trigger = instrument.round_price(trigger_price)
        if qty == 0 or trigger <= 0:
            return OrderResult(ok=False, error="invalid stop parameters")

        # Closing a long means selling, so the stop takes the opposite side.
        is_buy = size < 0
        # Once triggered this fills as a market order, but the wire format still
        # wants a limit price. Push it well through the trigger so it crosses.
        limit_px = instrument.round_price(
            trigger * (1 + (self.max_slippage if is_buy else -self.max_slippage))
        )
        order_type = {
            "trigger": {"triggerPx": trigger, "isMarket": True, "tpsl": "sl"}
        }
        try:
            raw = self.exchange.order(
                instrument.name,
                is_buy,
                qty,
                limit_px,
                order_type,
                reduce_only=True,
            )
        except Exception as exc:
            log.exception("could not place stop for %s", instrument.name)
            return OrderResult(ok=False, error=str(exc))

        if not isinstance(raw, dict) or raw.get("status") != "ok":
            return OrderResult(ok=False, error=str(raw), raw=raw)
        log.info("stop resting for %s: %.6f @ trigger %.6f", instrument.name, qty, trigger)
        return OrderResult(ok=True, raw=raw)

    def cancel_all(self, symbol: str) -> None:
        try:
            for order in self.info.open_orders(self.account_address):
                if order.get("coin") == symbol:
                    self.exchange.cancel(symbol, order["oid"])
        except Exception:
            log.exception("failed cancelling open orders for %s", symbol)

    def account_value(self) -> float:
        state = self.info.user_state(self.account_address)
        return float(state["marginSummary"]["accountValue"])

    def set_leverage(self, instrument: Instrument, leverage: int) -> None:
        leverage = max(1, min(int(leverage), instrument.max_leverage))
        try:
            self.exchange.update_leverage(leverage, instrument.name, is_cross=True)
        except Exception:
            log.warning("could not set leverage for %s", instrument.name, exc_info=True)
