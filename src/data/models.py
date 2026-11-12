import math
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from pydantic import PrivateAttr, field_validator, model_validator
from sqlalchemy import DDL, JSON, CheckConstraint, Column, Text, UniqueConstraint, event
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
    "day_start_equity",
    "peak_equity",
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


class PrivateUIDBindingRecord(SQLModel, table=True):
    """Provider UID observed for precisely one immutable account scope."""

    __tablename__ = "private_uid_binding"
    __table_args__ = (
        CheckConstraint("length(uid) BETWEEN 1 AND 256", name="ck_private_uid_length"),
        CheckConstraint("first_seen_at >= 0", name="ck_private_uid_first_seen"),
    )

    account_scope_id: str = Field(primary_key=True)
    uid: str
    first_seen_at: int
    provider: str | None = None


# Block direct UPDATE/DELETE and SQLite REPLACE, not merely API rebind attempts.
for _action in ("UPDATE", "DELETE", "INSERT"):
    _condition = (
        " WHEN EXISTS (SELECT 1 FROM private_uid_binding "
        "WHERE account_scope_id = NEW.account_scope_id)" if _action == "INSERT" else ""
    )
    event.listen(
        PrivateUIDBindingRecord.__table__, "after_create",
        DDL(
            f"CREATE TRIGGER private_uid_no_{_action.lower()} BEFORE {_action} "
            f"ON private_uid_binding{_condition} "
            "BEGIN SELECT RAISE(ABORT, 'immutable private UID binding'); END"
        ).execute_if(dialect="sqlite"),
    )


class PrivateAccountConfigRecord(SQLModel, table=True):
    """Append-only API observations, without trading or activation authority."""

    __tablename__ = "private_account_config"
    __table_args__ = (
        CheckConstraint("received_at >= 0", name="ck_private_config_received"),
        *(CheckConstraint(
            f"({name}_state IN ('missing','null') AND {name} IS NULL) OR "
            f"({name}_state = 'value' AND {name} IS NOT NULL)",
            name=f"ck_private_config_{name}_state",
        ) for name in (
            "acct_lv", "acct_stp_mode", "pos_mode", "auto_loan", "enable_spot_borrow",
            "fee_type", "settle_ccy", "main_uid",
        )),
    )

    account_scope_id: str = Field(primary_key=True)
    uid: str = Field(primary_key=True)
    payload_hash: str = Field(primary_key=True)
    received_at: int
    raw_json: str = Field(sa_column=Column(Text, nullable=False))
    acct_lv: str | None = None
    acct_lv_state: str
    acct_stp_mode: str | None = None
    acct_stp_mode_state: str
    pos_mode: str | None = None
    pos_mode_state: str
    auto_loan: str | None = None
    auto_loan_state: str
    enable_spot_borrow: str | None = None
    enable_spot_borrow_state: str
    fee_type: str | None = None
    fee_type_state: str
    settle_ccy: str | None = None
    settle_ccy_state: str
    main_uid: str | None = None
    main_uid_state: str


class PrivateAccountBalanceRecord(SQLModel, table=True):
    """Append-only raw balances; USD equity never represents USDT cash."""

    __tablename__ = "private_account_balance"
    __table_args__ = (
        CheckConstraint("received_at >= 0", name="ck_private_balance_received"),
        *(CheckConstraint(
            f"({name}_state IN ('missing','null') AND {name} IS NULL) OR "
            f"({name}_state = 'value' AND {name} IS NOT NULL)",
            name=f"ck_private_balance_{name}_state",
        ) for name in ("total_eq", "adj_eq", "avail_eq", "u_time")),
    )

    account_scope_id: str = Field(primary_key=True)
    uid: str = Field(primary_key=True)
    payload_hash: str = Field(primary_key=True)
    received_at: int
    raw_json: str = Field(sa_column=Column(Text, nullable=False))
    details_json: str = Field(sa_column=Column(Text, nullable=False))
    total_eq: str | None = None
    total_eq_state: str
    adj_eq: str | None = None
    adj_eq_state: str
    avail_eq: str | None = None
    avail_eq_state: str
    u_time: str | None = None
    u_time_state: str


# Keep audit observations append-only, including INSERT OR REPLACE collisions.
for _action in ("UPDATE", "DELETE", "INSERT"):
    _condition = (
        " WHEN EXISTS (SELECT 1 FROM private_account_balance "
        "WHERE account_scope_id = NEW.account_scope_id AND uid = NEW.uid "
        "AND payload_hash = NEW.payload_hash)" if _action == "INSERT" else ""
    )
    event.listen(
        PrivateAccountBalanceRecord.__table__, "after_create",
        DDL(
            f"CREATE TRIGGER private_balance_no_{_action.lower()} BEFORE {_action} "
            f"ON private_account_balance{_condition} "
            "BEGIN SELECT RAISE(ABORT, 'append-only private balance observation'); END"
        ).execute_if(dialect="sqlite"),
    )


class PrivateSpotTradeFeeRecord(SQLModel, table=True):
    """Raw account fee observations, bound to their requested SPOT instrument."""

    __tablename__ = "private_spot_trade_fee"
    __table_args__ = (
        CheckConstraint("received_at >= 0", name="ck_spot_trade_fee_received"),
        CheckConstraint("inst_type = 'SPOT'", name="ck_spot_trade_fee_type"),
        CheckConstraint("length(payload_hash) = 64", name="ck_spot_trade_fee_hash"),
        CheckConstraint("length(requested_inst_id) BETWEEN 1 AND 256",
                        name="ck_spot_trade_fee_request"),
        CheckConstraint("length(CAST(raw_json AS BLOB)) <= 4194304",
                        name="ck_spot_trade_fee_raw_size"),
        *(CheckConstraint(
            f"({name}_state IN ('missing','null') AND {name} IS NULL) OR "
            f"({name}_state = 'value' AND {name} IS NOT NULL)",
            name=f"ck_spot_trade_fee_{name}_state",
        ) for name in ("level", "maker", "taker", "maker_usdc", "taker_usdc",
                       "ts", "rule_type", "category")),
        CheckConstraint(
            "(fee_group_state IN ('missing','null') AND fee_group_json IS NULL) OR "
            "(fee_group_state = 'value' AND fee_group_json IS NOT NULL)",
            name="ck_spot_trade_fee_group_state",
        ),
    )

    account_scope_id: str = Field(primary_key=True)
    uid: str = Field(primary_key=True)
    requested_inst_id: str = Field(primary_key=True)
    payload_hash: str = Field(primary_key=True)
    inst_type: str
    received_at: int
    raw_json: str = Field(sa_column=Column(Text, nullable=False))
    level: str | None = None
    level_state: str
    maker: str | None = None
    maker_state: str
    taker: str | None = None
    taker_state: str
    maker_usdc: str | None = None
    maker_usdc_state: str
    taker_usdc: str | None = None
    taker_usdc_state: str
    ts: str | None = None
    ts_state: str
    rule_type: str | None = None
    rule_type_state: str
    category: str | None = None
    category_state: str
    fee_group_json: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    fee_group_state: str


for _action in ("UPDATE", "DELETE", "INSERT"):
    _condition = (
        " WHEN EXISTS (SELECT 1 FROM private_spot_trade_fee "
        "WHERE account_scope_id = NEW.account_scope_id AND uid = NEW.uid "
        "AND requested_inst_id = NEW.requested_inst_id AND payload_hash = NEW.payload_hash)"
        if _action == "INSERT" else ""
    )
    event.listen(
        PrivateSpotTradeFeeRecord.__table__, "after_create",
        DDL(
            f"CREATE TRIGGER private_spot_trade_fee_no_{_action.lower()} BEFORE {_action} "
            f"ON private_spot_trade_fee{_condition} "
            "BEGIN SELECT RAISE(ABORT, 'append-only spot trade fee observation'); END"
        ).execute_if(dialect="sqlite"),
    )


class PrivateSpotInstrumentResponseRecord(SQLModel, table=True):
    """One bounded raw public response, retained once within an account scope."""

    __tablename__ = "private_spot_instrument_response"
    __table_args__ = (
        CheckConstraint("received_at >= 0", name="ck_instrument_response_received"),
        CheckConstraint("length(payload_hash) = 64", name="ck_instrument_response_hash"),
        CheckConstraint("length(CAST(raw_json AS BLOB)) <= 4194304",
                        name="ck_instrument_response_size"),
    )

    account_scope_id: str = Field(primary_key=True)
    payload_hash: str = Field(primary_key=True)
    received_at: int
    raw_json: str = Field(sa_column=Column(Text, nullable=False))


class PrivateSpotInstrumentRecord(SQLModel, table=True):
    """Append-only public rule evidence; no UID, freshness or trading authority.

    Raw JSON lives in the response table. Repository reads attach that immutable
    text to detached values so callers need no open storage session.
    """

    __tablename__ = "private_spot_instrument"
    __table_args__ = (
        CheckConstraint("received_at >= 0", name="ck_spot_instrument_received"),
        CheckConstraint("length(payload_hash) = 64", name="ck_spot_instrument_hash"),
        CheckConstraint("inst_id = base_ccy || '-' || quote_ccy",
                        name="ck_spot_instrument_currencies"),
        *(CheckConstraint(
            f"length({name}) BETWEEN 1 AND 128", name=f"ck_spot_instrument_{name}_length",
        ) for name in ("tick_sz", "lot_sz", "min_sz")),
        *(CheckConstraint(
            f"({name}_state IN ('missing','null') AND {name} IS NULL) OR "
            f"({name}_state = 'value' AND {name} IS NOT NULL AND length({name}) <= 256)",
            name=f"ck_spot_instrument_{name}_state",
        ) for name in (
            "state", "rule_type", "trade_quote_ccy", "max_lmt_sz", "max_mkt_sz", "lever",
        )),
    )

    account_scope_id: str = Field(primary_key=True)
    payload_hash: str = Field(primary_key=True)
    inst_id: str = Field(primary_key=True)
    received_at: int
    base_ccy: str
    quote_ccy: str
    tick_sz: str
    lot_sz: str
    min_sz: str
    state: str | None = None
    state_state: str
    rule_type: str | None = None
    rule_type_state: str
    trade_quote_ccy: str | None = None
    trade_quote_ccy_state: str
    max_lmt_sz: str | None = None
    max_lmt_sz_state: str
    max_mkt_sz: str | None = None
    max_mkt_sz_state: str
    lever: str | None = None
    lever_state: str
    _raw_json: str = PrivateAttr(default="")

    @property
    def raw_json(self) -> str:
        return self._raw_json


event.listen(
    PrivateSpotInstrumentRecord.__table__, "after_create",
    DDL(
        "CREATE TRIGGER private_spot_instrument_response_required BEFORE INSERT "
        "ON private_spot_instrument WHEN NOT EXISTS "
        "(SELECT 1 FROM private_spot_instrument_response "
        "WHERE account_scope_id = NEW.account_scope_id AND payload_hash = NEW.payload_hash "
        "AND received_at = NEW.received_at) "
        "BEGIN SELECT RAISE(ABORT, 'spot instrument response mismatch'); END"
    ).execute_if(dialect="sqlite"),
)


# Prevent UPDATE/DELETE and SQLite REPLACE collisions for rows and raw evidence.
for _model in (PrivateSpotInstrumentResponseRecord, PrivateSpotInstrumentRecord):
    _table_name = _model.__tablename__
    for _action in ("UPDATE", "DELETE", "INSERT"):
        _identity = " AND ".join(
            f"{column.name} = NEW.{column.name}" for column in _model.__table__.primary_key
        )
        _condition = (
            f" WHEN EXISTS (SELECT 1 FROM {_table_name} WHERE {_identity})"
            if _action == "INSERT" else ""
        )
        event.listen(
            _model.__table__, "after_create",
            DDL(
                f"CREATE TRIGGER {_table_name}_no_{_action.lower()} BEFORE {_action} "
                f"ON {_table_name}{_condition} "
                "BEGIN SELECT RAISE(ABORT, 'append-only spot instrument evidence'); END"
            ).execute_if(dialect="sqlite"),
        )


class ExactSpotFillRecord(SQLModel, table=True):
    """Exact provider text. Optional state distinguishes absent, null and value."""

    __tablename__ = "exact_spot_fill"
    __table_args__ = (
        CheckConstraint("side IN ('buy','sell')", name="ck_exact_fill_side"),
        *(CheckConstraint(
            f"({name}_state IN ('missing','null') AND {name} IS NULL) OR "
            f"({name}_state = 'value' AND {name} IS NOT NULL)",
            name=f"ck_exact_fill_{name}_state",
        ) for name in ("cl_ord_id", "fee", "fee_ccy")),
    )

    account_scope_id: str = Field(primary_key=True)
    uid: str = Field(primary_key=True)
    bill_id: str = Field(primary_key=True)
    symbol: str
    inst_id: str
    trade_id: str
    ord_id: str
    side: str
    fill_sz: str
    fill_px: str
    fill_time: str
    record_time: str
    cl_ord_id: str | None = None
    cl_ord_id_state: str
    fee: str | None = None
    fee_state: str
    fee_ccy: str | None = None
    fee_ccy_state: str


class ExactSpotFillPageRecord(SQLModel, table=True):
    """One raw response and explicit traversal filters, never a sync receipt."""

    __tablename__ = "exact_spot_fill_page"
    __table_args__ = (
        CheckConstraint("inst_type = 'SPOT'", name="ck_exact_page_spot"),
        CheckConstraint(
            "coverage IN ('unknown','partial','complete')", name="ck_exact_page_coverage",
        ),
        CheckConstraint("received_at >= 0", name="ck_exact_page_received"),
    )

    page_id: str = Field(primary_key=True)
    traversal_id: str = Field(index=True)
    account_scope_id: str = Field(index=True)
    uid: str
    endpoint: str
    inst_type: str = "SPOT"
    inst_id: str | None = None
    requested_before: str | None = None
    requested_after: str | None = None
    next_after: str | None = None
    coverage: str
    received_at: int
    payload_hash: str
    raw_json: str = Field(sa_column=Column(Text, nullable=False))


class ExactSpotFillCursorRecord(SQLModel, table=True):
    """CAS version for one explicit traversal; paging proves no completeness."""

    __tablename__ = "exact_spot_fill_cursor"
    __table_args__ = (
        CheckConstraint("inst_type = 'SPOT'", name="ck_exact_cursor_spot"),
        CheckConstraint("version > 0", name="ck_exact_cursor_version"),
        CheckConstraint("complete = 0", name="ck_exact_cursor_incomplete"),
    )

    traversal_id: str = Field(primary_key=True)
    account_scope_id: str = Field(index=True)
    uid: str
    endpoint: str
    inst_type: str = "SPOT"
    inst_id: str | None = None
    requested_before: str | None = None
    version: int
    next_after: str | None = None
    complete: bool = False


class PrivateSyncReceiptRecord(SQLModel, table=True):
    """Bounded synchronization audit; never exact financial or execution evidence."""

    __tablename__ = "private_sync_receipt"
    __table_args__ = (
        UniqueConstraint("attempt_id", name="uq_private_sync_attempt"),
        UniqueConstraint("generation", name="uq_private_sync_generation"),
        CheckConstraint(
            "state IN ('in_progress','committed','partial','failed','abandoned')",
            name="ck_private_sync_state",
        ),
        CheckConstraint("generation > 0", name="ck_private_sync_generation"),
    )

    id: int | None = Field(default=None, primary_key=True)
    attempt_id: str = Field(index=True)
    generation: int = Field(index=True)
    account_scope_id: str = Field(index=True)
    state: str = Field(default="in_progress", index=True)
    symbols: list[str] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    since: int | None = None
    limit: int
    started_at: int
    completed_at: int | None = None
    received_at: int | None = None
    parts: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    cursor: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    result: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    error_code: str | None = None


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
    day_start_equity: float | None = None
    day_start_at: int | None = None
    peak_equity: float | None = None
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
        self.day_start_at = _safe_timestamp(self.day_start_at)
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
