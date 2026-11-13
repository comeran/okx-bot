"""行情、订单、风控、账本各自启停。一个边界重启时，其他边界保持运行。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable


class Boundary:
    def __init__(
        self,
        name: str,
        start: Callable[[], Awaitable[None]],
        stop: Callable[[], Awaitable[None]],
    ) -> None:
        self.name = name
        self._start = start
        self._stop = stop
        self.running = False
        self.restarts = 0

    async def start(self) -> None:
        await self._start()
        self.running = True

    async def stop(self) -> None:
        if not self.running:
            return
        await self._stop()
        self.running = False

    async def restart(self) -> None:
        await self.stop()
        self.restarts += 1
        await self.start()


class DeskRuntime:
    def __init__(self, boundaries: list[Boundary]) -> None:
        self._boundaries = {item.name: item for item in boundaries}

    async def start(self) -> None:
        for boundary in self._boundaries.values():
            await boundary.start()

    async def stop(self) -> None:
        for boundary in self._boundaries.values():
            await boundary.stop()

    async def restart(self, name: str) -> None:
        await self._boundaries[name].restart()

    def running(self, name: str) -> bool:
        return self._boundaries[name].running
