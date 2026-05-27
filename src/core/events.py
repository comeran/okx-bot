from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4


@dataclass
class Event:
    type: str
    data: dict[str, Any]


EventHandler = Callable[[Event], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, dict[str, EventHandler]] = {}

    def subscribe(self, event_type: str, handler: EventHandler) -> str:
        token = uuid4().hex
        self._handlers.setdefault(event_type, {})[token] = handler
        return token

    def unsubscribe(self, token: str) -> None:
        for handlers in self._handlers.values():
            handlers.pop(token, None)

    async def emit(self, event: Event) -> None:
        handlers = self._handlers.get(event.type, {})
        if handlers:
            await asyncio.gather(*(handler(event) for handler in handlers.values()))
