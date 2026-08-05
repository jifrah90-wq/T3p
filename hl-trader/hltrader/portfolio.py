"""Position and portfolio bookkeeping.

Shared by the backtester and the live runner so both value the account the same
way. Sizes are signed: positive is long, negative is short.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone


@dataclass
class Position:
    symbol: str
    size: float
    entry_price: float
    stop_price: float
    target_price: float | None = None
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    strategy: str = ""
    # Realised funding paid (negative) or collected (positive) while open.
    funding_paid: float = 0.0

    @property
    def is_long(self) -> bool:
        return self.size > 0

    @property
    def direction(self) -> int:
        return 1 if self.size > 0 else -1

    def notional(self, price: float) -> float:
        return abs(self.size) * price

    def unrealized_pnl(self, price: float) -> float:
        return self.size * (price - self.entry_price)

    def stop_hit(self, low: float, high: float) -> bool:
        return low <= self.stop_price if self.is_long else high >= self.stop_price

    def target_hit(self, low: float, high: float) -> bool:
        if self.target_price is None:
            return False
        return high >= self.target_price if self.is_long else low <= self.target_price


@dataclass
class Trade:
    """A completed round trip, for the performance report."""

    symbol: str
    strategy: str
    direction: int
    size: float
    entry_price: float
    exit_price: float
    entry_time: datetime
    exit_time: datetime
    pnl: float
    fees: float
    funding: float
    exit_reason: str

    @property
    def return_pct(self) -> float:
        cost = abs(self.size) * self.entry_price
        return self.pnl / cost if cost else 0.0


class Portfolio:
    """Cash, open positions, and the running record of what they did."""

    def __init__(self, starting_equity: float):
        self.starting_equity = starting_equity
        self.cash = starting_equity
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []
        self.equity_curve: list[tuple[datetime, float]] = []

        self.high_water_mark = starting_equity
        self._day: date | None = None
        self._day_start_equity = starting_equity
        self.total_fees = 0.0
        self.total_funding = 0.0

    # -- valuation --------------------------------------------------------

    def equity(self, prices: dict[str, float]) -> float:
        """Cash plus mark-to-market on every open position."""
        total = self.cash
        for symbol, pos in self.positions.items():
            price = prices.get(symbol)
            if price is not None:
                total += pos.unrealized_pnl(price)
        return total

    def gross_notional(self, prices: dict[str, float]) -> float:
        return sum(
            pos.notional(prices[pos.symbol])
            for pos in self.positions.values()
            if pos.symbol in prices
        )

    def net_notional(self, prices: dict[str, float]) -> float:
        return sum(
            pos.size * prices[pos.symbol]
            for pos in self.positions.values()
            if pos.symbol in prices
        )

    # -- lifecycle --------------------------------------------------------

    def mark(self, when: datetime, prices: dict[str, float]) -> float:
        """Record equity at `when` and roll daily/drawdown counters."""
        equity = self.equity(prices)
        self.equity_curve.append((when, equity))
        self.high_water_mark = max(self.high_water_mark, equity)

        day = when.astimezone(timezone.utc).date()
        if self._day != day:
            self._day = day
            self._day_start_equity = equity
        return equity

    def open_position(
        self,
        symbol: str,
        size: float,
        price: float,
        stop_price: float,
        target_price: float | None,
        fee: float,
        when: datetime,
        strategy: str = "",
    ) -> Position:
        if symbol in self.positions:
            raise ValueError(f"{symbol} already has an open position")
        self.cash -= fee
        self.total_fees += fee
        pos = Position(
            symbol=symbol,
            size=size,
            entry_price=price,
            stop_price=stop_price,
            target_price=target_price,
            opened_at=when,
            strategy=strategy,
        )
        self.positions[symbol] = pos
        return pos

    def close_position(
        self, symbol: str, price: float, fee: float, when: datetime, reason: str
    ) -> Trade:
        pos = self.positions.pop(symbol)
        pnl = pos.unrealized_pnl(price)
        self.cash += pnl - fee
        self.total_fees += fee
        trade = Trade(
            symbol=symbol,
            strategy=pos.strategy,
            direction=pos.direction,
            size=pos.size,
            entry_price=pos.entry_price,
            exit_price=price,
            entry_time=pos.opened_at,
            exit_time=when,
            pnl=pnl + pos.funding_paid,
            fees=fee,
            funding=pos.funding_paid,
            exit_reason=reason,
        )
        self.trades.append(trade)
        return trade

    def apply_funding(self, symbol: str, rate: float, price: float) -> float:
        """Charge or credit one funding period on an open position.

        Positive `rate` means longs pay shorts, so a long loses and a short
        gains. Settled into cash immediately, as the venue does.
        """
        pos = self.positions.get(symbol)
        if pos is None:
            return 0.0
        amount = -pos.size * price * rate
        pos.funding_paid += amount
        self.cash += amount
        self.total_funding += amount
        return amount

    # -- drawdown state ---------------------------------------------------

    def daily_pnl_pct(self, equity: float) -> float:
        if self._day_start_equity <= 0:
            return 0.0
        return equity / self._day_start_equity - 1.0

    def drawdown_pct(self, equity: float) -> float:
        """Current drawdown from the high-water mark, as a positive fraction."""
        if self.high_water_mark <= 0:
            return 0.0
        return max(0.0, 1.0 - equity / self.high_water_mark)
