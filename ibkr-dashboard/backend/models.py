"""Pydantic response models for the IBKR dashboard API."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    connected: bool
    account_id: str | None = None
    server_version: int | None = None
    message: str | None = None


class AccountSummaryResponse(BaseModel):
    account_id: str
    net_liquidation: float
    total_cash_balance: float
    unrealised_pnl: float
    realised_pnl: float
    buying_power: float
    currency: str


class PositionItem(BaseModel):
    symbol: str
    exchange: str
    currency: str
    position_size: float
    average_cost: float
    market_value: float
    unrealised_pnl: float


class PositionsResponse(BaseModel):
    account_id: str
    positions: list[PositionItem]


class PnlHistoryItem(BaseModel):
    date: str
    daily_pnl: float
    unrealised_pnl: float
    realised_pnl: float


class PnlHistoryResponse(BaseModel):
    account_id: str
    history: list[PnlHistoryItem]


class OrderItem(BaseModel):
    order_id: int
    symbol: str
    action: str
    quantity: float
    order_type: str
    limit_price: float | None = None
    aux_price: float | None = None
    status: str


class OrdersResponse(BaseModel):
    orders: list[OrderItem]


class MarketDataSnapshot(BaseModel):
    symbol: str
    last_price: float | None = None
    bid: float | None = None
    ask: float | None = None
    volume: int | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None


class TradeItem(BaseModel):
    trade_id: str
    symbol: str
    action: str
    quantity: float
    price: float
    commission: float
    time: str
    exchange: str


class TradesResponse(BaseModel):
    trades: list[TradeItem]


class ErrorResponse(BaseModel):
    detail: str
