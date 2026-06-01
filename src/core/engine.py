from __future__ import annotations

from typing import Any


class BotEngine:
    def __init__(self, strategies: list[Any] | None = None) -> None:
        self.strategies = strategies or []
        self.running = False

    async def start(self) -> None:
        for strategy in self.strategies:
            on_init = getattr(strategy, "on_init", None)
            if on_init is not None:
                await on_init()
        self.running = True

    async def stop(self) -> None:
        for strategy in self.strategies:
            on_shutdown = getattr(strategy, "on_shutdown", None)
            if on_shutdown is not None:
                await on_shutdown()
        self.running = False
