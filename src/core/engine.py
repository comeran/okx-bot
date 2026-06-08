from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

from src.core.types import Bar

StrategyErrorCallback = Callable[[str, Exception], Awaitable[None]]


class BotEngine:
    def __init__(
        self,
        strategies: list[Any] | None = None,
        market_data_service: Any | None = None,
        on_strategy_error: StrategyErrorCallback | None = None,
        stop_market_data_on_stop: bool = True,
    ) -> None:
        self.strategies = strategies or []
        self.market_data_service = market_data_service
        self.on_strategy_error = on_strategy_error
        self.stop_market_data_on_stop = stop_market_data_on_stop
        self.running = False
        self._active_strategies: dict[str, bool] = {}
        self._market_data_task: asyncio.Task | None = None
        self._has_market_data_subscription = False
        self._processing_bar_callbacks = 0

    async def start(self) -> None:
        for strategy in self.strategies:
            self._active_strategies[strategy.name] = True
            on_init = getattr(strategy, "on_init", None)
            if on_init is not None:
                await on_init()
            self._subscribe_strategy(strategy)
        self.running = True
        if self._has_market_data_subscription:
            self._start_market_data()
        if self._has_market_data_subscription:
            await asyncio.sleep(0)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._drain_bar_callbacks(), timeout=1)

    async def stop(self) -> None:
        for strategy in self.strategies:
            await self._shutdown_strategy(strategy)
        if self.market_data_service is not None and self.stop_market_data_on_stop:
            await self.market_data_service.stop()
            if self._market_data_task is not None and not self._market_data_task.done():
                self._market_data_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._market_data_task
        self.running = False

    def _subscribe_strategy(self, strategy: Any) -> None:
        if self.market_data_service is None:
            return
        symbol = getattr(strategy, "symbol", None)
        timeframe = getattr(strategy, "timeframe", None)
        if symbol is None or timeframe is None:
            return
        self.market_data_service.subscribe(
            symbol,
            timeframe,
            self._bar_callback(strategy),
        )
        self._has_market_data_subscription = True

    def _start_market_data(self) -> None:
        if self.market_data_service is None:
            return
        if getattr(self.market_data_service, "_running", False):
            return
        self._market_data_task = asyncio.create_task(self.market_data_service.start())

    def _bar_callback(self, strategy: Any):
        async def handle_bar(bar: Bar) -> None:
            self._processing_bar_callbacks += 1
            try:
                if not self._active_strategies.get(strategy.name, False):
                    return
                try:
                    await strategy.on_bar(bar)
                except Exception as exc:
                    await self._handle_strategy_error(strategy, exc)
            finally:
                self._processing_bar_callbacks -= 1

        return handle_bar

    async def _drain_bar_callbacks(self) -> None:
        while self._processing_bar_callbacks > 0:
            await asyncio.sleep(0)

    async def _handle_strategy_error(self, strategy: Any, error: Exception) -> None:
        await self._shutdown_strategy(strategy)
        if self.on_strategy_error is not None:
            await self.on_strategy_error(strategy.name, error)

    async def _shutdown_strategy(self, strategy: Any) -> None:
        if not self._active_strategies.get(strategy.name, False):
            return
        self._active_strategies[strategy.name] = False
        on_shutdown = getattr(strategy, "on_shutdown", None)
        if on_shutdown is not None:
            await on_shutdown()
