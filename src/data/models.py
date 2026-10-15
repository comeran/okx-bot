import math
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from pydantic import PrivateAttr, field_validator, model_validator
from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from src.core.types import (
    AssetBalance,
    _finite_nonnegative,
    _finite_nonnegative_or_none,
    _finite_or_zero,
    _safe_leverage,
    _safe_timestamp,
    _subtract_amounts,
    _validate_amount_reconciliation,
    execution_type_family,
    immutable_assets,
)

_ACCOUNT_BALANCE_FIELDS = (
    "initial_equity",
    "cash_balance",
    "available_balance",
    "equity",
    "fees_paid",
)
_ACCOUNT_PNL_FIELDS = ("realized_pnl", "unrealized_pnl", "daily_pnl")
_ASSET_BALANCE_FIELDS = ("cash_bal", "eq", "eq_utd", "avail_bal")
EXCHANGE_STRATEGY = "__exchange__"


def _strict_bool(value: object, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
    elif isinstance(value, (int, float)):
        try:
            if math.isfinite(float(value)) and value in (0, 1):
                return bool(value)
        except (OverflowError, TypeError, ValueError):
            pass
    raise ValueError(f"invalid {field_name}: {value!r}")


def is_exchange_placeholder_order_id(order_id: object) -> bool:
    return isinstance(order_id, str) and order_id.startswith("okx:")


def has_placeholder_trade_identity(trade: "TradeRecord") -> bool:
    return trade.strategy == EXCHANGE_STRATEGY and (
        not trade.order_id or is_exchange_placeholder_order_id(trade.order_id)
    )


def has_confirmed_local_trade_identity(trade: "TradeRecord") -> bool:
    return (
        trade.strategy != EXCHANGE_STRATEGY
        and bool(trade.order_id)
        and not is_exchange_placeholder_order_id(trade.order_id)
    )


def normalize_account_assets(assets: object) -> list[dict[str, Any]]:
    if not isinstance(assets, list):
        return []
    normalized: list[dict[str, Any]] = []
    for asset in assets:
        if isinstance(asset, AssetBalance):
            item = asdict(asset)
        elif isinstance(asset, Mapping):
            item = dict(asset)
        else:
            continue
        ccy = item.get("ccy")
        if not isinstance(ccy, str) or not ccy.strip():
            continue
        for field_name in _ASSET_BALANCE_FIELDS:
            if field_name in item:
                item[field_name] = _finite_nonnegative(item[field_name])
        if "upl" in item:
            item["upl"] = _finite_or_zero(item["upl"])
        normalized.append(item)
    return normalized


class AccountRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    strategy: str = Field(index=True)
    source: str = Field(default="unknown", index=True, nullable=True, exclude=True)
    runtime_mode: str = Field(default="unknown", index=True, nullable=True, exclude=True)
    initial_equity: float
    cash_balance: float
    available_balance: float = 0.0
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    daily_pnl: float
    fees_paid: float
    updated_at: int
    assets: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    # Legacy snapshots remain queryable for audit while only the latest active
    # snapshot participates in ordinary account reads.
    active: bool | None = Field(default=True, nullable=True, index=True)
    stale_at: int | None = Field(default=None, nullable=True)

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self.normalize_values()

    @model_validator(mode="after")
    def normalize_values(self) -> "AccountRecord":
        for field_name in _ACCOUNT_BALANCE_FIELDS:
            setattr(self, field_name, _finite_nonnegative(getattr(self, field_name)))
        for field_name in _ACCOUNT_PNL_FIELDS:
            setattr(self, field_name, _finite_or_zero(getattr(self, field_name)))
        self.updated_at = _safe_timestamp(self.updated_at)
        self.assets = immutable_assets(normalize_account_assets(self.assets))
        self.active = True if self.active is None else bool(self.active)
        self.stale_at = None if self.stale_at is None else _safe_timestamp(self.stale_at)
        return self


class CashLedgerRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(default="unknown", index=True, nullable=True, exclude=True)
    runtime_mode: str = Field(default="unknown", index=True, nullable=True, exclude=True)
    strategy: str = Field(index=True)
    symbol: str | None = Field(default=None, index=True)
    order_id: str | None = Field(default=None, index=True)
    trade_id: str | None = Field(default=None, index=True)
    event_type: str
    amount: float
    balance_after: float
    timestamp: int = Field(index=True)

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self.normalize_values()

    @model_validator(mode="after")
    def normalize_values(self) -> "CashLedgerRecord":
        self.source = self.source or "unknown"
        self.runtime_mode = self.runtime_mode or "unknown"
        self.amount = _finite_or_zero(self.amount)
        self.balance_after = _finite_or_zero(self.balance_after)
        self.timestamp = _safe_timestamp(self.timestamp)
        return self


class TradeRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(default="unknown", index=True, nullable=True, exclude=True)
    runtime_mode: str = Field(default="unknown", index=True, nullable=True, exclude=True)
    exchange_trade_id: str = Field(default="", index=True)
    exchange_order_id: str = Field(default="", index=True)
    client_order_id: str = Field(default="", index=True)
    order_id: str = Field(default="", index=True)
    strategy: str
    symbol: str
    side: str
    amount: float
    price: float
    fee: float
    timestamp: int
    # Explicit quantity semantics are retained for FIFO performance accounting.
    contracts: float | None = Field(default=None, nullable=True, exclude=True)
    quantity: float | None = Field(default=None, nullable=True, exclude=True)
    quantity_unit: str = Field(default="base", nullable=True, exclude=True)
    is_inverse: bool = Field(default=False, nullable=True, exclude=True)
    contract_size: float | None = Field(default=None, nullable=True, exclude=True)
    # Present only for trades with a blank exchange ID; generated by private sync.
    blank_trade_fingerprint: str = Field(default="", index=True)
    # Duplicate legacy fills are retained but excluded from ordinary trade reads.
    active: bool | None = Field(default=True, nullable=True, index=True)
    stale_at: int | None = Field(default=None, nullable=True)

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self.normalize_values()

    @field_validator("is_inverse", mode="before")
    @classmethod
    def validate_is_inverse(cls, value: object) -> bool:
        return _strict_bool(value, "is_inverse")

    @model_validator(mode="after")
    def normalize_values(self) -> "TradeRecord":
        for field in ("amount", "price", "fee", "contracts", "quantity", "contract_size"):
            if getattr(self, field, None) is None:
                continue
            try:
                value = float(getattr(self, field))
            except (TypeError, ValueError, OverflowError):
                value = 0.0
            if not math.isfinite(value) or value < 0.0:
                value = 0.0
            setattr(self, field, value)
        self.quantity_unit = (
            self.quantity_unit.strip().lower()
            if isinstance(self.quantity_unit, str)
            else "base"
        )
        if self.quantity_unit not in {"base", "contracts"}:
            self.quantity_unit = "base"
        self.is_inverse = _strict_bool(self.is_inverse, "is_inverse")
        self.timestamp = _safe_timestamp(self.timestamp)
        self.active = True if self.active is None else bool(self.active)
        self.stale_at = None if self.stale_at is None else _safe_timestamp(self.stale_at)
        return self


class OrderRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(default="unknown", index=True, nullable=True, exclude=True)
    runtime_mode: str = Field(default="unknown", index=True, nullable=True, exclude=True)
    order_id: str = Field(index=True)
    exchange_order_id: str | None = Field(default="", index=True)
    client_order_id: str | None = Field(default="", index=True)
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
    filled_amount: float = Field(default=0.0, exclude=True)
    remaining_amount: float | None = Field(default=None, exclude=True)
    snapshot_filled_amount: float = Field(default=0.0, exclude=True)
    # Explicit provenance for the atomic exchange snapshot quantity/status group.
    snapshot_updated_at: int | None = Field(default=None, exclude=True)
    execution_type_family: str = Field(default="", nullable=True, exclude=True)
    # Historical orders remain queryable for audit but are excluded from current reads.
    active: bool | None = Field(default=True, nullable=True, index=True, exclude=True)
    stale_at: int | None = Field(default=None, nullable=True, exclude=True)
    _snapshot_authoritative: bool = PrivateAttr(default=False)

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self.normalize_fill_amounts()

    @model_validator(mode="after")
    def normalize_fill_amounts(self) -> "OrderRecord":
        try:
            amount = float(self.amount)
        except (TypeError, ValueError, OverflowError):
            amount = 0.0
        if not math.isfinite(amount) or amount < 0.0:
            amount = 0.0

        try:
            filled_amount = float(self.filled_amount)
        except (TypeError, ValueError, OverflowError):
            filled_amount = 0.0
        if not math.isfinite(filled_amount) or filled_amount < 0.0:
            filled_amount = 0.0

        try:
            remaining_amount = (
                None
                if self.remaining_amount is None
                else float(self.remaining_amount)
            )
        except (TypeError, ValueError, OverflowError):
            remaining_amount = None
        remaining_is_valid = (
            remaining_amount is not None
            and math.isfinite(remaining_amount)
            and remaining_amount >= 0.0
            and remaining_amount <= amount
        )
        filled_is_valid = math.isfinite(filled_amount) and 0.0 <= filled_amount <= amount
        fields_set = getattr(self, "model_fields_set", set())
        if (
            "filled_amount" in fields_set
            and "remaining_amount" in fields_set
            and filled_is_valid
            and remaining_is_valid
        ):
            _validate_amount_reconciliation(amount, filled_amount, remaining_amount)
        if remaining_is_valid:
            filled_amount = _subtract_amounts(amount, remaining_amount)
        else:
            filled_amount = min(filled_amount, amount)
            remaining_amount = _subtract_amounts(amount, filled_amount)

        try:
            snapshot_filled_amount = float(self.snapshot_filled_amount)
        except (TypeError, ValueError, OverflowError):
            snapshot_filled_amount = 0.0
        if not math.isfinite(snapshot_filled_amount) or snapshot_filled_amount < 0.0:
            snapshot_filled_amount = 0.0
        snapshot_filled_amount = min(snapshot_filled_amount, amount)

        try:
            price = float(self.price)
        except (TypeError, ValueError, OverflowError):
            price = 0.0
        if not math.isfinite(price) or price < 0.0:
            price = 0.0
        try:
            fill_price = float(self.fill_price)
        except (TypeError, ValueError, OverflowError):
            fill_price = 0.0
        if not math.isfinite(fill_price) or fill_price < 0.0:
            fill_price = 0.0

        if self.status not in {"cancelled", "canceled", "cancel", "rejected"} and not (
            self.status == "filled" and filled_amount == 0.0
        ):
            self.status = (
                "pending"
                if filled_amount == 0.0
                else "filled"
                if filled_amount >= amount
                else "partially_filled"
            )
        self.amount = amount
        self.price = price
        self.fill_price = fill_price
        self.timestamp = _safe_timestamp(self.timestamp)
        self.updated_at = _safe_timestamp(self.updated_at)
        self.snapshot_updated_at = (
            None
            if self.snapshot_updated_at is None
            else _safe_timestamp(self.snapshot_updated_at)
        )
        self.filled_amount = filled_amount
        self.remaining_amount = remaining_amount
        self.snapshot_filled_amount = snapshot_filled_amount
        self.execution_type_family = execution_type_family(
            self.type,
            provider_type=self.execution_type_family or None,
        )
        self.active = True if self.active is None else bool(self.active)
        self.stale_at = None if self.stale_at is None else _safe_timestamp(self.stale_at)
        return self


class KillSwitchRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    engaged: bool = False
    reason: str = ""
    updated_at: int = 0

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self.updated_at = _safe_timestamp(self.updated_at)

    @model_validator(mode="after")
    def normalize_values(self) -> "KillSwitchRecord":
        self.updated_at = _safe_timestamp(self.updated_at)
        return self


class RiskEventRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(default="unknown", index=True, nullable=True, exclude=True)
    runtime_mode: str = Field(default="unknown", index=True, nullable=True, exclude=True)
    event_type: str = Field(index=True)
    strategy: str = Field(default="", index=True)
    reason_code: str = Field(default="", index=True)
    reason: str = ""
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    timestamp: int = Field(index=True)
    event_key: str | None = Field(default=None, index=True, unique=True)

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self.timestamp = _safe_timestamp(self.timestamp)

    @model_validator(mode="after")
    def normalize_values(self) -> "RiskEventRecord":
        self.source = self.source or "unknown"
        self.runtime_mode = self.runtime_mode or "unknown"
        self.timestamp = _safe_timestamp(self.timestamp)
        return self


class PositionRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(default="unknown", index=True, nullable=True, exclude=True)
    runtime_mode: str = Field(default="unknown", index=True, nullable=True, exclude=True)
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
    # ``amount`` remains the legacy API quantity. Explicit fields distinguish
    # exchange order quantity from the quote/notional used by risk and analytics.
    contracts: float | None = Field(default=None, nullable=True, exclude=True)
    quantity: float | None = Field(default=None, nullable=True, exclude=True)
    quantity_unit: str = Field(default="base", nullable=True, exclude=True)
    notional_value: float | None = Field(default=None, nullable=True, exclude=True)
    is_inverse: bool = Field(default=False, nullable=True, exclude=True)
    contract_size: float | None = Field(default=None, nullable=True, exclude=True)
    # Historical exchange aliases remain queryable for audit, but stale rows are
    # excluded from active exposure after a trusted scoped refresh.
    active: bool | None = Field(default=True, nullable=True)
    stale_at: int | None = Field(default=None, nullable=True)

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self.normalize_values()

    @field_validator("is_inverse", mode="before")
    @classmethod
    def validate_is_inverse(cls, value: object) -> bool:
        return _strict_bool(value, "is_inverse")

    @model_validator(mode="after")
    def normalize_values(self) -> "PositionRecord":
        try:
            amount = float(self.amount)
        except (TypeError, ValueError, OverflowError):
            amount = 0.0
        self.amount = abs(amount) if math.isfinite(amount) else 0.0
        self.entry_price = _finite_nonnegative(self.entry_price)
        self.mark_price = _finite_nonnegative_or_none(self.mark_price)
        self.quantity_unit = (
            self.quantity_unit.strip().lower()
            if isinstance(self.quantity_unit, str)
            else "base"
        )
        if self.quantity_unit not in {"base", "contracts"}:
            self.quantity_unit = "base"
        self.quantity = (
            _finite_nonnegative(self.quantity)
            if self.quantity is not None
            else self.amount
        )
        self.contracts = (
            _finite_nonnegative(self.contracts) if self.contracts is not None else None
        )
        self.contract_size = (
            _finite_nonnegative(self.contract_size)
            if self.contract_size is not None
            else None
        )
        self.notional_value = (
            _finite_nonnegative(self.notional_value)
            if self.notional_value is not None
            else None
        )
        self.is_inverse = _strict_bool(self.is_inverse, "is_inverse")
        for field in ("realized_pnl", "unrealized_pnl"):
            try:
                value = float(getattr(self, field))
            except (TypeError, ValueError, OverflowError):
                value = 0.0
            setattr(self, field, value if math.isfinite(value) else 0.0)
        self.leverage = _safe_leverage(self.leverage)
        self.timestamp = _safe_timestamp(self.timestamp)
        self.active = True if self.active is None else bool(self.active)
        self.stale_at = (
            None if self.stale_at is None else _safe_timestamp(self.stale_at)
        )
        return self


class KlineCache(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    symbol: str = Field(index=True)
    source: str = Field(default="unknown", index=True, nullable=True)
    runtime_mode: str = Field(default="unknown", index=True, nullable=True)
    timeframe: str
    timestamp: int = Field(index=True)
    open: float
    high: float
    low: float
    close: float
    volume: float
    # Invalid legacy candles remain auditable but are excluded from current reads.
    active: bool | None = Field(default=True, nullable=True, index=True)

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self.normalize_values()

    @model_validator(mode="after")
    def normalize_values(self) -> "KlineCache":
        self.source = self.source or "unknown"
        self.runtime_mode = self.runtime_mode or "unknown"
        values = {
            field_name: _finite_nonnegative(getattr(self, field_name))
            for field_name in ("open", "high", "low", "close")
        }
        if (
            values["high"] < values["low"]
            or not values["low"] <= values["open"] <= values["high"]
            or not values["low"] <= values["close"] <= values["high"]
        ):
            raise ValueError("invalid OHLC candle bounds")
        for field_name, value in values.items():
            setattr(self, field_name, value)
        self.volume = _finite_nonnegative(self.volume)
        self.timestamp = _safe_timestamp(self.timestamp)
        self.active = True if self.active is None else bool(self.active)
        return self


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
