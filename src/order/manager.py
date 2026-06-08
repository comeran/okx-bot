import time
from collections.abc import Awaitable, Callable
from typing import Any

from src.core.types import Order, OrderSide, OrderStatus, OrderType
from src.data.models import OrderRecord
from src.order.accounting import PaperAccountingService
from src.order.router import OrderRouter


class UnifiedOrderManager:
    def __init__(
        self,
        router: OrderRouter,
        repository=None,
        timestamp_ms: Callable[[], int] | None = None,
        initial_equity: float = 100000.0,
        fee_rate: float = 0.0,
        on_order_update: Callable[[str], Awaitable[None]] | None = None,
        risk_manager: Any | None = None,
        price_provider: Callable[[str], float | None] | None = None,
    ) -> None:
        self.router = router
        self.repository = repository
        self.timestamp_ms = timestamp_ms or self._current_timestamp_ms
        self.initial_equity = initial_equity
        self.fee_rate = fee_rate
        self.on_order_update = on_order_update
        self.risk_manager = risk_manager
        self.price_provider = price_provider
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
        if not self._passes_risk_gate(order, strategy_name):
            order.status = OrderStatus.REJECTED
            self._persist_order(order, strategy_name)
            if self.on_order_update is not None:
                await self.on_order_update(strategy_name)
            return order

        submitted_order = await self.router.submit(order)
        self._persist_order(submitted_order, strategy_name)
        if self.on_order_update is not None:
            await self.on_order_update(strategy_name)
        return submitted_order

    async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
        return await self.router.cancel(order_id, symbol)

    def get_position(self, strategy_name: str, symbol: str) -> Any:
        if self.repository is not None and hasattr(self.repository, "get_position"):
            return self.repository.get_position(strategy_name, symbol)
        return None

    def get_balance(self, strategy_name: str) -> float:
        if self.repository is not None and hasattr(self.repository, "get_account"):
            account = self.repository.get_account(strategy_name)
            if account is not None:
                return account.cash_balance
        return self._balances.get(strategy_name, 0.0)

    def set_balance(self, strategy_name: str, amount: float) -> None:
        self._balances[strategy_name] = amount

    def _passes_risk_gate(self, order: Order, strategy_name: str) -> bool:
        if self.risk_manager is None:
            return True

        account = None
        position = None
        if self.repository is not None:
            if hasattr(self.repository, "get_account"):
                account = self.repository.get_account(strategy_name)
            if hasattr(self.repository, "get_position"):
                position = self.repository.get_position(strategy_name, order.symbol)

        total_equity = account.equity if account is not None else self.initial_equity
        order_price = order.price
        if order_price is None and self.price_provider is not None:
            order_price = self.price_provider(order.symbol)

        current_amount = 0.0
        if position is not None:
            current_amount = abs(position.amount)
            if getattr(position, "side", "long") == "short":
                current_amount = -current_amount
        order_amount = order.amount if order.side == OrderSide.BUY else -order.amount
        resulting_position_value = abs(current_amount + order_amount) * (order_price or 0.0)
        daily_pnl = account.daily_pnl if account is not None else 0.0
        current_equity = account.equity if account is not None else self.initial_equity
        initial_equity = account.initial_equity if account is not None else self.initial_equity

        result = self.risk_manager.check_order(
            order=order,
            current_position_value=0.0,
            total_equity=total_equity,
            order_value=resulting_position_value,
            daily_pnl=daily_pnl,
            peak_equity=max(initial_equity, current_equity),
            current_equity=current_equity,
        )
        return result.passed

    def _persist_order(self, order: Order, strategy_name: str) -> None:
        if self.repository is None:
            return

        timestamp = order.fill_time or self.timestamp_ms()
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
                fill_price=order.fill_price or 0.0,
                timestamp=timestamp,
            )
        )

        if order.status == OrderStatus.FILLED:
            PaperAccountingService(
                repository=self.repository,
                initial_equity=self.initial_equity,
                fee_rate=self.fee_rate,
            ).process_filled_order(order, strategy_name, timestamp)

    @staticmethod
    def _current_timestamp_ms() -> int:
        return int(time.time() * 1000)
