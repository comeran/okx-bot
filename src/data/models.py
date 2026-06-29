from typing import Any

from sqlalchemy import JSON, Column
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
    exchange_trade_id: str = Field(default="", index=True)
    order_id: str = Field(default="", index=True)
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
    exchange_order_id: str = Field(default="", index=True)
    client_order_id: str = Field(default="", index=True)
    strategy: str
    symbol: str
    side: str
    type: str
    amount: float
    price: float
    status: str
    fill_price: float
    timestamp: int
    updated_at: int = 0


class KillSwitchRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    engaged: bool = False
    reason: str = ""
    updated_at: int = 0


class RiskEventRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    event_type: str = Field(index=True)
    strategy: str = Field(default="", index=True)
    reason_code: str = Field(default="", index=True)
    reason: str = ""
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    timestamp: int = Field(index=True)


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


class StrategyConfigRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    strategy_type: str = Field(index=True)
    symbol: str
    timeframe: str
    params: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    enabled: bool = True
    created_at: int = Field(index=True)
    updated_at: int = Field(index=True)


class BacktestResultRecord(SQLModel, table=True):
    id: str = Field(primary_key=True)
    strategy: str = Field(index=True)
    symbol: str = Field(index=True)
    timeframe: str = Field(index=True)
    start_time: int = Field(index=True)
    end_time: int = Field(index=True)
    initial_capital: float
    total_return: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    total_trades: int
    created_at: int = Field(index=True)


class BacktestTradeRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    result_id: str = Field(index=True)
    symbol: str = Field(index=True)
    side: str = Field(index=True)
    timestamp: int = Field(index=True)
    price: float
    amount: float
    fee: float
    pnl: float
