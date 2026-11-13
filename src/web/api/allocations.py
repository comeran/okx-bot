"""Configure drafts, paper reservations, and cached evidence without granting live ownership."""

from __future__ import annotations

import hashlib
import logging
import time
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from src.core.config import AppConfig
from src.core.execution_mode import account_environment
from src.core.execution_scope import build_execution_context
from src.core.runtime_settings import load_runtime_settings
from src.data.allocations import (
    AllocationConflict,
    AllocationDraft,
    AllocationNotFound,
    AllocationScope,
    AllocationStore,
    spot_symbol,
)
from src.data.exchange_store import ExchangeStoreError
from src.data.funded_allocation import (
    FundingError,
    TrustedActivationFacts,
    activate_funded_allocation,
    assert_funded_draft_editable,
    funded_book_payload,
)
from src.data.models import EXCHANGE_STRATEGY
from src.data.paper_book import PaperBook, PaperBookError, PaperBookNotFound
from src.data.pnl import strategy_realized_pnl
from src.data.repository import Repository
from src.ops.allocation_readiness import build_allocation_readiness
from src.strategy.definitions import StrategyConfigValidationError
from src.web.api.strategies import StrategyRuntimeState
from src.web.exchange_context import bind_exchange_store


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


def allocation_context(
    settings: AppConfig | None = None,
) -> tuple[dict[str, object], AllocationScope | None]:
    settings = settings if settings is not None else load_runtime_settings()
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


logger = logging.getLogger(__name__)


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
        settings = deepcopy(load_runtime_settings())
        context, scope = allocation_context(settings)
        execution_context, _ = build_execution_context(settings.exchange)
        status = lifecycle_status(name)
        draft = store.get_draft(scope, name) if scope is not None else None
        return {
            "context": context,
            "execution_context": execution_context,
            "draft": asdict(draft) if draft is not None else None,
            "reservation": _reservation_view(book, repository, scope, name),
            "funded_book": None if scope is None else funded_book_payload(
                repository, scope.scope_id, name, scope.environment,
            ),
            "other_scope_reservation": _other_scope_reservation(book, scope, name),
            "strategy": name,
            "symbol": symbol,
            "currency": currency,
            "lifecycle_status": status,
            "can_edit": scope is not None and status == "stopped",
            "realized_pnl": strategy_realized_pnl(repository, name),
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

    def _reject_funded_edit(
        scope: AllocationScope,
        name: str,
        symbol: str,
        budget: Decimal,
        *,
        deleting: bool = False,
    ) -> None:
        try:
            assert_funded_draft_editable(
                repository,
                scope_id=scope.scope_id,
                strategy=name,
                environment=scope.environment,
                symbol=symbol,
                budget=budget,
                deleting=deleting,
            )
        except FundingError as exc:
            raise _error(409, exc.code, str(exc)) from exc

    @router.get("/context")
    def context() -> dict[str, object]:
        return allocation_context()[0]

    def readiness_draft(
        drafts: list[AllocationDraft], name: str, symbol: str, expected_revision: int,
    ) -> AllocationDraft:
        draft = next((item for item in drafts if item.strategy == name), None)
        if draft is None or draft.revision != expected_revision:
            raise _error(409, "revision_conflict", "Saved allocation changed; reload the draft")
        if draft.symbol != symbol:
            raise _error(
                409, "allocation_symbol_changed", "Strategy symbol changed; reload the draft",
            )
        return draft

    @router.get("/{strategy}/readiness")
    async def get_readiness(
        strategy: str,
        expected_account_scope_id: Annotated[str, Query(min_length=1, max_length=128)],
        expected_scope_id: Annotated[str, Query(min_length=1, max_length=128)],
        expected_revision: Annotated[int, Query(ge=1)],
        expected_symbol: Annotated[str, Query(min_length=1, max_length=128)],
    ) -> dict[str, object]:
        from src.data.spot_ledger import SpotLedgerError, SpotLedgerStore

        async with runtime.lifecycle_lock(strategy):
            settings = deepcopy(load_runtime_settings())
            symbol, _ = strategy_symbol(strategy)
            _, scope = allocation_context(settings)
            bound = bind_exchange_store(
                settings.exchange, repository, expected_account_scope_id, require_available=True,
            )
            if scope is None or bound.repository is None or bound.scope is None:
                raise _error(
                    409, "account_context_unavailable", "Spot account context is unavailable",
                )
            if scope.scope_id != expected_scope_id:
                raise _error(
                    409, "allocation_scope_changed", "Account context changed; reload the draft",
                )
            try:
                loaded_symbol, _ = spot_symbol(expected_symbol)
            except ValueError as exc:
                raise _error(
                    422, "invalid_symbol", "A spot symbol precondition is required",
                ) from exc
            if loaded_symbol != symbol:
                raise _error(
                    409, "allocation_symbol_changed", "Strategy symbol changed; reload the draft",
                )
            # One master snapshot supplies both the target and every same-quote proposal.
            drafts = store.list_drafts(scope)
            draft = readiness_draft(drafts, strategy, symbol, expected_revision)
            facts = bound.repository
            try:
                account = facts.get_account(
                    EXCHANGE_STRATEGY, source="live_exchange", runtime_mode="live",
                )
                orders = facts.get_orders(source="live_exchange", runtime_mode="live")
                global_kill = repository.get_kill_switch().engaged
                account_kill = facts.get_kill_switch().engaged
                ledger = SpotLedgerStore(facts.engine, bound.scope).get_strategy(strategy)
            except (ExchangeStoreError, SpotLedgerError, SQLAlchemyError) as exc:
                raise _error(
                    503, "account_store_unavailable", "Exchange account facts are unavailable",
                ) from exc
            # Diagnostic reads span two files. Drift rejects this response; it never
            # becomes an atomic activation certificate or a reusable authorization.
            current_settings = load_runtime_settings()
            if (
                current_settings.exchange != settings.exchange
                or current_settings.mode != settings.mode
            ):
                raise _error(
                    409, "account_scope_changed", "Account context changed; reload the draft",
                )
            current_symbol, _ = strategy_symbol(strategy)
            if current_symbol != symbol:
                raise _error(
                    409, "allocation_symbol_changed", "Strategy symbol changed; reload the draft",
                )
            current_draft = store.get_draft(scope, strategy)
            readiness_draft(
                [current_draft] if current_draft is not None else [],
                strategy, symbol, expected_revision,
            )
            return build_allocation_readiness(
                draft=draft, drafts=drafts, execution_context=bound.context,
                lifecycle_status=lifecycle_status(strategy), account=account, orders=orders,
                global_kill=global_kill, account_kill=account_kill, ledger=ledger,
                evaluated_at=int(time.time() * 1000),
            )

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
            _reject_funded_edit(scope, strategy, symbol, Decimal(str(payload.budget)))
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
            _reject_funded_edit(scope, strategy, symbol, Decimal("0"), deleting=True)
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
            environment = "live" if mode == "live" else "sandbox"
            settings = load_runtime_settings()
            # 可信预检通过才拨款。实盘开仓仍由 allow_live_open_orders 关掉。
            facts = await load_trusted_activation_facts(
                symbol=symbol,
                scope_id=scope.scope_id,
                budget=_budget_text(draft.budget),
            )
            try:
                activate_funded_allocation(
                    repository,
                    scope_id=scope.scope_id,
                    strategy=strategy,
                    symbol=symbol,
                    budget=Decimal(str(draft.budget)),
                    environment=environment,
                    revision=draft.revision,
                    facts=facts,
                    now_ms=int(time.time() * 1000),
                    market_type=settings.exchange.market_type,
                )
            except FundingError as exc:
                raise _error(409, exc.code, str(exc)) from exc
            return envelope(strategy)

    return router


def _budget_text(budget: float) -> str:
    rendered = format(Decimal(str(budget)), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


async def load_trusted_activation_facts(
    *, symbol: str, scope_id: str, budget: str,
) -> TrustedActivationFacts:
    """只接受已保存且全部通过的现货预检。测试会替换这个函数。"""
    settings = load_runtime_settings()
    market = settings.exchange.market_type.strip().lower() or "spot"
    try:
        from src.ops.spot_preflight_evidence import build_stored_spot_preflight
        from src.web.exchange_context import bind_exchange_store

        binding = bind_exchange_store(
            settings.exchange, Repository(), scope_id, require_available=True,
        )
        if binding.repository is None:
            raise ValueError("stored spot evidence is unavailable")
        evidence = binding.repository.get_spot_preflight_evidence(
            symbol, expected_account_scope_id=binding.context["account_scope_id"],
        )
        result = build_stored_spot_preflight(
            evidence,
            symbol=symbol,
            side="buy",
            amount="1",
            price=budget,
            now_ms=int(time.time() * 1000),
        )
        available = Decimal("0")
        observed = 0
        balance = evidence.balance
        quote = symbol.split("-")[-1]
        if balance is not None and balance.fact is not None:
            observed = int(balance.received_at)
            matches = [
                row for row in balance.fact.details
                if getattr(row, "ccy", None) == quote
                and isinstance(getattr(row, "avail_bal", None), str)
            ]
            if len(matches) == 1:
                available = Decimal(matches[0].avail_bal)
        return TrustedActivationFacts(
            available_quote=available,
            observed_at=observed,
            preflight_passed=bool(result.get("can_activate")),
            market_type=market,
            account_scope_id=scope_id,
        )
    except Exception:
        logger.warning("可信预检不可用，交易所激活保持关闭")
        return TrustedActivationFacts(
            available_quote=Decimal("0"),
            observed_at=0,
            preflight_passed=False,
            market_type=market,
            account_scope_id=scope_id,
        )


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
    if mode != "paper":
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
