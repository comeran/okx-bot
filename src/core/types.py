from dataclasses import dataclass
from enum import StrEnum


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class OrderStatus(StrEnum):
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class PositionSide(StrEnum):
    LONG = "long"
    SHORT = "short"


@dataclass
class Bar:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Order:
    id: str
    symbol: str
    side: OrderSide
    type: OrderType
    amount: float
    price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    status: OrderStatus = OrderStatus.PENDING
    fill_price: float | None = None
    fill_time: int | None = None


@dataclass
class Position:
    symbol: str
    side: PositionSide
    amount: float
    entry_price: float
    unrealized_pnl: float
    leverage: int = 1


@dataclass
class AccountSnapshot:
    initial_equity: float
    cash_balance: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    daily_pnl: float
    fees_paid: float
    timestamp: int


@dataclass
class PositionSnapshot:
    symbol: str
    side: PositionSide
    amount: float
    entry_price: float
    mark_price: float
    realized_pnl: float
    unrealized_pnl: float
    leverage: int
    timestamp: int


@dataclass
class ExchangeOrderSnapshot:
    exchange_order_id: str
    client_order_id: str
    symbol: str
    side: OrderSide
    type: OrderType
    amount: float
    price: float
    status: OrderStatus
    fill_price: float
    timestamp: int
    updated_at: int


@dataclass
class ExchangeTradeSnapshot:
    exchange_trade_id: str
    exchange_order_id: str
    client_order_id: str
    symbol: str
    side: OrderSide
    amount: float
    price: float
    fee: float
    timestamp: int
