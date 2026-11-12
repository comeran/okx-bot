import hashlib
import json
import logging
import math
import os
import re
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import event, or_, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlmodel import Session, SQLModel, create_engine, select

from src.core.execution_scope import ExecutionScope
from src.core.private_facts import (
    MISSING,
    FactCoverage,
    SpotFillFact,
    SpotFillPage,
    canonical_spot_symbol,
    validate_raw_text,
)
from src.core.types import (
    OrderStatus,
    _finite_or_zero,
    _safe_leverage,
    _safe_timestamp,
    stable_blank_trade_fingerprint,
)
from src.data.allocations import release_strategy_drafts
from src.data.models import (
    EXCHANGE_STRATEGY,
    AccountRecord,
    BacktestResultRecord,
    BacktestTradeRecord,
    CashLedgerRecord,
    ExactSpotFillCursorRecord,
    ExactSpotFillPageRecord,
    ExactSpotFillRecord,
    KillSwitchRecord,
    KlineCache,
    OrderRecord,
    PositionRecord,
    PrivateAccountBalanceRecord,
    PrivateAccountConfigRecord,
    PrivateSpotInstrumentRecord,
    PrivateSpotInstrumentResponseRecord,
    PrivateSyncReceiptRecord,
    PrivateUIDBindingRecord,
    RiskEventRecord,
    StrategyConfigRecord,
    TradeRecord,
    has_confirmed_local_trade_identity,
    has_placeholder_trade_identity,
    normalize_account_assets,
)
from src.data.spot_preflight_evidence import PreflightObservation, SpotPreflightEvidence
from src.exchange.base import canonical_symbol
from src.exchange.okx_account_facts import (
    ACCOUNT_CONFIG_FIELDS,
    AccountConfigFact,
    parse_okx_account_config,
)
from src.exchange.okx_balance_facts import (
    ACCOUNT_BALANCE_FIELDS,
    AccountBalanceFact,
    parse_okx_account_balance,
)
from src.exchange.okx_instrument_facts import (
    INSTRUMENT_OPTIONAL_FIELDS,
    SpotInstrumentFact,
    parse_okx_spot_instruments,
)
from src.exchange.okx_private_facts import parse_okx_spot_fill_page

logger = logging.getLogger(__name__)


def _domain_event_key(event_key: str, source: str, runtime_mode: str) -> str:
    normalized_source = str(source or "unknown").strip().lower()
    normalized_runtime = str(runtime_mode or "unknown").strip().lower()
    if (normalized_source, normalized_runtime) == ("unknown", "unknown"):
        return event_key
    payload = {
        "event_key": event_key,
        "source": normalized_source,
        "runtime_mode": normalized_runtime,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _record_provenance(record: object) -> str:
    """Classify only explicit, internally consistent provenance pairs.

    A source marker cannot override a conflicting runtime marker.  Legacy rows
    with missing or partial markers remain unknown for read-only audit purposes.
    """
    payload = getattr(record, "payload", None)
    source = getattr(record, "source", None)
    runtime_mode = getattr(record, "runtime_mode", None)
    if isinstance(payload, dict):
        if source in (None, "", "unknown"):
            source = payload.get("source", source)
        if runtime_mode in (None, "", "unknown"):
            runtime_mode = payload.get("runtime_mode", payload.get("mode", runtime_mode))

    normalized_source = str(source or "unknown").strip().lower()
    normalized_runtime = str(runtime_mode or "unknown").strip().lower()
    pairs = {
        ("live_exchange", "live"): "live",
        ("paper", "paper"): "paper",
        ("backtest", "backtest"): "backtest",
    }
    return pairs.get((normalized_source, normalized_runtime), "unknown")


def _is_sqlite_lock_error(error: OperationalError) -> bool:
    return "locked" in str(error).lower() or "busy" in str(error).lower()


def _merge_retry(method):
    """Retry a whole read/merge/commit transaction after SQLite write contention."""
    def wrapped(self: "Repository", *args, **kwargs):
        for attempt in range(3):
            try:
                return method(self, *args, **kwargs)
            except IntegrityError:
                if attempt == 2:
                    raise
                logger.warning("SQLite merge integrity race; retrying transaction")
            except OperationalError as exc:
                if attempt == 2 or not _is_sqlite_lock_error(exc):
                    raise
                logger.warning("SQLite merge transaction was locked; retrying")
        raise AssertionError("unreachable")

    return wrapped


def _merge_account_record(existing: AccountRecord, incoming: AccountRecord) -> AccountRecord:
    """Apply the public account upsert's timestamp and equal-time rules."""
    incoming.normalize_values()
    same_record = (
        incoming.id is not None
        and existing.id is not None
        and incoming.id == existing.id
    )
    if incoming.updated_at < existing.updated_at:
        return existing
    if incoming.updated_at == existing.updated_at and not same_record:
        if existing.initial_equity == 0.0 and incoming.initial_equity > 0.0:
            existing.initial_equity = incoming.initial_equity
        return existing

    if existing.initial_equity == 0.0 and incoming.initial_equity > 0.0:
        existing.initial_equity = incoming.initial_equity
    existing.cash_balance = incoming.cash_balance
    existing.available_balance = incoming.available_balance
    existing.equity = incoming.equity
    existing.realized_pnl = incoming.realized_pnl
    existing.unrealized_pnl = incoming.unrealized_pnl
    existing.daily_pnl = incoming.daily_pnl
    existing.fees_paid = incoming.fees_paid
    existing.updated_at = incoming.updated_at
    existing.assets = incoming.assets
    existing.active = incoming.active
    existing.stale_at = incoming.stale_at
    if incoming.source != "unknown":
        existing.source = incoming.source
    if incoming.runtime_mode != "unknown":
        existing.runtime_mode = incoming.runtime_mode
    return existing


def _merge_position_record(existing: PositionRecord, incoming: PositionRecord) -> PositionRecord:
    """Apply the public position upsert's timestamp rules."""
    incoming.normalize_values()
    if incoming.timestamp < existing.timestamp:
        return existing
    existing.amount = incoming.amount
    existing.entry_price = incoming.entry_price
    existing.leverage = incoming.leverage
    existing.timestamp = incoming.timestamp
    existing.mark_price = incoming.mark_price
    existing.realized_pnl = incoming.realized_pnl
    existing.unrealized_pnl = incoming.unrealized_pnl
    existing.contracts = incoming.contracts
    existing.quantity = incoming.quantity
    existing.quantity_unit = incoming.quantity_unit
    existing.notional_value = incoming.notional_value
    existing.is_inverse = incoming.is_inverse
    existing.contract_size = incoming.contract_size
    existing.active = incoming.active
    existing.stale_at = incoming.stale_at
    if incoming.source != "unknown":
        existing.source = incoming.source
    if incoming.runtime_mode != "unknown":
        existing.runtime_mode = incoming.runtime_mode
    return existing


def _merge_order_record(existing: OrderRecord, incoming: OrderRecord) -> OrderRecord:
    """Merge mutable order state consistently for ordinary and atomic upserts."""
    incoming.normalize_fill_amounts()
    is_snapshot = getattr(incoming, "_snapshot_authoritative", False) or (
        incoming.snapshot_updated_at is not None
        and incoming.snapshot_updated_at == incoming.updated_at
    )
    if is_snapshot:
        existing_provenance = existing.snapshot_updated_at
        if (
            existing_provenance is not None
            and incoming.snapshot_updated_at is not None
            and incoming.snapshot_updated_at < existing_provenance
        ):
            return existing
    elif incoming.updated_at < existing.updated_at:
        return existing

    terminal_statuses = {
        OrderStatus.FILLED.value, OrderStatus.CANCELLED.value, "canceled", "cancel",
        OrderStatus.REJECTED.value,
    }
    previous_updated_at = existing.updated_at
    if is_snapshot and incoming.amount <= 0.0 < existing.amount:
        logger.warning(
            "Rejecting invalid authoritative snapshot amount for exchange order %s",
            incoming.exchange_order_id or existing.exchange_order_id,
        )
        return existing
    for field in ("exchange_order_id", "client_order_id"):
        if not getattr(existing, field) and getattr(incoming, field):
            setattr(existing, field, getattr(incoming, field))
    existing.timestamp = max(existing.timestamp, incoming.timestamp)
    existing.updated_at = max(existing.updated_at, incoming.updated_at)
    if incoming.execution_type_family:
        existing.execution_type_family = incoming.execution_type_family

    if is_snapshot:
        # A terminal provider snapshot may omit fill fields. The sync layer can
        # derive a real fill from trades in the same transaction; do not let the
        # sparse snapshot overwrite that evidence during the second upsert.
        sparse_terminal_fill_with_trade_evidence = (
            getattr(incoming, "_snapshot_authoritative", False)
            and incoming.status in terminal_statuses | {OrderStatus.PARTIALLY_FILLED.value}
            and incoming.filled_amount > 0.0
            and incoming.snapshot_filled_amount == 0.0
            and incoming.fill_price > 0.0
        )
        if sparse_terminal_fill_with_trade_evidence:
            existing.amount = max(existing.amount, incoming.amount)
            existing.filled_amount = max(existing.filled_amount, incoming.filled_amount)
            existing.remaining_amount = max(0.0, existing.amount - existing.filled_amount)
            existing.fill_price = incoming.fill_price
            existing.status = incoming.status
            existing.snapshot_filled_amount = max(
                existing.snapshot_filled_amount, incoming.snapshot_filled_amount
            )
            existing.snapshot_updated_at = incoming.snapshot_updated_at
            existing.normalize_fill_amounts()
            return existing
        legacy_snapshot_regresses_known_quantity = (
            existing.snapshot_updated_at is None
            and (
                incoming.amount < existing.amount
                or incoming.filled_amount < existing.filled_amount
            )
        )
        snapshot_downgrades_terminal = (
            existing.status in terminal_statuses and incoming.status not in terminal_statuses
        )
        if legacy_snapshot_regresses_known_quantity:
            existing.amount = max(existing.amount, incoming.amount)
            existing.filled_amount = min(existing.amount, existing.filled_amount)
            existing.remaining_amount = max(0.0, existing.amount - existing.filled_amount)
            existing.status = (
                existing.status if existing.status in terminal_statuses else (
                    OrderStatus.PENDING.value if existing.filled_amount == 0.0 else
                    OrderStatus.FILLED.value if existing.filled_amount >= existing.amount else
                    OrderStatus.PARTIALLY_FILLED.value
                )
            )
            existing.snapshot_updated_at = incoming.snapshot_updated_at
        elif snapshot_downgrades_terminal:
            existing.snapshot_updated_at = incoming.snapshot_updated_at
        else:
            for field in (
                "amount", "filled_amount", "remaining_amount", "fill_price", "status",
                "snapshot_filled_amount", "snapshot_updated_at",
            ):
                setattr(existing, field, getattr(incoming, field))
    else:
        if incoming.fill_price and (
            existing.fill_price == 0.0 or incoming.updated_at > previous_updated_at
        ):
            existing.fill_price = incoming.fill_price
        existing.filled_amount = max(existing.filled_amount, incoming.filled_amount)
        existing.snapshot_filled_amount = max(
            existing.snapshot_filled_amount, incoming.snapshot_filled_amount
        )
        existing.remaining_amount = max(0.0, existing.amount - existing.filled_amount)
        if existing.status not in terminal_statuses:
            existing.status = incoming.status
    existing.active = incoming.active
    existing.stale_at = incoming.stale_at
    if incoming.source != "unknown":
        existing.source = incoming.source
    if incoming.runtime_mode != "unknown":
        existing.runtime_mode = incoming.runtime_mode
    existing.normalize_fill_amounts()
    return existing


def _receipt_integer(value: object, label: str, *, positive: bool = False) -> int:
    if (
        isinstance(value, bool) or not isinstance(value, int)
        or value < (1 if positive else 0) or value > 2**63 - 1
    ):
        raise ValueError(f"Invalid private sync {label}")
    return value


def _receipt_json(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"Private sync {label} must be an object")
    try:
        encoded = json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ValueError(f"Invalid private sync {label}") from None
    if len(encoded.encode("utf-8")) > 65_536:
        raise ValueError(f"Private sync {label} is too large")
    return json.loads(encoded)


def _receipt_identity(value: object) -> str:
    if (
        not isinstance(value, str) or not value or len(value) > 128
        or value != value.strip() or any(ord(character) < 33 for character in value)
    ):
        raise ValueError("Invalid private sync attempt identity")
    return value


def _receipt_copy(record: PrivateSyncReceiptRecord) -> PrivateSyncReceiptRecord:
    return PrivateSyncReceiptRecord(**json.loads(json.dumps(record.model_dump())))


def _receipt_scope(session: Session, expected: str | None = None) -> str:
    try:
        rows = session.execute(text(
            "SELECT schema_version, account_scope_id, exchange, environment, "
            "account_fingerprint FROM execution_scope_header"
        )).all()
    except SQLAlchemyError:
        raise ValueError("Private sync receipts require a validated account facts store") from None
    if len(rows) != 1 or rows[0][0] != 1:
        raise ValueError("Invalid private sync account binding")
    version, scope_id, exchange, environment, fingerprint = rows[0]
    try:
        scope = ExecutionScope(exchange, environment, fingerprint)
    except ValueError:
        raise ValueError("Invalid private sync account binding") from None
    if scope_id != scope.account_scope_id or (expected is not None and expected != scope_id):
        raise ValueError("Private sync account scope changed")
    return scope_id


_EXACT_PAGE_MAX_BYTES = 4 * 1024 * 1024
_EXACT_ENDPOINT = "private_get_trade_fills_history"
_EXACT_OPTIONALS = ("cl_ord_id", "fee", "fee_ccy")


def _exact_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, allow_nan=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _exact_payload(raw: object) -> tuple[dict, str, str]:
    """Bound raw responses independently of the deliberately small receipt JSON."""
    def check(value: object, depth: int = 0) -> None:
        if depth > 32:
            raise ValueError("Exact raw payload is too deeply nested")
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ValueError("Invalid exact raw payload key")
                normalized = re.sub(r"[_-]", "", key).lower()
                if normalized in {
                    "apikey", "apisecret", "apisecretkey", "secret", "secretkey",
                    "passphrase", "apipassphrase", "password", "privatekey",
                    "authorization", "accesstoken", "refreshtoken", "cookie",
                    "okaccesskey", "okaccesssign", "okaccesspassphrase",
                }:
                    raise ValueError("Exact raw payload must not contain credentials")
                check(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                check(item, depth + 1)
        elif value is not None and type(value) not in (str, int, float, bool):
            raise ValueError("Invalid exact raw JSON value")

    def unique_object(pairs: list) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate exact raw JSON key")
            result[key] = value
        return result

    try:
        if isinstance(raw, str):
            if len(raw) > _EXACT_PAGE_MAX_BYTES or len(raw.encode("utf-8")) > _EXACT_PAGE_MAX_BYTES:
                raise ValueError("Exact raw payload is too large")
            decoded = json.loads(raw, object_pairs_hook=unique_object)
            raw_json = raw
        else:
            decoded = raw
            check(decoded)
            raw_json = json.dumps(decoded, allow_nan=False, sort_keys=True, separators=(",", ":"))
        if not isinstance(decoded, dict):
            raise ValueError("Exact raw payload must be an object")
        check(decoded)
        canonical = json.dumps(decoded, allow_nan=False, sort_keys=True, separators=(",", ":"))
        byte_length = max(len(raw_json.encode("utf-8")), len(canonical.encode("utf-8")))
        if byte_length > _EXACT_PAGE_MAX_BYTES:
            raise ValueError("Exact raw payload is too large")
        return decoded, raw_json, hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
        raise ValueError("Invalid, oversize, or credential-bearing exact raw payload") from None


def _verified_observation(record, parser, optional_fields, required_fields=()):
    """Reparse the journal and reject drift in its hash or exact projection."""
    decoded, _, payload_hash = _exact_payload(record.raw_json)
    if record.payload_hash != payload_hash:
        raise ValueError("Stored preflight payload hash mismatch")
    fact = parser(decoded)
    _verify_projection(record, fact, optional_fields, required_fields)
    return PreflightObservation(
        fact, payload_hash, _receipt_integer(record.received_at, "preflight receipt time"),
    )


def _verify_projection(record, fact, optional_fields, required_fields=()):
    for name in required_fields:
        if getattr(record, name) != getattr(fact, name):
            raise ValueError("Stored preflight fact mismatch")
    for name in optional_fields:
        value = getattr(fact, name)
        state = "missing" if value is MISSING else "null" if value is None else "value"
        if (getattr(record, name) != (None if value is MISSING else value)
                or getattr(record, f"{name}_state") != state):
            raise ValueError("Stored preflight optional state mismatch")


def _exact_traversal(scope_id: str, uid: str, endpoint: str,
                     inst_id: str | None, before: str | None) -> str:
    validate_raw_text(uid, "UID")
    validate_raw_text(endpoint, "endpoint")
    if inst_id is not None:
        canonical_spot_symbol(inst_id)
    if before is not None:
        validate_raw_text(before, "before")
    return _exact_hash([scope_id, uid, endpoint, "SPOT", inst_id, before])


def _exact_record_values(fact: SpotFillFact) -> dict:
    values = {name: getattr(fact, name) for name in (
        "bill_id", "symbol", "inst_id", "trade_id", "ord_id", "side", "fill_sz",
        "fill_px", "fill_time", "record_time",
    )}
    for name in _EXACT_OPTIONALS:
        value = getattr(fact, name)
        values[name] = None if value is MISSING else value
        values[f"{name}_state"] = "missing" if value is MISSING else (
            "null" if value is None else "value"
        )
    return values


def _exact_fact(record: ExactSpotFillRecord) -> SpotFillFact:
    values = {name: getattr(record, name) for name in (
        "bill_id", "inst_id", "trade_id", "ord_id", "side", "fill_sz", "fill_px",
        "fill_time", "record_time",
    )}
    for name in _EXACT_OPTIONALS:
        state = getattr(record, f"{name}_state")
        values[name] = MISSING if state == "missing" else getattr(record, name)
    return SpotFillFact(**values)


def _exact_copy(record):
    # Return detached values even within a caller-owned transaction.
    return type(record)(**record.model_dump())


class _AccountingTransaction:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_orders(
        self,
        *,
        source: str | None = None,
        runtime_mode: str | None = None,
        include_inactive: bool = False,
    ) -> list[OrderRecord]:
        statement = select(OrderRecord)
        if not include_inactive:
            statement = statement.where(
                or_(OrderRecord.active.is_(True), OrderRecord.active.is_(None))
            )
        if source is not None:
            statement = statement.where(OrderRecord.source == source)
        if runtime_mode is not None:
            statement = statement.where(OrderRecord.runtime_mode == runtime_mode)
        return list(self.session.exec(statement).all())

    def get_account(
        self,
        strategy: str,
        *,
        source: str | None = None,
        runtime_mode: str | None = None,
        include_inactive: bool = False,
    ) -> AccountRecord | None:
        statement = select(AccountRecord).where(AccountRecord.strategy == strategy)
        if not include_inactive:
            statement = statement.where(
                or_(AccountRecord.active.is_(True), AccountRecord.active.is_(None))
            )
        if source is not None:
            statement = statement.where(AccountRecord.source == source)
        if runtime_mode is not None:
            statement = statement.where(AccountRecord.runtime_mode == runtime_mode)
        account = self.session.exec(
            statement.order_by(AccountRecord.updated_at.desc(), AccountRecord.id.desc())
        ).first()
        if account is None:
            return None
        account.normalize_values()
        # Accounting mutates the returned record before its eventual upsert. Return
        # a value copy so stale callbacks cannot alter the persisted ORM instance.
        return AccountRecord(
            **account.model_dump(),
            source=account.source,
            runtime_mode=account.runtime_mode,
        )

    def insert_account(self, account: AccountRecord) -> AccountRecord:
        account.normalize_values()
        self.session.add(account)
        self.session.flush()
        return account

    def mark_account_stale(
        self,
        strategy: str,
        stale_at: int,
        *,
        source: str | None = None,
        runtime_mode: str | None = None,
    ) -> None:
        if source is None or runtime_mode is None:
            return
        account = self.session.exec(
            select(AccountRecord).where(
                AccountRecord.strategy == strategy,
                AccountRecord.source == source,
                AccountRecord.runtime_mode == runtime_mode,
                or_(AccountRecord.active.is_(True), AccountRecord.active.is_(None)),
            ).order_by(AccountRecord.updated_at.desc(), AccountRecord.id.desc())
        ).first()
        if account is None:
            return
        account.active = False
        account.stale_at = _safe_timestamp(stale_at)
        self.session.add(account)

    def upsert_account(self, account: AccountRecord) -> AccountRecord:
        account.normalize_values()
        existing = self.session.exec(
            select(AccountRecord).where(
                AccountRecord.strategy == account.strategy,
                AccountRecord.source == account.source,
                AccountRecord.runtime_mode == account.runtime_mode,
                or_(AccountRecord.active.is_(True), AccountRecord.active.is_(None)),
            ).order_by(AccountRecord.updated_at.desc(), AccountRecord.id.desc())
        ).first()
        if existing is None:
            self.session.add(account)
            self.session.flush()
            return account
        merged = _merge_account_record(existing, account)
        self.session.add(merged)
        return merged

    def get_positions(
        self,
        strategy: str,
        symbol: str | None = None,
        *,
        source: str | None = None,
        runtime_mode: str | None = None,
        include_inactive: bool = False,
    ) -> list[PositionRecord]:
        statement = select(PositionRecord).where(PositionRecord.strategy == strategy)
        if not include_inactive:
            statement = statement.where(
                or_(PositionRecord.active.is_(True), PositionRecord.active.is_(None))
            )
        if symbol is not None:
            statement = statement.where(PositionRecord.symbol == symbol)
        if source is not None:
            statement = statement.where(PositionRecord.source == source)
        if runtime_mode is not None:
            statement = statement.where(PositionRecord.runtime_mode == runtime_mode)
        return list(self.session.exec(statement).all())

    def get_position(
        self,
        strategy: str,
        symbol: str,
        side: str | None = None,
        *,
        source: str | None = None,
        runtime_mode: str | None = None,
        include_inactive: bool = False,
    ) -> PositionRecord | None:
        statement = select(PositionRecord).where(
            PositionRecord.strategy == strategy,
            PositionRecord.symbol == symbol,
        )
        if not include_inactive:
            statement = statement.where(
                or_(PositionRecord.active.is_(True), PositionRecord.active.is_(None))
            )
        if source is not None:
            statement = statement.where(PositionRecord.source == source)
        if runtime_mode is not None:
            statement = statement.where(PositionRecord.runtime_mode == runtime_mode)
        if side is not None:
            statement = statement.where(PositionRecord.side == side)
        return self.session.exec(
            statement.order_by(PositionRecord.timestamp.desc(), PositionRecord.id.desc())
        ).first()

    def get_open_positions(
        self,
        strategy: str,
        *,
        source: str | None = None,
        runtime_mode: str | None = None,
        include_inactive: bool = False,
    ) -> list[PositionRecord]:
        statement = select(PositionRecord).where(
            PositionRecord.strategy == strategy,
            PositionRecord.amount != 0,
        )
        if not include_inactive:
            statement = statement.where(
                or_(PositionRecord.active.is_(True), PositionRecord.active.is_(None))
            )
        if source is not None:
            statement = statement.where(PositionRecord.source == source)
        if runtime_mode is not None:
            statement = statement.where(PositionRecord.runtime_mode == runtime_mode)
        return list(self.session.exec(statement).all())

    def mark_position_stale(
        self,
        strategy: str,
        symbol: str,
        side: str,
        stale_at: int,
        *,
        source: str | None = None,
        runtime_mode: str | None = None,
    ) -> None:
        position = self.get_position(
            strategy,
            symbol,
            side,
            source=source,
            runtime_mode=runtime_mode,
        )
        if position is None:
            return
        position.active = False
        position.stale_at = _safe_timestamp(stale_at)
        self.session.add(position)

    def insert_position(self, position: PositionRecord) -> PositionRecord:
        position.normalize_values()
        self.session.add(position)
        self.session.flush()
        return position

    def upsert_position(self, position: PositionRecord) -> PositionRecord:
        position.normalize_values()
        existing = self.session.exec(
            select(PositionRecord).where(
                PositionRecord.strategy == position.strategy,
                PositionRecord.symbol == position.symbol,
                PositionRecord.side == position.side,
                PositionRecord.source == position.source,
                PositionRecord.runtime_mode == position.runtime_mode,
                or_(PositionRecord.active.is_(True), PositionRecord.active.is_(None)),
            )
        ).first()
        if existing is None and position.id is not None:
            identity_candidate = self.session.exec(
                select(PositionRecord).where(PositionRecord.id == position.id)
            ).first()
            if identity_candidate is not None:
                if _same_provenance(identity_candidate, position):
                    existing = identity_candidate
                else:
                    logger.warning(
                        "Position upsert provenance conflict for id=%s; preserving "
                        "source=%s runtime_mode=%s and inserting an isolated row",
                        position.id,
                        identity_candidate.source,
                        identity_candidate.runtime_mode,
                    )
                    position.id = None
        if existing is None:
            self.session.add(position)
            self.session.flush()
            return position
        merged = _merge_position_record(existing, position)
        self.session.add(merged)
        return merged

    def delete_position(
        self,
        strategy: str,
        symbol: str,
        side: str | None = None,
        *,
        source: str | None = None,
        runtime_mode: str | None = None,
        stale_at: int | None = None,
    ) -> None:
        if source is None or runtime_mode is None:
            logger.warning(
                (
                    "Refusing unscoped position archive for %s/%s; "
                    "source and runtime_mode are required"
                ),
                strategy,
                symbol,
            )
            return
        statement = select(PositionRecord).where(
            PositionRecord.strategy == strategy,
            PositionRecord.symbol == symbol,
            or_(PositionRecord.active.is_(True), PositionRecord.active.is_(None)),
        )
        if side is not None:
            statement = statement.where(PositionRecord.side == side)
        if source is not None:
            statement = statement.where(PositionRecord.source == source)
        if runtime_mode is not None:
            statement = statement.where(PositionRecord.runtime_mode == runtime_mode)
        for position in self.session.exec(statement).all():
            position.active = False
            position.stale_at = _safe_timestamp(
                stale_at if stale_at is not None else position.timestamp
            )
            self.session.add(position)

    def get_trades(
        self,
        strategy: str | None = None,
        order_id: str | None = None,
        *,
        source: str | None = None,
        runtime_mode: str | None = None,
        include_inactive: bool = False,
    ):
        statement = select(TradeRecord)
        if not include_inactive:
            statement = statement.where(
                or_(TradeRecord.active.is_(True), TradeRecord.active.is_(None))
            )
        if strategy is not None:
            statement = statement.where(TradeRecord.strategy == strategy)
        if order_id is not None:
            statement = statement.where(TradeRecord.order_id == order_id)
        if source is not None:
            statement = statement.where(TradeRecord.source == source)
        if runtime_mode is not None:
            statement = statement.where(TradeRecord.runtime_mode == runtime_mode)
        return list(self.session.exec(statement).all())

    def save_trade(self, trade: TradeRecord) -> TradeRecord:
        self.session.add(trade)
        self.session.flush()
        return trade

    def upsert_trade(self, trade: TradeRecord) -> TradeRecord:
        return _upsert_trade_in_session(self.session, trade)

    def rebind_trades_to_orders(self, orders: list[OrderRecord] | None = None) -> None:
        target_orders = list(orders) if orders is not None else self.get_orders()
        for trade in self.session.exec(select(TradeRecord)).all():
            if not has_placeholder_trade_identity(trade):
                continue
            trade_provenance = _record_provenance(trade)
            matches = [
                order for order in target_orders
                if (
                    _record_provenance(order) == "live"
                    and trade_provenance == "live"
                    or _record_provenance(order) == trade_provenance
                    and trade_provenance != "live"
                )
                and Repository._trade_matches_order(trade, order)
            ]
            if len(matches) != 1:
                if len(matches) > 1:
                    logger.warning(
                        "Rejecting ambiguous placeholder trade rebind; matched multiple orders: "
                        "exchange_order_id=%s client_order_id=%s",
                        trade.exchange_order_id,
                        trade.client_order_id,
                    )
                continue
            order = matches[0]
            trade.order_id = order.order_id
            trade.strategy = order.strategy
            self.session.add(trade)

    def get_private_uid(self) -> str | None:
        scope_id = _receipt_scope(self.session)
        bindings = self.session.exec(select(PrivateUIDBindingRecord)).all()
        if any(binding.account_scope_id != scope_id for binding in bindings):
            raise ValueError("Private UID account scope mismatch")
        return bindings[0].uid if bindings else None

    def get_spot_preflight_evidence(
        self, symbol: str, *, expected_account_scope_id: str,
    ) -> SpotPreflightEvidence:
        canonical_spot_symbol(symbol)
        validate_raw_text(expected_account_scope_id, "expected account scope")
        scope_id = _receipt_scope(self.session, expected_account_scope_id)
        uid = self.get_private_uid()
        if uid is not None:
            validate_raw_text(uid, "UID")
        # Reject foreign records rather than hiding them through a WHERE scope filter.
        models = (PrivateAccountConfigRecord, PrivateAccountBalanceRecord,
                  PrivateSpotInstrumentResponseRecord, PrivateSpotInstrumentRecord)
        for model in models:
            invalid = model.account_scope_id != scope_id
            if model in (PrivateAccountConfigRecord, PrivateAccountBalanceRecord):
                invalid = or_(invalid, model.uid != uid) if uid is not None else True
            if self.session.exec(select(model).where(invalid).limit(1)).first() is not None:
                raise ValueError("Stored preflight scope or UID mismatch")

        def latest(model):
            return self.session.exec(select(model).order_by(
                model.received_at.desc(), text(f"{model.__tablename__}.rowid DESC"),
            ).limit(1)).first()

        config_record = latest(PrivateAccountConfigRecord)
        config = None if config_record is None else _verified_observation(
            config_record, parse_okx_account_config, ACCOUNT_CONFIG_FIELDS, ("uid",),
        )
        balance_record = latest(PrivateAccountBalanceRecord)
        balance = None if balance_record is None else _verified_observation(
            balance_record, parse_okx_account_balance, ACCOUNT_BALANCE_FIELDS,
        )
        if balance is not None:
            # details_json is a retained exact projection, not a second source.
            decoded, _, _ = _exact_payload(balance_record.raw_json)
            canonical_details = json.dumps(decoded["data"][0]["details"], allow_nan=False,
                                          sort_keys=True, separators=(",", ":"))
            if balance_record.details_json != canonical_details:
                raise ValueError("Stored preflight balance details mismatch")

        response = latest(PrivateSpotInstrumentResponseRecord)
        instrument = None
        if response is not None:
            decoded, _, payload_hash = _exact_payload(response.raw_json)
            received_at = _receipt_integer(response.received_at, "instrument receipt time")
            if payload_hash != response.payload_hash:
                raise ValueError("Stored preflight instrument hash mismatch")
            facts = parse_okx_spot_instruments(decoded)
            records = self.session.exec(select(PrivateSpotInstrumentRecord).where(
                PrivateSpotInstrumentRecord.payload_hash == payload_hash,
            )).all()
            by_id = {record.inst_id: record for record in records}
            if set(by_id) != {fact.inst_id for fact in facts}:
                raise ValueError("Stored preflight instrument row set mismatch")
            for fact in facts:
                record = by_id[fact.inst_id]
                if record.received_at != received_at:
                    raise ValueError("Stored preflight instrument receipt mismatch")
                _verify_projection(record, fact, INSTRUMENT_OPTIONAL_FIELDS, (
                    "inst_id", "base_ccy", "quote_ccy", "tick_sz", "lot_sz", "min_sz",
                ))
            selected = next((fact for fact in facts if fact.inst_id == symbol), None)
            instrument = PreflightObservation(selected, payload_hash, received_at)
        return SpotPreflightEvidence(scope_id, uid, config, balance, instrument)

    def commit_private_spot_instruments(
        self, facts: tuple[SpotInstrumentFact, ...], *, raw_payload: object,
        received_at: int, requested_inst_id: str | None = None,
    ) -> list[PrivateSpotInstrumentRecord]:
        """Atomically retain one public response and all its exact rule rows."""
        scope_id = _receipt_scope(self.session)
        received_at = _receipt_integer(received_at, "instrument received time")
        decoded, raw_json, payload_hash = _exact_payload(raw_payload)
        parsed = parse_okx_spot_instruments(decoded, inst_id=requested_inst_id)
        if (not isinstance(facts, tuple)
                or any(not isinstance(fact, SpotInstrumentFact) for fact in facts)
                or parsed != facts):
            raise ValueError("Raw spot instruments do not match typed facts")
        existing = self.session.get(PrivateSpotInstrumentResponseRecord, (scope_id, payload_hash))
        if existing is None:
            self.session.add(PrivateSpotInstrumentResponseRecord(
                account_scope_id=scope_id, payload_hash=payload_hash,
                received_at=received_at, raw_json=raw_json,
            ))
            self.session.flush()
            for fact in facts:
                values = {name: getattr(fact, name) for name in (
                    "inst_id", "base_ccy", "quote_ccy", "tick_sz", "lot_sz", "min_sz",
                )}
                for name in INSTRUMENT_OPTIONAL_FIELDS:
                    value = getattr(fact, name)
                    values[name] = None if value is MISSING else value
                    values[f"{name}_state"] = "missing" if value is MISSING else (
                        "null" if value is None else "value"
                    )
                self.session.add(PrivateSpotInstrumentRecord(
                    account_scope_id=scope_id, payload_hash=payload_hash,
                    received_at=received_at, **values,
                ))
            self.session.flush()
        # Preserve response order and return detached records, including on replay.
        response = self.session.get(PrivateSpotInstrumentResponseRecord, (scope_id, payload_hash))
        records = self.session.exec(select(PrivateSpotInstrumentRecord).where(
            PrivateSpotInstrumentRecord.account_scope_id == scope_id,
            PrivateSpotInstrumentRecord.payload_hash == payload_hash,
        )).all()
        detached = {}
        for record in records:
            copy = _exact_copy(record)
            copy._raw_json = response.raw_json
            detached[record.inst_id] = copy
        return [detached[fact.inst_id] for fact in facts]

    def get_private_spot_instruments(
        self, inst_id: str | None = None,
    ) -> list[PrivateSpotInstrumentRecord]:
        scope_id = _receipt_scope(self.session)
        if inst_id is not None:
            canonical_spot_symbol(inst_id)
        responses = self.session.exec(select(PrivateSpotInstrumentResponseRecord)).all()
        records = self.session.exec(select(PrivateSpotInstrumentRecord).order_by(
            PrivateSpotInstrumentRecord.received_at, text("private_spot_instrument.rowid"),
        )).all()
        if any(r.account_scope_id != scope_id for r in (*responses, *records)):
            raise ValueError("Spot instrument account scope mismatch")
        response_by_hash = {response.payload_hash: response for response in responses}
        result = []
        for record in records:
            response = response_by_hash.get(record.payload_hash)
            if response is None or response.received_at != record.received_at:
                raise ValueError("Spot instrument response mismatch")
            if inst_id is None or inst_id == record.inst_id:
                detached = _exact_copy(record)
                detached._raw_json = response.raw_json
                result.append(detached)
        return result

    def bind_private_uid(self, uid: str, first_seen_at: int) -> PrivateUIDBindingRecord:
        scope_id = _receipt_scope(self.session)
        validate_raw_text(uid, "UID")
        first_seen_at = _receipt_integer(first_seen_at, "UID first seen time")
        bound = self.get_private_uid()
        if bound is not None and bound != uid:
            raise ValueError("Private UID binding conflict")
        existing = self.session.get(PrivateUIDBindingRecord, scope_id)
        if existing is not None:
            return _exact_copy(existing)
        record = PrivateUIDBindingRecord(
            account_scope_id=scope_id, uid=uid, first_seen_at=first_seen_at,
        )
        self.session.add(record)
        self.session.flush()
        return _exact_copy(record)

    def _exact_uid_scope(self, uid: str) -> str:
        scope_id = _receipt_scope(self.session)
        validate_raw_text(uid, "UID")
        bound = self.get_private_uid()
        if bound is not None and bound != uid:
            raise ValueError("Private UID binding conflict")
        return scope_id

    def commit_private_account_config(
        self, fact: AccountConfigFact, *, raw_payload: object, received_at: int,
    ) -> PrivateAccountConfigRecord:
        """Retain raw config evidence and bind its UID, with no operational writes."""
        scope_id = _receipt_scope(self.session)
        received_at = _receipt_integer(received_at, "account config received time")
        decoded, raw_json, payload_hash = _exact_payload(raw_payload)
        if not isinstance(fact, AccountConfigFact) or parse_okx_account_config(decoded) != fact:
            raise ValueError("Raw account config does not match typed fact")
        self._exact_uid_scope(fact.uid)
        self.bind_private_uid(fact.uid, received_at)
        existing = self.session.get(
            PrivateAccountConfigRecord, (scope_id, fact.uid, payload_hash),
        )
        if existing is not None:
            return _exact_copy(existing)
        values = {}
        for name in ACCOUNT_CONFIG_FIELDS:
            value = getattr(fact, name)
            values[name] = None if value is MISSING else value
            values[f"{name}_state"] = "missing" if value is MISSING else (
                "null" if value is None else "value"
            )
        record = PrivateAccountConfigRecord(
            account_scope_id=scope_id, uid=fact.uid, payload_hash=payload_hash,
            received_at=received_at, raw_json=raw_json, **values,
        )
        self.session.add(record)
        self.session.flush()
        return _exact_copy(record)

    def get_private_account_config(
        self, uid: str | None = None,
    ) -> list[PrivateAccountConfigRecord]:
        scope_id = _receipt_scope(self.session)
        bound_uid = self.get_private_uid()
        if uid is not None:
            self._exact_uid_scope(uid)
        records = self.session.exec(select(PrivateAccountConfigRecord).order_by(
            PrivateAccountConfigRecord.received_at, text("private_account_config.rowid"),
        )).all()
        if any(r.account_scope_id != scope_id or r.uid != bound_uid for r in records):
            raise ValueError("Private account config scope or UID mismatch")
        return [_exact_copy(record) for record in records]

    def commit_private_account_balance(
        self, fact: AccountBalanceFact, *, raw_payload: object, received_at: int,
        uid: str | None = None,
    ) -> PrivateAccountBalanceRecord:
        """Retain balance evidence under an existing UID, never bind from balances."""
        scope_id = _receipt_scope(self.session)
        received_at = _receipt_integer(received_at, "account balance received time")
        decoded, raw_json, payload_hash = _exact_payload(raw_payload)
        if not isinstance(fact, AccountBalanceFact) or parse_okx_account_balance(decoded) != fact:
            raise ValueError("Raw account balance does not match typed fact")
        bound_uid = self.get_private_uid()
        if bound_uid is None:
            raise ValueError("Account balance requires an existing private UID binding")
        if uid is not None:
            self._exact_uid_scope(uid)
        existing = self.session.get(
            PrivateAccountBalanceRecord, (scope_id, bound_uid, payload_hash),
        )
        if existing is not None:
            return _exact_copy(existing)
        values = {}
        for name in ACCOUNT_BALANCE_FIELDS:
            value = getattr(fact, name)
            values[name] = None if value is MISSING else value
            values[f"{name}_state"] = "missing" if value is MISSING else (
                "null" if value is None else "value"
            )
        record = PrivateAccountBalanceRecord(
            account_scope_id=scope_id, uid=bound_uid, payload_hash=payload_hash,
            received_at=received_at, raw_json=raw_json,
            details_json=json.dumps(decoded["data"][0]["details"], allow_nan=False,
                                    sort_keys=True, separators=(",", ":")),
            **values,
        )
        self.session.add(record)
        self.session.flush()
        return _exact_copy(record)

    def get_private_account_balances(
        self, uid: str | None = None,
    ) -> list[PrivateAccountBalanceRecord]:
        scope_id = _receipt_scope(self.session)
        bound_uid = self.get_private_uid()
        if uid is not None:
            self._exact_uid_scope(uid)
        records = self.session.exec(select(PrivateAccountBalanceRecord).order_by(
            PrivateAccountBalanceRecord.received_at, text("private_account_balance.rowid"),
        )).all()
        if any(r.account_scope_id != scope_id or r.uid != bound_uid for r in records):
            raise ValueError("Private account balance scope or UID mismatch")
        return [_exact_copy(record) for record in records]

    def get_exact_spot_fills(self, *, uid: str) -> list[SpotFillFact]:
        scope_id = self._exact_uid_scope(uid)
        records = self.session.exec(select(ExactSpotFillRecord).where(
            ExactSpotFillRecord.account_scope_id == scope_id, ExactSpotFillRecord.uid == uid,
        ).order_by(ExactSpotFillRecord.bill_id.desc())).all()
        return [_exact_fact(record) for record in records]

    def get_exact_spot_fill_pages(self, *, uid: str) -> list[ExactSpotFillPageRecord]:
        scope_id = self._exact_uid_scope(uid)
        records = self.session.exec(select(ExactSpotFillPageRecord).where(
            ExactSpotFillPageRecord.account_scope_id == scope_id,
            ExactSpotFillPageRecord.uid == uid,
        ).order_by(ExactSpotFillPageRecord.received_at, text("exact_spot_fill_page.rowid"))).all()
        return [_exact_copy(record) for record in records]

    def get_exact_spot_fill_cursor(
        self, *, uid: str, endpoint: str = _EXACT_ENDPOINT,
        inst_id: str | None = None, before: str | None = None,
    ) -> ExactSpotFillCursorRecord | None:
        scope_id = self._exact_uid_scope(uid)
        traversal = _exact_traversal(scope_id, uid, endpoint, inst_id, before)
        record = self.session.get(ExactSpotFillCursorRecord, traversal)
        if record is not None and (record.account_scope_id != scope_id or record.uid != uid):
            raise ValueError("Exact cursor account scope mismatch")
        return _exact_copy(record) if record is not None else None

    def commit_exact_spot_fill_page(
        self, page: SpotFillPage, *, uid: str, raw_payload: object, received_at: int,
        expected_cursor_version: int = 0, endpoint: str = _EXACT_ENDPOINT,
    ) -> ExactSpotFillCursorRecord:
        """Atomically bind identity, retain response/facts and CAS its traversal.

        Replaying an identical request/response returns the *current* cursor,
        including when it has advanced beyond that page. A short/empty response
        never sets completeness. No float projections or ledger are involved.
        """
        scope_id = self._exact_uid_scope(uid)
        received_at = _receipt_integer(received_at, "exact page received time")
        expected_cursor_version = _receipt_integer(expected_cursor_version, "cursor version")
        if not isinstance(page, SpotFillPage) or not isinstance(page.coverage, FactCoverage):
            raise ValueError("Invalid exact SPOT fill page")
        traversal = _exact_traversal(
            scope_id, uid, endpoint, page.inst_id, page.requested_before,
        )
        decoded, raw_json, payload_hash = _exact_payload(raw_payload)
        page_id = _exact_hash([traversal, page.requested_after])
        existing = self.session.get(ExactSpotFillPageRecord, page_id)
        cursor = self.session.get(ExactSpotFillCursorRecord, traversal)
        for record in (existing, cursor):
            if record is not None and (record.account_scope_id != scope_id or record.uid != uid):
                raise ValueError("Exact page/cursor account scope mismatch")
        previous_pages: list[SpotFillPage] = []
        # Reconstruct traversal evidence from durable raw pages, rather than
        # trusting caller-supplied history or only the current cursor value.
        # A legal replay already has an identity and must bypass repeated-page
        # detection while still validating the typed/raw response below.
        if existing is None:
            prior_records = self.session.exec(select(ExactSpotFillPageRecord).where(
                ExactSpotFillPageRecord.traversal_id == traversal,
            ).order_by(text("exact_spot_fill_page.rowid"))).all()
            for prior in prior_records:
                if prior.account_scope_id != scope_id or prior.uid != uid:
                    raise ValueError("Exact page account scope mismatch")
                observed = parse_okx_spot_fill_page(
                    json.loads(prior.raw_json), inst_id=prior.inst_id,
                    after=prior.requested_after, before=prior.requested_before,
                    previous_pages=previous_pages,
                )
                if (observed.next_after != prior.next_after
                        or observed.coverage.value != prior.coverage):
                    raise ValueError("Persisted exact page evidence conflict")
                previous_pages.append(observed)
        parsed = parse_okx_spot_fill_page(
            decoded, inst_id=page.inst_id, after=page.requested_after,
            before=page.requested_before, previous_pages=previous_pages,
        )
        if (parsed.fills != page.fills or parsed.next_after != page.next_after
                or parsed.coverage is not page.coverage):
            raise ValueError("Exact typed page conflicts with raw payload or coverage")
        if existing is not None:
            if (existing.payload_hash != payload_hash or existing.next_after != page.next_after
                    or existing.coverage != page.coverage.value):
                raise ValueError("Exact page identity conflict")
            if cursor is None:
                raise ValueError("Exact page cursor is missing")
            return _exact_copy(cursor)
        if (cursor.version if cursor is not None else 0) != expected_cursor_version:
            raise ValueError("Exact page cursor version conflict")
        if cursor is not None and (
            cursor.next_after is None or cursor.next_after != page.requested_after
        ):
            raise ValueError("Exact page request cursor conflict")
        self.bind_private_uid(uid, received_at)
        # Flush each insertion to expose conflicts before the cursor can advance.
        # The enclosing Repository transaction rolls every insertion back on error.
        for fact in page.fills:
            values = _exact_record_values(fact)
            existing_fact = self.session.get(
                ExactSpotFillRecord, (scope_id, uid, fact.bill_id),
            )
            if existing_fact is not None:
                immutable = (
                    "inst_id", "symbol", "trade_id", "ord_id", "side", "fill_sz", "fill_px",
                    "fill_time", "fee", "fee_state", "fee_ccy", "fee_ccy_state",
                )
                if any(getattr(existing_fact, name) != values[name] for name in immutable):
                    raise ValueError("Exact billId financial conflict")
            else:
                self.session.add(ExactSpotFillRecord(account_scope_id=scope_id, uid=uid, **values))
                self.session.flush()
        self.session.add(ExactSpotFillPageRecord(
            page_id=page_id, traversal_id=traversal, account_scope_id=scope_id, uid=uid,
            endpoint=endpoint, inst_id=page.inst_id, requested_before=page.requested_before,
            requested_after=page.requested_after, next_after=page.next_after,
            coverage=page.coverage.value, received_at=received_at,
            payload_hash=payload_hash, raw_json=raw_json,
        ))
        if cursor is None:
            cursor = ExactSpotFillCursorRecord(
                traversal_id=traversal, account_scope_id=scope_id, uid=uid, endpoint=endpoint,
                inst_id=page.inst_id, requested_before=page.requested_before,
                version=1, next_after=page.next_after,
            )
        else:
            cursor.version += 1
            cursor.next_after = page.next_after
        self.session.add(cursor)
        self.session.flush()
        return _exact_copy(cursor)

    def start_private_sync_receipt(
        self, *, account_scope_id: str, symbols: list[str] | None, since: int | None,
        limit: int, started_at: int, attempt_id: str | None = None,
    ) -> PrivateSyncReceiptRecord:
        _receipt_scope(self.session, account_scope_id)
        started_at = _receipt_integer(started_at, "start time")
        if since is not None:
            since = _receipt_integer(since, "since")
        limit = _receipt_integer(limit, "limit", positive=True)
        if limit > 1000:
            raise ValueError("Private sync limit exceeds 1000")
        if symbols is not None and (
            not isinstance(symbols, list) or len(symbols) > 100
            or any(not isinstance(symbol, str) or not symbol or len(symbol) > 128
                   or symbol != symbol.strip() for symbol in symbols)
        ):
            raise ValueError("Invalid private sync symbol scope")
        attempt_id = _receipt_identity(attempt_id if attempt_id is not None else uuid.uuid4().hex)
        values = dict(account_scope_id=account_scope_id, symbols=symbols, since=since,
                      limit=limit, started_at=started_at)
        existing = self.session.exec(select(PrivateSyncReceiptRecord).where(
            PrivateSyncReceiptRecord.attempt_id == attempt_id,
        )).first()
        if existing is not None:
            if any(getattr(existing, key) != value for key, value in values.items()):
                raise ValueError("Private sync attempt identity conflicts")
            return _receipt_copy(existing)
        previous = self.session.exec(select(PrivateSyncReceiptRecord.generation).order_by(
            PrivateSyncReceiptRecord.generation.desc(),
        )).first()
        record = PrivateSyncReceiptRecord(
            attempt_id=attempt_id, generation=(previous or 0) + 1, **values,
        )
        self.session.add(record)
        self.session.flush()
        return _receipt_copy(record)

    def get_private_sync_receipt(self, attempt_id: str) -> PrivateSyncReceiptRecord | None:
        scope_id = _receipt_scope(self.session)
        attempt_id = _receipt_identity(attempt_id)
        record = self.session.exec(select(PrivateSyncReceiptRecord).where(
            PrivateSyncReceiptRecord.attempt_id == attempt_id,
        )).first()
        if record is not None and record.account_scope_id != scope_id:
            raise ValueError("Private sync receipt account scope mismatch")
        return _receipt_copy(record) if record is not None else None

    def get_private_sync_status(self) -> dict[str, PrivateSyncReceiptRecord | None]:
        scope_id = _receipt_scope(self.session)
        latest = self.session.exec(select(PrivateSyncReceiptRecord).order_by(
            PrivateSyncReceiptRecord.generation.desc(),
        )).first()
        committed = self.session.exec(select(PrivateSyncReceiptRecord).where(
            PrivateSyncReceiptRecord.state.in_(["committed", "partial"]),
        ).order_by(PrivateSyncReceiptRecord.generation.desc())).first()
        if any(record is not None and record.account_scope_id != scope_id
               for record in (latest, committed)):
            raise ValueError("Private sync receipt account scope mismatch")
        return {
            "latest_attempt": _receipt_copy(latest) if latest is not None else None,
            "last_committed": _receipt_copy(committed) if committed is not None else None,
        }

    def _finish_private_sync_receipt(
        self, attempt_id: str, *, state: str, completed_at: int, received_at: int | None,
        parts: dict, cursor: dict, result: dict, error_code: str | None,
    ) -> PrivateSyncReceiptRecord:
        scope_id = _receipt_scope(self.session)
        attempt_id = _receipt_identity(attempt_id)
        record = self.session.exec(select(PrivateSyncReceiptRecord).where(
            PrivateSyncReceiptRecord.attempt_id == attempt_id,
        )).first()
        if record is None or record.account_scope_id != scope_id:
            raise ValueError("Private sync attempt not found in this account")
        completed_at = _receipt_integer(completed_at, "completion time")
        if completed_at < record.started_at:
            raise ValueError("Private sync completion predates its start")
        if received_at is not None:
            received_at = _receipt_integer(received_at, "receipt time")
            if not record.started_at <= received_at <= completed_at:
                raise ValueError("Private sync received time is outside its observation interval")
        values = dict(state=state, completed_at=completed_at, received_at=received_at,
                      parts=_receipt_json(parts, "parts"), cursor=_receipt_json(cursor, "cursor"),
                      result=_receipt_json(result, "result"), error_code=error_code)
        if state in {"committed", "partial"}:
            newer_committed = self.session.exec(select(PrivateSyncReceiptRecord.generation).where(
                PrivateSyncReceiptRecord.state.in_(["committed", "partial"]),
                PrivateSyncReceiptRecord.generation > record.generation,
            )).first()
            if newer_committed is not None:
                # The caller writes projections in this same transaction. Reject
                # stale retries too, so none can overwrite a newer committed sync.
                raise ValueError("Private sync attempt superseded by a newer committed generation")
        if record.state != "in_progress":
            if any(getattr(record, key) != value for key, value in values.items()):
                raise ValueError("Private sync terminal receipt conflicts")
            return _receipt_copy(record)
        for key, value in values.items():
            setattr(record, key, value)
        self.session.add(record)
        self.session.flush()
        return _receipt_copy(record)

    def finalize_private_sync_receipt(
        self, attempt_id: str, *, state: str, completed_at: int, received_at: int,
        parts: dict, cursor: dict, result: dict,
    ) -> PrivateSyncReceiptRecord:
        if state not in {"committed", "partial"}:
            raise ValueError("Invalid private sync committed state")
        return self._finish_private_sync_receipt(
            attempt_id, state=state, completed_at=completed_at, received_at=received_at,
            parts=parts, cursor=cursor, result=result, error_code=None,
        )

    def fail_private_sync_receipt(
        self, attempt_id: str, *, completed_at: int, error_code: str, state: str = "failed",
        parts: dict | None = None, cursor: dict | None = None,
    ) -> PrivateSyncReceiptRecord:
        if state not in {"failed", "abandoned"}:
            raise ValueError("Invalid private sync failure state")
        if not isinstance(error_code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error_code):
            raise ValueError("Private sync failure requires a bounded reason code")
        return self._finish_private_sync_receipt(
            attempt_id, state=state, completed_at=completed_at, received_at=None,
            parts=parts if parts is not None else {}, cursor=cursor if cursor is not None else {},
            result={}, error_code=error_code,
        )

    def get_kill_switch(self) -> KillSwitchRecord:
        state = self.session.exec(select(KillSwitchRecord)).first()
        return state or KillSwitchRecord(engaged=False, reason="", updated_at=0)

    def set_kill_switch(self, engaged: bool, reason: str, updated_at: int) -> KillSwitchRecord:
        state = self.session.exec(select(KillSwitchRecord)).first()
        if state is None:
            state = KillSwitchRecord(engaged=engaged, reason=reason, updated_at=updated_at)
        else:
            state.engaged = engaged
            state.reason = reason
            state.updated_at = _safe_timestamp(updated_at)
        self.session.add(state)
        self.session.flush()
        return state

    def save_risk_event(
        self,
        event: dict[str, object],
        *,
        source: str = "unknown",
        runtime_mode: str = "unknown",
    ) -> RiskEventRecord:
        record, _ = self.save_risk_event_with_created(
            event, source=source, runtime_mode=runtime_mode
        )
        return record

    def save_risk_event_with_created(
        self,
        event: dict[str, object],
        *,
        source: str = "unknown",
        runtime_mode: str = "unknown",
    ) -> tuple[RiskEventRecord, bool]:
        event_source = str(event.get("source", source) or "unknown")
        event_runtime_mode = str(event.get("runtime_mode", runtime_mode) or "unknown")
        domain_scoped = bool(event.pop("_event_key_domain_scoped", False))
        event_key = str(event["event_key"]) if event.get("event_key") else None
        if event_key and not domain_scoped:
            event_key = _domain_event_key(event_key, event_source, event_runtime_mode)
        if event_key:
            existing = self.session.exec(
                select(RiskEventRecord).where(RiskEventRecord.event_key == event_key)
            ).first()
            if existing is not None:
                return existing, False
        record = RiskEventRecord(
            source=event_source,
            runtime_mode=event_runtime_mode,
            event_type=str(event.get("type", "risk_event")),
            strategy=str(event.get("strategy", "")),
            reason_code=str(event.get("reason_code", "")),
            reason=str(event.get("reason", "")),
            payload=dict(event),
            timestamp=_safe_timestamp(event.get("timestamp", 0)),
            event_key=event_key,
        )
        self.session.add(record)
        self.session.flush()
        return record, True

    def save_cash_ledger(self, entry: CashLedgerRecord) -> CashLedgerRecord:
        self.session.add(entry)
        self.session.flush()
        return entry

    def upsert_order(self, order: OrderRecord) -> OrderRecord:
        return _upsert_order_in_session(self.session, order)


def _same_provenance(left: object, right: object) -> bool:
    def key(record: object) -> tuple[str, str]:
        source = str(getattr(record, "source", "unknown") or "unknown").strip().lower()
        runtime = str(
            getattr(record, "runtime_mode", "unknown") or "unknown"
        ).strip().lower()
        return source, runtime

    left_key = key(left)
    right_key = key(right)
    if left_key == right_key:
        return True
    # Missing provenance remains a compatible legacy domain only with other
    # missing provenance; it cannot absorb a row from an explicit runtime.
    return left_key == ("unknown", "unknown") and right_key == ("unknown", "unknown")


def _order_identity_candidates(session: Session, order: OrderRecord) -> list[OrderRecord]:
    candidates: list[OrderRecord] = []
    identity_filters = [OrderRecord.order_id == order.order_id]
    for field in ("exchange_order_id", "client_order_id"):
        external_id = getattr(order, field) or ""
        if external_id:
            identity_filters.append(getattr(OrderRecord, field) == external_id)
    for identity_filter in identity_filters:
        candidate = session.exec(select(OrderRecord).where(identity_filter)).first()
        if candidate is not None and candidate not in candidates:
            candidates.append(candidate)

    incoming_domain = _record_provenance(order)
    foreign = [
        candidate
        for candidate in candidates
        if _record_provenance(candidate) != incoming_domain
    ]
    if foreign:
        logger.warning(
            "Order upsert provenance conflict; preserving source=%s runtime_mode=%s "
            "order_id=%s",
            foreign[0].source,
            foreign[0].runtime_mode,
            foreign[0].order_id,
        )
        raise ValueError("order provenance conflict")
    if len(candidates) > 1:
        logger.warning(
            "Rejecting order upsert with conflicting order identity candidates; "
            "external IDs resolve to different existing orders"
        )
        raise ValueError("conflicting order identity candidates")
    return candidates


def _upsert_order_in_session(session: Session, order: OrderRecord) -> OrderRecord:
    order.normalize_fill_amounts()
    candidates = _order_identity_candidates(session, order)
    existing = candidates[0] if candidates else None
    if existing is None:
        session.add(order)
        session.flush()
        return order

    immutable_fields = ("order_id", "strategy", "symbol", "side", "type")
    conflicts = [
        field
        for field in immutable_fields
        if getattr(existing, field) != getattr(order, field)
    ]
    conflicts.extend(
        field
        for field in ("exchange_order_id", "client_order_id")
        if getattr(order, field)
        and getattr(existing, field)
        and getattr(order, field) != getattr(existing, field)
    )
    if conflicts:
        logger.warning(
            "Rejecting order upsert with conflicting order identity candidates; "
            "order identity immutable conflict for %s; preserving existing order: %s",
            existing.order_id,
            ", ".join(conflicts),
        )
        return existing

    merged = _merge_order_record(existing, order)
    session.add(merged)
    session.flush()
    return existing


def _external_trade_ids_are_compatible(left: TradeRecord, right: TradeRecord) -> bool:
    return all(
        not getattr(left, field) or not getattr(right, field)
        or getattr(left, field) == getattr(right, field)
        for field in ("exchange_order_id", "client_order_id")
    )


def _trades_share_external_order_alias(left: TradeRecord, right: TradeRecord) -> bool:
    return any(
        getattr(left, field) and getattr(left, field) == getattr(right, field)
        for field in ("exchange_order_id", "client_order_id")
    )


def _merge_trade_identity(existing: TradeRecord, incoming: TradeRecord) -> None:
    immutable_fields = ("symbol", "side", "amount", "price", "fee", "timestamp")
    conflicts = [
        field
        for field in immutable_fields
        if getattr(existing, field) != getattr(incoming, field)
    ]
    can_upgrade_placeholder = (
        has_placeholder_trade_identity(existing)
        and has_confirmed_local_trade_identity(incoming)
        and _external_trade_ids_are_compatible(existing, incoming)
    )
    if can_upgrade_placeholder:
        existing.order_id = incoming.order_id
        existing.strategy = incoming.strategy
    else:
        conflicts.extend(
            field
            for field in ("order_id", "strategy")
            if getattr(existing, field) != getattr(incoming, field)
        )
    for field in ("exchange_order_id", "client_order_id"):
        incoming_id = getattr(incoming, field) or ""
        existing_id = getattr(existing, field) or ""
        if incoming_id and existing_id and incoming_id != existing_id:
            conflicts.append(field)
        elif incoming_id and not existing_id:
            setattr(existing, field, incoming_id)
    if conflicts:
        logger.warning(
            "Trade identity immutable conflict for exchange_trade_id=%s; "
            "preserving existing fields: %s",
            existing.exchange_trade_id,
            ", ".join(conflicts),
        )
    if incoming.exchange_trade_id and not existing.exchange_trade_id:
        existing.exchange_trade_id = incoming.exchange_trade_id
    existing.active = incoming.active
    existing.stale_at = incoming.stale_at
    if not existing.blank_trade_fingerprint and incoming.blank_trade_fingerprint:
        existing.blank_trade_fingerprint = incoming.blank_trade_fingerprint
    if incoming.source != "unknown":
        existing.source = incoming.source
    if incoming.runtime_mode != "unknown":
        existing.runtime_mode = incoming.runtime_mode


def _upsert_trade_in_session(session: Session, trade: TradeRecord) -> TradeRecord:
    trade.normalize_values()
    for field in ("exchange_trade_id", "exchange_order_id", "client_order_id", "order_id"):
        setattr(trade, field, str(getattr(trade, field) or "").strip())

    trade_order = Repository._enrich_placeholder_trade_from_orders(session, trade)
    trade.blank_trade_fingerprint = (
        stable_blank_trade_fingerprint(
            exchange_order_id=trade.exchange_order_id,
            client_order_id=trade.client_order_id,
            symbol=canonical_symbol(trade.symbol, market_type="auto"),
            side=trade.side,
            amount=trade.amount,
            price=trade.price,
            fee=trade.fee,
            timestamp=trade.timestamp,
        )
        if trade.exchange_order_id or trade.client_order_id
        else ""
    ) or ""

    existing = None
    if trade.exchange_trade_id:
        identity_match = session.exec(
            select(TradeRecord).where(
                TradeRecord.exchange_trade_id == trade.exchange_trade_id,
                or_(TradeRecord.active.is_(True), TradeRecord.active.is_(None)),
            )
        ).first()
        if identity_match is not None and _same_provenance(identity_match, trade):
            existing = identity_match
        elif identity_match is not None:
            logger.warning(
                "Trade external identity belongs to a different provenance pair; "
                "retaining old row and inserting an isolated row"
            )
    if existing is None:
        alias_candidates = Repository._trade_alias_candidates(session, trade, trade_order)
        if len(alias_candidates) == 1:
            existing = alias_candidates[0]
    if existing is None:
        fingerprint_values = Repository._trade_identity_fingerprints(trade)
        if trade.blank_trade_fingerprint:
            fingerprint_values.add(trade.blank_trade_fingerprint)
        candidates = (
            session.exec(
                select(TradeRecord).where(
                    TradeRecord.blank_trade_fingerprint.in_(fingerprint_values)
                )
            ).all()
            if fingerprint_values
            else []
        )
        compatible = [
            candidate
            for candidate in candidates
            if _same_provenance(candidate, trade)
            and Repository._trade_facts_match(candidate, trade)
            and _external_trade_ids_are_compatible(candidate, trade)
            and (
                not trade.exchange_trade_id
                or not candidate.exchange_trade_id
                or _trades_share_external_order_alias(candidate, trade)
            )
        ]
        existing = next(
            (
                candidate for candidate in compatible
                if not trade.exchange_trade_id or not candidate.exchange_trade_id
            ),
            None,
        )
    if existing is None:
        session.add(trade)
        session.flush()
        return trade

    _merge_trade_identity(existing, trade)
    session.add(existing)
    session.flush()
    return existing


class Repository:
    _MIGRATED_SQLITE_DATABASES: set[str] = set()

    def __init__(self, engine: Engine | None = None, db_path: str | None = None):
        if engine is None:
            db_path = db_path or os.environ.get("OKX_BOT_DB_PATH", "data/bot.db")
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            engine = create_engine(f"sqlite:///{db_path}", echo=False)
            self._enable_wal(engine)
        self._enable_sqlite_write_lock(engine)
        SQLModel.metadata.create_all(engine)
        migration_key = None
        if engine.dialect.name == "sqlite" and engine.url.database != ":memory:":
            migration_key = str(engine.url)
        if (
            migration_key is None
            or migration_key not in Repository._MIGRATED_SQLITE_DATABASES
        ):
            self._migrate_sqlite_schema(engine)
            self._ensure_sqlite_order_external_id_indexes(engine)
            self._ensure_sqlite_current_state_indexes(engine)
            self._ensure_live_submission_lease_table(engine)
            if migration_key is not None:
                Repository._MIGRATED_SQLITE_DATABASES.add(migration_key)
        self.engine = engine

    def atomic_accounting(self, callback: Callable[[_AccountingTransaction], object]) -> object:
        is_sqlite = self.engine.dialect.name == "sqlite"
        if is_sqlite:
            with self.engine.connect() as connection:
                if self.engine.url.database == ":memory:":
                    connection.exec_driver_sql("BEGIN IMMEDIATE")
                with Session(bind=connection) as session:
                    try:
                        result = callback(_AccountingTransaction(session))
                        session.flush()
                        connection.commit()
                        return result
                    except BaseException:
                        session.rollback()
                        connection.rollback()
                        raise
        with Session(self.engine) as session:
            with session.begin():
                return callback(_AccountingTransaction(session))

    def atomic_private_sync(self, callback: Callable[[_AccountingTransaction], object]) -> object:
        return self.atomic_accounting(callback)

    def persist_order_and_accounting(
        self,
        order: OrderRecord,
        accounting: Callable[[_AccountingTransaction], object],
    ) -> object:
        return self.atomic_accounting(
            lambda transaction: (
                transaction.upsert_order(order),
                accounting(transaction),
            )[1]
        )

    def acquire_live_submission_lease(
        self,
        owner: str,
        *,
        timeout: float = 1.0,
        lease_seconds: float = 30.0,
    ) -> bool:
        now = time.time()
        expires_at = now + max(1.0, lease_seconds)
        with self.engine.connect() as connection:
            if self.engine.dialect.name == "sqlite":
                # Set the busy timeout on the raw DBAPI connection: executing a
                # PRAGMA through SQLAlchemy would autobegin the transaction and
                # the engine-level "begin" listener would then issue its own
                # BEGIN IMMEDIATE, colliding with the explicit one below.
                cursor = connection.connection.cursor()
                try:
                    cursor.execute(f"PRAGMA busy_timeout={max(1, int(timeout * 1000))}")
                finally:
                    cursor.close()
            with connection.begin():
                row = connection.execute(text(
                    "SELECT owner, expires_at FROM live_submission_lease WHERE name = 'live'"
                )).first()
                if row is not None and row[1] > now and row[0] != owner:
                    return False
                statement = text(
                    "INSERT INTO live_submission_lease(name, owner, expires_at) "
                    "VALUES ('live', :owner, :expires_at) "
                    "ON CONFLICT(name) DO UPDATE SET owner = excluded.owner, "
                    "expires_at = excluded.expires_at"
                )
                connection.execute(statement, {"owner": owner, "expires_at": expires_at})
            return True

    def release_live_submission_lease(self, owner: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(text(
                "DELETE FROM live_submission_lease WHERE name = 'live' AND owner = :owner"
            ), {"owner": owner})

    def get_private_uid(self) -> str | None:
        with Session(self.engine) as session:
            return _AccountingTransaction(session).get_private_uid()

    def commit_private_spot_instruments(
        self, facts: tuple[SpotInstrumentFact, ...], *, raw_payload: object,
        received_at: int, requested_inst_id: str | None = None,
    ) -> list[PrivateSpotInstrumentRecord]:
        return self.atomic_accounting(
            lambda transaction: transaction.commit_private_spot_instruments(
                facts, raw_payload=raw_payload, received_at=received_at,
                requested_inst_id=requested_inst_id,
            ),
        )

    def get_private_spot_instruments(
        self, inst_id: str | None = None,
    ) -> list[PrivateSpotInstrumentRecord]:
        with Session(self.engine) as session:
            return _AccountingTransaction(session).get_private_spot_instruments(inst_id)

    def get_spot_preflight_evidence(
        self, symbol: str, *, expected_account_scope_id: str,
    ) -> SpotPreflightEvidence:
        """Select and validate diagnostics in one transaction, with no row writes."""
        with self.engine.connect() as connection:
            if self.engine.dialect.name == "sqlite" and self.engine.url.database == ":memory:":
                connection.exec_driver_sql("BEGIN")
            # File-backed SQLite uses the engine's existing BEGIN listener.
            with Session(bind=connection) as session:
                return _AccountingTransaction(session).get_spot_preflight_evidence(
                    symbol, expected_account_scope_id=expected_account_scope_id,
                )

    def bind_private_uid(self, uid: str, first_seen_at: int) -> PrivateUIDBindingRecord:
        return self.atomic_accounting(lambda transaction: transaction.bind_private_uid(
            uid, first_seen_at,
        ))

    def commit_exact_spot_fill_page(
        self, page: SpotFillPage, *, uid: str, raw_payload: object, received_at: int,
        expected_cursor_version: int = 0, endpoint: str = _EXACT_ENDPOINT,
    ) -> ExactSpotFillCursorRecord:
        return self.atomic_accounting(lambda transaction: transaction.commit_exact_spot_fill_page(
            page, uid=uid, raw_payload=raw_payload, received_at=received_at,
            expected_cursor_version=expected_cursor_version, endpoint=endpoint,
        ))

    def commit_private_account_config(
        self, fact: AccountConfigFact, *, raw_payload: object, received_at: int,
    ) -> PrivateAccountConfigRecord:
        return self.atomic_accounting(lambda transaction: transaction.commit_private_account_config(
            fact, raw_payload=raw_payload, received_at=received_at,
        ))

    def get_private_account_config(
        self, uid: str | None = None,
    ) -> list[PrivateAccountConfigRecord]:
        with Session(self.engine) as session:
            return _AccountingTransaction(session).get_private_account_config(uid=uid)

    def commit_private_account_balance(
        self, fact: AccountBalanceFact, *, raw_payload: object, received_at: int,
        uid: str | None = None,
    ) -> PrivateAccountBalanceRecord:
        return self.atomic_accounting(
            lambda transaction: transaction.commit_private_account_balance(
                fact, uid=uid, raw_payload=raw_payload, received_at=received_at,
            ),
        )

    def get_private_account_balances(
        self, uid: str | None = None,
    ) -> list[PrivateAccountBalanceRecord]:
        with Session(self.engine) as session:
            return _AccountingTransaction(session).get_private_account_balances(uid=uid)

    def get_exact_spot_fills(self, *, uid: str) -> list[SpotFillFact]:
        with Session(self.engine) as session:
            return _AccountingTransaction(session).get_exact_spot_fills(uid=uid)

    def get_exact_spot_fill_pages(self, *, uid: str) -> list[ExactSpotFillPageRecord]:
        with Session(self.engine) as session:
            return _AccountingTransaction(session).get_exact_spot_fill_pages(uid=uid)

    def get_exact_spot_fill_cursor(
        self, *, uid: str, endpoint: str = _EXACT_ENDPOINT,
        inst_id: str | None = None, before: str | None = None,
    ) -> ExactSpotFillCursorRecord | None:
        with Session(self.engine) as session:
            return _AccountingTransaction(session).get_exact_spot_fill_cursor(
                uid=uid, endpoint=endpoint, inst_id=inst_id, before=before,
            )

    def start_private_sync_receipt(self, **values) -> PrivateSyncReceiptRecord:
        return self.atomic_private_sync(lambda transaction: transaction.start_private_sync_receipt(
            **values,
        ))

    def finalize_private_sync_receipt(self, attempt_id: str, **values) -> PrivateSyncReceiptRecord:
        return self.atomic_private_sync(
            lambda transaction: transaction.finalize_private_sync_receipt(attempt_id, **values),
        )

    def fail_private_sync_receipt(self, attempt_id: str, **values) -> PrivateSyncReceiptRecord:
        return self.atomic_private_sync(lambda transaction: transaction.fail_private_sync_receipt(
            attempt_id, **values,
        ))

    def get_private_sync_receipt(self, attempt_id: str) -> PrivateSyncReceiptRecord | None:
        with Session(self.engine) as session:
            return _AccountingTransaction(session).get_private_sync_receipt(attempt_id)

    def get_private_sync_status(self) -> dict[str, PrivateSyncReceiptRecord | None]:
        with Session(self.engine) as session:
            return _AccountingTransaction(session).get_private_sync_status()

    def get_kill_switch(self) -> KillSwitchRecord:
        with Session(self.engine) as session:
            state = session.exec(select(KillSwitchRecord)).first()
            if state is None:
                return KillSwitchRecord(engaged=False, reason="", updated_at=0)
            return state

    def set_kill_switch(self, engaged: bool, reason: str, updated_at: int) -> KillSwitchRecord:
        with Session(self.engine) as session:
            state = session.exec(select(KillSwitchRecord)).first()
            if state is None:
                state = KillSwitchRecord(engaged=engaged, reason=reason, updated_at=updated_at)
            else:
                state.engaged = engaged
                state.reason = reason
                state.updated_at = _safe_timestamp(updated_at)
            session.add(state)
            session.commit()
            session.refresh(state)
            return state

    def save_risk_event(
        self,
        event: dict[str, object],
        *,
        source: str = "unknown",
        runtime_mode: str = "unknown",
    ) -> RiskEventRecord:
        record, _ = self.save_risk_event_with_created(
            event, source=source, runtime_mode=runtime_mode
        )
        return record

    def save_risk_event_with_created(
        self,
        event: dict[str, object],
        *,
        source: str = "unknown",
        runtime_mode: str = "unknown",
    ) -> tuple[RiskEventRecord, bool]:
        event_source = str(event.get("source", source) or "unknown")
        event_runtime_mode = str(event.get("runtime_mode", runtime_mode) or "unknown")
        domain_scoped = bool(event.pop("_event_key_domain_scoped", False))
        event_key = str(event["event_key"]) if event.get("event_key") else None
        if event_key and not domain_scoped:
            event_key = _domain_event_key(event_key, event_source, event_runtime_mode)
        record = RiskEventRecord(
            source=event_source,
            runtime_mode=event_runtime_mode,
            event_type=str(event.get("type", "risk_event")),
            strategy=str(event.get("strategy", "")),
            reason_code=str(event.get("reason_code", "")),
            reason=str(event.get("reason", "")),
            payload=dict(event),
            timestamp=_safe_timestamp(event.get("timestamp", 0)),
            event_key=event_key,
        )
        with Session(self.engine) as session:
            if record.event_key is not None:
                existing = session.exec(
                    select(RiskEventRecord).where(
                        RiskEventRecord.event_key == record.event_key
                    )
                ).first()
                if existing is not None:
                    return existing, False
            session.add(record)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                if record.event_key is None:
                    raise
                existing = session.exec(
                    select(RiskEventRecord).where(
                        RiskEventRecord.event_key == record.event_key
                    )
                ).first()
                if existing is None:
                    raise
                return existing, False
            session.refresh(record)
            return record, True

    def get_risk_events(self) -> list[RiskEventRecord]:
        statement = select(RiskEventRecord).order_by(RiskEventRecord.timestamp)
        with Session(self.engine) as session:
            return list(session.exec(statement).all())

    def insert_account(self, account: AccountRecord) -> AccountRecord:
        account.normalize_values()
        with Session(self.engine) as session:
            session.add(account)
            session.commit()
            session.refresh(account)
            return account

    @_merge_retry
    def upsert_account(self, account: AccountRecord) -> AccountRecord:
        account.normalize_values()
        with Session(self.engine) as session:
            existing = session.exec(
                select(AccountRecord)
                .where(
                    AccountRecord.strategy == account.strategy,
                    AccountRecord.source == account.source,
                    AccountRecord.runtime_mode == account.runtime_mode,
                    or_(AccountRecord.active.is_(True), AccountRecord.active.is_(None)),
                )
                .order_by(AccountRecord.updated_at.desc(), AccountRecord.id.desc())
            ).first()
            if existing is None:
                session.add(account)
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    existing = session.exec(
                        select(AccountRecord)
                        .where(
                            AccountRecord.strategy == account.strategy,
                            AccountRecord.source == account.source,
                            AccountRecord.runtime_mode == account.runtime_mode,
                            or_(AccountRecord.active.is_(True), AccountRecord.active.is_(None)),
                        )
                        .order_by(AccountRecord.updated_at.desc(), AccountRecord.id.desc())
                    ).first()
                    if existing is None:
                        raise
                else:
                    session.refresh(account)
                    return account

            merged = _merge_account_record(existing, account)
            session.add(merged)
            session.commit()
            session.refresh(merged)
            return merged

    save_account = upsert_account

    def mark_account_stale(
        self,
        strategy: str,
        stale_at: int,
        *,
        source: str | None = None,
        runtime_mode: str | None = None,
    ) -> None:
        # Unscoped legacy calls are intentionally read-only: they cannot safely
        # choose between live and paper rows for the same strategy.
        if source is None or runtime_mode is None:
            return
        with Session(self.engine) as session:
            account = session.exec(
                select(AccountRecord).where(
                    AccountRecord.strategy == strategy,
                    AccountRecord.source == source,
                    AccountRecord.runtime_mode == runtime_mode,
                    or_(AccountRecord.active.is_(True), AccountRecord.active.is_(None)),
                ).order_by(AccountRecord.updated_at.desc(), AccountRecord.id.desc())
            ).first()
            if account is None:
                return
            account.active = False
            account.stale_at = _safe_timestamp(stale_at)
            session.add(account)
            session.commit()

    def get_account(
        self,
        strategy: str | None = None,
        *,
        source: str | None = None,
        runtime_mode: str | None = None,
        include_inactive: bool = False,
    ) -> AccountRecord | None:
        with Session(self.engine) as session:
            if strategy is not None:
                statement = select(AccountRecord).where(AccountRecord.strategy == strategy)
                if not include_inactive:
                    statement = statement.where(
                        or_(AccountRecord.active.is_(True), AccountRecord.active.is_(None))
                    )
                if source is not None:
                    statement = statement.where(AccountRecord.source == source)
                if runtime_mode is not None:
                    statement = statement.where(AccountRecord.runtime_mode == runtime_mode)
                account = session.exec(
                    statement.order_by(AccountRecord.updated_at.desc(), AccountRecord.id.desc())
                ).first()
                if account is not None:
                    account.normalize_values()
                return account

            statement = select(AccountRecord)
            if not include_inactive:
                statement = statement.where(
                    or_(AccountRecord.active.is_(True), AccountRecord.active.is_(None))
                )
            if source is not None:
                statement = statement.where(AccountRecord.source == source)
            if runtime_mode is not None:
                statement = statement.where(AccountRecord.runtime_mode == runtime_mode)
            accounts = list(session.exec(statement).all())
            for account in accounts:
                account.normalize_values()
            if not accounts:
                return None
            if len(accounts) == 1:
                return accounts[0]
            exchange_account = next(
                (account for account in accounts if account.strategy == "__exchange__"),
                None,
            )
            if exchange_account is not None:
                return exchange_account
            return AccountRecord(
                strategy="",
                initial_equity=sum(account.initial_equity for account in accounts),
                cash_balance=sum(account.cash_balance for account in accounts),
                available_balance=sum(account.available_balance for account in accounts),
                equity=sum(account.equity for account in accounts),
                realized_pnl=sum(account.realized_pnl for account in accounts),
                unrealized_pnl=sum(account.unrealized_pnl for account in accounts),
                daily_pnl=sum(account.daily_pnl for account in accounts),
                fees_paid=sum(account.fees_paid for account in accounts),
                updated_at=max(account.updated_at for account in accounts),
            )

    def get_accounts(
        self,
        *,
        source: str | None = None,
        runtime_mode: str | None = None,
        include_inactive: bool = False,
    ) -> list[AccountRecord]:
        with Session(self.engine) as session:
            statement = select(AccountRecord)
            if not include_inactive:
                statement = statement.where(
                    or_(AccountRecord.active.is_(True), AccountRecord.active.is_(None))
                )
            if source is not None:
                statement = statement.where(AccountRecord.source == source)
            if runtime_mode is not None:
                statement = statement.where(AccountRecord.runtime_mode == runtime_mode)
            accounts = list(session.exec(statement).all())
            for account in accounts:
                account.normalize_values()
            return accounts

    def audit_legacy_live_state_ownership(self) -> dict[str, int]:
        """Report legacy rows only when their live provenance is explicit.

        Strategy-scoped rows without a persisted mode/source marker remain unknown;
        the audit is intentionally read-only and never upgrades them to live state.
        """
        with Session(self.engine) as session:
            accounts = list(session.exec(select(AccountRecord)).all())
            positions = list(session.exec(select(PositionRecord)).all())
            orders = list(session.exec(select(OrderRecord)).all())
            trades = list(session.exec(select(TradeRecord)).all())

        records = {
            "accounts": accounts,
            "positions": positions,
            "orders": orders,
            "trades": trades,
        }
        audit: dict[str, int] = {}
        for kind, rows in records.items():
            legacy_rows = [row for row in rows if getattr(row, "strategy", "") != EXCHANGE_STRATEGY]
            audit[f"active_{kind}"] = sum(
                getattr(row, "active", True) is not False for row in rows
            )
            audit[f"inactive_{kind}"] = sum(
                getattr(row, "active", True) is False for row in rows
            )
            audit[f"live_{kind}"] = sum(
                _record_provenance(row) == "live" for row in legacy_rows
            )
            audit[f"paper_{kind}"] = sum(
                _record_provenance(row) == "paper" for row in legacy_rows
            )
            audit[f"backtest_{kind}"] = sum(
                _record_provenance(row) == "backtest" for row in legacy_rows
            )
            audit[f"unknown_{kind}"] = sum(
                _record_provenance(row) == "unknown" for row in legacy_rows
            )
            audit[f"legacy_strategy_{kind}"] = audit[f"live_{kind}"]
            audit[f"paper_backtest_{kind}"] = (
                audit[f"paper_{kind}"] + audit[f"backtest_{kind}"]
            )
            audit[f"unknown_strategy_{kind}"] = audit[f"unknown_{kind}"]

        audit["exchange_accounts"] = sum(
            account.strategy == EXCHANGE_STRATEGY for account in accounts
        )
        audit["exchange_positions"] = sum(
            position.strategy == EXCHANGE_STRATEGY for position in positions
        )
        audit["exchange_orders"] = sum(
            order.strategy == EXCHANGE_STRATEGY for order in orders
        )
        audit["exchange_trades"] = sum(
            trade.strategy == EXCHANGE_STRATEGY for trade in trades
        )
        return audit

    def save_cash_ledger(self, entry: CashLedgerRecord) -> CashLedgerRecord:
        with Session(self.engine) as session:
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry

    save_cash_ledger_entry = save_cash_ledger

    def get_cash_ledger(self, strategy: str | None = None) -> list[CashLedgerRecord]:
        statement = select(CashLedgerRecord)
        if strategy is not None:
            statement = statement.where(CashLedgerRecord.strategy == strategy)
        statement = statement.order_by(CashLedgerRecord.timestamp)
        with Session(self.engine) as session:
            return list(session.exec(statement).all())

    @staticmethod
    def _trade_matches_order(trade: TradeRecord, order: OrderRecord) -> bool:
        if (
            trade.symbol
            and order.symbol
            and canonical_symbol(trade.symbol, market_type="auto")
            != canonical_symbol(order.symbol, market_type="auto")
        ):
            return False
        trade_side = str(trade.side or "").strip().lower()
        order_side = str(order.side or "").strip().lower()
        if not trade_side or not order_side or trade_side != order_side:
            if trade_side and order_side and trade_side != order_side:
                logger.warning(
                    "Trade/order side conflict prevents rebinding: "
                    "trade_side=%s order_side=%s exchange_order_id=%s client_order_id=%s",
                    trade.side,
                    order.side,
                    trade.exchange_order_id,
                    trade.client_order_id,
                )
            return False
        matches = False
        for field in ("exchange_order_id", "client_order_id"):
            trade_id = getattr(trade, field) or ""
            order_id = getattr(order, field) or ""
            if trade_id and order_id:
                if trade_id != order_id:
                    return False
                matches = True
        return matches

    @classmethod
    def _enrich_placeholder_trade_from_orders(
        cls,
        session: Session,
        trade: TradeRecord,
    ) -> OrderRecord | None:
        external_ids = [
            getattr(trade, field)
            for field in ("exchange_order_id", "client_order_id")
            if getattr(trade, field)
        ]
        if not external_ids:
            return None
        candidates = session.exec(
            select(OrderRecord).where(
                or_(
                    OrderRecord.exchange_order_id.in_(external_ids),
                    OrderRecord.client_order_id.in_(external_ids),
                )
            )
        ).all()
        matches = [
            order for order in candidates
            if _same_provenance(trade, order) and cls._trade_matches_order(trade, order)
        ]
        if len(matches) != 1:
            return None
        order = matches[0]
        if has_placeholder_trade_identity(trade):
            trade.order_id = order.order_id
            trade.strategy = order.strategy
        for field in ("exchange_order_id", "client_order_id"):
            if not getattr(trade, field) and getattr(order, field):
                setattr(trade, field, getattr(order, field))
        return order

    @staticmethod
    def _trade_facts_match(left: TradeRecord, right: TradeRecord) -> bool:
        return (
            canonical_symbol(left.symbol, market_type="auto")
            == canonical_symbol(right.symbol, market_type="auto")
            and all(
                getattr(left, field) == getattr(right, field)
                for field in ("side", "amount", "price", "fee", "timestamp")
            )
        )

    @classmethod
    def _matching_order_for_trade(
        cls,
        session: Session,
        trade: TradeRecord,
    ) -> OrderRecord | None:
        external_ids = [
            getattr(trade, field) or ""
            for field in ("exchange_order_id", "client_order_id")
            if getattr(trade, field)
        ]
        if not external_ids:
            return None
        candidates = session.exec(
            select(OrderRecord).where(
                or_(
                    OrderRecord.exchange_order_id.in_(external_ids),
                    OrderRecord.client_order_id.in_(external_ids),
                )
            )
        ).all()
        matches = [
            order for order in candidates
            if _same_provenance(trade, order) and cls._trade_matches_order(trade, order)
        ]
        return matches[0] if len(matches) == 1 else None

    @classmethod
    def _trade_alias_candidates(
        cls,
        session: Session,
        trade: TradeRecord,
        order: OrderRecord | None,
    ) -> list[TradeRecord]:
        if order is None:
            return []
        external_ids = [
            getattr(order, field) or ""
            for field in ("exchange_order_id", "client_order_id")
            if getattr(order, field)
        ]
        if not external_ids:
            return []
        candidates = session.exec(
            select(TradeRecord).where(
                or_(
                    TradeRecord.exchange_order_id.in_(external_ids),
                    TradeRecord.client_order_id.in_(external_ids),
                )
            )
        ).all()
        return [
            candidate
            for candidate in candidates
            if candidate.id != trade.id
            and _same_provenance(candidate, trade)
            and cls._trade_facts_match(candidate, trade)
            and cls._matching_order_for_trade(session, candidate) is order
            and _external_trade_ids_are_compatible(candidate, trade)
            and (not trade.exchange_trade_id or not candidate.exchange_trade_id)
        ]

    @staticmethod
    def _trade_identity_fingerprints(trade: TradeRecord) -> set[str]:
        fingerprints: set[str] = set()
        for field in ("exchange_order_id", "client_order_id"):
            value = getattr(trade, field) or ""
            if not value:
                continue
            fingerprints.add(
                stable_blank_trade_fingerprint(
                    exchange_order_id=value if field == "exchange_order_id" else "",
                    client_order_id=value if field == "client_order_id" else "",
                    symbol=canonical_symbol(trade.symbol, market_type="auto"),
                    side=trade.side,
                    amount=trade.amount,
                    price=trade.price,
                    fee=trade.fee,
                    timestamp=trade.timestamp,
                )
                or ""
            )
        return fingerprints - {""}

    def rebind_trades_to_orders(self, orders: list[OrderRecord] | None = None) -> None:
        with Session(self.engine) as session:
            target_orders = list(orders) if orders is not None else list(
                session.exec(select(OrderRecord)).all()
            )
            for trade in session.exec(select(TradeRecord)).all():
                if not has_placeholder_trade_identity(trade):
                    continue
                trade_provenance = _record_provenance(trade)
                matches = [
                    order for order in target_orders
                    if (
                        _record_provenance(order) == "live"
                        and trade_provenance == "live"
                        or _record_provenance(order) == trade_provenance
                        and trade_provenance != "live"
                    )
                    and self._trade_matches_order(trade, order)
                ]
                if len(matches) != 1:
                    if len(matches) > 1:
                        logger.warning(
                            "Rejecting ambiguous placeholder trade rebind; matched multiple "
                            "orders: exchange_order_id=%s client_order_id=%s",
                            trade.exchange_order_id,
                            trade.client_order_id,
                        )
                    continue
                order = matches[0]
                trade.order_id = order.order_id
                trade.strategy = order.strategy
                session.add(trade)
            session.commit()

    def save_trade(self, trade: TradeRecord) -> TradeRecord:
        return self.upsert_trade(trade)

    @contextmanager
    def _trade_session(self) -> Iterator[Session]:
        is_file_sqlite = (
            self.engine.dialect.name == "sqlite"
            and self.engine.url.database != ":memory:"
        )
        if not is_file_sqlite:
            with Session(self.engine) as session:
                yield session
            return

        with self.engine.connect() as connection:
            with Session(bind=connection) as session:
                try:
                    yield session
                except BaseException:
                    session.rollback()
                    connection.rollback()
                    raise
                else:
                    connection.commit()

    def upsert_trade(self, trade: TradeRecord) -> TradeRecord:
        trade.normalize_values()
        for field in ("exchange_trade_id", "exchange_order_id", "client_order_id", "order_id"):
            setattr(trade, field, str(getattr(trade, field) or "").strip())

        for attempt in range(3):
            try:
                return self._upsert_trade_once(trade)
            except IntegrityError:
                if attempt == 2:
                    raise
                logger.warning("Trade insert raced; retrying alias merge")
            except OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 2:
                    raise
                logger.warning("Trade transaction was locked; retrying alias merge")
        raise AssertionError("unreachable")

    def _upsert_trade_once(self, trade: TradeRecord) -> TradeRecord:
        with self._trade_session() as session:
            result = _upsert_trade_in_session(session, trade)
            session.commit()
            session.refresh(result)
            return result

    def get_trades(
        self,
        strategy: str | None = None,
        order_id: str | None = None,
        *,
        source: str | None = None,
        runtime_mode: str | None = None,
        include_inactive: bool = False,
    ) -> list[TradeRecord]:
        statement = select(TradeRecord)
        if not include_inactive:
            statement = statement.where(
                or_(TradeRecord.active.is_(True), TradeRecord.active.is_(None))
            )
        if strategy is not None:
            statement = statement.where(TradeRecord.strategy == strategy)
        if order_id is not None:
            statement = statement.where(TradeRecord.order_id == order_id)
        if source is not None:
            statement = statement.where(TradeRecord.source == source)
        if runtime_mode is not None:
            statement = statement.where(TradeRecord.runtime_mode == runtime_mode)
        statement = statement.order_by(TradeRecord.timestamp, TradeRecord.id)
        with Session(self.engine) as session:
            return list(session.exec(statement).all())

    def create_strategy_config(self, config: StrategyConfigRecord) -> StrategyConfigRecord:
        with Session(self.engine) as session:
            session.add(config)
            session.commit()
            session.refresh(config)
            return config

    def update_strategy_config(
        self,
        name: str,
        config: StrategyConfigRecord,
    ) -> StrategyConfigRecord | None:
        with Session(self.engine) as session:
            existing = session.exec(
                select(StrategyConfigRecord).where(StrategyConfigRecord.name == name)
            ).first()
            if existing is None:
                return None

            existing.strategy_type = config.strategy_type
            existing.symbol = config.symbol
            existing.timeframe = config.timeframe
            existing.params = config.params
            existing.enabled = config.enabled
            existing.updated_at = config.updated_at
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing

    def delete_strategy_config(self, name: str) -> bool:
        with Session(self.engine) as session:
            existing = session.exec(
                select(StrategyConfigRecord).where(StrategyConfigRecord.name == name)
            ).first()
            if existing is None:
                return False
            release_strategy_drafts(session, name, int(time.time() * 1000))
            session.delete(existing)
            session.commit()
            return True

    def upsert_strategy_config(self, config: StrategyConfigRecord) -> StrategyConfigRecord:
        with Session(self.engine) as session:
            existing = session.exec(
                select(StrategyConfigRecord).where(StrategyConfigRecord.name == config.name)
            ).first()
            if existing is None:
                session.add(config)
                session.commit()
                session.refresh(config)
                return config

            existing.strategy_type = config.strategy_type
            existing.symbol = config.symbol
            existing.timeframe = config.timeframe
            existing.params = config.params
            existing.enabled = config.enabled
            existing.updated_at = config.updated_at
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing

    def get_strategy_config(self, name: str) -> StrategyConfigRecord | None:
        with Session(self.engine) as session:
            return session.exec(
                select(StrategyConfigRecord).where(StrategyConfigRecord.name == name)
            ).first()

    def get_strategy_configs(self) -> list[StrategyConfigRecord]:
        statement = select(StrategyConfigRecord).order_by(StrategyConfigRecord.created_at.desc())
        with Session(self.engine) as session:
            return list(session.exec(statement).all())

    def save_order(self, order: OrderRecord) -> OrderRecord:
        with Session(self.engine) as session:
            session.add(order)
            session.commit()
            session.refresh(order)
            return order

    @_merge_retry
    def upsert_order(self, order: OrderRecord, _retry_after_conflict: bool = False) -> OrderRecord:
        with Session(self.engine) as session:
            try:
                result = _upsert_order_in_session(session, order)
                session.commit()
                session.refresh(result)
                return result
            except IntegrityError:
                session.rollback()
                if _retry_after_conflict:
                    raise
                return self.upsert_order(order, _retry_after_conflict=True)

    def _legacy_upsert_order_removed(self, order: OrderRecord, _retry_after_conflict: bool = False):
        raise AssertionError("unreachable legacy method")

    def _unused_order_upsert_placeholder(self):
        return None

    def get_orders(
        self,
        order_id: str | None = None,
        *,
        source: str | None = None,
        runtime_mode: str | None = None,
        include_inactive: bool = False,
    ) -> list[OrderRecord]:
        statement = select(OrderRecord)
        if not include_inactive:
            statement = statement.where(
                or_(OrderRecord.active.is_(True), OrderRecord.active.is_(None))
            )
        if order_id is not None:
            statement = statement.where(OrderRecord.order_id == order_id)
        if source is not None:
            statement = statement.where(OrderRecord.source == source)
        if runtime_mode is not None:
            statement = statement.where(OrderRecord.runtime_mode == runtime_mode)
        with Session(self.engine) as session:
            return list(session.exec(statement).all())

    def save_position(self, position: PositionRecord) -> PositionRecord:
        with Session(self.engine) as session:
            session.add(position)
            session.commit()
            session.refresh(position)
            return position

    def get_position(
        self,
        strategy: str,
        symbol: str,
        side: str | None = None,
        *,
        source: str | None = None,
        runtime_mode: str | None = None,
        include_inactive: bool = False,
    ) -> PositionRecord | None:
        statement = select(PositionRecord).where(
            PositionRecord.strategy == strategy,
            PositionRecord.symbol == symbol,
        )
        if not include_inactive:
            statement = statement.where(
                or_(PositionRecord.active.is_(True), PositionRecord.active.is_(None))
            )
        if source is not None:
            statement = statement.where(PositionRecord.source == source)
        if runtime_mode is not None:
            statement = statement.where(PositionRecord.runtime_mode == runtime_mode)
        if side is not None:
            statement = statement.where(PositionRecord.side == side)
        statement = statement.order_by(PositionRecord.timestamp.desc(), PositionRecord.id.desc())
        with Session(self.engine) as session:
            return session.exec(statement).first()

    @_merge_retry
    def insert_position(self, position: PositionRecord) -> PositionRecord:
        position.normalize_values()
        with Session(self.engine) as session:
            session.add(position)
            session.commit()
            session.refresh(position)
            return position

    @_merge_retry
    def upsert_position(self, position: PositionRecord) -> PositionRecord:
        position.normalize_values()
        for attempt in range(2):
            try:
                with Session(self.engine) as session:
                    existing = session.exec(
                        select(PositionRecord)
                        .where(
                            PositionRecord.strategy == position.strategy,
                            PositionRecord.symbol == position.symbol,
                            PositionRecord.side == position.side,
                            PositionRecord.source == position.source,
                            PositionRecord.runtime_mode == position.runtime_mode,
                            or_(PositionRecord.active.is_(True), PositionRecord.active.is_(None)),
                        )
                    ).first()
                    if existing is None and position.id is not None:
                        identity_candidate = session.exec(
                            select(PositionRecord).where(PositionRecord.id == position.id)
                        ).first()
                        if identity_candidate is not None:
                            if _same_provenance(identity_candidate, position):
                                existing = identity_candidate
                            else:
                                logger.warning(
                                    "Position upsert provenance conflict for id=%s; preserving "
                                    "source=%s runtime_mode=%s and inserting an isolated row",
                                    position.id,
                                    identity_candidate.source,
                                    identity_candidate.runtime_mode,
                                )
                                position.id = None
                    if existing is None:
                        session.add(position)
                        session.commit()
                        session.refresh(position)
                        return position

                    merged = _merge_position_record(existing, position)
                    session.add(merged)
                    session.commit()
                    session.refresh(merged)
                    return merged
            except IntegrityError:
                if attempt == 1:
                    raise
                logger.warning(
                    "Position insert raced for %s/%s/%s; retrying timestamp merge",
                    position.strategy,
                    position.symbol,
                    position.side,
                )
        raise AssertionError("unreachable")

    def delete_position(
        self,
        strategy: str,
        symbol: str,
        side: str | None = None,
        *,
        source: str | None = None,
        runtime_mode: str | None = None,
        stale_at: int | None = None,
    ) -> None:
        if source is None or runtime_mode is None:
            logger.warning(
                (
                    "Refusing unscoped position archive for %s/%s; "
                    "source and runtime_mode are required"
                ),
                strategy,
                symbol,
            )
            return
        with Session(self.engine) as session:
            statement = select(PositionRecord).where(
                PositionRecord.strategy == strategy,
                PositionRecord.symbol == symbol,
                or_(PositionRecord.active.is_(True), PositionRecord.active.is_(None)),
            )
            if side is not None:
                statement = statement.where(PositionRecord.side == side)
            if source is not None:
                statement = statement.where(PositionRecord.source == source)
            if runtime_mode is not None:
                statement = statement.where(PositionRecord.runtime_mode == runtime_mode)
            positions = list(session.exec(statement).all())
            for position in positions:
                position.active = False
                position.stale_at = _safe_timestamp(
                    stale_at if stale_at is not None else position.timestamp
                )
                session.add(position)
            if positions:
                session.commit()

    def get_positions(
        self,
        strategy: str | None = None,
        symbol: str | None = None,
        *,
        source: str | None = None,
        runtime_mode: str | None = None,
        include_inactive: bool = False,
    ) -> list[PositionRecord]:
        statement = select(PositionRecord)
        if not include_inactive:
            statement = statement.where(
                or_(PositionRecord.active.is_(True), PositionRecord.active.is_(None))
            )
        if strategy is not None:
            statement = statement.where(PositionRecord.strategy == strategy)
        if symbol is not None:
            statement = statement.where(PositionRecord.symbol == symbol)
        if source is not None:
            statement = statement.where(PositionRecord.source == source)
        if runtime_mode is not None:
            statement = statement.where(PositionRecord.runtime_mode == runtime_mode)
        with Session(self.engine) as session:
            return list(session.exec(statement).all())

    def get_open_positions(
        self,
        strategy: str | None = None,
        *,
        source: str | None = None,
        runtime_mode: str | None = None,
        include_inactive: bool = False,
    ) -> list[PositionRecord]:
        statement = select(PositionRecord).where(PositionRecord.amount != 0)
        if not include_inactive:
            statement = statement.where(
                or_(PositionRecord.active.is_(True), PositionRecord.active.is_(None))
            )
        if strategy is not None:
            statement = statement.where(PositionRecord.strategy == strategy)
        if source is not None:
            statement = statement.where(PositionRecord.source == source)
        if runtime_mode is not None:
            statement = statement.where(PositionRecord.runtime_mode == runtime_mode)
        with Session(self.engine) as session:
            return list(session.exec(statement).all())

    def mark_position_stale(
        self,
        strategy: str,
        symbol: str,
        side: str,
        stale_at: int,
        *,
        source: str | None = None,
        runtime_mode: str | None = None,
    ) -> None:
        with Session(self.engine) as session:
            statement = select(PositionRecord).where(
                PositionRecord.strategy == strategy,
                PositionRecord.symbol == symbol,
                PositionRecord.side == side,
                or_(PositionRecord.active.is_(True), PositionRecord.active.is_(None)),
            )
            if source is not None:
                statement = statement.where(PositionRecord.source == source)
            if runtime_mode is not None:
                statement = statement.where(PositionRecord.runtime_mode == runtime_mode)
            position = session.exec(statement).first()
            if position is None:
                return
            position.active = False
            position.stale_at = _safe_timestamp(stale_at)
            session.add(position)
            session.commit()

    def save_kline(self, kline: KlineCache) -> KlineCache | None:
        try:
            kline.normalize_values()
        except ValueError:
            logger.warning(
                "Skipping kline cache row with invalid OHLC candle bounds: %s/%s at %s",
                kline.symbol,
                kline.timeframe,
                kline.timestamp,
            )
            return None
        with Session(self.engine) as session:
            existing = self._find_active_kline(session, kline)
            if existing is None:
                session.add(kline)
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    existing = self._find_active_kline(session, kline)
                    if existing is None:
                        raise
                    self._apply_kline_values(existing, kline)
                    session.add(existing)
                    session.commit()
                else:
                    session.refresh(kline)
                    return kline
            self._apply_kline_values(existing, kline)
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing

    @staticmethod
    def _find_active_kline(session: Session, kline: KlineCache) -> KlineCache | None:
        return session.exec(
            select(KlineCache)
            .where(KlineCache.symbol == kline.symbol)
            .where(KlineCache.timeframe == kline.timeframe)
            .where(KlineCache.source == kline.source)
            .where(KlineCache.runtime_mode == kline.runtime_mode)
            .where(KlineCache.timestamp == kline.timestamp)
            .where(or_(KlineCache.active.is_(True), KlineCache.active.is_(None)))
            .order_by(KlineCache.id.desc())
        ).first()

    @staticmethod
    def _apply_kline_values(target: KlineCache, source: KlineCache) -> None:
        target.open = source.open
        target.high = source.high
        target.low = source.low
        target.close = source.close
        target.volume = source.volume
        target.active = True

    def get_klines(
        self,
        symbol: str,
        timeframe: str,
        start: int,
        end: int,
        *,
        source: str | None = None,
        runtime_mode: str | None = None,
        include_inactive: bool = False,
    ) -> list[KlineCache]:
        statement = (
            select(KlineCache)
            .where(KlineCache.symbol == symbol)
            .where(KlineCache.timeframe == timeframe)
            .where(KlineCache.timestamp >= start)
            .where(KlineCache.timestamp <= end)
        )
        if not include_inactive:
            statement = statement.where(
                or_(KlineCache.active.is_(True), KlineCache.active.is_(None))
            )
        if source is None and runtime_mode is None:
            statement = statement.where(
                KlineCache.source == "unknown", KlineCache.runtime_mode == "unknown"
            )
        else:
            if source is not None:
                statement = statement.where(KlineCache.source == source)
            if runtime_mode is not None:
                statement = statement.where(KlineCache.runtime_mode == runtime_mode)
        statement = statement.order_by(KlineCache.timestamp)
        with Session(self.engine) as session:
            return list(session.exec(statement).all())

    def save_backtest_result(self, result: BacktestResultRecord) -> BacktestResultRecord:
        with Session(self.engine) as session:
            session.add(result)
            session.commit()
            session.refresh(result)
            return result

    def save_backtest_result_with_trades(
        self,
        result: BacktestResultRecord,
        trades: list[BacktestTradeRecord],
    ) -> BacktestResultRecord:
        with Session(self.engine) as session:
            session.add(result)
            for trade in trades:
                session.add(trade)
            session.commit()
            session.refresh(result)
            return result

    def get_backtest_result(self, result_id: str) -> BacktestResultRecord | None:
        with Session(self.engine) as session:
            return session.exec(
                select(BacktestResultRecord).where(BacktestResultRecord.id == result_id)
            ).first()

    def get_backtest_results(self, limit: int = 50) -> list[BacktestResultRecord]:
        statement = (
            select(BacktestResultRecord)
            .order_by(BacktestResultRecord.created_at.desc())
            .limit(limit)
        )
        with Session(self.engine) as session:
            return list(session.exec(statement).all())

    def save_backtest_trades(self, trades: list[BacktestTradeRecord]) -> list[BacktestTradeRecord]:
        with Session(self.engine) as session:
            for trade in trades:
                session.add(trade)
            session.commit()
            for trade in trades:
                session.refresh(trade)
            return trades

    def get_backtest_trades(self, result_id: str) -> list[BacktestTradeRecord]:
        statement = (
            select(BacktestTradeRecord)
            .where(BacktestTradeRecord.result_id == result_id)
            .order_by(BacktestTradeRecord.timestamp, BacktestTradeRecord.id)
        )
        with Session(self.engine) as session:
            return list(session.exec(statement).all())

    @staticmethod
    def _ensure_live_submission_lease_table(engine: Engine) -> None:
        if engine.dialect.name != "sqlite":
            return
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE IF NOT EXISTS live_submission_lease ("
                "name VARCHAR PRIMARY KEY, owner VARCHAR NOT NULL, expires_at FLOAT NOT NULL)"
            ))

    @staticmethod
    def _enable_wal(engine: Engine) -> None:
        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    @staticmethod
    def _enable_sqlite_write_lock(engine: Engine) -> None:
        if (
            engine.dialect.name != "sqlite"
            or engine.url.database == ":memory:"
            or getattr(engine, "_sqlite_write_lock_enabled", False)
        ):
            return
        engine._sqlite_write_lock_enabled = True

        @event.listens_for(engine, "begin")
        def begin_immediate(connection):
            cursor = connection.connection.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
            finally:
                cursor.close()

    @staticmethod
    def _finite_nonnegative(value: object) -> float:
        try:
            normalized = float(value)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        return normalized if math.isfinite(normalized) and normalized >= 0.0 else 0.0

    @staticmethod
    def _finite_nonnegative_or_none(value: object) -> float | None:
        if value is None:
            return None
        try:
            normalized = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return normalized if math.isfinite(normalized) and normalized >= 0.0 else None

    @staticmethod
    def _safe_trade_timestamp(value: object) -> int:
        return _safe_timestamp(value)

    @staticmethod
    def _ensure_sqlite_order_external_id_indexes(engine: Engine) -> None:
        if engine.dialect.name != "sqlite":
            return
        with engine.begin() as connection:
            columns = {
                row[1] for row in connection.execute(text("PRAGMA table_info(orderrecord)"))
            }
            if {"exchange_order_id", "client_order_id"} <= columns:
                for field in ("exchange_order_id", "client_order_id"):
                    duplicate_count = connection.execute(text(
                        f"SELECT COUNT(*) FROM ("
                        f"SELECT {field} FROM orderrecord "
                        f"WHERE {field} IS NOT NULL AND {field} != '' "
                        f"GROUP BY {field} HAVING COUNT(*) > 1)"
                    )).scalar_one()
                    if duplicate_count:
                        logger.warning(
                            "Skipping unique %s index because legacy duplicate rows are retained",
                            field,
                        )
                        continue
                    connection.execute(text(
                        f"CREATE UNIQUE INDEX IF NOT EXISTS "
                        f"uq_orderrecord_{field} ON orderrecord ({field}) "
                        f"WHERE {field} IS NOT NULL AND {field} != ''"
                    ))

    @staticmethod
    def _ensure_sqlite_current_state_indexes(engine: Engine) -> None:
        if engine.dialect.name != "sqlite":
            return
        with engine.begin() as connection:
            # The original current-state indexes ignored provenance, so a live
            # exchange snapshot could overwrite a paper/legacy row with the same
            # strategy identity. Replace them before establishing provenance keys.
            for legacy_index in (
                "uq_accountrecord_active_strategy",
                "uq_positionrecord_active_identity",
            ):
                connection.execute(text(f'DROP INDEX IF EXISTS "{legacy_index}"'))
            connection.execute(
                text('DROP INDEX IF EXISTS "uq_traderecord_active_exchange_trade_id"')
            )
            indexes = (
                (
                    "uq_accountrecord_active_strategy",
                    "accountrecord",
                    "strategy, source, runtime_mode",
                    "active = 1",
                ),
                (
                    "uq_positionrecord_active_identity",
                    "positionrecord",
                    "strategy, symbol, side, source, runtime_mode",
                    "active = 1",
                ),
                (
                    "uq_traderecord_active_exchange_trade_identity",
                    "traderecord",
                    "exchange_trade_id, source, runtime_mode",
                    "active = 1 AND exchange_trade_id IS NOT NULL AND exchange_trade_id != '' "
                    "AND ((source = 'paper' AND runtime_mode = 'paper') "
                    "OR (source = 'live_exchange' AND runtime_mode = 'live') "
                    "OR (source = 'backtest' AND runtime_mode = 'backtest'))",
                ),
                (
                    "uq_traderecord_active_blank_trade_fingerprint",
                    "traderecord",
                    "blank_trade_fingerprint",
                    "active = 1 AND (exchange_trade_id IS NULL OR exchange_trade_id = '') "
                    "AND blank_trade_fingerprint IS NOT NULL AND blank_trade_fingerprint != '' "
                    "AND ((source = 'paper' AND runtime_mode = 'paper') "
                    "OR (source = 'live_exchange' AND runtime_mode = 'live') "
                    "OR (source = 'backtest' AND runtime_mode = 'backtest'))",
                ),
            )
            for name, table, columns, predicate in indexes:
                columns_present = {
                    row[1] for row in connection.execute(text(f"PRAGMA table_info({table})"))
                }
                required = {column.strip() for column in columns.split(",")}
                if not required <= columns_present or "active" not in columns_present:
                    continue
                try:
                    connection.execute(text(
                        f'CREATE UNIQUE INDEX IF NOT EXISTS "{name}" ON {table} ({columns}) '
                        f"WHERE {predicate}"
                    ))
                except SQLAlchemyError:
                    logger.warning(
                        "Could not create active-state unique index %s; preserving all rows",
                        name,
                        exc_info=True,
                    )

            lookup_indexes = (
                (
                    "ix_positionrecord_scope_lookup",
                    "positionrecord",
                    "strategy, symbol, side, active",
                ),
                (
                    "ix_traderecord_strategy_order",
                    "traderecord",
                    "strategy, order_id",
                ),
                (
                    "ix_klinecache_scope_timestamp",
                    "klinecache",
                    "symbol, timeframe, source, runtime_mode, timestamp",
                ),
            )
            for name, table, columns in lookup_indexes:
                columns_present = {
                    row[1] for row in connection.execute(text(f"PRAGMA table_info({table})"))
                }
                required = {column.strip() for column in columns.split(",")}
                if not required <= columns_present:
                    continue
                connection.execute(
                    text(f'CREATE INDEX IF NOT EXISTS "{name}" ON {table} ({columns})')
                )

            kline_columns = {
                row[1] for row in connection.execute(text("PRAGMA table_info(klinecache)"))
            }
            if {"symbol", "timeframe", "source", "runtime_mode", "timestamp", "active"} <= (
                kline_columns
            ):
                connection.execute(text(
                    "UPDATE klinecache SET active = 0 WHERE active = 1 AND id NOT IN ("
                    "SELECT MAX(id) FROM klinecache WHERE active = 1 "
                    "GROUP BY symbol, timeframe, COALESCE(source, 'unknown'), "
                    "COALESCE(runtime_mode, 'unknown'), timestamp)"
                ))
                connection.execute(text(
                    'CREATE UNIQUE INDEX IF NOT EXISTS "uq_klinecache_active_identity" '
                    "ON klinecache (symbol, timeframe, COALESCE(source, 'unknown'), "
                    "COALESCE(runtime_mode, 'unknown'), timestamp) WHERE active = 1"
                ))

    @staticmethod
    def _migrate_sqlite_schema(engine: Engine) -> None:
        if engine.dialect.name != "sqlite":
            return
        with engine.begin() as connection:
            table_migrations = {
                "accountrecord": {
                    "available_balance": (
                        "ALTER TABLE accountrecord ADD COLUMN available_balance FLOAT DEFAULT 0.0"
                    ),
                    "assets": "ALTER TABLE accountrecord ADD COLUMN assets JSON DEFAULT '[]'",
                    "source": (
                        "ALTER TABLE accountrecord ADD COLUMN source VARCHAR DEFAULT 'unknown'"
                    ),
                    "runtime_mode": (
                        "ALTER TABLE accountrecord ADD COLUMN runtime_mode VARCHAR "
                        "DEFAULT 'unknown'"
                    ),
                    "active": "ALTER TABLE accountrecord ADD COLUMN active BOOLEAN DEFAULT 1",
                    "stale_at": "ALTER TABLE accountrecord ADD COLUMN stale_at INTEGER",
                    "day_start_equity": (
                        "ALTER TABLE accountrecord ADD COLUMN day_start_equity FLOAT DEFAULT 0.0"
                    ),
                    "day_start_at": (
                        "ALTER TABLE accountrecord ADD COLUMN day_start_at INTEGER DEFAULT 0"
                    ),
                    "peak_equity": (
                        "ALTER TABLE accountrecord ADD COLUMN peak_equity FLOAT DEFAULT 0.0"
                    ),
                },
                "positionrecord": {
                    "mark_price": "ALTER TABLE positionrecord ADD COLUMN mark_price FLOAT",
                    "realized_pnl": (
                        "ALTER TABLE positionrecord ADD COLUMN realized_pnl FLOAT DEFAULT 0.0"
                    ),
                    "unrealized_pnl": (
                        "ALTER TABLE positionrecord ADD COLUMN unrealized_pnl FLOAT DEFAULT 0.0"
                    ),
                    "contracts": (
                        "ALTER TABLE positionrecord ADD COLUMN contracts FLOAT"
                    ),
                    "quantity": (
                        "ALTER TABLE positionrecord ADD COLUMN quantity FLOAT"
                    ),
                    "quantity_unit": (
                        "ALTER TABLE positionrecord ADD COLUMN quantity_unit VARCHAR DEFAULT 'base'"
                    ),
                    "notional_value": (
                        "ALTER TABLE positionrecord ADD COLUMN notional_value FLOAT"
                    ),
                    "is_inverse": (
                        "ALTER TABLE positionrecord ADD COLUMN is_inverse BOOLEAN DEFAULT 0"
                    ),
                    "contract_size": (
                        "ALTER TABLE positionrecord ADD COLUMN contract_size FLOAT"
                    ),
                    "source": (
                        "ALTER TABLE positionrecord ADD COLUMN source VARCHAR DEFAULT 'unknown'"
                    ),
                    "runtime_mode": (
                        "ALTER TABLE positionrecord ADD COLUMN runtime_mode VARCHAR "
                        "DEFAULT 'unknown'"
                    ),
                    "active": "ALTER TABLE positionrecord ADD COLUMN active BOOLEAN DEFAULT 1",
                    "stale_at": "ALTER TABLE positionrecord ADD COLUMN stale_at INTEGER",
                },
                "traderecord": {
                    "exchange_trade_id": (
                        "ALTER TABLE traderecord ADD COLUMN exchange_trade_id VARCHAR DEFAULT ''"
                    ),
                    "exchange_order_id": (
                        "ALTER TABLE traderecord ADD COLUMN exchange_order_id VARCHAR DEFAULT ''"
                    ),
                    "client_order_id": (
                        "ALTER TABLE traderecord ADD COLUMN client_order_id VARCHAR DEFAULT ''"
                    ),
                    "order_id": "ALTER TABLE traderecord ADD COLUMN order_id VARCHAR DEFAULT ''",
                    "contracts": "ALTER TABLE traderecord ADD COLUMN contracts FLOAT",
                    "quantity": "ALTER TABLE traderecord ADD COLUMN quantity FLOAT",
                    "quantity_unit": (
                        "ALTER TABLE traderecord ADD COLUMN quantity_unit VARCHAR DEFAULT 'base'"
                    ),
                    "is_inverse": "ALTER TABLE traderecord ADD COLUMN is_inverse BOOLEAN DEFAULT 0",
                    "contract_size": "ALTER TABLE traderecord ADD COLUMN contract_size FLOAT",
                    "blank_trade_fingerprint": (
                        "ALTER TABLE traderecord ADD COLUMN "
                        "blank_trade_fingerprint VARCHAR DEFAULT ''"
                    ),
                    "source": "ALTER TABLE traderecord ADD COLUMN source VARCHAR DEFAULT 'unknown'",
                    "runtime_mode": (
                        "ALTER TABLE traderecord ADD COLUMN runtime_mode VARCHAR DEFAULT 'unknown'"
                    ),
                    "active": "ALTER TABLE traderecord ADD COLUMN active BOOLEAN DEFAULT 1",
                    "stale_at": "ALTER TABLE traderecord ADD COLUMN stale_at INTEGER",
                },
                "orderrecord": {
                    "exchange_order_id": (
                        "ALTER TABLE orderrecord ADD COLUMN exchange_order_id VARCHAR DEFAULT ''"
                    ),
                    "client_order_id": (
                        "ALTER TABLE orderrecord ADD COLUMN client_order_id VARCHAR DEFAULT ''"
                    ),
                    "updated_at": "ALTER TABLE orderrecord ADD COLUMN updated_at INTEGER DEFAULT 0",
                    "filled_amount": (
                        "ALTER TABLE orderrecord ADD COLUMN filled_amount FLOAT DEFAULT 0.0"
                    ),
                    "remaining_amount": (
                        "ALTER TABLE orderrecord ADD COLUMN remaining_amount FLOAT DEFAULT 0.0"
                    ),
                    "snapshot_filled_amount": (
                        "ALTER TABLE orderrecord ADD COLUMN "
                        "snapshot_filled_amount FLOAT DEFAULT 0.0"
                    ),
                    "snapshot_updated_at": (
                        "ALTER TABLE orderrecord ADD COLUMN snapshot_updated_at INTEGER"
                    ),
                    "execution_type_family": (
                        "ALTER TABLE orderrecord ADD COLUMN "
                        "execution_type_family VARCHAR DEFAULT ''"
                    ),
                    "source": "ALTER TABLE orderrecord ADD COLUMN source VARCHAR DEFAULT 'unknown'",
                    "runtime_mode": (
                        "ALTER TABLE orderrecord ADD COLUMN runtime_mode VARCHAR DEFAULT 'unknown'"
                    ),
                    "active": "ALTER TABLE orderrecord ADD COLUMN active BOOLEAN DEFAULT 1",
                    "stale_at": "ALTER TABLE orderrecord ADD COLUMN stale_at INTEGER",
                },
                "cashledgerrecord": {
                    "source": (
                        "ALTER TABLE cashledgerrecord ADD COLUMN source VARCHAR DEFAULT 'unknown'"
                    ),
                    "runtime_mode": (
                        "ALTER TABLE cashledgerrecord ADD COLUMN runtime_mode VARCHAR "
                        "DEFAULT 'unknown'"
                    ),
                },
                "klinecache": {
                    "source": "ALTER TABLE klinecache ADD COLUMN source VARCHAR DEFAULT 'unknown'",
                    "runtime_mode": (
                        "ALTER TABLE klinecache ADD COLUMN runtime_mode VARCHAR DEFAULT 'unknown'"
                    ),
                    "active": "ALTER TABLE klinecache ADD COLUMN active BOOLEAN DEFAULT 1",
                },
                "riskeventrecord": {
                    "source": (
                        "ALTER TABLE riskeventrecord ADD COLUMN source VARCHAR DEFAULT 'unknown'"
                    ),
                    "runtime_mode": (
                        "ALTER TABLE riskeventrecord ADD COLUMN runtime_mode VARCHAR "
                        "DEFAULT 'unknown'"
                    ),
                    "event_key": "ALTER TABLE riskeventrecord ADD COLUMN event_key VARCHAR",
                },
            }
            added_columns: dict[str, set[str]] = {}
            for table, migrations in table_migrations.items():
                columns = {
                    row[1] for row in connection.execute(text(f"PRAGMA table_info({table})"))
                }
                if not columns:
                    continue
                for column, statement in migrations.items():
                    if column not in columns:
                        connection.execute(text(statement))
                        added_columns.setdefault(table, set()).add(column)

            account_columns = {
                row[1] for row in connection.execute(text("PRAGMA table_info(accountrecord)"))
            }
            if {
                "initial_equity", "cash_balance", "available_balance", "equity",
                "realized_pnl", "unrealized_pnl", "daily_pnl", "fees_paid", "updated_at",
            } <= account_columns:
                rows = connection.execute(text(
                    "SELECT id, initial_equity, cash_balance, available_balance, equity, "
                    "realized_pnl, unrealized_pnl, daily_pnl, fees_paid, updated_at, assets "
                    "FROM accountrecord"
                )).mappings()
                for row in rows:
                    raw_assets = row["assets"]
                    if isinstance(raw_assets, str):
                        try:
                            raw_assets = json.loads(raw_assets)
                        except (TypeError, ValueError):
                            raw_assets = []
                    connection.execute(
                        text(
                            "UPDATE accountrecord SET initial_equity = :initial_equity, "
                            "cash_balance = :cash_balance, available_balance = :available_balance, "
                            "equity = :equity, realized_pnl = :realized_pnl, "
                            "unrealized_pnl = :unrealized_pnl, daily_pnl = :daily_pnl, "
                            "fees_paid = :fees_paid, updated_at = :updated_at, assets = :assets "
                            "WHERE id = :id"
                        ),
                        {
                            "id": row["id"],
                            "initial_equity": Repository._finite_nonnegative(row["initial_equity"]),
                            "cash_balance": Repository._finite_nonnegative(row["cash_balance"]),
                            "available_balance": Repository._finite_nonnegative(
                                row["available_balance"]
                            ),
                            "equity": Repository._finite_nonnegative(row["equity"]),
                            "realized_pnl": _finite_or_zero(row["realized_pnl"]),
                            "unrealized_pnl": _finite_or_zero(row["unrealized_pnl"]),
                            "daily_pnl": _finite_or_zero(row["daily_pnl"]),
                            "fees_paid": Repository._finite_nonnegative(row["fees_paid"]),
                            "updated_at": _safe_timestamp(row["updated_at"]),
                            "assets": json.dumps(normalize_account_assets(raw_assets)),
                        },
                    )

                duplicate_accounts = connection.execute(text(
                    "SELECT strategy, COALESCE(source, 'unknown') AS source, "
                    "COALESCE(runtime_mode, 'unknown') AS runtime_mode, COUNT(*) AS row_count "
                    "FROM accountrecord GROUP BY strategy, source, runtime_mode "
                    "HAVING COUNT(*) > 1"
                )).mappings()
                connection.execute(text(
                    "UPDATE accountrecord SET active = COALESCE(active, 1)"
                ))
                for duplicate in duplicate_accounts:
                    current_id = connection.execute(text(
                        "SELECT id FROM accountrecord WHERE strategy = :strategy "
                        "AND COALESCE(source, 'unknown') = :source "
                        "AND COALESCE(runtime_mode, 'unknown') = :runtime_mode "
                        "ORDER BY updated_at DESC, id DESC LIMIT 1"
                    ), duplicate).scalar_one()
                    connection.execute(text(
                        "UPDATE accountrecord SET active = 0, stale_at = updated_at "
                        "WHERE strategy = :strategy "
                        "AND COALESCE(source, 'unknown') = :source "
                        "AND COALESCE(runtime_mode, 'unknown') = :runtime_mode "
                        "AND id != :current_id"
                    ), {
                        **duplicate,
                        "current_id": current_id,
                    })
                    logger.warning(
                        "AccountRecord migration retained %s historical rows for strategy=%s "
                        "source=%s runtime_mode=%s; latest row remains active",
                        duplicate["row_count"], duplicate["strategy"],
                        duplicate["source"], duplicate["runtime_mode"],
                    )

            order_info = list(connection.execute(text("PRAGMA table_info(orderrecord)")))
            order_columns = {row[1] for row in order_info}
            external_id_fields = {"exchange_order_id", "client_order_id"}
            external_id_is_required = {
                row[1]: bool(row[3])
                for row in order_info
                if row[1] in external_id_fields
            }
            order_indexes_to_restore: list[tuple[str, str | None, bool, list[str | None]]] = []
            if any(external_id_is_required.values()):
                for index in connection.execute(text("PRAGMA index_list(orderrecord)")):
                    index_name = index[1]
                    index_columns = [
                        row[2]
                        for row in connection.execute(
                            text(f'PRAGMA index_info("{index_name}")')
                        )
                    ]
                    index_sql = connection.execute(
                        text(
                            "SELECT sql FROM sqlite_schema "
                            "WHERE type = 'index' AND name = :index_name"
                        ),
                        {"index_name": index_name},
                    ).scalar_one_or_none()
                    is_external_id_constraint = (
                        bool(index[2])
                        and len(index_columns) == 1
                        and index_columns[0] in external_id_fields
                    )
                    if not is_external_id_constraint:
                        order_indexes_to_restore.append(
                            (index_name, index_sql, bool(index[2]), index_columns)
                        )

                # Keep the old indexes attached to the legacy table until it is
                # dropped. Dropping them first can leave SQLite's schema locked.
                connection.execute(text("ALTER TABLE orderrecord RENAME TO orderrecord_legacy"))
                connection.execute(text("""
                    CREATE TABLE orderrecord (
                        id INTEGER PRIMARY KEY,
                        order_id VARCHAR NOT NULL,
                        exchange_order_id VARCHAR,
                        client_order_id VARCHAR,
                        strategy VARCHAR NOT NULL,
                        symbol VARCHAR NOT NULL,
                        side VARCHAR NOT NULL,
                        type VARCHAR NOT NULL,
                        amount FLOAT NOT NULL,
                        price FLOAT NOT NULL,
                        status VARCHAR NOT NULL,
                        fill_price FLOAT NOT NULL,
                        timestamp INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        filled_amount FLOAT,
                        remaining_amount FLOAT,
                        snapshot_filled_amount FLOAT,
                        snapshot_updated_at INTEGER,
                        execution_type_family VARCHAR,
                        source VARCHAR,
                        runtime_mode VARCHAR,
                        active BOOLEAN DEFAULT 1,
                        stale_at INTEGER
                    )
                """))
                connection.execute(text("""
                    INSERT INTO orderrecord (
                        id, order_id, exchange_order_id, client_order_id, strategy,
                        symbol, side, type, amount, price, status, fill_price,
                        timestamp, updated_at, filled_amount, remaining_amount,
                        snapshot_filled_amount, snapshot_updated_at, execution_type_family,
                        source, runtime_mode, active, stale_at
                    )
                    SELECT id, order_id, exchange_order_id, client_order_id, strategy,
                        symbol, side, type, COALESCE(amount, 0.0), price, status, fill_price,
                        timestamp, updated_at, filled_amount, remaining_amount,
                        snapshot_filled_amount, snapshot_updated_at, execution_type_family,
                        COALESCE(source, 'unknown'), COALESCE(runtime_mode, 'unknown'),
                        COALESCE(active, 1), stale_at
                    FROM orderrecord_legacy
                """))
                connection.execute(text("DROP TABLE orderrecord_legacy"))
                order_columns = {
                    row[1] for row in connection.execute(text("PRAGMA table_info(orderrecord)"))
                }

            if {"filled_amount", "remaining_amount"} <= order_columns:
                rows = connection.execute(
                    text(
                        "SELECT id, amount, status, timestamp, updated_at, snapshot_updated_at, "
                        "filled_amount, remaining_amount FROM orderrecord"
                    )
                ).mappings()
                for row in rows:
                    amount = Repository._finite_nonnegative(row["amount"])
                    filled = Repository._finite_nonnegative_or_none(row["filled_amount"])
                    remaining = (
                        None
                        if "remaining_amount" in added_columns.get("orderrecord", set())
                        else Repository._finite_nonnegative_or_none(
                            row["remaining_amount"]
                        )
                    )
                    if remaining is not None and remaining <= amount:
                        filled = amount - remaining
                    else:
                        filled_missing = filled is None or (
                            "filled_amount" in added_columns.get("orderrecord", set())
                            and filled == 0.0
                        )
                        if filled_missing:
                            filled = amount if row["status"] == "filled" else 0.0
                        filled = min(filled, amount)
                        remaining = amount - filled
                    status = row["status"]
                    if status not in {
                        OrderStatus.CANCELLED.value,
                        "canceled",
                        "cancel",
                        OrderStatus.REJECTED.value,
                    }:
                        status = (
                            OrderStatus.PENDING.value
                            if filled <= 0.0
                            else OrderStatus.FILLED.value
                            if filled >= amount
                            else OrderStatus.PARTIALLY_FILLED.value
                        )
                    connection.execute(
                        text(
                            "UPDATE orderrecord SET amount = :amount, status = :status, "
                            "timestamp = :timestamp, updated_at = :updated_at, "
                            "snapshot_updated_at = :snapshot_updated_at, "
                            "filled_amount = :filled, remaining_amount = :remaining "
                            "WHERE id = :id"
                        ),
                        {
                            "id": row["id"],
                            "amount": amount,
                            "status": status,
                            "timestamp": Repository._safe_trade_timestamp(row["timestamp"]),
                            "updated_at": Repository._safe_trade_timestamp(row["updated_at"]),
                            "snapshot_updated_at": (
                                Repository._safe_trade_timestamp(row["snapshot_updated_at"])
                                if row["snapshot_updated_at"] is not None
                                else None
                            ),
                            "filled": filled,
                            "remaining": remaining,
                        },
                    )


            if {"price", "fill_price"} <= order_columns:
                rows = connection.execute(
                    text("SELECT id, price, fill_price FROM orderrecord")
                ).mappings()
                for row in rows:
                    connection.execute(
                        text(
                            "UPDATE orderrecord SET price = :price, fill_price = :fill_price "
                            "WHERE id = :id"
                        ),
                        {
                            "id": row["id"],
                            "price": Repository._finite_nonnegative(row["price"]),
                            "fill_price": Repository._finite_nonnegative(row["fill_price"]),
                        },
                    )

            if {"exchange_order_id", "client_order_id"} <= order_columns:
                for field in ("exchange_order_id", "client_order_id"):
                    duplicate_ids = connection.execute(text(
                        f"SELECT {field} AS external_id, COUNT(*) AS row_count "
                        f"FROM orderrecord WHERE {field} IS NOT NULL AND {field} != '' "
                        f"GROUP BY {field} HAVING COUNT(*) > 1"
                    )).mappings()
                    for duplicate in duplicate_ids:
                        logger.warning(
                            "OrderRecord migration retained %s historical rows for %s=%s",
                            duplicate["row_count"], field, duplicate["external_id"],
                        )

                for index_name, index_sql, is_unique, index_columns in order_indexes_to_restore:
                    if index_sql is None:
                        if any(column is None for column in index_columns):
                            logger.warning(
                                "Could not restore orderrecord index %s: "
                                "SQLite did not provide reconstructible SQL",
                                index_name,
                            )
                            continue
                        index_sql = (
                            f"CREATE {'UNIQUE ' if is_unique else ''}INDEX "
                            f'"{index_name}" ON orderrecord '
                            f"({', '.join(index_columns)})"
                        )
                    try:
                        connection.execute(text(index_sql))
                    except SQLAlchemyError:
                        logger.warning(
                            "Could not restore orderrecord index %s after migration",
                            index_name,
                            exc_info=True,
                        )

            trade_columns = {
                row[1]
                for row in connection.execute(text("PRAGMA table_info(traderecord)"))
            }
            if {"amount", "price", "fee", "timestamp"} <= trade_columns:
                rows = connection.execute(
                    text("SELECT id, amount, price, fee, timestamp FROM traderecord")
                ).mappings()
                for row in rows:
                    connection.execute(
                        text(
                            "UPDATE traderecord SET amount = :amount, price = :price, "
                            "fee = :fee, timestamp = :timestamp WHERE id = :id"
                        ),
                        {
                            "id": row["id"],
                            "amount": Repository._finite_nonnegative(row["amount"]),
                            "price": Repository._finite_nonnegative(row["price"]),
                            "fee": Repository._finite_nonnegative(row["fee"]),
                            "timestamp": Repository._safe_trade_timestamp(row["timestamp"]),
                        },
                    )
                if "active" in trade_columns:
                    connection.execute(text(
                        "UPDATE traderecord SET active = COALESCE(active, 1)"
                    ))

            position_columns = {
                row[1]
                for row in connection.execute(text("PRAGMA table_info(positionrecord)"))
            }
            required_position_columns = {
                "amount", "entry_price", "mark_price", "realized_pnl", "unrealized_pnl",
                "leverage", "timestamp",
            }
            if required_position_columns <= position_columns:
                rows = list(connection.execute(
                    text(
                        "SELECT id, amount, entry_price, mark_price, realized_pnl, "
                        "unrealized_pnl, leverage, timestamp FROM positionrecord"
                    )
                ).mappings())
                for row in rows:
                    try:
                        raw_amount = float(row["amount"])
                    except (TypeError, ValueError, OverflowError):
                        raw_amount = 0.0
                    amount = abs(raw_amount) if math.isfinite(raw_amount) else 0.0
                    for_pnl = []
                    for field in ("realized_pnl", "unrealized_pnl"):
                        try:
                            value = float(row[field])
                        except (TypeError, ValueError, OverflowError):
                            value = 0.0
                        for_pnl.append(value if math.isfinite(value) else 0.0)
                    connection.execute(
                        text(
                            "UPDATE positionrecord SET amount = :amount, "
                            "entry_price = :entry_price, mark_price = :mark_price, "
                            "realized_pnl = :realized_pnl, unrealized_pnl = :unrealized_pnl, "
                            "leverage = :leverage, timestamp = :timestamp WHERE id = :id"
                        ),
                        {
                            "id": row["id"],
                            "amount": amount,
                            "entry_price": Repository._finite_nonnegative(row["entry_price"]),
                            "mark_price": Repository._finite_nonnegative_or_none(row["mark_price"]),
                            "realized_pnl": for_pnl[0],
                            "unrealized_pnl": for_pnl[1],
                            "leverage": _safe_leverage(row["leverage"]),
                            "timestamp": Repository._safe_trade_timestamp(row["timestamp"]),
                        },
                    )

                if "quantity" in position_columns:
                    connection.execute(text(
                        "UPDATE positionrecord SET quantity = COALESCE(quantity, amount), "
                        "quantity_unit = COALESCE(NULLIF(quantity_unit, ''), 'base'), "
                        "is_inverse = COALESCE(is_inverse, 0)"
                    ))
                if "active" in position_columns:
                    connection.execute(text(
                        "UPDATE positionrecord SET active = COALESCE(active, 1)"
                    ))
                if {"source", "runtime_mode"} <= position_columns:
                    connection.execute(
                        text(
                            "UPDATE positionrecord SET source = "
                            "COALESCE(NULLIF(source, ''), 'unknown'), "
                            "runtime_mode = COALESCE(NULLIF(runtime_mode, ''), 'unknown')"
                        )
                    )

                for index in list(connection.execute(text("PRAGMA index_list(positionrecord)"))):
                    index_name = index[1]
                    if index_name.startswith("sqlite_autoindex_"):
                        continue
                    index_columns = [
                        row[2]
                        for row in connection.execute(
                            text(f'PRAGMA index_info("{index_name}")')
                        )
                    ]
                    if index_columns in (["strategy", "symbol"], ["symbol", "strategy"]):
                        connection.execute(text(f'DROP INDEX IF EXISTS "{index_name}"'))

                duplicate_positions = connection.execute(text(
                    "SELECT strategy, symbol, side, COALESCE(source, 'unknown') AS source, "
                    "COALESCE(runtime_mode, 'unknown') AS runtime_mode, "
                    "MAX(timestamp) AS latest_timestamp, COUNT(*) AS row_count "
                    "FROM positionrecord GROUP BY strategy, symbol, side, source, runtime_mode "
                    "HAVING COUNT(*) > 1"
                )).mappings()
                for duplicate in duplicate_positions:
                    keep_id = connection.execute(text(
                        "SELECT MAX(id) FROM positionrecord WHERE strategy = :strategy "
                        "AND symbol = :symbol AND side = :side "
                        "AND COALESCE(source, 'unknown') = :source "
                        "AND COALESCE(runtime_mode, 'unknown') = :runtime_mode "
                        "AND timestamp = :timestamp"
                    ), {
                        **duplicate,
                        "timestamp": duplicate["latest_timestamp"],
                    }).scalar_one()
                    if "active" in position_columns:
                        connection.execute(text(
                            "UPDATE positionrecord SET active = 0, stale_at = :stale_at "
                            "WHERE strategy = :strategy AND symbol = :symbol AND side = :side "
                            "AND COALESCE(source, 'unknown') = :source "
                            "AND COALESCE(runtime_mode, 'unknown') = :runtime_mode "
                            "AND id != :keep_id"
                        ), {
                            **duplicate,
                            "keep_id": keep_id,
                            "stale_at": duplicate["latest_timestamp"],
                        })
                    logger.warning(
                        "PositionRecord migration retained %s historical rows for %s/%s/%s "
                        "source=%s runtime_mode=%s; latest row remains active",
                        duplicate["row_count"], duplicate["strategy"],
                        duplicate["symbol"], duplicate["side"],
                        duplicate["source"], duplicate["runtime_mode"],
                    )

            fingerprint_columns = {
                "exchange_trade_id", "exchange_order_id", "client_order_id",
                "symbol", "side", "amount", "price", "fee", "timestamp",
                "blank_trade_fingerprint",
            }
            if fingerprint_columns <= trade_columns:
                seen_fingerprints = set(
                    connection.execute(
                        text(
                            "SELECT blank_trade_fingerprint FROM traderecord "
                            "WHERE (exchange_trade_id IS NULL OR exchange_trade_id = '') "
                            "AND blank_trade_fingerprint IS NOT NULL "
                            "AND blank_trade_fingerprint != ''"
                        )
                    ).scalars()
                )
                blank_trades = connection.execute(
                    text(
                        "SELECT id, exchange_order_id, client_order_id, symbol, side, "
                        "amount, price, fee, timestamp FROM traderecord "
                        "WHERE (exchange_trade_id IS NULL OR exchange_trade_id = '') "
                        "AND (blank_trade_fingerprint IS NULL "
                        "OR blank_trade_fingerprint = '') ORDER BY id"
                    )
                ).mappings()
                for trade in blank_trades:
                    if not trade["exchange_order_id"] and not trade["client_order_id"]:
                        continue
                    fingerprint = stable_blank_trade_fingerprint(
                        exchange_order_id=trade["exchange_order_id"],
                        client_order_id=trade["client_order_id"],
                        symbol=trade["symbol"],
                        side=trade["side"],
                        amount=trade["amount"],
                        price=trade["price"],
                        fee=trade["fee"],
                        timestamp=Repository._safe_trade_timestamp(trade["timestamp"]),
                    )
                    if fingerprint is None:
                        continue
                    if fingerprint in seen_fingerprints:
                        logger.warning(
                            "TradeRecord migration retained duplicate historical fingerprint=%s "
                            "for id=%s",
                            fingerprint,
                            trade["id"],
                        )
                    connection.execute(
                        text(
                            "UPDATE traderecord SET blank_trade_fingerprint = :fingerprint "
                            "WHERE id = :id"
                        ),
                        {"id": trade["id"], "fingerprint": fingerprint},
                    )
                    seen_fingerprints.add(fingerprint)

            if "exchange_trade_id" in trade_columns:
                duplicate_trade_ids = connection.execute(text(
                    "SELECT exchange_trade_id, COALESCE(source, 'unknown') AS source, "
                    "COALESCE(runtime_mode, 'unknown') AS runtime_mode, COUNT(*) AS row_count "
                    "FROM traderecord "
                    "WHERE exchange_trade_id IS NOT NULL AND exchange_trade_id != '' "
                    "GROUP BY exchange_trade_id, source, runtime_mode "
                    "HAVING COUNT(*) > 1"
                )).mappings()
                for duplicate in duplicate_trade_ids:
                    logger.warning(
                        "TradeRecord migration retained %s historical rows for "
                        "exchange_trade_id=%s source=%s runtime_mode=%s without rewriting "
                        "legacy active state",
                        duplicate["row_count"], duplicate["exchange_trade_id"],
                        duplicate["source"], duplicate["runtime_mode"],
                    )

                duplicate_fingerprints = connection.execute(text(
                    "SELECT blank_trade_fingerprint, COALESCE(source, 'unknown') AS source, "
                    "COALESCE(runtime_mode, 'unknown') AS runtime_mode, COUNT(*) AS row_count "
                    "FROM traderecord WHERE blank_trade_fingerprint IS NOT NULL "
                    "AND blank_trade_fingerprint != '' "
                    "GROUP BY blank_trade_fingerprint, source, runtime_mode "
                    "HAVING COUNT(*) > 1"
                )).mappings()
                for duplicate in duplicate_fingerprints:
                    logger.warning(
                        "TradeRecord migration retained %s historical rows for blank "
                        "fingerprint=%s source=%s runtime_mode=%s without rewriting legacy "
                        "active state",
                        duplicate["row_count"], duplicate["blank_trade_fingerprint"],
                        duplicate["source"], duplicate["runtime_mode"],
                    )

            kline_columns = {
                row[1]
                for row in connection.execute(text("PRAGMA table_info(klinecache)"))
            }
            if {"timestamp", "open", "high", "low", "close", "volume"} <= kline_columns:
                if {"source", "runtime_mode"} <= kline_columns:
                    connection.execute(text(
                        "UPDATE klinecache SET source = COALESCE(NULLIF(source, ''), 'unknown'), "
                        "runtime_mode = COALESCE(NULLIF(runtime_mode, ''), 'unknown')"
                    ))
                rows = connection.execute(text(
                    "SELECT id, timestamp, open, high, low, close, volume FROM klinecache"
                )).mappings()
                for row in rows:
                    try:
                        prices = {
                            field_name: float(row[field_name])
                            for field_name in ("open", "high", "low", "close")
                        }
                        valid_ohlc = all(
                            math.isfinite(value) and value >= 0.0
                            for value in prices.values()
                        ) and (
                            prices["high"] >= prices["low"]
                            and prices["low"] <= prices["open"] <= prices["high"]
                            and prices["low"] <= prices["close"] <= prices["high"]
                        )
                    except (TypeError, ValueError, OverflowError):
                        valid_ohlc = False
                    if not valid_ohlc:
                        logger.warning(
                            "Retaining invalid historical kline cache row id=%s as inactive",
                            row["id"],
                        )
                        connection.execute(
                            text("UPDATE klinecache SET active = 0 WHERE id = :id"),
                            {"id": row["id"]},
                        )
                        continue
                    connection.execute(
                        text(
                            "UPDATE klinecache SET timestamp = :timestamp, open = :open, "
                            "high = :high, low = :low, close = :close, volume = :volume, "
                            "active = 1 WHERE id = :id"
                        ),
                        {
                            "id": row["id"],
                            "timestamp": _safe_timestamp(row["timestamp"]),
                            "open": prices["open"],
                            "high": prices["high"],
                            "low": prices["low"],
                            "close": prices["close"],
                            "volume": Repository._finite_nonnegative(row["volume"]),
                        },
                    )
                if {"source", "runtime_mode"} <= kline_columns:
                    connection.execute(text(
                        "UPDATE klinecache SET source = COALESCE(NULLIF(source, ''), 'unknown'), "
                        "runtime_mode = COALESCE(NULLIF(runtime_mode, ''), 'unknown')"
                    ))

            cash_columns = {
                row[1]
                for row in connection.execute(text("PRAGMA table_info(cashledgerrecord)"))
            }
            if {"amount", "balance_after", "timestamp"} <= cash_columns:
                rows = connection.execute(text(
                    "SELECT id, amount, balance_after, timestamp FROM cashledgerrecord"
                )).mappings()
                for row in rows:
                    connection.execute(
                        text(
                            "UPDATE cashledgerrecord SET amount = :amount, "
                            "balance_after = :balance_after, timestamp = :timestamp WHERE id = :id"
                        ),
                        {
                            "id": row["id"],
                            "amount": _finite_or_zero(row["amount"]),
                            "balance_after": _finite_or_zero(row["balance_after"]),
                            "timestamp": _safe_timestamp(row["timestamp"]),
                        },
                    )

            kill_switch_columns = {
                row[1]
                for row in connection.execute(text("PRAGMA table_info(killswitchrecord)"))
            }
            if "updated_at" in kill_switch_columns:
                rows = connection.execute(
                    text("SELECT id, updated_at FROM killswitchrecord")
                ).mappings()
                for row in rows:
                    connection.execute(
                        text(
                            "UPDATE killswitchrecord SET updated_at = :updated_at WHERE id = :id"
                        ),
                        {"id": row["id"], "updated_at": _safe_timestamp(row["updated_at"])},
                    )

            risk_columns = {
                row[1]
                for row in connection.execute(text("PRAGMA table_info(riskeventrecord)"))
            }
            if "timestamp" in risk_columns:
                rows = connection.execute(
                    text("SELECT id, timestamp FROM riskeventrecord")
                ).mappings()
                for row in rows:
                    connection.execute(
                        text(
                            "UPDATE riskeventrecord SET timestamp = :timestamp WHERE id = :id"
                        ),
                        {"id": row["id"], "timestamp": _safe_timestamp(row["timestamp"])},
                    )
            if "event_key" in risk_columns:
                duplicate_keys = connection.execute(text(
                    "SELECT event_key, COUNT(*) AS row_count FROM riskeventrecord "
                    "WHERE event_key IS NOT NULL GROUP BY event_key HAVING COUNT(*) > 1"
                )).mappings()
                for duplicate in duplicate_keys:
                    logger.warning(
                        "RiskEventRecord migration retained %s historical rows for event_key=%s",
                        duplicate["row_count"], duplicate["event_key"],
                    )
                connection.execute(text(
                    "CREATE TRIGGER IF NOT EXISTS uq_riskeventrecord_event_key_insert "
                    "BEFORE INSERT ON riskeventrecord "
                    "WHEN NEW.event_key IS NOT NULL AND EXISTS ("
                    "SELECT 1 FROM riskeventrecord WHERE event_key = NEW.event_key) "
                    "BEGIN SELECT RAISE(ABORT, 'duplicate risk event key'); END"
                ))
