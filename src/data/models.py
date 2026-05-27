from sqlmodel import Field, SQLModel


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
