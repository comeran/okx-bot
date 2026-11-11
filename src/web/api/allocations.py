"""Configure allocation drafts without consulting funds or granting live ownership."""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from src.core.runtime_settings import load_runtime_settings
from src.data.allocations import (
    AllocationConflict,
    AllocationNotFound,
    AllocationScope,
    AllocationStore,
    spot_symbol,
)
from src.data.repository import Repository
from src.strategy.definitions import StrategyConfigValidationError
from src.web.api.strategies import StrategyRuntimeState


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    budget: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
    expected_revision: Annotated[int, Field(strict=True, ge=0)]
    expected_scope_id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    expected_symbol: Annotated[str, Field(strict=True, min_length=1, max_length=128)]


def allocation_context() -> tuple[dict[str, object], AllocationScope | None]:
    settings = load_runtime_settings()
    exchange = settings.exchange
    environment = "sandbox" if exchange.demo else "live"
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
            try:
                store.delete_draft(
                    scope, strategy, expected_revision=revision, timestamp=int(time.time() * 1000),
                )
            except AllocationNotFound as exc:
                raise _error(404, "allocation_not_found", str(exc)) from exc
            except AllocationConflict as exc:
                raise _error(409, exc.code, str(exc)) from exc
            return envelope(strategy)

    return router
