from dataclasses import dataclass, field
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
    trigger_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    status: OrderStatus = OrderStatus.PENDING
    fill_price: float | None = None
    fill_time: int | None = None
    exchange_order_id: str = ""
    client_order_id: str = ""
    updated_at: int = 0
    reduce_only: bool = False
    params: dict[str, object] = field(default_factory=dict)


@dataclass
class Position:
    symbol: str
    side: PositionSide
    amount: float
    entry_price: float
    unrealized_pnl: float
    leverage: int = 1


@dataclass(frozen=True)
class AssetBalance:
    ccy: str
    cash_bal: float = 0.0
    eq: float = 0.0
    eq_utd: float = 0.0
    avail_bal: float = 0.0
    upl: float = 0.0


@dataclass(frozen=True)
class AccountSnapshot:
    equity: float = 0.0
    cash_balance: float = 0.0
    initial_equity: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    daily_pnl: float = 0.0
    fees_paid: float = 0.0
    timestamp: int = 0
    currency: str = ""
    available_balance: float | None = None
    updated_at: int = 0
    assets: list[AssetBalance] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.initial_equity == 0.0 and self.equity != 0.0:
            object.__setattr__(self, "initial_equity", self.equity)
        if self.available_balance is None:
            object.__setattr__(self, "available_balance", self.cash_balance)
        else:
            object.__setattr__(self, "available_balance", float(self.available_balance))
        if self.updated_at == 0 and self.timestamp != 0:
            object.__setattr__(self, "updated_at", self.timestamp)
        if self.timestamp == 0 and self.updated_at != 0:
            object.__setattr__(self, "timestamp", self.updated_at)


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    side: PositionSide
    amount: float
    entry_price: float
    mark_price: float | None = None
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    leverage: int = 1
    timestamp: int = 0
    updated_at: int = 0

    def __post_init__(self) -> None:
        if self.updated_at == 0 and self.timestamp != 0:
            object.__setattr__(self, "updated_at", self.timestamp)
        if self.timestamp == 0 and self.updated_at != 0:
            object.__setattr__(self, "timestamp", self.updated_at)


@dataclass(frozen=True)
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


@dataclass(frozen=True)
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
