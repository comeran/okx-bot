from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any

from ccxt.base.errors import (
    AuthenticationError,
    DDoSProtection,
    ExchangeNotAvailable,
    NetworkError,
    NotSupported,
    PermissionDenied,
    RateLimitExceeded,
    RequestTimeout,
)
from fastapi import APIRouter, HTTPException

from src.analytics.strategy_performance import build_strategy_performance
from src.core.runtime_settings import load_runtime_settings
from src.data.models import EXCHANGE_STRATEGY, AccountRecord, OrderRecord
from src.data.repository import Repository
from src.exchange.base import canonical_symbol, is_supported_canonical_symbol
from src.exchange.errors import LiveConfigurationError, MalformedProviderData
from src.exchange.live_sync import refresh_okx_live_state
from src.order.value import position_notional

router = APIRouter()

PAPER_ACCOUNT_KEYS = (
    "cash_balance",
    "available_balance",
    "equity",
    "realized_pnl",
    "unrealized_pnl",
    "daily_pnl",
    "fees_paid",
)
ZERO_PAPER_ACCOUNT: dict[str, Any] = {key: 0.0 for key in PAPER_ACCOUNT_KEYS}
ZERO_PAPER_ACCOUNT["assets"] = []
ORDER_PUBLIC_FIELDS = (
    "id",
    "order_id",
    "strategy",
    "symbol",
    "side",
    "type",
    "amount",
    "price",
    "status",
    "fill_price",
    "timestamp",
    "filled_amount",
    "remaining_amount",
)
TRADE_PUBLIC_FIELDS = (
    "id",
    "order_id",
    "strategy",
    "symbol",
    "side",
    "amount",
    "price",
    "fee",
    "timestamp",
)


def current_timestamp_ms() -> int:
    return int(time.time() * 1000)


def record_value(record: object, key: str, default: float = 0.0) -> float:
    if isinstance(record, dict):
        value = record.get(key, default)
    elif hasattr(record, key):
        value = getattr(record, key)
    elif hasattr(record, "model_dump"):
        value = record.model_dump().get(key, default)
    else:
        value = default
    return value if isinstance(value, int | float) else default


def record_timestamp(record: object) -> int:
    return int(record_value(record, "timestamp", 0.0))


def record_strategy(record: object) -> str:
    value = serialize_record(record).get("strategy", "")
    return value if isinstance(value, str) else ""


def parse_trading_scope(
    scope: str | None,
    strategy: str | None,
    *,
    invalid_scope_detail: str,
) -> tuple[str, str | None]:
    resolved_scope = scope or "aggregate"
    if resolved_scope not in {"aggregate", "exchange", "strategy"}:
        raise HTTPException(status_code=422, detail=invalid_scope_detail)
    if resolved_scope == "exchange" and strategy is not None:
        raise HTTPException(status_code=422, detail="strategy is not valid for exchange scope")
    if resolved_scope == "strategy" and strategy is None:
        raise HTTPException(status_code=422, detail="strategy is required for strategy scope")
    if strategy is not None and resolved_scope == "aggregate":
        resolved_scope = "strategy"
    if resolved_scope == "strategy" and strategy == EXCHANGE_STRATEGY:
        raise HTTPException(
            status_code=422,
            detail="__exchange__ requires exchange scope",
        )
    return resolved_scope, strategy


def _repository_has_strategy_data(repository, strategy: str) -> bool:
    for method_name in (
        "get_account",
        "get_open_positions",
        "get_positions",
        "get_orders",
        "get_trades",
    ):
        method = getattr(repository, method_name, None)
        if method is None:
            continue
        for args, kwargs in (((strategy,), {}), ((), {"strategy": strategy}), ((), {})):
            try:
                rows = method(*args, **kwargs)
            except (AttributeError, TypeError):
                continue
            candidates = rows if isinstance(rows, list | tuple) else [rows]
            if any(record_strategy(row) == strategy for row in candidates if row is not None):
                return True
            break
    return False


def validate_strategy_scope(
    repository,
    scope: str,
    strategy: str | None,
    records=None,
) -> None:
    if scope != "strategy" or strategy is None:
        return
    from src.web.api.strategies import create_strategy_registry

    registry = create_strategy_registry()
    if strategy in registry.list_strategies():
        return
    get_strategy_config = getattr(repository, "get_strategy_config", None)
    if get_strategy_config is not None:
        try:
            if get_strategy_config(strategy) is not None:
                return
        except (AttributeError, TypeError):
            pass
    get_strategy_configs = getattr(repository, "get_strategy_configs", None)
    if get_strategy_configs is not None:
        try:
            if any(getattr(config, "name", None) == strategy for config in get_strategy_configs()):
                return
        except (AttributeError, TypeError):
            pass
    if records is not None:
        candidates = records if isinstance(records, list | tuple) else [records]
        if any(record_strategy(record) == strategy for record in candidates if record is not None):
            return
        if _repository_has_strategy_data(repository, strategy):
            return
    raise HTTPException(status_code=404, detail="Strategy not found")


def resolve_runtime_scope(runtime_mode: str | None) -> tuple[str, str]:
    configured_mode = load_runtime_settings().mode.strip().lower()
    resolved = runtime_mode or {
        "demo": "paper",
        "paper": "paper",
        "live": "live",
        "backtest": "backtest",
    }.get(configured_mode, configured_mode)
    if resolved not in {"paper", "live", "backtest"}:
        raise HTTPException(status_code=422, detail="Invalid runtime_mode")
    source = "live_exchange" if resolved == "live" else resolved
    return source, resolved


def repository_read(
    repository,
    method_name: str,
    *args,
    strict: bool = False,
    fallback=None,
    **kwargs,
):
    method = getattr(repository, method_name)
    try:
        return method(*args, **kwargs)
    except TypeError as exc:
        if "unexpected keyword argument" not in str(exc):
            raise
        if strict:
            return fallback
        return method(*args)


def serialize_record(record: object) -> dict[str, Any]:
    if hasattr(record, "model_dump"):
        return record.model_dump()
    if isinstance(record, dict):
        return record
    return vars(record)


def is_live_exchange_record(record: object, *, allow_legacy: bool = False) -> bool:
    if getattr(record, "strategy", None) != EXCHANGE_STRATEGY:
        return False
    source = str(getattr(record, "source", "unknown") or "unknown").strip().lower()
    runtime_mode = str(
        getattr(record, "runtime_mode", "unknown") or "unknown"
    ).strip().lower()
    if (source, runtime_mode) == ("live_exchange", "live"):
        return True
    return allow_legacy and source in {"", "unknown", "legacy"} and runtime_mode in {
        "", "unknown", "legacy"
    }


def filter_runtime_rows(rows, source: str, runtime_mode: str):
    filtered = []
    for row in rows:
        values = serialize_record(row)
        active = getattr(row, "active", values.get("active", True))
        row_source = getattr(row, "source", values.get("source", "unknown"))
        row_runtime_mode = getattr(
            row, "runtime_mode", values.get("runtime_mode", "unknown")
        )
        if active is False:
            continue
        if str(row_source or "unknown").strip().lower() != source:
            continue
        if str(row_runtime_mode or "unknown").strip().lower() != runtime_mode:
            continue
        filtered.append(row)
    return filtered


def exchange_rows(repository, method_name: str, strategy: str | None = None):
    rows = (
        repository_read(
            repository,
            method_name,
            strategy,
            source="live_exchange",
            runtime_mode="live",
            strict=True,
            fallback=[],
        )
        if strategy is not None
        else repository_read(
            repository,
            method_name,
            source="live_exchange",
            runtime_mode="live",
            strict=True,
            fallback=[],
        )
    )
    return [row for row in rows if is_live_exchange_record(row, allow_legacy=False)]


def exchange_account(repository):
    account = repository_read(
        repository,
        "get_account",
        EXCHANGE_STRATEGY,
        source="live_exchange",
        runtime_mode="live",
        strict=True,
        fallback=None,
    )
    return account if account is not None and is_live_exchange_record(
        account, allow_legacy=False
    ) else None


def serialize_order_record(record: OrderRecord) -> dict[str, Any]:
    values = record.model_dump()
    return {
        field: values.get(field, getattr(record, field, 0.0))
        for field in ORDER_PUBLIC_FIELDS
    }


def serialize_trade_record(record: object) -> dict[str, Any]:
    values = serialize_record(record)
    return {field: values[field] for field in TRADE_PUBLIC_FIELDS if field in values}


def serialize_records(records: list[object]) -> list[dict[str, Any]]:
    public_records: list[dict[str, Any]] = []
    for record in records:
        values = dict(serialize_record(record))
        if (
            getattr(record, "notional_value", None) is not None
            or getattr(record, "contracts", None) is not None
            or getattr(record, "is_inverse", False)
        ):
            for field in (
                "contracts", "quantity", "quantity_unit", "notional_value",
                "is_inverse", "contract_size",
            ):
                if hasattr(record, field):
                    values[field] = getattr(record, field)
            if getattr(record, "is_inverse", False) and values.get("notional_value") is None:
                values["notional_value"] = position_notional(record)
        values.pop("source", None)
        values.pop("runtime_mode", None)
        public_records.append(values)
    return public_records


def account_mapping(account: object) -> dict[str, Any]:
    if hasattr(account, "model_dump"):
        return account.model_dump()
    if isinstance(account, dict):
        return account
    keys = (*PAPER_ACCOUNT_KEYS, "assets")
    return {key: getattr(account, key) for key in keys if hasattr(account, key)}


def serialize_account(
    account: object | None,
    *,
    scope: str = "aggregate",
    strategy: str | None = None,
) -> dict[str, Any]:
    if account is None:
        response = dict(ZERO_PAPER_ACCOUNT)
        response["scope"] = scope
        if strategy is not None:
            response["strategy"] = strategy
        return response

    values = account_mapping(account)
    response: dict[str, Any] = {
        key: float(values.get(key, 0.0)) if isinstance(values.get(key, 0.0), int | float) else 0.0
        for key in PAPER_ACCOUNT_KEYS
    }
    assets = values.get("assets", [])
    public_asset_fields = ("ccy", "cash_bal", "eq", "eq_utd", "avail_bal", "upl")
    response["assets"] = [
        {
            field: asset[field]
            for field in public_asset_fields
            if field in asset
        }
        for asset in assets
        if isinstance(asset, dict)
    ] if isinstance(assets, list) else []
    response["scope"] = scope
    if strategy is not None:
        response["strategy"] = strategy
    return response


@router.post("/live-state/refresh")
async def refresh_live_state(strategy: str, symbol: str | None = None) -> dict[str, Any]:
    settings = load_runtime_settings()
    if settings.mode.strip().lower() != "live":
        raise HTTPException(status_code=400, detail="Live state refresh requires live mode")
    if strategy != "__exchange__":
        raise HTTPException(
            status_code=400,
            detail="Live account and positions belong to __exchange__",
        )
    scoped_symbols: list[str] | None = None
    if symbol is not None:
        if not symbol.strip():
            raise HTTPException(status_code=422, detail="symbol must not be empty")
        canonical = canonical_symbol(
            symbol,
            market_type=settings.exchange.market_type,
        )
        if not is_supported_canonical_symbol(
            canonical, market_type=settings.exchange.market_type
        ):
            raise HTTPException(status_code=422, detail="Invalid symbol")
        scoped_symbols = [canonical]
    repository = Repository()
    try:
        result = await refresh_okx_live_state(
            settings.exchange,
            repository,
            strategy,
            scoped_symbols,
            current_timestamp_ms,
        )
    except LiveConfigurationError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid live OKX configuration: {exc}",
        ) from exc
    except MalformedProviderData as exc:
        raise HTTPException(status_code=502, detail="Malformed OKX provider data") from exc
    except NotSupported as exc:
        raise HTTPException(
            status_code=501, detail="OKX provider capability unsupported"
        ) from exc
    except PermissionDenied as exc:
        raise HTTPException(status_code=502, detail="OKX permission denied") from exc
    except AuthenticationError as exc:
        raise HTTPException(status_code=502, detail="OKX authentication failed") from exc
    except (
        NetworkError,
        RequestTimeout,
        ExchangeNotAvailable,
        DDoSProtection,
        RateLimitExceeded,
        TimeoutError,
        ConnectionError,
    ) as exc:
        raise HTTPException(status_code=502, detail="OKX provider unavailable") from exc
    return {
        "account": serialize_account(
            result.account, scope="exchange", strategy=EXCHANGE_STRATEGY
        ),
        "positions": serialize_records(result.positions),
    }


@router.get("/positions")
async def get_positions(
    strategy: str | None = None,
    scope: str = "aggregate",
    runtime_mode: str | None = None,
) -> list[dict[str, Any]]:
    scope, strategy = parse_trading_scope(
        scope,
        strategy,
        invalid_scope_detail="Invalid position scope",
    )

    source, runtime_mode = resolve_runtime_scope(runtime_mode)
    repository = Repository()
    if scope == "strategy" and not (
        hasattr(repository, "get_open_positions") or hasattr(repository, "get_positions")
    ):
        validate_strategy_scope(repository, scope, strategy, [])
    selected_strategy = EXCHANGE_STRATEGY if scope == "exchange" else strategy
    if scope == "exchange":
        positions = exchange_rows(
            repository,
            "get_open_positions" if hasattr(repository, "get_open_positions") else "get_positions",
            EXCHANGE_STRATEGY,
        )
        positions = [position for position in positions if record_value(position, "amount") != 0]
    elif hasattr(repository, "get_open_positions"):
        positions = repository_read(
            repository,
            "get_open_positions",
            selected_strategy,
            source=source,
            runtime_mode=runtime_mode,
            strict=scope == "strategy",
            fallback=[],
        )
    else:
        positions = [
            position
            for position in repository_read(
                repository,
                "get_positions",
                selected_strategy,
                source=source,
                runtime_mode=runtime_mode,
                strict=scope == "strategy",
                fallback=[],
            )
            if record_value(position, "amount") != 0
        ]
    validate_strategy_scope(repository, scope, strategy, positions)
    if scope != "exchange":
        positions = filter_runtime_rows(positions, source, runtime_mode)
    if scope == "exchange":
        positions = [
            position for position in positions
            if record_strategy(position) == EXCHANGE_STRATEGY
        ]
    else:
        positions = [
            position for position in positions
            if record_strategy(position) != EXCHANGE_STRATEGY
            and (strategy is None or record_strategy(position) == strategy)
        ]

    positions = sorted(positions, key=record_timestamp, reverse=True)
    return serialize_records(positions)


@router.get("/orders")
async def get_orders(
    strategy: str | None = None,
    scope: str = "aggregate",
    runtime_mode: str | None = None,
) -> list[dict[str, Any]]:
    scope, strategy = parse_trading_scope(
        scope,
        strategy,
        invalid_scope_detail="Invalid order scope",
    )

    source, runtime_mode = resolve_runtime_scope(runtime_mode)
    repository = Repository()
    if scope == "strategy" and not hasattr(repository, "get_orders"):
        validate_strategy_scope(repository, scope, strategy, [])
    orders = (
        exchange_rows(repository, "get_orders")
        if scope == "exchange"
        else repository_read(
            repository,
            "get_orders",
            source=source,
            runtime_mode=runtime_mode,
            strict=scope == "strategy",
            fallback=[],
        )
    )
    validate_strategy_scope(repository, scope, strategy, orders)
    if scope == "exchange":
        orders = [order for order in orders if record_strategy(order) == EXCHANGE_STRATEGY]
    else:
        orders = filter_runtime_rows(orders, source, runtime_mode)
    if scope != "exchange":
        orders = [
            order for order in orders
            if record_strategy(order) != EXCHANGE_STRATEGY
            and (strategy is None or record_strategy(order) == strategy)
        ]
    orders = sorted(orders, key=record_timestamp, reverse=True)
    return [serialize_order_record(order) for order in orders]


@router.get("/trades")
async def get_trades(
    strategy: str | None = None,
    scope: str = "aggregate",
    runtime_mode: str | None = None,
) -> list[dict[str, Any]]:
    scope, strategy = parse_trading_scope(
        scope,
        strategy,
        invalid_scope_detail="Invalid trade scope",
    )

    source, runtime_mode = resolve_runtime_scope(runtime_mode)
    repository = Repository()
    if scope == "strategy" and not hasattr(repository, "get_trades"):
        validate_strategy_scope(repository, scope, strategy, [])
    selected_strategy = EXCHANGE_STRATEGY if scope == "exchange" else strategy
    trades = (
        exchange_rows(repository, "get_trades", EXCHANGE_STRATEGY)
        if scope == "exchange"
        else repository_read(
            repository,
            "get_trades",
            selected_strategy,
            source=source,
            runtime_mode=runtime_mode,
            strict=scope == "strategy",
            fallback=[],
        )
    )
    validate_strategy_scope(repository, scope, strategy, trades)
    if scope == "exchange":
        trades = [trade for trade in trades if record_strategy(trade) == EXCHANGE_STRATEGY]
    else:
        trades = filter_runtime_rows(trades, source, runtime_mode)
        trades = [
            trade for trade in trades
            if record_strategy(trade) != EXCHANGE_STRATEGY
            and (strategy is None or record_strategy(trade) == strategy)
        ]
    trades = sorted(trades, key=record_timestamp, reverse=True)
    return [serialize_trade_record(trade) for trade in trades]


@router.get("/strategy-performance")
async def get_strategy_performance(
    runtime_mode: str | None = None,
) -> list[dict[str, Any]]:
    if runtime_mode is not None:
        runtime_mode = runtime_mode.strip().lower()
        if runtime_mode not in {"demo", "paper", "live", "backtest"}:
            raise HTTPException(status_code=422, detail="Invalid performance runtime_mode")
    if runtime_mode is None:
        configured_mode = load_runtime_settings().mode.strip().lower()
        runtime_mode = {
            "demo": "paper",
            "paper": "paper",
            "live": "live",
            "backtest": "backtest",
        }.get(configured_mode)
    if runtime_mode is None:
        raise HTTPException(
            status_code=422,
            detail="Strategy performance requires a single paper or live runtime_mode",
        )
    runtime_modes = {runtime_mode}

    repository = Repository()
    performances = build_strategy_performance(
        repository.get_accounts(),
        repository.get_positions(),
        repository.get_orders(),
        repository.get_trades(),
        runtime_modes=runtime_modes,
    )
    return [asdict(performance) for performance in performances]


def _aggregate_strategy_accounts(accounts: list[AccountRecord]) -> AccountRecord | None:
    strategy_accounts = [
        account for account in accounts if account.strategy != EXCHANGE_STRATEGY
    ]
    if not strategy_accounts:
        return None
    return AccountRecord(
        strategy="",
        initial_equity=sum(account.initial_equity for account in strategy_accounts),
        cash_balance=sum(account.cash_balance for account in strategy_accounts),
        available_balance=sum(account.available_balance for account in strategy_accounts),
        equity=sum(account.equity for account in strategy_accounts),
        realized_pnl=sum(account.realized_pnl for account in strategy_accounts),
        unrealized_pnl=sum(account.unrealized_pnl for account in strategy_accounts),
        daily_pnl=sum(account.daily_pnl for account in strategy_accounts),
        fees_paid=sum(account.fees_paid for account in strategy_accounts),
        updated_at=max(account.updated_at for account in strategy_accounts),
    )


@router.get("/account")
async def get_account(
    strategy: str | None = None,
    scope: str | None = None,
    runtime_mode: str | None = None,
) -> dict[str, Any]:
    source, runtime_mode = resolve_runtime_scope(runtime_mode)
    repository = Repository()
    scope, strategy = parse_trading_scope(
        scope,
        strategy,
        invalid_scope_detail="Invalid account scope",
    )
    if scope == "exchange":
        account = exchange_account(repository)
        return serialize_account(account, scope="exchange", strategy=EXCHANGE_STRATEGY)
    if scope == "strategy":
        if not hasattr(repository, "get_account"):
            validate_strategy_scope(repository, scope, strategy, [])
        account = repository_read(
            repository,
            "get_account",
            strategy,
            source=source,
            runtime_mode=runtime_mode,
            strict=True,
            fallback=None,
        )
        if account is not None:
            account_rows = filter_runtime_rows([account], source, runtime_mode)
            account = account_rows[0] if account_rows else None
        validate_strategy_scope(
            repository,
            scope,
            strategy,
            [account] if account is not None else [],
        )
        return serialize_account(account, scope="strategy", strategy=strategy)
    if hasattr(repository, "get_accounts"):
        account = _aggregate_strategy_accounts(
            filter_runtime_rows(
                repository_read(
                    repository,
                    "get_accounts",
                    source=source,
                    runtime_mode=runtime_mode,
                ),
                source,
                runtime_mode,
            )
        )
    elif hasattr(repository, "get_account"):
        account = repository_read(
            repository,
            "get_account",
            source=source,
            runtime_mode=runtime_mode,
        )
        if account is not None:
            account_rows = filter_runtime_rows([account], source, runtime_mode)
            account = account_rows[0] if account_rows else None
    else:
        account = None
    return serialize_account(account, scope="aggregate")
