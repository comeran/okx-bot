"""Prepared strategy-local spot accounting; no exchange or execution authority.

The three tables deliberately use their own SQLAlchemy metadata, never SQLModel's
global registry. Every operation opens a short SQLite transaction; serialized
writes do not depend on the caller's Engine listeners. Order/seed/terminal retry
timestamps are observations; fill timestamps are immutable event identity.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import sqlite3
from contextlib import closing, contextmanager
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from pathlib import Path

from sqlalchemy import Column, Integer, MetaData, String, Table, Text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from src.core.execution_scope import ExecutionScope
from src.exchange.base import canonical_symbol, is_supported_canonical_symbol

_CONTEXT = Context(
    prec=80,
    rounding=ROUND_HALF_EVEN,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)
_ZERO = Decimal(0)
_METADATA = MetaData()
_BOOK = Table(
    "spot_ledger_book",
    _METADATA,
    Column("strategy", String, primary_key=True),
    Column("symbol", String, nullable=False, unique=True),
    Column("currency", String, nullable=False),
    Column("base_currency", String, nullable=False),
    Column("allocation_scope_id", String, nullable=False),
    Column("draft_revision", Integer, nullable=False),
    Column("initial_quote", Text, nullable=False),
    Column("quote_balance", Text, nullable=False),
    Column("base_balance", Text, nullable=False),
    Column("cost_basis_quote", Text, nullable=False),
    Column("realized_pnl_quote", Text, nullable=False),
    Column("quote_fees", Text, nullable=False),
    Column("base_fees", Text, nullable=False),
    Column("version", Integer, nullable=False),
    Column("created_at", Integer, nullable=False),
    Column("updated_at", Integer, nullable=False),
)
_ORDER = Table(
    "spot_ledger_order",
    _METADATA,
    Column("order_id", String, primary_key=True),
    Column("strategy", String, nullable=False, index=True),
    Column("side", String, nullable=False),
    Column("amount", Text, nullable=False),
    Column("price_cap", Text),
    Column("fee_buffer", Text, nullable=False),
    Column("spent_fee_buffer", Text, nullable=False),
    Column("filled_quantity", Text, nullable=False),
    Column("quote_reserved", Text, nullable=False),
    Column("base_reserved", Text, nullable=False),
    Column("status", String, nullable=False),
    Column("terminal_quantity", Text),
    Column("created_at", Integer, nullable=False),
    Column("updated_at", Integer, nullable=False),
)
_EVENT = Table(
    "spot_ledger_event",
    _METADATA,
    Column("id", Integer, primary_key=True),
    Column("event_key", String, nullable=False, unique=True),
    Column("event_type", String, nullable=False),
    Column("strategy", String, nullable=False, index=True),
    Column("order_id", String),
    Column("trade_id", String, unique=True),
    Column("payload", Text, nullable=False),
    Column("quote_delta", Text, nullable=False),
    Column("base_delta", Text, nullable=False),
    Column("timestamp", Integer, nullable=False),
)
_TERMINAL = {"canceled", "rejected", "filled"}
_DECIMAL_FIELDS = (
    "quote_balance",
    "base_balance",
    "cost_basis_quote",
    "realized_pnl_quote",
    "quote_fees",
    "base_fees",
)


class SpotLedgerError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _identity(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 256
        or any(ord(character) < 32 for character in value)
    ):
        raise SpotLedgerError("invalid_input", f"A canonical {label} is required")
    return value


def _integer(value: object, label: str, *, positive: bool = False) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < (1 if positive else 0)
        or value > 2**63 - 1
    ):
        raise SpotLedgerError("invalid_input", f"Invalid {label}")
    return value


def _number(value: object, label: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, str, int)):
        raise SpotLedgerError("invalid_input", f"{label} requires an exact decimal")
    if isinstance(value, str) and (
        len(value) > 39 or not re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value)
    ):
        raise SpotLedgerError("invalid_input", f"Invalid {label}")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError):
        raise SpotLedgerError("invalid_input", f"Invalid {label}") from None
    if (
        not result.is_finite()
        or result < 0
        or (positive and result == 0)
        or result.adjusted() > 17
        or result.as_tuple().exponent < -18
    ):
        raise SpotLedgerError("invalid_input", f"{label} exceeds supported decimal bounds")
    return result


def _text(value: Decimal) -> str:
    if value == 0:
        return "0"
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def _json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class SpotLedgerStore:
    """An exact local accounting model, always prepared and non-executing.

    Mutation methods return the current prepared strategy snapshot. Reads never
    initialize a strategy. `path` is the validated facts-file path; no persistent
    connection or exchange client is retained.
    """

    def __init__(self, engine: Engine, scope: ExecutionScope) -> None:
        if not isinstance(scope, ExecutionScope) or not isinstance(engine, Engine):
            raise SpotLedgerError("invalid_scope", "A bound execution scope is required")
        database = engine.url.database
        if (
            engine.dialect.name != "sqlite"
            or not database
            or database == ":memory:"
            or database.startswith("file:")
            or engine.url.query.get("uri")
        ):
            raise SpotLedgerError("invalid_scope", "A persistent bound SQLite store is required")
        self.path = Path(database).absolute()
        self.scope = scope
        if (
            self.path.is_symlink()
            or not self.path.is_file()
            or self.path.parent.is_symlink()
            or self.path.parent.parent.is_symlink()
        ):
            raise SpotLedgerError("invalid_scope", "The facts binding is missing or invalid")
        try:
            with closing(sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True)) as connection:
                self._validate_header(connection)
            lock_path = self.path.with_suffix(".bootstrap.lock")
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                with closing(
                    sqlite3.connect(
                        f"{self.path.as_uri()}?mode=ro",
                        uri=True,
                    )
                ) as connection:
                    self._validate_header(connection)
                _METADATA.create_all(engine)
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
        except (sqlite3.Error, OSError, SQLAlchemyError):
            raise SpotLedgerError(
                "storage_unavailable", "Spot ledger storage is unavailable"
            ) from None

    def _validate_header(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT schema_version, account_scope_id, exchange, environment, "
            "account_fingerprint FROM execution_scope_header"
        ).fetchall()
        expected = (
            1,
            self.scope.account_scope_id,
            self.scope.exchange,
            self.scope.environment,
            self.scope.account_fingerprint,
        )
        if [tuple(row) for row in rows] != [expected]:
            raise SpotLedgerError("invalid_scope", "Spot ledger account binding is incompatible")

    @contextmanager
    def _transaction(self, *, write: bool = False):
        try:
            mode = "rw" if write else "ro"
            with (
                closing(
                    sqlite3.connect(
                        f"{self.path.as_uri()}?mode={mode}",
                        uri=True,
                        timeout=30,
                        isolation_level=None,
                    )
                ) as connection,
                localcontext(_CONTEXT),
            ):
                connection.row_factory = sqlite3.Row
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                try:
                    self._validate_header(connection)
                    yield connection
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
        except sqlite3.Error:
            raise SpotLedgerError("storage_unavailable", "Spot ledger transaction failed") from None

    @staticmethod
    def _book(connection, strategy: str) -> dict:
        row = connection.execute(
            "SELECT * FROM spot_ledger_book WHERE strategy=?",
            (strategy,),
        ).fetchone()
        if row is None:
            raise SpotLedgerError("not_initialized", "Strategy ledger is not initialized")
        return dict(row)

    @staticmethod
    def _order(connection, strategy: str, order_id: str) -> dict:
        row = connection.execute(
            "SELECT * FROM spot_ledger_order WHERE order_id=?",
            (order_id,),
        ).fetchone()
        if row is None:
            raise SpotLedgerError("unknown_order", "A reserved order is required")
        if row["strategy"] != strategy:
            raise SpotLedgerError("conflict", "Order identity belongs to another strategy")
        return dict(row)

    @staticmethod
    def _reserved(connection, strategy: str) -> tuple[Decimal, Decimal]:
        rows = connection.execute(
            "SELECT quote_reserved, base_reserved FROM spot_ledger_order WHERE strategy=?",
            (strategy,),
        ).fetchall()
        return (
            sum((Decimal(row[0]) for row in rows), _ZERO),
            sum((Decimal(row[1]) for row in rows), _ZERO),
        )

    def _snapshot(self, connection, strategy: str) -> dict:
        book = self._book(connection, strategy)
        quote_reserved, base_reserved = self._reserved(connection, strategy)
        return {
            **{
                key: book[key]
                for key in (
                    "strategy",
                    "symbol",
                    "currency",
                    "base_currency",
                    "allocation_scope_id",
                    "draft_revision",
                    "initial_quote",
                    "quote_balance",
                    "base_balance",
                    "cost_basis_quote",
                    "realized_pnl_quote",
                    "version",
                    "updated_at",
                )
            },
            "status": "prepared",
            "execution_ready": False,
            "quote_reserved": _text(quote_reserved),
            "quote_available": _text(Decimal(book["quote_balance"]) - quote_reserved),
            "base_reserved": _text(base_reserved),
            "base_available": _text(Decimal(book["base_balance"]) - base_reserved),
            "fees": {
                book["currency"]: book["quote_fees"],
                book["base_currency"]: book["base_fees"],
            },
        }

    def _assert_backed(self, connection, strategy: str) -> None:
        book = self._book(connection, strategy)
        quote_reserved, base_reserved = self._reserved(connection, strategy)
        if (
            quote_reserved < 0
            or base_reserved < 0
            or Decimal(book["quote_balance"]) < quote_reserved
            or Decimal(book["base_balance"]) < base_reserved
            or Decimal(book["cost_basis_quote"]) < 0
        ):
            raise SpotLedgerError("insufficient_funds", "Strategy assets do not cover reservations")

    @staticmethod
    def _event_exists(connection, event_key: str, payload: dict) -> bool:
        row = connection.execute(
            "SELECT payload FROM spot_ledger_event WHERE event_key=?",
            (event_key,),
        ).fetchone()
        if row is None:
            return False
        if row[0] != _json(payload):
            raise SpotLedgerError("conflict", "Event identity has a different economic payload")
        return True

    @staticmethod
    def _event(
        connection,
        event_type: str,
        event_key: str,
        payload: dict,
        timestamp: int,
        *,
        quote_delta: Decimal = _ZERO,
        base_delta: Decimal = _ZERO,
    ) -> None:
        connection.execute(
            "INSERT INTO spot_ledger_event "
            "(event_key,event_type,strategy,order_id,trade_id,payload,"
            "quote_delta,base_delta,timestamp) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                event_key,
                event_type,
                payload["strategy"],
                payload.get("order_id"),
                payload.get("trade_id"),
                _json(payload),
                _text(quote_delta),
                _text(base_delta),
                timestamp,
            ),
        )

    @staticmethod
    def _save_book(connection, book: dict, timestamp: int) -> None:
        fields = (*_DECIMAL_FIELDS, "version", "updated_at")
        book["version"] += 1
        book["updated_at"] = timestamp
        connection.execute(
            "UPDATE spot_ledger_book SET "
            + ",".join(f"{key}=?" for key in fields)
            + " WHERE strategy=?",
            [book[key] for key in fields] + [book["strategy"]],
        )

    @staticmethod
    def _save_order(connection, order: dict, timestamp: int) -> None:
        fields = (
            "spent_fee_buffer",
            "filled_quantity",
            "quote_reserved",
            "base_reserved",
            "status",
            "terminal_quantity",
            "updated_at",
        )
        order["updated_at"] = timestamp
        connection.execute(
            "UPDATE spot_ledger_order SET "
            + ",".join(f"{key}=?" for key in fields)
            + " WHERE order_id=?",
            [order[key] for key in fields] + [order["order_id"]],
        )

    def get_strategy(self, strategy: str) -> dict | None:
        strategy = _identity(strategy, "strategy")
        with self._transaction() as connection:
            if (
                connection.execute(
                    "SELECT 1 FROM spot_ledger_book WHERE strategy=?",
                    (strategy,),
                ).fetchone()
                is None
            ):
                return None
            return self._snapshot(connection, strategy)

    def get_order(self, order_id: str) -> dict | None:
        order_id = _identity(order_id, "order identity")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM spot_ledger_order WHERE order_id=?",
                (order_id,),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["remaining_quantity"] = _text(
                Decimal(result["amount"]) - Decimal(result["filled_quantity"]),
            )
            result["unspent_fee_buffer"] = _text(
                Decimal(result["fee_buffer"]) - Decimal(result["spent_fee_buffer"]),
            )
            return result

    def get_events(self, strategy: str) -> list[dict]:
        strategy = _identity(strategy, "strategy")
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM spot_ledger_event WHERE strategy=? ORDER BY id",
                (strategy,),
            ).fetchall()
            return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def initialize_strategy(
        self,
        *,
        strategy: str,
        symbol: str,
        initial_quote,
        allocation_scope_id: str,
        draft_revision: int,
        timestamp: int,
    ) -> dict:
        strategy = _identity(strategy, "strategy")
        allocation_scope_id = _identity(allocation_scope_id, "allocation scope")
        draft_revision = _integer(draft_revision, "draft revision", positive=True)
        timestamp = _integer(timestamp, "timestamp")
        initial = _number(initial_quote, "initial quote", positive=True)
        symbol = canonical_symbol(_identity(symbol, "spot symbol"), market_type="spot")
        if not is_supported_canonical_symbol(symbol, market_type="spot"):
            raise SpotLedgerError("invalid_input", "One spot symbol is required")
        base, quote = symbol.split("-")
        if base == quote:
            raise SpotLedgerError("invalid_input", "Spot base and quote must differ")
        payload = dict(
            strategy=strategy,
            symbol=symbol,
            initial_quote=_text(initial),
            allocation_scope_id=allocation_scope_id,
            draft_revision=draft_revision,
        )
        with self._transaction(write=True) as connection:
            if self._event_exists(connection, f"initialize:{strategy}", payload):
                return self._snapshot(connection, strategy)
            if connection.execute(
                "SELECT 1 FROM spot_ledger_book WHERE symbol=? OR strategy=?",
                (symbol, strategy),
            ).fetchone():
                raise SpotLedgerError("conflict", "Strategy or symbol already has a ledger")
            connection.execute(
                "INSERT INTO spot_ledger_book "
                "(strategy,symbol,currency,base_currency,allocation_scope_id,draft_revision,"
                "initial_quote,quote_balance,base_balance,cost_basis_quote,realized_pnl_quote,"
                "quote_fees,base_fees,version,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    strategy,
                    symbol,
                    quote,
                    base,
                    allocation_scope_id,
                    draft_revision,
                    _text(initial),
                    _text(initial),
                    "0",
                    "0",
                    "0",
                    "0",
                    "0",
                    1,
                    timestamp,
                    timestamp,
                ),
            )
            self._event(
                connection,
                "initialize",
                f"initialize:{strategy}",
                payload,
                timestamp,
                quote_delta=initial,
            )
            return self._snapshot(connection, strategy)

    def reserve_order(
        self,
        *,
        strategy: str,
        order_id: str,
        side: str,
        amount,
        price_cap=None,
        fee_buffer="0",
        timestamp: int,
    ) -> dict:
        strategy = _identity(strategy, "strategy")
        order_id = _identity(order_id, "order identity")
        timestamp = _integer(timestamp, "timestamp")
        if not isinstance(side, str) or side not in {"buy", "sell"}:
            raise SpotLedgerError("invalid_input", "Spot side must be buy or sell")
        quantity = _number(amount, "order amount", positive=True)
        fee = _number(fee_buffer, "fee buffer")
        cap = None if price_cap is None else _number(price_cap, "price cap", positive=True)
        if side == "buy" and cap is None:
            raise SpotLedgerError("invalid_input", "Buy requires a maximum execution price")
        with self._transaction(write=True) as connection:
            book = self._book(connection, strategy)
            payload = dict(
                strategy=strategy,
                symbol=book["symbol"],
                order_id=order_id,
                side=side,
                amount=_text(quantity),
                price_cap=None if cap is None else _text(cap),
                fee_buffer=_text(fee),
            )
            row = connection.execute(
                "SELECT status FROM spot_ledger_order WHERE order_id=?",
                (order_id,),
            ).fetchone()
            if row is not None:
                if self._event_exists(connection, f"reserve:{order_id}", payload):
                    return self._snapshot(connection, strategy)
                raise SpotLedgerError("conflict", "Order identity already exists")
            quote_reserved = quantity * cap + fee if side == "buy" else _ZERO
            base_reserved = quantity + fee if side == "sell" else _ZERO
            connection.execute(
                "INSERT INTO spot_ledger_order "
                "(order_id,strategy,side,amount,price_cap,fee_buffer,spent_fee_buffer,"
                "filled_quantity,quote_reserved,base_reserved,status,terminal_quantity,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    order_id,
                    strategy,
                    side,
                    _text(quantity),
                    payload["price_cap"],
                    _text(fee),
                    "0",
                    "0",
                    _text(quote_reserved),
                    _text(base_reserved),
                    "open",
                    None,
                    timestamp,
                    timestamp,
                ),
            )
            self._assert_backed(connection, strategy)
            self._save_book(connection, book, timestamp)
            self._event(connection, "reserve", f"reserve:{order_id}", payload, timestamp)
            return self._snapshot(connection, strategy)

    def apply_fill(
        self,
        *,
        strategy: str,
        order_id: str,
        trade_id: str,
        amount,
        price,
        fee,
        fee_currency: str,
        timestamp: int,
    ) -> dict:
        strategy = _identity(strategy, "strategy")
        order_id = _identity(order_id, "order identity")
        trade_id = _identity(trade_id, "trade identity")
        fee_currency = _identity(fee_currency, "fee currency")
        timestamp = _integer(timestamp, "fill timestamp")
        quantity = _number(amount, "fill amount", positive=True)
        fill_price = _number(price, "fill price", positive=True)
        fill_fee = _number(fee, "fill fee")
        payload = dict(
            strategy=strategy,
            order_id=order_id,
            trade_id=trade_id,
            amount=_text(quantity),
            price=_text(fill_price),
            fee=_text(fill_fee),
            fee_currency=fee_currency,
            timestamp=timestamp,
        )
        with self._transaction(write=True) as connection:
            if self._event_exists(connection, f"fill:{trade_id}", payload):
                return self._snapshot(connection, strategy)
            book = self._book(connection, strategy)
            order = self._order(connection, strategy, order_id)
            if order["status"] in _TERMINAL:
                raise SpotLedgerError(
                    "conflict", "A new fill after terminal requires reconciliation"
                )
            if fee_currency not in {book["currency"], book["base_currency"]}:
                raise SpotLedgerError("unsupported_fee", "Only base and quote fees are supported")
            filled = Decimal(order["filled_quantity"]) + quantity
            if filled > Decimal(order["amount"]):
                raise SpotLedgerError("invalid_fill", "Fill exceeds reserved order quantity")
            quote_fee = fill_fee if fee_currency == book["currency"] else _ZERO
            base_fee = fill_fee if fee_currency == book["base_currency"] else _ZERO
            notional = quantity * fill_price
            balances = {key: Decimal(book[key]) for key in _DECIMAL_FIELDS}
            remaining_fee = Decimal(order["fee_buffer"]) - Decimal(order["spent_fee_buffer"])
            if order["side"] == "buy":
                if fill_price > Decimal(order["price_cap"]) or quote_fee > remaining_fee:
                    raise SpotLedgerError("invalid_fill", "Buy exceeds price or fee cap")
                if base_fee >= quantity:
                    raise SpotLedgerError("invalid_fill", "Buy requires positive net base received")
                quote_delta = -(notional + quote_fee)
                base_delta = quantity - base_fee
                balances["cost_basis_quote"] += notional + quote_fee
                spent_fee = quote_fee
            else:
                if base_fee > remaining_fee or quote_fee > notional:
                    raise SpotLedgerError("invalid_fill", "Sell exceeds fee cap or proceeds")
                disposed = quantity + base_fee
                if disposed > balances["base_balance"]:
                    raise SpotLedgerError("insufficient_funds", "Sell exceeds owned base")
                cost = (
                    balances["cost_basis_quote"]
                    if disposed == balances["base_balance"]
                    else balances["cost_basis_quote"] * disposed / balances["base_balance"]
                )
                quote_delta = notional - quote_fee
                base_delta = -disposed
                balances["cost_basis_quote"] -= cost
                balances["realized_pnl_quote"] += quote_delta - cost
                spent_fee = base_fee
            balances["quote_balance"] += quote_delta
            balances["base_balance"] += base_delta
            balances["quote_fees"] += quote_fee
            balances["base_fees"] += base_fee
            book.update({key: _text(value) for key, value in balances.items()})
            order["filled_quantity"] = _text(filled)
            order["spent_fee_buffer"] = _text(Decimal(order["spent_fee_buffer"]) + spent_fee)
            remaining = Decimal(order["amount"]) - filled
            remaining_fee -= spent_fee
            order["quote_reserved"] = _text(
                remaining * Decimal(order["price_cap"]) + remaining_fee
                if order["side"] == "buy"
                else _ZERO,
            )
            order["base_reserved"] = _text(
                remaining + remaining_fee if order["side"] == "sell" else _ZERO,
            )
            self._save_order(connection, order, timestamp)
            self._save_book(connection, book, timestamp)
            self._assert_backed(connection, strategy)
            self._event(
                connection,
                "fill",
                f"fill:{trade_id}",
                payload,
                timestamp,
                quote_delta=quote_delta,
                base_delta=base_delta,
            )
            return self._snapshot(connection, strategy)

    def mark_unknown(self, *, strategy: str, order_id: str, timestamp: int) -> dict:
        strategy = _identity(strategy, "strategy")
        order_id = _identity(order_id, "order identity")
        timestamp = _integer(timestamp, "timestamp")
        with self._transaction(write=True) as connection:
            book = self._book(connection, strategy)
            order = self._order(connection, strategy, order_id)
            if order["status"] in _TERMINAL:
                raise SpotLedgerError("conflict", "A terminal order cannot become unknown")
            if order["status"] == "unknown":
                return self._snapshot(connection, strategy)
            order["status"] = "unknown"
            self._save_order(connection, order, timestamp)
            self._save_book(connection, book, timestamp)
            payload = dict(strategy=strategy, order_id=order_id)
            self._event(connection, "unknown", f"unknown:{order_id}", payload, timestamp)
            return self._snapshot(connection, strategy)

    def confirm_terminal(
        self,
        *,
        strategy: str,
        order_id: str,
        status: str,
        confirmed_filled_quantity,
        timestamp: int,
    ) -> dict:
        strategy = _identity(strategy, "strategy")
        order_id = _identity(order_id, "order identity")
        timestamp = _integer(timestamp, "timestamp")
        confirmed = _number(confirmed_filled_quantity, "confirmed filled quantity")
        if not isinstance(status, str) or status not in _TERMINAL:
            raise SpotLedgerError("invalid_input", "An explicit terminal status is required")
        payload = dict(
            strategy=strategy,
            order_id=order_id,
            status=status,
            confirmed_filled_quantity=_text(confirmed),
        )
        with self._transaction(write=True) as connection:
            if self._event_exists(connection, f"terminal:{order_id}", payload):
                return self._snapshot(connection, strategy)
            book = self._book(connection, strategy)
            order = self._order(connection, strategy, order_id)
            if order["status"] in _TERMINAL:
                raise SpotLedgerError("conflict", "Terminal order checkpoint changed")
            if (
                confirmed != Decimal(order["filled_quantity"])
                or (status == "filled" and confirmed != Decimal(order["amount"]))
                or (status == "rejected" and confirmed != 0)
            ):
                raise SpotLedgerError(
                    "reconciliation_required", "Terminal fills are not reconciled"
                )
            order.update(
                status=status,
                terminal_quantity=_text(confirmed),
                quote_reserved="0",
                base_reserved="0",
            )
            self._save_order(connection, order, timestamp)
            self._save_book(connection, book, timestamp)
            self._assert_backed(connection, strategy)
            self._event(connection, "terminal", f"terminal:{order_id}", payload, timestamp)
            return self._snapshot(connection, strategy)
