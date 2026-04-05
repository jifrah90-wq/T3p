"""Trade log endpoint — executed trades (fills) for the current session."""

import logging

from fastapi import APIRouter, HTTPException

from connection import ib_manager
from models import TradeItem, TradesResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Trades"])


@router.get("/trades", response_model=TradesResponse)
async def get_trades():
    """Return executed trades (executions/fills) for the current session."""
    await ib_manager.ensure_connected()
    if not ib_manager.connected:
        raise HTTPException(
            status_code=503,
            detail="Not connected to IB Gateway / TWS.",
        )

    ib = ib_manager.ib

    try:
        fills = ib.fills()
    except Exception as exc:
        logger.exception("Error fetching trades: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    trades = []
    for fill in fills:
        execution = fill.execution
        contract = fill.contract
        commission_report = fill.commissionReport
        trades.append(
            TradeItem(
                trade_id=execution.execId,
                symbol=contract.symbol,
                action=execution.side,
                quantity=execution.shares,
                price=execution.price,
                commission=commission_report.commission if commission_report else 0.0,
                time=execution.time.isoformat() if execution.time else "",
                exchange=execution.exchange,
            )
        )

    return TradesResponse(trades=trades)
