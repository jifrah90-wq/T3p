"""Positions endpoint."""

import logging

from fastapi import APIRouter, HTTPException

from connection import ib_manager
from models import PositionItem, PositionsResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Positions"])


@router.get("/positions", response_model=PositionsResponse)
async def get_positions():
    """Return all current positions with market value and unrealised P&L."""
    await ib_manager.ensure_connected()
    if not ib_manager.connected:
        raise HTTPException(
            status_code=503,
            detail="Not connected to IB Gateway / TWS.",
        )

    ib = ib_manager.ib
    account_id = ib_manager.account_id or ""

    try:
        portfolio_items = ib.portfolio()
    except Exception as exc:
        logger.exception("Error fetching positions: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    positions = []
    for item in portfolio_items:
        positions.append(
            PositionItem(
                symbol=item.contract.symbol,
                exchange=item.contract.exchange or item.contract.primaryExchange or "",
                currency=item.contract.currency or "",
                position_size=item.position,
                average_cost=item.averageCost,
                market_value=item.marketValue,
                unrealised_pnl=item.unrealizedPNL,
            )
        )

    return PositionsResponse(account_id=account_id, positions=positions)
