"""Account data endpoints."""

import logging

from fastapi import APIRouter, HTTPException

from connection import ib_manager
from models import AccountSummaryResponse, PnlHistoryResponse, PnlHistoryItem

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Account"])


@router.get("/account/summary", response_model=AccountSummaryResponse)
async def get_account_summary():
    """Fetch account summary: NAV, cash, P&L, buying power."""
    await ib_manager.ensure_connected()
    if not ib_manager.connected:
        raise HTTPException(
            status_code=503,
            detail="Not connected to IB Gateway / TWS. Ensure it is running.",
        )

    ib = ib_manager.ib
    account_id = ib_manager.account_id or ""

    try:
        summary = ib.accountSummary(account_id)
    except Exception as exc:
        logger.exception("Error fetching account summary: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    def _val(tag: str) -> float:
        for item in summary:
            if item.tag == tag:
                try:
                    return float(item.value)
                except (ValueError, TypeError):
                    return 0.0
        return 0.0

    def _str(tag: str) -> str:
        for item in summary:
            if item.tag == tag:
                return item.value
        return ""

    return AccountSummaryResponse(
        account_id=account_id,
        net_liquidation=_val("NetLiquidation"),
        total_cash_balance=_val("TotalCashValue"),
        unrealised_pnl=_val("UnrealizedPnL"),
        realised_pnl=_val("RealizedPnL"),
        buying_power=_val("BuyingPower"),
        currency=_str("Currency") or "USD",
    )


@router.get("/account/pnl-history", response_model=PnlHistoryResponse)
async def get_pnl_history():
    """Return daily P&L history if available via the PnL subscription."""
    await ib_manager.ensure_connected()
    if not ib_manager.connected:
        raise HTTPException(
            status_code=503,
            detail="Not connected to IB Gateway / TWS.",
        )

    ib = ib_manager.ib
    account_id = ib_manager.account_id or ""

    try:
        # Request PnL — ib_insync keeps a live subscription
        pnl = ib.reqPnL(account_id)
        await ib.sleep(1)  # give it a moment to populate

        history: list[PnlHistoryItem] = []
        if pnl:
            history.append(
                PnlHistoryItem(
                    date="today",
                    daily_pnl=pnl.dailyPnL or 0.0,
                    unrealised_pnl=pnl.unrealizedPnL or 0.0,
                    realised_pnl=pnl.realizedPnL or 0.0,
                )
            )
        ib.cancelPnL(pnl)
    except Exception as exc:
        logger.exception("Error fetching PnL history: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return PnlHistoryResponse(account_id=account_id, history=history)
