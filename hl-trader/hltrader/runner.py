"""The live/paper trading loop.

One cycle:
  1. refresh the universe and pull fresh candles
  2. reconcile local state against what the venue actually holds
  3. value the account, check the circuit breakers
  4. manage open positions (stops, targets, strategy exits)
  5. generate signals, size them, place the survivors

Live and paper differ only in which Broker is injected.
"""

from __future__ import annotations

import logging
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .broker import Broker, HyperliquidBroker, PaperBroker
from .config import Config
from .marketdata import MarketData
from .portfolio import Portfolio
from .risk import RiskManager, SizedOrder
from .state import RunState, StateStore, TradeLog
from .strategies import Strategy, build_strategy
from .strategies.funding_carry import FundingCarry
from .strategies.momentum import CrossSectionalMomentum

log = logging.getLogger(__name__)


@dataclass
class CycleReport:
    equity: float
    open_positions: int
    gross_leverage: float
    halted: bool
    halt_reason: str
    orders_placed: int
    exits: int


class Runner:
    def __init__(self, cfg: Config, broker: Broker | None = None):
        self.cfg = cfg
        self.market = MarketData(cfg.base_url, cache_dir=Path(cfg.state_dir) / "cache")
        self.broker = broker or self._build_broker()
        self.strategies: list[tuple[Strategy, float]] = [
            (build_strategy(s.name, s.params), s.weight)
            for s in cfg.strategies
            if s.enabled
        ]
        self.risk = RiskManager(cfg.risk, taker_fee=cfg.taker_fee)

        state_dir = Path(cfg.state_dir)
        self.store = StateStore(state_dir / "state.json")
        self.trade_log = TradeLog(state_dir / "trades.jsonl")
        self.state: RunState = self.store.load(cfg.starting_equity)
        if self.state.hard_halt_reason:
            self.risk._hard_halt_reason = self.state.hard_halt_reason

        self.portfolio = Portfolio(cfg.starting_equity)
        self.portfolio.high_water_mark = self.state.high_water_mark or cfg.starting_equity
        self._stop_requested = False
        self._universe: list[str] = []

    def _build_broker(self) -> Broker:
        if self.cfg.execution.dry_run:
            log.info("starting in PAPER mode -- no real orders will be sent")
            return PaperBroker(
                self.market,
                self.cfg.starting_equity,
                taker_fee=self.cfg.taker_fee,
                slippage=self.cfg.risk.max_slippage / 4,
            )
        log.warning("starting in LIVE mode on %s -- real money", self.cfg.network)
        return HyperliquidBroker(
            self.cfg.base_url,
            self.cfg.secret_key,
            self.cfg.account_address,
            self.market,
            taker_fee=self.cfg.taker_fee,
            max_slippage=self.cfg.risk.max_slippage,
        )

    # -- main loop --------------------------------------------------------

    def run(self, max_cycles: int | None = None) -> None:
        self._install_signal_handlers()
        log.info(
            "runner started: %s | %s | %d strategies | interval %s",
            self.cfg.network,
            "paper" if self.cfg.execution.dry_run else "LIVE",
            len(self.strategies),
            self.cfg.interval,
        )
        cycles = 0
        while not self._stop_requested:
            started = time.time()
            try:
                report = self.run_cycle()
                log.info(
                    "cycle %d | equity %.2f | %d positions | gross %.2fx | %s",
                    self.state.cycles,
                    report.equity,
                    report.open_positions,
                    report.gross_leverage,
                    report.halt_reason or "trading",
                )
            except KeyboardInterrupt:
                break
            except Exception:
                # A single bad cycle (network blip, venue hiccup) must not take
                # the process down and leave positions unmanaged.
                log.exception("cycle failed; continuing")

            cycles += 1
            if max_cycles and cycles >= max_cycles:
                break
            elapsed = time.time() - started
            self._sleep(max(0.0, self.cfg.poll_seconds - elapsed))

        log.info("runner stopped after %d cycles", cycles)

    def run_cycle(self) -> CycleReport:
        now = datetime.now(timezone.utc)

        if not self._universe or self.state.cycles % 30 == 0:
            self._universe = self.market.select_universe(
                min_volume_usd=self.cfg.universe.min_daily_volume_usd,
                min_open_interest_usd=self.cfg.universe.min_open_interest_usd,
                max_symbols=self.cfg.universe.max_symbols,
                blacklist=self.cfg.universe.blacklist,
                whitelist=self.cfg.universe.whitelist,
                include_spot=self.cfg.universe.include_spot,
            )
            log.info("universe: %d symbols", len(self._universe))

        frames = self._load_frames()
        prices = {
            symbol: float(df["close"].iloc[-1])
            for symbol, df in frames.items()
            if not df.empty
        }
        if not prices:
            raise RuntimeError("no prices available this cycle")

        self._reconcile(prices)
        equity = self._equity(prices)
        self.portfolio.mark(now, prices)
        self.state.roll_day(equity, now)
        self.portfolio.high_water_mark = max(self.portfolio.high_water_mark, equity)
        self.portfolio._day_start_equity = self.state.day_start_equity

        halt = self.risk.check_halt(self.portfolio, equity)
        exits = self._manage_positions(prices, frames, halt.halted)

        placed = 0
        if not halt.halted:
            placed = self._open_new(frames, prices, equity)
        elif halt.hard:
            log.error("hard halt active -- flattening and standing down")
            exits += self._flatten("hard halt")

        self.state.cycles += 1
        self.state.high_water_mark = self.portfolio.high_water_mark
        self.state.hard_halt_reason = self.risk._hard_halt_reason
        self.state.stops = {s: p.stop_price for s, p in self.portfolio.positions.items()}
        self.state.position_strategy = {
            s: p.strategy for s, p in self.portfolio.positions.items()
        }
        self.store.save(self.state)

        gross = self.portfolio.gross_notional(prices)
        return CycleReport(
            equity=equity,
            open_positions=len(self.portfolio.positions),
            gross_leverage=gross / equity if equity > 0 else 0.0,
            halted=halt.halted,
            halt_reason=halt.reason,
            orders_placed=placed,
            exits=exits,
        )

    # -- cycle steps ------------------------------------------------------

    def _load_frames(self) -> dict[str, pd.DataFrame]:
        frames: dict[str, pd.DataFrame] = {}
        for symbol in self._universe:
            try:
                df = self.market.candles(symbol, self.cfg.interval, self.cfg.lookback_bars)
            except Exception:
                log.warning("could not load candles for %s", symbol, exc_info=True)
                continue
            if not df.empty:
                frames[symbol] = df
        return frames

    def _equity(self, prices: dict[str, float]) -> float:
        if self.cfg.execution.dry_run:
            return self.portfolio.equity(prices)
        try:
            return self.broker.account_value()
        except Exception:
            log.exception("could not read account value; falling back to local mark")
            return self.portfolio.equity(prices)

    def _reconcile(self, prices: dict[str, float]) -> None:
        """Make local state match the venue.

        Positions can change underneath us: an exchange stop fires, a position
        gets liquidated, or someone trades the account by hand. Trusting local
        state after any of those is how a bot ends up doubling a position it
        thinks it does not have.
        """
        if self.cfg.execution.dry_run:
            return
        try:
            state = self.broker.info.user_state(self.broker.account_address)
        except Exception:
            log.exception("could not reconcile with venue; skipping this cycle's sync")
            return

        venue: dict[str, float] = {}
        for entry in state.get("assetPositions", []):
            pos = entry.get("position", {})
            size = float(pos.get("szi", 0))
            if size != 0:
                venue[pos["coin"]] = size

        for symbol in list(self.portfolio.positions):
            if symbol not in venue:
                price = prices.get(symbol, self.portfolio.positions[symbol].entry_price)
                log.warning("%s closed at venue but open locally; booking it", symbol)
                self.portfolio.close_position(symbol, price, 0.0, datetime.now(timezone.utc), "closed at venue")
                self.trade_log.record("reconcile_close", symbol=symbol, price=price)

        for symbol, size in venue.items():
            local = self.portfolio.positions.get(symbol)
            if local is None:
                price = prices.get(symbol)
                if price is None:
                    continue
                log.warning("%s open at venue but not locally; adopting it", symbol)
                stop = self.state.stops.get(symbol)
                direction = 1 if size > 0 else -1
                self.portfolio.open_position(
                    symbol,
                    size,
                    price,
                    stop or price * (1 - direction * self.cfg.risk.risk_per_trade * 3),
                    None,
                    0.0,
                    datetime.now(timezone.utc),
                    self.state.position_strategy.get(symbol, "adopted"),
                )
            elif abs(local.size - size) > abs(size) * 0.01:
                log.warning(
                    "%s size drift: local %.6f vs venue %.6f; trusting venue",
                    symbol,
                    local.size,
                    size,
                )
                local.size = size

    def _manage_positions(
        self, prices: dict[str, float], frames: dict[str, pd.DataFrame], halted: bool
    ) -> int:
        """Close anything that hit its stop/target or lost its thesis."""
        exits = 0
        for symbol in list(self.portfolio.positions):
            pos = self.portfolio.positions[symbol]
            price = prices.get(symbol)
            if price is None:
                continue

            reason = ""
            if (pos.is_long and price <= pos.stop_price) or (
                not pos.is_long and price >= pos.stop_price
            ):
                reason = "stop"
            elif pos.target_price and (
                (pos.is_long and price >= pos.target_price)
                or (not pos.is_long and price <= pos.target_price)
            ):
                reason = "target"
            elif not halted:
                df = frames.get(symbol)
                if df is not None:
                    for strategy, _ in self.strategies:
                        if strategy.name != pos.strategy:
                            continue
                        sig = strategy.generate(symbol, df)
                        # Flat, or flipped against the position: thesis is gone.
                        if sig is not None and (
                            sig.is_flat or sig.direction * pos.direction < 0
                        ):
                            reason = "strategy exit"

            if reason and self._close(symbol, price, reason):
                exits += 1
        return exits

    def _close(self, symbol: str, price: float, reason: str) -> bool:
        pos = self.portfolio.positions.get(symbol)
        if pos is None:
            return False
        instrument = self.market.instrument(symbol)
        result = self.broker.market_order(instrument, -pos.size, reduce_only=True)
        if not result.ok:
            log.error("failed to close %s: %s", symbol, result.error)
            return False

        self.broker.cancel_all(symbol)
        fill_price = result.fill.price if result.fill else price
        fee = result.fill.fee if result.fill else 0.0
        trade = self.portfolio.close_position(
            symbol, fill_price, fee, datetime.now(timezone.utc), reason
        )
        self.state.realized_pnl += trade.pnl
        self.trade_log.record(
            "close",
            symbol=symbol,
            reason=reason,
            price=fill_price,
            size=trade.size,
            pnl=trade.pnl,
            strategy=trade.strategy,
        )
        log.info("closed %s (%s) pnl %.2f", symbol, reason, trade.pnl)
        return True

    def _open_new(
        self, frames: dict[str, pd.DataFrame], prices: dict[str, float], equity: float
    ) -> int:
        # Cross-sectional and funding strategies need universe-wide context
        # before any single symbol can be scored.
        for strategy, _ in self.strategies:
            if isinstance(strategy, CrossSectionalMomentum):
                strategy.rank_universe(frames)
            elif isinstance(strategy, FundingCarry):
                strategy.set_funding(self.market.funding_rates())

        orders: list[SizedOrder] = []
        for symbol, df in frames.items():
            if symbol in self.portfolio.positions:
                continue
            best_signal = None
            best_name = ""
            best_score = 0.0
            for strategy, weight in self.strategies:
                try:
                    sig = strategy.generate(symbol, df)
                except Exception:
                    log.warning("%s failed on %s", strategy.name, symbol, exc_info=True)
                    continue
                if sig is None or sig.is_flat:
                    continue
                score = weight * abs(sig.direction)
                if score > best_score:
                    best_signal, best_name, best_score = sig, strategy.name, score

            if best_signal is None:
                continue
            order = self.risk.size_order(
                best_signal,
                prices[symbol],
                equity,
                self.portfolio,
                prices,
                strategy=best_name,
                max_venue_leverage=self.market.instrument(symbol).max_leverage,
            )
            if order:
                orders.append(order)

        orders.sort(key=lambda o: o.notional, reverse=True)
        placed = 0
        for order in orders:
            # Re-check limits between orders: each fill consumes headroom.
            if len(self.portfolio.positions) >= self.cfg.risk.max_concurrent_positions:
                break
            if self._place(order, equity, prices):
                placed += 1
        return placed

    def _place(self, order: SizedOrder, equity: float, prices: dict[str, float]) -> bool:
        instrument = self.market.instrument(order.symbol)
        size = instrument.round_size(order.size)
        if size == 0 or abs(size) * order.reference_price < self.cfg.risk.min_order_notional:
            return False

        if not self.cfg.execution.dry_run:
            self.broker.set_leverage(instrument, self.cfg.risk.venue_leverage)

        result = self.broker.market_order(instrument, size)
        if not result.ok or result.fill is None:
            log.error("order rejected for %s: %s", order.symbol, result.error or "no fill")
            return False

        fill = result.fill
        direction = 1 if fill.size > 0 else -1
        # Anchor the stop to the real fill, not to the price we hoped for.
        stop_distance = abs(order.reference_price - order.stop_price) / order.reference_price
        stop_price = fill.price * (1 - direction * stop_distance)
        target_price = None
        if order.target_price:
            target_distance = (
                abs(order.target_price - order.reference_price) / order.reference_price
            )
            target_price = fill.price * (1 + direction * target_distance)

        self.portfolio.open_position(
            order.symbol,
            fill.size,
            fill.price,
            stop_price,
            target_price,
            fill.fee,
            datetime.now(timezone.utc),
            order.strategy,
        )

        if self.cfg.execution.use_exchange_stops and not self.cfg.execution.dry_run:
            stop_result = self.broker.place_stop(instrument, fill.size, stop_price)
            if not stop_result.ok:
                # An unprotected position is worse than no position.
                log.error(
                    "stop rejected for %s (%s); closing the position immediately",
                    order.symbol,
                    stop_result.error,
                )
                self._close(order.symbol, fill.price, "no stop protection")
                return False

        self.trade_log.record(
            "open",
            symbol=order.symbol,
            size=fill.size,
            price=fill.price,
            stop=stop_price,
            target=target_price,
            strategy=order.strategy,
            reason=order.reason,
        )
        log.info(
            "opened %s %s %.6f @ %.6f stop %.6f (%s: %s)",
            "LONG" if direction > 0 else "SHORT",
            order.symbol,
            abs(fill.size),
            fill.price,
            stop_price,
            order.strategy,
            order.reason,
        )
        return True

    def _flatten(self, reason: str) -> int:
        closed = 0
        prices = self.market.mid_prices()
        for symbol in list(self.portfolio.positions):
            price = prices.get(symbol, self.portfolio.positions[symbol].entry_price)
            if self._close(symbol, price, reason):
                closed += 1
        return closed

    # -- lifecycle --------------------------------------------------------

    def _install_signal_handlers(self) -> None:
        def handler(signum, _frame):
            log.warning("signal %s received; finishing this cycle then stopping", signum)
            self._stop_requested = True

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handler)
            except ValueError:
                pass  # not on the main thread

    def _sleep(self, seconds: float) -> None:
        """Sleep in slices so a stop signal is honoured promptly."""
        deadline = time.time() + seconds
        while time.time() < deadline and not self._stop_requested:
            time.sleep(min(1.0, deadline - time.time()))
