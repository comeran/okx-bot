from abc import ABC, abstractmethod
from typing import Any

from src.core.types import Order


class OrderHandler(ABC):
    @abstractmethod
    async def submit(self, order: Order) -> Order:
        """Submit an order."""

    @abstractmethod
    async def cancel(self, order_id: str, symbol: str | None = None) -> Any:
        """Cancel an order by ID and return the provider response."""


class OrderRouter:
    def __init__(
        self,
        backtest: OrderHandler | None,
        demo: OrderHandler | None = None,
        live: OrderHandler | None = None,
        mode: str = "backtest",
    ) -> None:
        self.backtest = backtest
        self.demo = demo
        self.live = live
        self.mode = mode

    def _get_handler(self) -> OrderHandler:
        handler = {
            "backtest": self.backtest,
            "demo": self.demo,
            "live": self.live,
        }.get(self.mode)
        if handler is None:
            raise ValueError(f"No order handler configured for mode: {self.mode}")
        return handler

    async def submit(self, order: Order) -> Order:
        return await self._get_handler().submit(order)

    async def cancel(self, order_id: str, symbol: str | None = None) -> Any:
        return await self._get_handler().cancel(order_id, symbol)
