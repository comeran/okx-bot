from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

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
from pydantic import BaseModel, field_validator

from src.data.models import EXCHANGE_STRATEGY
from src.data.repository import Repository
from src.exchange.base import canonical_symbol, is_supported_canonical_symbol
from src.exchange.errors import MalformedProviderData
from src.exchange.factory import create_okx_adapter
from src.notify.telegram import RiskEventTelegramNotifier, TelegramNotifier
from src.ops.private_sync import sync_private_state
from src.web.api import settings as settings_api
from src.web.api import trading

logger = logging.getLogger(__name__)

RuntimeBroadcaster = Callable[[dict[str, object]], Awaitable[None]]


class KillSwitchRequest(BaseModel):
    engaged: bool
    reason: str = ""


class PrivateSyncRequest(BaseModel):
    symbols: list[str] | None = None
    since: int | None = None

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
    method = getattr(repository, method_name)
    try:
        rows = method(
            strategy,
            source="live_exchange",
            runtime_mode="live",
        ) if strategy is not None else method(
            source="live_exchange",
            runtime_mode="live",
        )
    except TypeError:
        rows = method(strategy) if strategy is not None else method()
    return [row for row in rows if _is_live_exchange_record(row, allow_legacy=False)]


def _exchange_account(repository: Repository):
    method = repository.get_account
    try:
        account = method(
            EXCHANGE_STRATEGY,
            source="live_exchange",
            runtime_mode="live",
        )
    except TypeError:
        account = method(EXCHANGE_STRATEGY)
    return account if account is not None and _is_live_exchange_record(
        account, allow_legacy=False
    ) else None


def exchange_snapshot(repository: Repository) -> dict[str, object]:
    positions = _exchange_rows(repository, "get_open_positions", EXCHANGE_STRATEGY)
    orders = _exchange_rows(repository, "get_orders")
    account = _exchange_account(repository)
    return {
        "type": "snapshot",
        "scope": "exchange",
        "strategy": EXCHANGE_STRATEGY,
        "data": {
            "scope": "exchange",
            "strategy": EXCHANGE_STRATEGY,
            "account": trading.serialize_account(
                account, scope="exchange", strategy=EXCHANGE_STRATEGY
            ),
            "positions": trading.serialize_records(positions),
            "orders": [trading.serialize_order_record(order) for order in orders],
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


def create_router(broadcast: RuntimeBroadcaster | None = None) -> APIRouter:
    router = APIRouter()

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

    @router.post("/sync/private")
    async def sync_private(request: PrivateSyncRequest) -> dict[str, object]:
        settings = settings_api._load_settings()
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
        notifier = create_risk_event_notifier(settings)

        async def publish_high_risk_event(payload: dict[str, object]) -> None:
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
            adapter = _create_adapter(settings)
            result = await sync_private_state(
                Repository(),
                adapter,
                symbols=request.symbols,
                since=request.since,
                timestamp_ms=current_timestamp_ms,
                risk_event_notifier=publish_high_risk_event,
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
        finally:
            if adapter is not None:
                try:
                    await adapter.close()
                except Exception:
                    logger.warning("Failed to close private sync adapter", exc_info=True)
        if broadcast is not None:
            await broadcast(exchange_snapshot(Repository()))
        return {"status": "ok", **result.as_response()}

    return router
