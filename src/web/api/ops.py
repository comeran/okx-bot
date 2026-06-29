from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.data.repository import Repository
from src.exchange.okx_futures import OKXFuturesAdapter
from src.exchange.okx_options import OKXOptionsAdapter
from src.exchange.okx_spot import OKXSpotAdapter
from src.exchange.okx_swap import OKXSwapAdapter
from src.notify.telegram import RiskEventTelegramNotifier, TelegramNotifier
from src.ops.private_sync import sync_private_state
from src.web.api import settings as settings_api

logger = logging.getLogger(__name__)

RuntimeBroadcaster = Callable[[dict[str, object]], Awaitable[None]]


class KillSwitchRequest(BaseModel):
    engaged: bool
    reason: str = ""


class PrivateSyncRequest(BaseModel):
    symbols: list[str] = Field(default_factory=list)
    since: int | None = None


def current_timestamp_ms() -> int:
    return int(time.time() * 1000)


def serialize_kill_switch(state) -> dict[str, object]:
    return {
        "engaged": state.engaged,
        "reason": state.reason,
        "updated_at": state.updated_at,
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
    adapter_cls = {
        "spot": OKXSpotAdapter,
        "swap": OKXSwapAdapter,
        "future": OKXFuturesAdapter,
        "futures": OKXFuturesAdapter,
        "option": OKXOptionsAdapter,
        "options": OKXOptionsAdapter,
    }.get(settings.exchange.market_type)
    if adapter_cls is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported OKX market_type: {settings.exchange.market_type}",
        )
    return adapter_cls(
        settings.exchange.api_key,
        settings.exchange.secret,
        settings.exchange.passphrase,
        demo=settings.exchange.demo,
    )


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

        adapter = _create_adapter(settings)
        try:
            result = await sync_private_state(
                Repository(),
                adapter,
                symbols=request.symbols,
                since=request.since,
                timestamp_ms=current_timestamp_ms,
                risk_event_notifier=publish_high_risk_event,
            )
        finally:
            await adapter.close()
        return {"status": "ok", **result.as_response()}

    return router
