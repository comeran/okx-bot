from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from copy import deepcopy
from inspect import Parameter, signature
from types import SimpleNamespace
from typing import Annotated, Literal

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
from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator
from sqlalchemy.exc import SQLAlchemyError

from src.core.private_facts import MAX_NUMERIC_TEXT_LENGTH, canonical_spot_symbol
from src.data.models import EXCHANGE_STRATEGY
from src.data.repository import Repository
from src.exchange.base import canonical_symbol, is_supported_canonical_symbol
from src.exchange.errors import MalformedProviderData
from src.exchange.factory import create_okx_adapter
from src.notify.telegram import RiskEventTelegramNotifier, TelegramNotifier
from src.ops.flatten import cancel_open_orders, flatten_exposure
from src.ops.private_sync import sync_private_state
from src.ops.spot_preflight_evidence import build_stored_spot_preflight
from src.web.api import settings as settings_api
from src.web.api import trading
from src.web.exchange_context import bind_exchange_store

logger = logging.getLogger(__name__)

RuntimeBroadcaster = Callable[[dict[str, object]], Awaitable[None]]


class KillSwitchRequest(BaseModel):
    engaged: bool
    reason: str = ""


class OpeningPauseRequest(BaseModel):
    paused: bool
    reason: str = ""


class SpotPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    symbol: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    side: Literal["buy", "sell"]
    amount: Annotated[str, StringConstraints(min_length=1, max_length=MAX_NUMERIC_TEXT_LENGTH)]
    price: Annotated[str, StringConstraints(min_length=1, max_length=MAX_NUMERIC_TEXT_LENGTH)]

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        canonical_spot_symbol(value)
        return value


class PrivateSyncRequest(BaseModel):
    symbols: list[str] | None = None
    since: int | None = None

    @field_validator("since", mode="before")
    @classmethod
    def validate_since(cls, value: object) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**63 - 1:
            raise ValueError("since must be a nonnegative signed 64-bit integer")
        return value

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, symbols: list[str] | None) -> list[str] | None:
        if symbols is None or not symbols:
            return symbols
        if any(
            not is_supported_canonical_symbol(canonical_symbol(symbol, market_type="auto"))
            for symbol in symbols
        ):
            raise ValueError("Invalid symbol scope")
        return symbols


def current_timestamp_ms() -> int:
    return int(time.time() * 1000)


def serialize_kill_switch(state) -> dict[str, object]:
    return {
        "engaged": state.engaged,
        "reason": state.reason,
        "updated_at": state.updated_at,
    }


def serialize_opening_pause(state) -> dict[str, object]:
    return {
        "paused": state.paused,
        "reason": state.reason,
        "updated_at": state.updated_at,
    }


def engage_emergency_stop(reason: str) -> tuple[object, object]:
    repository = Repository()
    now = current_timestamp_ms()
    kill_switch = repository.set_kill_switch(True, reason, now)
    opening_pause = repository.set_opening_pause(True, reason, now)
    return kill_switch, opening_pause


def emergency_order_manager():
    from src.web.api.strategies import create_order_manager

    repository = Repository()
    return create_order_manager(
        repository=repository,
        kill_switch_checker=lambda: bool(repository.get_kill_switch().engaged),
    )


def _emergency_payload(result: dict[str, object], kill_switch, opening_pause) -> dict[str, object]:
    return {
        **result,
        "kill_switch": serialize_kill_switch(kill_switch),
        "opening_pause": serialize_opening_pause(opening_pause),
    }


def _is_live_exchange_record(record: object, *, allow_legacy: bool = False) -> bool:
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


def _exchange_rows(repository: Repository, method_name: str, strategy: str | None = None):
    if repository is None:
        return []
    method = getattr(repository, method_name)
    rows = method(
        strategy, source="live_exchange", runtime_mode="live",
    ) if strategy is not None else method(source="live_exchange", runtime_mode="live")
    return [row for row in rows if _is_live_exchange_record(row, allow_legacy=False)]


def _exchange_account(repository: Repository | None):
    if repository is None:
        return None
    account = repository.get_account(
        EXCHANGE_STRATEGY, source="live_exchange", runtime_mode="live",
    )
    return account if account is not None and _is_live_exchange_record(
        account, allow_legacy=False
    ) else None


def exchange_snapshot(
    repository: Repository | None,
    *,
    context: dict[str, object],
    master: Repository,
) -> dict[str, object]:
    positions = _exchange_rows(repository, "get_open_positions", EXCHANGE_STRATEGY)
    orders = _exchange_rows(repository, "get_orders")
    account = _exchange_account(repository)
    global_kill = serialize_kill_switch(master.get_kill_switch())
    account_kill = (
        serialize_kill_switch(repository.get_kill_switch()) if repository is not None else None
    )
    states = [state for state in (global_kill, account_kill) if state is not None]
    effective = {
        "engaged": any(state["engaged"] for state in states),
        "reason": "; ".join(str(state["reason"]) for state in states
                            if state["engaged"] and state["reason"]),
        "updated_at": max(int(state["updated_at"]) for state in states),
    }
    return {
        "type": "snapshot", "scope": "exchange", "strategy": EXCHANGE_STRATEGY,
        "context": context,
        "data": {
            "scope": "exchange", "strategy": EXCHANGE_STRATEGY,
            "account": trading.serialize_account(
                account, scope="exchange", strategy=EXCHANGE_STRATEGY,
            ) if account is not None else None,
            "positions": trading.serialize_records(positions),
            "orders": [trading.serialize_order_record(order) for order in orders],
            "kill_switch": {"global": global_kill, "account": account_kill,
                            "effective": effective},
        },
    }


def create_risk_event_notifier(settings) -> RiskEventTelegramNotifier | None:
    notify = settings.notify
    if not notify.telegram_bot_token or not notify.telegram_chat_id:
        return None
    return RiskEventTelegramNotifier(
        TelegramNotifier(
            bot_token=notify.telegram_bot_token,
            chat_id=notify.telegram_chat_id,
        )
    )


def _create_adapter(settings):
    try:
        return create_okx_adapter(settings.exchange)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _sync_scope_arguments(account_scope_id: str) -> dict[str, str]:
    try:
        parameters = signature(sync_private_state).parameters.values()
    except (TypeError, ValueError):
        return {}
    if any(parameter.kind == Parameter.VAR_KEYWORD or (
        parameter.name == "account_scope_id"
        and parameter.kind in {Parameter.POSITIONAL_OR_KEYWORD, Parameter.KEYWORD_ONLY}
    ) for parameter in parameters):
        return {"account_scope_id": account_scope_id}
    return {}


def create_router(broadcast: RuntimeBroadcaster | None = None) -> APIRouter:
    router = APIRouter()

    @router.post("/spot/preflight")
    async def spot_preflight(
        request: SpotPreflightRequest, expected_account_scope_id: str | None = None,
    ) -> dict[str, object]:
        settings = deepcopy(settings_api._load_settings())
        binding = bind_exchange_store(
            settings.exchange, Repository(), expected_account_scope_id, require_available=True,
        )
        try:
            evidence = binding.repository.get_spot_preflight_evidence(
                request.symbol, expected_account_scope_id=binding.context["account_scope_id"],
            )
            result = build_stored_spot_preflight(
                evidence, symbol=request.symbol, side=request.side, amount=request.amount,
                price=request.price, now_ms=current_timestamp_ms(),
            )
        except (ValueError, SQLAlchemyError) as exc:
            raise HTTPException(status_code=503, detail={
                "code": "spot_preflight_evidence_unavailable",
                "message": "Stored spot preflight evidence is unavailable",
            }) from exc
        return {"context": binding.context, **result}

    @router.get("/kill-switch")
    async def get_kill_switch() -> dict[str, object]:
        return serialize_kill_switch(Repository().get_kill_switch())

    @router.put("/kill-switch")
    async def set_kill_switch(request: KillSwitchRequest) -> dict[str, object]:
        state = Repository().set_kill_switch(
            request.engaged,
            request.reason,
            current_timestamp_ms(),
        )
        return serialize_kill_switch(state)

    @router.get("/opening-pause")
    async def get_opening_pause() -> dict[str, object]:
        return serialize_opening_pause(Repository().get_opening_pause())

    @router.put("/opening-pause")
    async def set_opening_pause(request: OpeningPauseRequest) -> dict[str, object]:
        state = Repository().set_opening_pause(
            request.paused,
            request.reason,
            current_timestamp_ms(),
        )
        return serialize_opening_pause(state)

    @router.post("/cancel-orders")
    async def cancel_orders() -> dict[str, object]:
        kill_switch, opening_pause = engage_emergency_stop("Cancel open orders")
        result = await cancel_open_orders(emergency_order_manager())
        return _emergency_payload(result, kill_switch, opening_pause)

    @router.post("/flatten")
    async def flatten_positions() -> dict[str, object]:
        kill_switch, opening_pause = engage_emergency_stop("Flatten positions")
        result = await flatten_exposure(emergency_order_manager())
        return _emergency_payload(result, kill_switch, opening_pause)

    @router.get("/sync/private/status")
    async def get_private_sync_status(
        expected_account_scope_id: str | None = None,
    ) -> dict[str, object]:
        settings = deepcopy(settings_api._load_settings())
        binding = bind_exchange_store(
            settings.exchange, Repository(), expected_account_scope_id, require_available=True,
        )
        try:
            status = binding.repository.get_private_sync_status()
            return {
                "context": binding.context,
                **{key: record.model_dump() if record is not None else None
                   for key, record in status.items()},
            }
        except (ValueError, SQLAlchemyError) as exc:
            raise HTTPException(status_code=503, detail={
                "code": "private_sync_status_unavailable",
                "message": "Private synchronization evidence is unavailable",
            }) from exc

    @router.post("/sync/private")
    async def sync_private(
        request: PrivateSyncRequest, expected_account_scope_id: str | None = None,
    ) -> dict[str, object]:
        settings = deepcopy(settings_api._load_settings())
        if not settings.exchange.demo:
            raise HTTPException(
                status_code=403,
                detail="Private sync is only available in OKX demo mode",
            )
        if request.symbols is not None and any(
            not is_supported_canonical_symbol(
                canonical_symbol(symbol, market_type=settings.exchange.market_type),
                market_type=settings.exchange.market_type,
            )
            for symbol in request.symbols
        ):
            raise HTTPException(status_code=422, detail="Invalid symbol scope")
        binding = bind_exchange_store(
            settings.exchange, Repository(), expected_account_scope_id, require_available=True,
        )
        notifier = create_risk_event_notifier(settings)

        async def publish_high_risk_event(payload: dict[str, object]) -> None:
            payload = {**payload, "scope": "exchange", "context": binding.context}
            if broadcast is not None:
                await broadcast(payload)
            if notifier is None:
                return
            try:
                await notifier.send_risk_event(payload)
            except Exception:
                logger.warning("Failed to send Telegram risk notification", exc_info=True)

        adapter = None
        try:
            adapter = _create_adapter(SimpleNamespace(exchange=binding.exchange))
            result = await sync_private_state(
                binding.repository,
                adapter,
                symbols=request.symbols,
                since=request.since,
                timestamp_ms=current_timestamp_ms,
                risk_event_notifier=publish_high_risk_event,
                **_sync_scope_arguments(binding.context["account_scope_id"]),
            )
        except HTTPException:
            raise
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
        except (ValueError, SQLAlchemyError) as exc:
            if isinstance(exc, ValueError) and str(exc) == (
                "Private sync attempt superseded by a newer committed generation"
            ):
                raise HTTPException(status_code=409, detail={
                    "code": "private_sync_superseded",
                    "message": "A newer private synchronization has already committed",
                }) from exc
            raise HTTPException(status_code=503, detail={
                "code": "private_sync_unavailable",
                "message": "Private synchronization is unavailable",
            }) from exc
        finally:
            if adapter is not None:
                try:
                    await adapter.close()
                except Exception:
                    logger.warning("Failed to close private sync adapter", exc_info=True)
        if broadcast is not None:
            await broadcast(exchange_snapshot(
                binding.repository, context=binding.context, master=binding.master,
            ))
        return {"status": "ok", "context": binding.context, **result.as_response()}

    return router
