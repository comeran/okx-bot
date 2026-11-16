"""用可信的现货预检原子拨款，并按策略记账。

这里的账只服务现货。合约不能借这条路径激活。拨款成功也不打开实盘开仓。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)

from sqlalchemy import text

from src.exchange.base import canonical_symbol, is_supported_canonical_symbol

_CONTEXT = Context(
    prec=80,
    rounding=ROUND_HALF_EVEN,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)
_ZERO = Decimal(0)
_TERMINAL = {"filled", "cancelled", "canceled", "rejected"}
_OPEN = "open"


class FundingError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class TrustedActivationFacts:
    available_quote: Decimal
    observed_at: int
    preflight_passed: bool
    market_type: str
    account_scope_id: str


@dataclass(frozen=True)
class StrategyBook:
    scope_id: str
    strategy: str
    environment: str
    symbol: str
    revision: int
    cash: Decimal
    base_position: Decimal
    realized_pnl: Decimal
    reserved_quote: Decimal
    reserved_base: Decimal


def ensure_funded_tables(engine) -> None:
    if getattr(getattr(engine, "dialect", None), "name", None) != "sqlite":
        return
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS funded_spot_book ("
            "scope VARCHAR NOT NULL, strategy VARCHAR NOT NULL, "
            "environment VARCHAR NOT NULL, symbol VARCHAR NOT NULL, "
            "revision INTEGER NOT NULL, quote_budget VARCHAR NOT NULL, "
            "quote_cash VARCHAR NOT NULL, base_position VARCHAR NOT NULL, "
            "cost_basis VARCHAR NOT NULL, realized_pnl VARCHAR NOT NULL, "
            "updated_at INTEGER NOT NULL, "
            "PRIMARY KEY (scope, strategy, environment))"
        ))
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS funded_spot_order ("
            "order_id VARCHAR PRIMARY KEY, scope VARCHAR NOT NULL, "
            "strategy VARCHAR NOT NULL, environment VARCHAR NOT NULL, "
            "symbol VARCHAR NOT NULL, side VARCHAR NOT NULL, "
            "quote_reserved VARCHAR NOT NULL, base_reserved VARCHAR NOT NULL, "
            "filled_base VARCHAR NOT NULL, status VARCHAR NOT NULL, "
            "created_at INTEGER NOT NULL)"
        ))


def activate_funded_allocation(
    repository,
    *,
    scope_id: str,
    strategy: str,
    symbol: str,
    budget: Decimal,
    environment: str,
    revision: int,
    facts: TrustedActivationFacts,
    now_ms: int,
    market_type: str,
    max_age_ms: int = 30_000,
) -> StrategyBook:
    """校验新鲜预检后，在一个事务里写入独占现货资金。"""
    if (
        str(market_type).strip().lower() != "spot"
        or str(facts.market_type).strip().lower() != "spot"
    ):
        raise FundingError(
            "derivative_activation_closed",
            "Derivatives need their own account and risk design before activation",
        )
    if environment not in {"sandbox", "live"}:
        raise FundingError(
            "ownership_environment_invalid",
            "Ownership can only be recorded for sandbox or live",
        )
    if (
        not isinstance(facts, TrustedActivationFacts)
        or facts.preflight_passed is not True
        or not isinstance(facts.available_quote, Decimal)
        or not facts.available_quote.is_finite()
        or facts.available_quote < 0
    ):
        raise FundingError(
            "preflight_not_trusted",
            "Fresh spot preflight did not pass",
        )
    if facts.account_scope_id != scope_id:
        raise FundingError(
            "account_scope_changed",
            "Exchange facts belong to another account",
        )
    if (
        type(now_ms) is not int
        or type(facts.observed_at) is not int
        or type(max_age_ms) is not int
        or now_ms < 0
        or facts.observed_at < 0
        or max_age_ms < 0
        or now_ms - facts.observed_at > max_age_ms
        or facts.observed_at - now_ms > 5_000
    ):
        raise FundingError(
            "stale_exchange_facts",
            "Exchange facts are missing or stale",
        )
    if not isinstance(budget, Decimal) or not budget.is_finite() or budget <= 0:
        raise FundingError("balance_insufficient", "Draft budget must be positive")
    if facts.available_quote < budget:
        raise FundingError(
            "balance_insufficient",
            "Exchange quote balance does not cover the draft budget",
        )
    try:
        normalized = canonical_symbol(symbol, market_type="spot")
    except ValueError as exc:
        raise FundingError("invalid_symbol", "A spot symbol is required") from exc
    if not is_supported_canonical_symbol(normalized, market_type="spot"):
        raise FundingError("invalid_symbol", "A spot symbol is required")
    ensure_funded_tables(repository.engine)
    with repository.engine.begin() as connection:
        existing = _book_row(connection, scope_id, strategy, environment)
        if existing is not None:
            if existing["symbol"] != normalized or _decimal(existing["quote_budget"]) != budget:
                raise FundingError(
                    "funded_allocation_locked",
                    "Release the funded allocation before changing it",
                )
            if int(existing["revision"]) != int(revision):
                connection.execute(text(
                    "UPDATE funded_spot_book SET revision=:revision, updated_at=:updated "
                    "WHERE scope=:scope AND strategy=:strategy AND environment=:environment"
                ), {
                    "revision": int(revision),
                    "updated": int(now_ms),
                    "scope": scope_id,
                    "strategy": strategy,
                    "environment": environment,
                })
            _upsert_ownership(
                connection, scope_id, strategy, normalized, environment, budget,
                facts.available_quote, now_ms,
            )
        else:
            owner = connection.execute(text(
                "SELECT strategy FROM funded_spot_book "
                "WHERE scope=:scope AND environment=:environment AND symbol=:symbol"
            ), {
                "scope": scope_id,
                "environment": environment,
                "symbol": normalized,
            }).first()
            if owner is not None:
                raise FundingError(
                    "position_overlap",
                    "Another strategy already owns this spot position",
                )
            others = connection.execute(text(
                "SELECT quote_budget FROM funded_spot_book "
                "WHERE scope=:scope AND environment=:environment AND strategy!=:strategy"
            ), {
                "scope": scope_id,
                "environment": environment,
                "strategy": strategy,
            }).fetchall()
            already = sum((_decimal(row[0]) for row in others), _ZERO)
            if already + budget > facts.available_quote:
                raise FundingError(
                    "budget_overlap",
                    "Existing strategy budgets already use this exchange balance",
                )
            connection.execute(text(
                "INSERT INTO funded_spot_book("
                "scope, strategy, environment, symbol, revision, quote_budget, "
                "quote_cash, base_position, cost_basis, realized_pnl, updated_at) "
                "VALUES (:scope, :strategy, :environment, :symbol, :revision, :budget, "
                ":cash, :base, :cost, :pnl, :updated)"
            ), {
                "scope": scope_id,
                "strategy": strategy,
                "environment": environment,
                "symbol": normalized,
                "revision": int(revision),
                "budget": _text(budget),
                "cash": _text(budget),
                "base": _text(_ZERO),
                "cost": _text(_ZERO),
                "pnl": _text(_ZERO),
                "updated": int(now_ms),
            })
            _upsert_ownership(
                connection, scope_id, strategy, normalized, environment, budget,
                facts.available_quote, now_ms,
            )
    book = strategy_book(repository, scope_id, strategy, environment)
    if book is None:
        raise FundingError("preflight_not_trusted", "Funded book was not recorded")
    return book


def strategy_book(
    repository, scope_id: str, strategy: str, environment: str,
) -> StrategyBook | None:
    ensure_funded_tables(repository.engine)
    with repository.engine.connect() as connection:
        row = _book_row(connection, scope_id, strategy, environment)
        if row is None:
            return None
        reserved_quote, reserved_base = _reserved(connection, scope_id, strategy, environment)
        return StrategyBook(
            scope_id=scope_id,
            strategy=strategy,
            environment=environment,
            symbol=str(row["symbol"]),
            revision=int(row["revision"]),
            cash=_decimal(row["quote_cash"]),
            base_position=_decimal(row["base_position"]),
            realized_pnl=_decimal(row["realized_pnl"]),
            reserved_quote=reserved_quote,
            reserved_base=reserved_base,
        )


def funded_book_payload(
    repository, scope_id: str | None, strategy: str, environment: str,
) -> dict[str, object] | None:
    if not scope_id or environment not in {"sandbox", "live"}:
        return None
    book = strategy_book(repository, scope_id, strategy, environment)
    if book is None:
        return None
    return {
        "strategy": book.strategy,
        "symbol": book.symbol,
        "environment": book.environment,
        "scope_id": book.scope_id,
        "cash": _text(book.cash),
        "base_position": _text(book.base_position),
        "realized_pnl": _text(book.realized_pnl),
        "reserved_quote": _text(book.reserved_quote),
        "reserved_base": _text(book.reserved_base),
        "revision": book.revision,
        "live_open_orders": False,
    }


def assert_funded_draft_editable(
    repository,
    *,
    scope_id: str,
    strategy: str,
    environment: str,
    symbol: str,
    budget: Decimal,
    deleting: bool = False,
) -> None:
    if environment not in {"sandbox", "live"}:
        return
    ensure_funded_tables(repository.engine)
    try:
        normalized = canonical_symbol(symbol, market_type="spot")
    except ValueError as exc:
        raise FundingError("invalid_symbol", "A spot symbol is required") from exc
    with repository.engine.connect() as connection:
        row = _book_row(connection, scope_id, strategy, environment)
    if row is None:
        return
    if (
        deleting
        or str(row["symbol"]) != normalized
        or _decimal(row["quote_budget"]) != budget
    ):
        raise FundingError(
            "funded_allocation_locked",
            "Release the funded allocation before editing the draft",
        )


def capacity_block(
    repository,
    *,
    scope_id: str | None,
    strategy: str,
    environment: str,
    symbol: str,
    side: str,
    amount: Decimal,
    price: Decimal,
) -> str | None:
    """返回拒绝原因。没有拨款时返回 None，让原有下单路径继续。"""
    if environment not in {"sandbox", "live"}:
        return None
    ensure_funded_tables(repository.engine)
    if not scope_id:
        with repository.engine.connect() as connection:
            found = connection.execute(text(
                "SELECT 1 FROM funded_spot_book "
                "WHERE strategy=:strategy AND environment=:environment"
            ), {"strategy": strategy, "environment": environment}).first()
        return "Funded allocation scope is unavailable" if found is not None else None
    try:
        normalized = canonical_symbol(symbol, market_type="spot")
    except ValueError:
        return "Funded allocation does not include this symbol"
    if side not in {"buy", "sell"} or amount <= 0 or price <= 0:
        return "Funded allocation requires a positive price"
    with repository.engine.connect() as connection:
        row = _book_row(connection, scope_id, strategy, environment)
        if row is None:
            return None
        if str(row["symbol"]) != normalized:
            return "Funded allocation does not include this symbol"
        reserved_quote, reserved_base = _reserved(connection, scope_id, strategy, environment)
        if side == "buy" and _decimal(row["quote_cash"]) - reserved_quote < amount * price:
            return "Funded quote budget exceeded"
        if side == "sell" and _decimal(row["base_position"]) - reserved_base < amount:
            return "Funded base budget exceeded"
    return None


def reserve_funded_order(
    repository,
    *,
    scope_id: str | None,
    strategy: str,
    environment: str,
    symbol: str,
    order_id: str,
    side: str,
    amount: Decimal,
    price: Decimal,
    now_ms: int = 0,
) -> str:
    """没有拨款时返回 absent。成功预留返回 reserved。其余字符串是拒绝原因。"""
    if environment not in {"sandbox", "live"}:
        return "absent"
    ensure_funded_tables(repository.engine)
    if not scope_id:
        with repository.engine.connect() as connection:
            found = connection.execute(text(
                "SELECT 1 FROM funded_spot_book "
                "WHERE strategy=:strategy AND environment=:environment"
            ), {"strategy": strategy, "environment": environment}).first()
        return "Funded allocation scope is unavailable" if found is not None else "absent"
    try:
        normalized = canonical_symbol(symbol, market_type="spot")
    except ValueError:
        return "Funded allocation does not include this symbol"
    if side not in {"buy", "sell"} or amount <= 0 or price <= 0:
        return "Funded allocation requires a positive price"
    quote_need = amount * price if side == "buy" else _ZERO
    base_need = amount if side == "sell" else _ZERO
    with repository.engine.begin() as connection:
        row = _book_row(connection, scope_id, strategy, environment)
        if row is None:
            return "absent"
        if str(row["symbol"]) != normalized:
            return "Funded allocation does not include this symbol"
        existing = _order_row(connection, order_id)
        if existing is not None:
            same = (
                existing["strategy"] == strategy
                and existing["status"] == _OPEN
                and existing["side"] == side
            )
            return "reserved" if same else "Funded order identity already exists"
        reserved_quote, reserved_base = _reserved(connection, scope_id, strategy, environment)
        cash = _decimal(row["quote_cash"])
        base = _decimal(row["base_position"])
        if side == "buy" and cash - reserved_quote < quote_need:
            return "Funded quote budget exceeded"
        if side == "sell" and base - reserved_base < base_need:
            return "Funded base budget exceeded"
        connection.execute(text(
            "INSERT INTO funded_spot_order("
            "order_id, scope, strategy, environment, symbol, side, quote_reserved, "
            "base_reserved, filled_base, status, created_at) "
            "VALUES (:order_id, :scope, :strategy, :environment, :symbol, :side, "
            ":quote_reserved, :base_reserved, :filled, :status, :created)"
        ), {
            "order_id": order_id,
            "scope": scope_id,
            "strategy": strategy,
            "environment": environment,
            "symbol": normalized,
            "side": side,
            "quote_reserved": _text(quote_need),
            "base_reserved": _text(base_need),
            "filled": _text(_ZERO),
            "status": _OPEN,
            "created": int(now_ms),
        })
    return "reserved"


def release_funded_order(repository, order_id: str) -> None:
    settle_funded_order(
        repository,
        order_id,
        status="cancelled",
        filled_amount=_ZERO,
        fill_price=_ZERO,
    )


def settle_funded_order(
    repository,
    order_id: str,
    *,
    status: str,
    filled_amount: Decimal,
    fill_price: Decimal,
) -> None:
    ensure_funded_tables(repository.engine)
    normalized_status = str(status or "").strip().lower()
    with repository.engine.begin() as connection:
        order = _order_row(connection, order_id)
        if order is None or order["status"] != _OPEN:
            return
        book = _book_row(connection, order["scope"], order["strategy"], order["environment"])
        if book is None:
            return
        filled_before = _decimal(order["filled_base"])
        target = filled_amount if filled_amount > filled_before else filled_before
        delta = target - filled_before
        cash = _decimal(book["quote_cash"])
        base = _decimal(book["base_position"])
        cost = _decimal(book["cost_basis"])
        pnl = _decimal(book["realized_pnl"])
        if delta > 0:
            if fill_price <= 0:
                raise FundingError(
                    "invalid_fill",
                    "Funded fill requires a positive price",
                )
            with localcontext(_CONTEXT):
                if order["side"] == "buy":
                    spent = fill_price * delta
                    cash -= spent
                    base += delta
                    cost += spent
                else:
                    if base < delta:
                        raise FundingError(
                            "funded_base_exceeded",
                            "Funded base budget exceeded",
                        )
                    proceeds = fill_price * delta
                    removed = cost * delta / base if base != 0 else _ZERO
                    pnl += proceeds - removed
                    cash += proceeds
                    base -= delta
                    cost -= removed
        terminal = normalized_status in _TERMINAL
        if delta == 0 and not terminal:
            return
        connection.execute(text(
            "UPDATE funded_spot_book SET quote_cash=:cash, base_position=:base, "
            "cost_basis=:cost, realized_pnl=:pnl, updated_at=:updated "
            "WHERE scope=:scope AND strategy=:strategy AND environment=:environment"
        ), {
            "cash": _text(cash),
            "base": _text(base),
            "cost": _text(cost),
            "pnl": _text(pnl),
            "updated": int(book["updated_at"]),
            "scope": order["scope"],
            "strategy": order["strategy"],
            "environment": order["environment"],
        })
        connection.execute(text(
            "UPDATE funded_spot_order SET filled_base=:filled, quote_reserved=:quote, "
            "base_reserved=:base, status=:status WHERE order_id=:order_id"
        ), {
            "filled": _text(target),
            "quote": _text(_ZERO if terminal else _decimal(order["quote_reserved"])),
            "base": _text(_ZERO if terminal else _decimal(order["base_reserved"])),
            "status": "settled" if terminal else _OPEN,
            "order_id": order_id,
        })


def _upsert_ownership(
    connection,
    scope_id: str,
    strategy: str,
    symbol: str,
    environment: str,
    budget: Decimal,
    available: Decimal,
    now_ms: int,
) -> None:
    connection.execute(text(
        "INSERT INTO strategy_ownership("
        "scope, strategy, symbol, environment, quote_budget, quote_available, updated_at) "
        "VALUES (:scope, :strategy, :symbol, :environment, :budget, :available, :updated) "
        "ON CONFLICT(scope, strategy) DO UPDATE SET "
        "symbol=excluded.symbol, environment=excluded.environment, "
        "quote_budget=excluded.quote_budget, quote_available=excluded.quote_available, "
        "updated_at=excluded.updated_at"
    ), {
        "scope": scope_id,
        "strategy": strategy,
        "symbol": symbol,
        "environment": environment,
        "budget": float(budget),
        "available": float(available),
        "updated": int(now_ms),
    })


def _book_row(connection, scope_id: str, strategy: str, environment: str):
    row = connection.execute(text(
        "SELECT * FROM funded_spot_book "
        "WHERE scope=:scope AND strategy=:strategy AND environment=:environment"
    ), {"scope": scope_id, "strategy": strategy, "environment": environment}).first()
    return None if row is None else row._mapping


def _order_row(connection, order_id: str):
    row = connection.execute(text(
        "SELECT * FROM funded_spot_order WHERE order_id=:order_id"
    ), {"order_id": order_id}).first()
    return None if row is None else row._mapping


def _reserved(
    connection, scope_id: str, strategy: str, environment: str,
) -> tuple[Decimal, Decimal]:
    rows = connection.execute(text(
        "SELECT quote_reserved, base_reserved FROM funded_spot_order "
        "WHERE scope=:scope AND strategy=:strategy AND environment=:environment "
        "AND status=:status"
    ), {
        "scope": scope_id,
        "strategy": strategy,
        "environment": environment,
        "status": _OPEN,
    }).fetchall()
    return (
        sum((_decimal(row[0]) for row in rows), _ZERO),
        sum((_decimal(row[1]) for row in rows), _ZERO),
    )


def _decimal(value: object) -> Decimal:
    number = Decimal(str(value))
    if not number.is_finite():
        raise FundingError("invalid_book", "Funded book amount is not finite")
    return number


def _text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"
