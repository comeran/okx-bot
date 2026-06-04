from sqlmodel import Field, SQLModel


class AccountRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    strategy: str = Field(index=True)
    initial_equity: float
    cash_balance: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    daily_pnl: float
    fees_paid: float
    updated_at: int


class CashLedgerRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    strategy: str = Field(index=True)
    symbol: str | None = Field(default=None, index=True)
    order_id: str | None = Field(default=None, index=True)
    trade_id: str | None = Field(default=None, index=True)
    event_type: str
    amount: float
    balance_after: float
    timestamp: int = Field(index=True)


class TradeRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    strategy: str
    symbol: str
    side: str
    amount: float
    price: float
    fee: float
    timestamp: int


class OrderRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    order_id: str = Field(index=True)
    strategy: str
    symbol: str
    side: str
    type: str
    amount: float
    price: float
    status: str
    fill_price: float
    timestamp: int


class PositionRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    strategy: str
    symbol: str
    side: str
    amount: float
    entry_price: float
    leverage: int
    timestamp: int
    mark_price: float | None = None
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0


class KlineCache(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    symbol: str = Field(index=True)
    timeframe: str
    timestamp: int = Field(index=True)
    open: float
    high: float
    low: float
    close: float
    volume: float
