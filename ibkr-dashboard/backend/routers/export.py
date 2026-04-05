"""Export endpoint — download any dataset as CSV or Excel."""

import csv
import io
import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from openpyxl import Workbook

from connection import ib_manager

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Export"])

ALLOWED_DATASETS = {"positions", "orders", "trades", "account_summary"}


def _get_positions_rows() -> tuple[list[str], list[list]]:
    ib = ib_manager.ib
    headers = [
        "Symbol", "Exchange", "Currency", "Position", "Avg Cost",
        "Market Value", "Unrealised P&L",
    ]
    rows = []
    for item in ib.portfolio():
        rows.append([
            item.contract.symbol,
            item.contract.exchange or item.contract.primaryExchange or "",
            item.contract.currency or "",
            item.position,
            item.averageCost,
            item.marketValue,
            item.unrealizedPNL,
        ])
    return headers, rows


def _get_orders_rows() -> tuple[list[str], list[list]]:
    ib = ib_manager.ib
    headers = [
        "Order ID", "Symbol", "Action", "Quantity", "Order Type",
        "Limit Price", "Status",
    ]
    rows = []
    for trade in ib.openTrades():
        order = trade.order
        rows.append([
            order.orderId,
            trade.contract.symbol,
            order.action,
            order.totalQuantity,
            order.orderType,
            order.lmtPrice if order.lmtPrice != 1e308 else "",
            trade.orderStatus.status,
        ])
    return headers, rows


def _get_trades_rows() -> tuple[list[str], list[list]]:
    ib = ib_manager.ib
    headers = [
        "Trade ID", "Symbol", "Action", "Quantity", "Price",
        "Commission", "Time", "Exchange",
    ]
    rows = []
    for fill in ib.fills():
        ex = fill.execution
        cr = fill.commissionReport
        rows.append([
            ex.execId,
            fill.contract.symbol,
            ex.side,
            ex.shares,
            ex.price,
            cr.commission if cr else 0.0,
            ex.time.isoformat() if ex.time else "",
            ex.exchange,
        ])
    return headers, rows


def _get_account_summary_rows() -> tuple[list[str], list[list]]:
    ib = ib_manager.ib
    account_id = ib_manager.account_id or ""
    summary = ib.accountSummary(account_id)
    headers = ["Tag", "Value", "Currency"]
    rows = [[item.tag, item.value, item.currency] for item in summary]
    return headers, rows


DATASET_FETCHERS = {
    "positions": _get_positions_rows,
    "orders": _get_orders_rows,
    "trades": _get_trades_rows,
    "account_summary": _get_account_summary_rows,
}


@router.get("/export/{dataset}")
async def export_dataset(
    dataset: str,
    fmt: str = Query("csv", regex="^(csv|xlsx)$"),
):
    """Export a dataset as CSV or Excel. Use ?fmt=csv or ?fmt=xlsx."""
    if dataset not in ALLOWED_DATASETS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown dataset '{dataset}'. Allowed: {', '.join(sorted(ALLOWED_DATASETS))}",
        )

    await ib_manager.ensure_connected()
    if not ib_manager.connected:
        raise HTTPException(
            status_code=503,
            detail="Not connected to IB Gateway / TWS.",
        )

    try:
        headers, rows = DATASET_FETCHERS[dataset]()
    except Exception as exc:
        logger.exception("Error exporting %s: %s", dataset, exc)
        raise HTTPException(status_code=500, detail=str(exc))

    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(headers)
        writer.writerows(rows)
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={dataset}.csv"},
        )

    # Excel
    wb = Workbook()
    ws = wb.active
    ws.title = dataset
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={dataset}.xlsx"},
    )
