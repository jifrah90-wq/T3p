"""Open orders endpoint."""

import logging

from fastapi import APIRouter, HTTPException

from connection import ib_manager
from models import OrderItem, OrdersResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Orders"])


@router.get("/orders", response_model=OrdersResponse)
async def get_open_orders():
    """Return all open / pending orders."""
    await ib_manager.ensure_connected()
    if not ib_manager.connected:
        raise HTTPException(
            status_code=503,
            detail="Not connected to IB Gateway / TWS.",
        )

    ib = ib_manager.ib

    try:
        open_trades = ib.openTrades()
    except Exception as exc:
        logger.exception("Error fetching open orders: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    orders = []
    for trade in open_trades:
        order = trade.order
        contract = trade.contract
        orders.append(
            OrderItem(
                order_id=order.orderId,
                symbol=contract.symbol,
                action=order.action,
                quantity=order.totalQuantity,
                order_type=order.orderType,
                limit_price=order.lmtPrice if order.lmtPrice != 1e308 else None,
                aux_price=order.auxPrice if order.auxPrice != 1e308 else None,
                status=trade.orderStatus.status,
            )
        )

    return OrdersResponse(orders=orders)
