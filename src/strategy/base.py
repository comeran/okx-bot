from abc import ABC, abstractmethod
from typing import Any

from src.core.types import Bar, Order, OrderSide, OrderType, Position


class BaseStrategy(ABC):
    name: str = ""

    def __init__(self) -> None:
        self._order_manager: Any = None
        self._capital_pct = 0.1

    def set_order_manager(self, manager: Any) -> None:
        self._order_manager = manager

    async def on_init(self) -> None:
        pass

    @abstractmethod
    async def on_bar(self, bar: Bar) -> None:
        pass

    async def on_order(self, order: Order) -> None:
        pass

    async def on_position(self, position: Position) -> None:
        pass

    async def buy(
        self,
        symbol: str,
        amount: float,
        price: float | None = None,
        sl: float | None = None,
        tp: float | None = None,
    ) -> Any:
        return await self._submit_order(
            symbol=symbol,
            side=OrderSide.BUY,
            amount=amount,
            price=price,
            stop_loss=sl,
            take_profit=tp,
        )

    async def sell(
        self,
        symbol: str,
        amount: float,
        price: float | None = None,
    ) -> Any:
        return await self._submit_order(
            symbol=symbol,
            side=OrderSide.SELL,
            amount=amount,
            price=price,
        )

    async def cancel(self, order_id: str) -> Any:
        if self._order_manager is None:
            raise RuntimeError("Order manager not set")
        return await self._order_manager.cancel(order_id)

    def get_position(self, symbol: str) -> Any:
        if self._order_manager is None:
            return None
        return self._order_manager.get_position(self.name, symbol)

    def get_balance(self) -> float:
        if self._order_manager is None:
            return 0.0
        return self._order_manager.get_balance(self.name)

    async def _submit_order(
        self,
        symbol: str,
        side: OrderSide,
        amount: float,
        price: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> Any:
        if self._order_manager is None:
            raise RuntimeError("Order manager not set")

        order_type = OrderType.LIMIT if price is not None else OrderType.MARKET
        return await self._order_manager.submit(
            symbol=symbol,
            side=side,
            order_type=order_type,
            amount=amount,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            strategy_name=self.name,
        )
