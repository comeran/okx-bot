import time
from collections.abc import Callable

from src.core.types import Order, OrderSide, OrderStatus, OrderType, Position, PositionSide
from src.data.models import OrderRecord, PositionRecord, TradeRecord
from src.order.router import OrderRouter


class UnifiedOrderManager:
    def __init__(
        self,
        router: OrderRouter,
        repository=None,
        timestamp_ms: Callable[[], int] | None = None,
    ) -> None:
        self.router = router
        self.repository = repository
        self.timestamp_ms = timestamp_ms or self._current_timestamp_ms
        self._positions: dict[str, dict[str, Position]] = {}
        self._balances: dict[str, float] = {}
        self._order_seq = 0

    async def submit(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        amount: float,
        price: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        strategy_name: str = "",
    ) -> Order:
        self._order_seq += 1
        order = Order(
            id=f"{strategy_name}-{symbol}-{id(self)}-{self._order_seq}",
            symbol=symbol,
            side=side,
            type=order_type,
            amount=amount,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        submitted_order = await self.router.submit(order)
        self._persist_order(submitted_order, strategy_name)
        return submitted_order

    async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
        return await self.router.cancel(order_id, symbol)

    def get_position(self, strategy_name: str, symbol: str) -> Position | None:
        return self._positions.get(strategy_name, {}).get(symbol)

    def get_balance(self, strategy_name: str) -> float:
        return self._balances.get(strategy_name, 0.0)

    def set_balance(self, strategy_name: str, amount: float) -> None:
        self._balances[strategy_name] = amount

    def _persist_order(self, order: Order, strategy_name: str) -> None:
        if self.repository is None:
            return

        timestamp = order.fill_time or self.timestamp_ms()
        fill_price = order.fill_price or 0.0
        self.repository.save_order(
            OrderRecord(
                order_id=order.id,
                strategy=strategy_name,
                symbol=order.symbol,
                side=order.side.value,
                type=order.type.value,
                amount=order.amount,
                price=order.price or 0.0,
                status=order.status.value,
                fill_price=fill_price,
                timestamp=timestamp,
            )
        )

        if order.status == OrderStatus.FILLED:
            self._persist_fill(order, strategy_name, timestamp, fill_price)

    def _persist_fill(
        self,
        order: Order,
        strategy_name: str,
        timestamp: int,
        fill_price: float,
    ) -> None:
        self.repository.save_trade(
            TradeRecord(
                strategy=strategy_name,
                symbol=order.symbol,
                side=order.side.value,
                amount=order.amount,
                price=fill_price,
                fee=0.0,
                timestamp=timestamp,
            )
        )

        position_side = PositionSide.LONG if order.side == OrderSide.BUY else PositionSide.SHORT
        position = Position(
            symbol=order.symbol,
            side=position_side,
            amount=order.amount,
            entry_price=fill_price,
            unrealized_pnl=0.0,
        )
        self._positions.setdefault(strategy_name, {})[order.symbol] = position
        self.repository.save_position(
            PositionRecord(
                strategy=strategy_name,
                symbol=order.symbol,
                side=position_side.value,
                amount=order.amount,
                entry_price=fill_price,
                leverage=position.leverage,
                timestamp=timestamp,
            )
        )

    @staticmethod
    def _current_timestamp_ms() -> int:
        return int(time.time() * 1000)
