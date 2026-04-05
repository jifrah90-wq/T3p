"""Market data snapshot endpoint."""

import logging

from fastapi import APIRouter, HTTPException

from ib_insync import Stock

from connection import ib_manager
from models import MarketDataSnapshot

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Market Data"])


@router.get("/market-data/{symbol}", response_model=MarketDataSnapshot)
async def get_market_data(symbol: str):
    """Fetch a real-time snapshot for a given US stock symbol."""
    await ib_manager.ensure_connected()
    if not ib_manager.connected:
        raise HTTPException(
            status_code=503,
            detail="Not connected to IB Gateway / TWS.",
        )

    ib = ib_manager.ib
    contract = Stock(symbol.upper(), "SMART", "USD")

    try:
        ib.qualifyContracts(contract)
        ticker = ib.reqMktData(contract, snapshot=True)
        # Wait for snapshot data to populate (up to 5 seconds)
        for _ in range(50):
            await ib.sleep(0.1)
            if ticker.last is not None and ticker.last == ticker.last:
                break
    except Exception as exc:
        logger.exception("Error fetching market data for %s: %s", symbol, exc)
        raise HTTPException(status_code=500, detail=str(exc))

    def _safe(val):
        """Return None for NaN or empty values."""
        if val is None:
            return None
        try:
            if val != val:  # NaN check
                return None
        except TypeError:
            return None
        return val

    return MarketDataSnapshot(
        symbol=symbol.upper(),
        last_price=_safe(ticker.last),
        bid=_safe(ticker.bid),
        ask=_safe(ticker.ask),
        volume=int(ticker.volume) if _safe(ticker.volume) is not None else None,
        high=_safe(ticker.high),
        low=_safe(ticker.low),
        close=_safe(ticker.close),
    )
