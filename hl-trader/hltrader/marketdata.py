"""Market data access: instrument metadata, the tradable universe, and candles.

This wraps the Hyperliquid `Info` endpoint. Everything is read-only and needs
no wallet, so it works identically in backtest, paper and live modes.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from hyperliquid.info import Info

log = logging.getLogger(__name__)

# Bar sizes Hyperliquid serves, in milliseconds.
INTERVAL_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}

# The API caps a single candleSnapshot response; page below this.
MAX_CANDLES_PER_REQUEST = 5000


@dataclass(frozen=True)
class Instrument:
    """Everything needed to size and price an order for one market."""

    name: str
    sz_decimals: int
    max_leverage: int
    is_spot: bool = False
    # Snapshot stats used by the liquidity screen.
    day_volume_usd: float = 0.0
    open_interest_usd: float = 0.0
    mark_px: float = 0.0
    funding_rate: float = 0.0

    @property
    def px_decimals(self) -> int:
        """Max decimal places Hyperliquid accepts for a limit price."""
        return (8 if self.is_spot else 6) - self.sz_decimals

    def round_size(self, size: float) -> float:
        return round(size, self.sz_decimals)

    def round_price(self, price: float) -> float:
        """Hyperliquid takes at most 5 significant figures AND a decimal cap.

        Integers are always accepted regardless of significant figures, which
        matters for high-priced assets like BTC.
        """
        if price <= 0:
            return 0.0
        sig = 5 - math.floor(math.log10(price)) - 1
        return round(float(f"{price:.5g}"), max(0, min(sig, self.px_decimals)))


class MarketData:
    def __init__(self, base_url: str, cache_dir: str | Path | None = None):
        self.info = Info(base_url, skip_ws=True)
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._instruments: dict[str, Instrument] = {}

    # -- instruments ------------------------------------------------------

    def refresh_instruments(self, include_spot: bool = False) -> dict[str, Instrument]:
        """Pull metadata + live context for every market on the venue."""
        instruments: dict[str, Instrument] = {}

        meta, ctxs = self.info.meta_and_asset_ctxs()
        for asset, ctx in zip(meta["universe"], ctxs):
            if asset.get("isDelisted"):
                continue
            mark = _f(ctx.get("markPx"))
            oi = _f(ctx.get("openInterest")) * mark
            instruments[asset["name"]] = Instrument(
                name=asset["name"],
                sz_decimals=asset["szDecimals"],
                max_leverage=asset.get("maxLeverage", 1),
                is_spot=False,
                day_volume_usd=_f(ctx.get("dayNtlVlm")),
                open_interest_usd=oi,
                mark_px=mark,
                funding_rate=_f(ctx.get("funding")),
            )

        if include_spot:
            spot_meta, spot_ctxs = self.info.spot_meta_and_asset_ctxs()
            tokens = {t["index"]: t for t in spot_meta["tokens"]}
            for pair, ctx in zip(spot_meta["universe"], spot_ctxs):
                base = tokens.get(pair["tokens"][0])
                if base is None:
                    continue
                mark = _f(ctx.get("markPx"))
                # Spot names on the wire are "@<index>" for non-canonical pairs;
                # the display name is what the order endpoint accepts.
                instruments[pair["name"]] = Instrument(
                    name=pair["name"],
                    sz_decimals=base["szDecimals"],
                    max_leverage=1,
                    is_spot=True,
                    day_volume_usd=_f(ctx.get("dayNtlVlm")),
                    open_interest_usd=float("inf"),
                    mark_px=mark,
                )

        self._instruments = instruments
        log.info("loaded %d instruments", len(instruments))
        return instruments

    @property
    def instruments(self) -> dict[str, Instrument]:
        if not self._instruments:
            self.refresh_instruments()
        return self._instruments

    def instrument(self, symbol: str) -> Instrument:
        try:
            return self.instruments[symbol]
        except KeyError:
            raise KeyError(f"unknown instrument {symbol!r}") from None

    def select_universe(
        self,
        min_volume_usd: float,
        min_open_interest_usd: float,
        max_symbols: int,
        blacklist: list[str] | None = None,
        whitelist: list[str] | None = None,
        include_spot: bool = False,
    ) -> list[str]:
        """Rank every market by liquidity and keep the tradable top slice.

        Illiquid markets are where a small account quietly bleeds to slippage,
        so this screen is the first line of risk control, not a convenience.
        """
        instruments = self.refresh_instruments(include_spot=include_spot)
        blocked = set(blacklist or [])
        forced = [s for s in (whitelist or []) if s in instruments]

        candidates = [
            inst
            for name, inst in instruments.items()
            if name not in blocked
            and name not in forced
            and inst.mark_px > 0
            and inst.day_volume_usd >= min_volume_usd
            and inst.open_interest_usd >= min_open_interest_usd
        ]
        candidates.sort(key=lambda i: i.day_volume_usd, reverse=True)

        selected = forced + [i.name for i in candidates]
        dropped = len(selected) - max_symbols
        if dropped > 0:
            log.info("universe truncated to %d symbols (%d dropped)", max_symbols, dropped)
        return selected[:max_symbols]

    # -- candles ----------------------------------------------------------

    def candles(
        self,
        symbol: str,
        interval: str,
        bars: int,
        end_ms: int | None = None,
        use_cache: bool = False,
    ) -> pd.DataFrame:
        """Fetch `bars` candles ending at `end_ms`, paging as needed.

        Returns a DataFrame indexed by bar open time (UTC) with float columns
        open/high/low/close/volume/trades. The final bar may still be forming.
        """
        if interval not in INTERVAL_MS:
            raise ValueError(f"unsupported interval {interval!r}")
        step = INTERVAL_MS[interval]
        end_ms = end_ms or int(time.time() * 1000)
        start_ms = end_ms - bars * step

        cache_path = None
        if use_cache and self.cache_dir:
            cache_path = self.cache_dir / f"{_safe(symbol)}_{interval}.parquet"
            cached = _read_cache(cache_path)
            if cached is not None and not cached.empty:
                have_from = int(cached.index[0].timestamp() * 1000)
                have_to = int(cached.index[-1].timestamp() * 1000)
                if have_from <= start_ms and have_to >= end_ms - 2 * step:
                    return cached.loc[cached.index >= pd.Timestamp(start_ms, unit="ms", tz="UTC")]

        rows: list[dict] = []
        cursor = start_ms
        while cursor < end_ms:
            chunk_end = min(cursor + MAX_CANDLES_PER_REQUEST * step, end_ms)
            batch = self.info.candles_snapshot(symbol, interval, cursor, chunk_end)
            if not batch:
                break
            rows.extend(batch)
            last_open = batch[-1]["t"]
            # Guard against a venue that returns the same page forever.
            if last_open + step <= cursor:
                break
            cursor = last_open + step

        if not rows:
            return _empty_frame()

        df = pd.DataFrame(rows)
        df = df.rename(
            columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume", "n": "trades"}
        )
        df["timestamp"] = pd.to_datetime(df["t"], unit="ms", utc=True)
        df = df[["timestamp", "open", "high", "low", "close", "volume", "trades"]]
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = df[col].astype(float)
        df["trades"] = df["trades"].astype(int)
        df = df.drop_duplicates("timestamp").set_index("timestamp").sort_index()

        if cache_path is not None:
            _write_cache(cache_path, df)
        return df

    def mid_prices(self) -> dict[str, float]:
        return {k: float(v) for k, v in self.info.all_mids().items()}

    def funding_rates(self) -> dict[str, float]:
        """Current hourly funding rate per perp, positive = longs pay shorts."""
        return {
            name: inst.funding_rate
            for name, inst in self.instruments.items()
            if not inst.is_spot
        }


def _f(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe(symbol: str) -> str:
    return symbol.replace("/", "-").replace("@", "at")


def _empty_frame() -> pd.DataFrame:
    df = pd.DataFrame(columns=["open", "high", "low", "close", "volume", "trades"], dtype=float)
    df.index = pd.DatetimeIndex([], tz="UTC", name="timestamp")
    return df


def _read_cache(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path)
    except Exception as exc:  # a corrupt cache should never stop trading
        log.warning("ignoring unreadable cache %s: %s", path, exc)
        return None


def _write_cache(path: Path, df: pd.DataFrame) -> None:
    try:
        df.to_parquet(path)
    except Exception as exc:
        log.warning("could not write cache %s: %s", path, exc)
