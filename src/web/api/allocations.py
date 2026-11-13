"""Configure allocation drafts and local paper reservations without granting live ownership."""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from src.core.execution_mode import account_environment
from src.core.runtime_settings import load_runtime_settings
from src.data.allocations import (
    AllocationConflict,
    AllocationNotFound,
    AllocationScope,
    AllocationStore,
    spot_symbol,
)
from src.data.live_ownership import OwnershipError, grant_ownership_from_balance
from src.data.paper_book import PaperBook, PaperBookError, PaperBookNotFound
from src.data.repository import Repository
from src.strategy.definitions import StrategyConfigValidationError
from src.web.api.strategies import StrategyRuntimeState


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    budget: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
    expected_revision: Annotated[int, Field(strict=True, ge=0)]
    expected_scope_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    expected_symbol: Annotated[str, Field(strict=True, min_length=1, max_length=128)]


class ReservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: Annotated[int, Field(strict=True, ge=0)]
    expected_scope_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    expected_symbol: Annotated[str, Field(strict=True, min_length=1, max_length=128)]


def allocation_context() -> tuple[dict[str, object], AllocationScope | None]:
    settings = load_runtime_settings()
    exchange = settings.exchange
    environment = account_environment(settings.mode, exchange.demo)
    market_type = exchange.market_type.strip().lower()
    configured = all(
        isinstance(value, str) and value.strip()
        for value in (exchange.api_key, exchange.secret, exchange.passphrase)
    )
    reason = (
        "credentials_unavailable" if not configured
        else "unsupported_market" if market_type != "spot"
        else None
    )
    scope = None
    if reason is None:
        scope = AllocationScope(
            account_fingerprint=hashlib.sha256(exchange.api_key.strip().encode()).hexdigest(),
            environment=environment,
            market_type=market_type,
        )
    return {
        "available": scope is not None,
        "reason": reason,
        "scope_id": scope.scope_id if scope is not None else None,
        "exchange": "okx",
        "environment": environment,
        "account_fingerprint": scope.account_fingerprint if scope is not None else None,
        "identity_verified": False,
        "market_type": market_type,
        # The configured execution mode is independent of the exchange environment.
        "execution_mode": settings.mode,
        "draft_only": True,
        "execution_ready": False,
    }, scope


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def create_router(runtime: StrategyRuntimeState) -> APIRouter:
    router = APIRouter()
    repository = Repository()
    store = AllocationStore(repository.engine)
    book = PaperBook(repository.engine)

    def strategy_symbol(name: str) -> tuple[str, str]:
        config = repository.get_strategy_config(name)
        try:
            if config is not None:
                strategy = runtime.registry.create_instance(
                    config.name, config.strategy_type, config.symbol,
                    config.timeframe, config.params,
                )
            elif name in runtime.registry.list_implicit_strategies():
                strategy = runtime.registry.create(name)
            else:
                raise _error(404, "strategy_not_found", "Strategy not found")
            return spot_symbol(strategy.symbol)
        except (KeyError, StrategyConfigValidationError, ValueError) as exc:
            raise _error(422, "unsupported_strategy", "A valid spot strategy is required") from exc

    def lifecycle_status(name: str) -> str:
        if name in runtime.starting_engines:
            return "starting"
        if name in runtime.engines:
            return "running"
        return runtime.strategy_status.get(name, "unknown")

    def envelope(name: str) -> dict[str, object]:
        symbol, currency = strategy_symbol(name)
        context, scope = allocation_context()
        status = lifecycle_status(name)
        draft = store.get_draft(scope, name) if scope is not None else None
        return {
            "context": context,
            "draft": asdict(draft) if draft is not None else None,
            "reservation": _reservation_view(book, repository, scope, name),
            "other_scope_reservation": _other_scope_reservation(book, scope, name),
            "strategy": name,
            "symbol": symbol,
            "currency": currency,
            "lifecycle_status": status,
            "can_edit": scope is not None and status == "stopped",
        }

    def mutable_scope(
        name: str, expected_scope_id: str, expected_symbol: str, symbol: str,
    ) -> AllocationScope:
        if lifecycle_status(name) != "stopped":
            raise _error(409, "strategy_not_stopped", "Strategy must be stopped to edit drafts")
        _, scope = allocation_context()
        if scope is None:
            raise _error(
                409, "context_unavailable", "Configured spot account context is unavailable",
            )
        if scope.scope_id != expected_scope_id:
            raise _error(
                409, "allocation_scope_changed", "Account context changed; reload the draft",
            )
        try:
            loaded_symbol, _ = spot_symbol(expected_symbol)
        except ValueError as exc:
            raise _error(422, "invalid_symbol", "A spot symbol precondition is required") from exc
        if loaded_symbol != symbol:
            raise _error(
                409, "allocation_symbol_changed", "Strategy symbol changed; reload the draft",
            )
        return scope

    def _reject_active_reservation(name: str, scope: AllocationScope) -> None:
        if book.get(scope.scope_id, name) is not None:
            raise _error(
                409,
                "reservation_active",
                "Release the local paper reservation before editing the draft",
            )

    @router.get("/context")
    def context() -> dict[str, object]:
        return allocation_context()[0]

    @router.get("/{strategy}")
    async def get_draft(strategy: str) -> dict[str, object]:
        async with runtime.lifecycle_lock(strategy):
            return envelope(strategy)

    @router.put("/{strategy}")
    async def save_draft(strategy: str, payload: DraftRequest) -> dict[str, object]:
        async with runtime.lifecycle_lock(strategy):
            symbol, _ = strategy_symbol(strategy)
            scope = mutable_scope(
                strategy, payload.expected_scope_id, payload.expected_symbol, symbol,
            )
            _reject_active_reservation(strategy, scope)
            try:
                store.save_draft(
                    scope, strategy=strategy, symbol=symbol, budget=payload.budget,
                    expected_revision=payload.expected_revision, timestamp=int(time.time() * 1000),
                )
            except AllocationConflict as exc:
                raise _error(409, exc.code, str(exc)) from exc
            return envelope(strategy)

    @router.delete("/{strategy}")
    async def delete_draft(
        strategy: str,
        revision: Annotated[int, Query(ge=1)],
        expected_scope_id: Annotated[str, Query(min_length=1, max_length=128)],
        expected_symbol: Annotated[str, Query(min_length=1, max_length=128)],
    ) -> dict[str, object]:
        async with runtime.lifecycle_lock(strategy):
            symbol, _ = strategy_symbol(strategy)
            scope = mutable_scope(strategy, expected_scope_id, expected_symbol, symbol)
            _reject_active_reservation(strategy, scope)
            try:
                store.delete_draft(
                    scope, strategy, expected_revision=revision, timestamp=int(time.time() * 1000),
                )
            except AllocationNotFound as exc:
                raise _error(404, "allocation_not_found", str(exc)) from exc
            except AllocationConflict as exc:
                raise _error(409, exc.code, str(exc)) from exc
            return envelope(strategy)

    @router.post("/{strategy}/activate")
    async def activate_reservation(
        strategy: str, payload: ReservationRequest,
    ) -> dict[str, object]:
        _require_local_paper()
        async with runtime.lifecycle_lock(strategy):
            _require_local_paper()
            symbol, _currency = strategy_symbol(strategy)
            scope = mutable_scope(
                strategy, payload.expected_scope_id, payload.expected_symbol, symbol,
            )
            try:
                book.activate(
                    scope.scope_id,
                    strategy,
                    symbol=symbol,
                    expected_revision=payload.expected_revision,
                    book_equity=load_runtime_settings().backtest.initial_capital,
                    timestamp=int(time.time() * 1000),
                )
            except PaperBookNotFound as exc:
                raise _error(404, exc.code, str(exc)) from exc
            except PaperBookError as exc:
                raise _error(409, exc.code, str(exc)) from exc
            return envelope(strategy)

    @router.post("/{strategy}/release")
    async def release_reservation(
        strategy: str, payload: ReservationRequest,
    ) -> dict[str, object]:
        _require_local_paper()
        async with runtime.lifecycle_lock(strategy):
            _require_local_paper()
            symbol, _currency = strategy_symbol(strategy)
            scope = mutable_scope(
                strategy, payload.expected_scope_id, payload.expected_symbol, symbol,
            )
            try:
                book.release(
                    scope.scope_id,
                    strategy,
                    expected_revision=payload.expected_revision,
                    symbol=symbol,
                    timestamp=int(time.time() * 1000),
                )
            except PaperBookNotFound as exc:
                raise _error(404, exc.code, str(exc)) from exc
            except PaperBookError as exc:
                raise _error(409, exc.code, str(exc)) from exc
            return envelope(strategy)

    @router.post("/{strategy}/exchange-activate")
    async def exchange_activate(
        strategy: str, payload: ReservationRequest,
    ) -> dict[str, object]:
        mode = load_runtime_settings().mode.strip().lower()
        if mode not in {"demo", "live"}:
            raise _error(
                409,
                "exchange_activation_only",
                "Exchange activation is only available in demo or live mode",
            )
        async with runtime.lifecycle_lock(strategy):
            symbol, _currency = strategy_symbol(strategy)
            scope = mutable_scope(
                strategy, payload.expected_scope_id, payload.expected_symbol, symbol,
            )
            draft = store.get_draft(scope, strategy)
            if draft is None:
                raise _error(404, "draft_not_found", "Create a draft before exchange activation")
            if draft.revision != payload.expected_revision:
                raise _error(409, "revision_conflict", "Reload the draft and retry")
            available = await fetch_exchange_available()
            environment = "live" if mode == "live" else "sandbox"
            try:
                grant_ownership_from_balance(
                    repository,
                    scope_id=scope.scope_id,
                    strategy=strategy,
                    symbol=symbol,
                    budget=float(draft.budget),
                    available=available,
                    environment=environment,
                    timestamp=int(time.time() * 1000),
                )
            except OwnershipError as exc:
                raise _error(409, exc.code, str(exc)) from exc
            return envelope(strategy)

    return router


async def fetch_exchange_available() -> float:
    """读取当前模式对应的 OKX 可用余额。测试会替换这个函数。"""
    from dataclasses import replace

    from src.exchange.factory import create_okx_adapter

    settings = load_runtime_settings()
    demo = settings.mode.strip().lower() != "live"
    exchange = replace(settings.exchange, demo=demo)
    adapter = create_okx_adapter(exchange)
    snapshot = await adapter.fetch_account_snapshot()
    available = snapshot.available_balance
    if available is None:
        available = snapshot.cash_balance
    return float(available)


def _require_local_paper() -> None:
    mode = load_runtime_settings().mode.strip().lower()
    if mode == "live":
        raise _error(
            409,
            "live_activation_blocked",
            "Live activation stays closed until process recovery",
        )
    if mode not in {"demo", "paper"}:
        raise _error(
            409,
            "paper_activation_only",
            "Local paper activation is unavailable in this mode",
        )


def _reservation_view(
    book: PaperBook,
    repository: Repository,
    scope: AllocationScope | None,
    name: str,
):
    if scope is None:
        return None
    row = book.get(scope.scope_id, name)
    if row is None:
        return None
    account = repository.get_account(name, source="paper", runtime_mode="paper")
    holdings = 0.0
    for position in repository.get_open_positions(name, source="paper", runtime_mode="paper"):
        if getattr(position, "symbol", None) == row.symbol:
            holdings += float(getattr(position, "amount", 0.0) or 0.0)
    cash = row.budget if account is None else float(account.cash_balance)
    return {
        "strategy": row.strategy,
        "symbol": row.symbol,
        "budget": row.budget,
        "cash": cash,
        "holdings": holdings,
        "revision": row.revision,
        "scope_id": row.scope_id,
        "status": "active",
    }


def _other_scope_reservation(
    book: PaperBook, scope: AllocationScope | None, name: str,
) -> bool:
    current = None if scope is None else scope.scope_id
    return any(item.scope_id != current for item in book.for_strategy(name))
