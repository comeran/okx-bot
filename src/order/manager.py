import inspect
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from src.core.types import Order, OrderSide, OrderStatus, OrderType
from src.data.models import OrderRecord
from src.order.accounting import PaperAccountingService
from src.order.router import OrderRouter

OrderUpdateCallback = Callable[[str], Awaitable[None] | None]
RiskEventCallback = Callable[[dict[str, object]], Awaitable[None] | None]
LiveStateRefresher = Callable[[str, str], Awaitable[None]]


@dataclass(frozen=True)
class RiskGateResult:
    passed: bool
    reason: str = ""
    order_value: float = 0.0
    effective_price: float | None = None


def risk_reason_code(reason: str) -> str:
    return {
        "Order exceeds maximum position size": "max_position_exceeded",
        "Daily loss exceeds maximum allowed loss": "daily_loss_exceeded",
        "Drawdown exceeds maximum allowed drawdown": "drawdown_exceeded",
        "Order requires a stop loss": "stop_loss_required",
        "Kill switch engaged": "kill_switch_engaged",
        "Live safeguards require a configured risk manager": "live_safeguard_unavailable",
        "Live safeguards require account state": "live_safeguard_unavailable",
        "Live safeguards require current position state": "live_safeguard_unavailable",
        "Live safeguards require a current price": "live_safeguard_unavailable",
        "Live orders must reduce existing exposure": "live_reduce_only_required",
        "Live opening orders are disabled": "live_opening_disabled",
        "Live order exceeds configured notional cap": "live_order_notional_exceeded",
        "Live spot sell requires existing position": "live_spot_position_required",
    }.get(reason, "risk_rejected")


def position_notional(position: Any) -> float:
    price = float(
        getattr(position, "mark_price", None) or getattr(position, "entry_price", 0.0) or 0.0
    )
    return abs(float(getattr(position, "amount", 0.0) or 0.0)) * price


class UnifiedOrderManager:
    def __init__(
        self,
        router: OrderRouter,
        repository=None,
        timestamp_ms: Callable[[], int] | None = None,
        initial_equity: float = 100000.0,
        fee_rate: float = 0.0,
        on_order_update: OrderUpdateCallback | None = None,
        on_risk_event: RiskEventCallback | None = None,
        risk_manager: Any | None = None,
        price_provider: Callable[[str], float | None] | None = None,
        kill_switch_checker: Callable[[], bool] | None = None,
        live_safeguards: bool = False,
        live_market_type: str = "",
        live_state_refresher: LiveStateRefresher | None = None,
        allow_live_open_orders: bool = False,
        live_max_order_notional: float = 0.0,
    ) -> None:
        self.router = router
        self.repository = repository
        self.timestamp_ms = timestamp_ms or self._current_timestamp_ms
        self.initial_equity = initial_equity
        self.fee_rate = fee_rate
        self.on_order_update = on_order_update
        self.on_risk_event = on_risk_event
        self.risk_manager = risk_manager
        self.price_provider = price_provider
        self.kill_switch_checker = kill_switch_checker
        self.live_safeguards = live_safeguards
        self.live_market_type = live_market_type.strip().lower()
        self.live_state_refresher = live_state_refresher
        self.allow_live_open_orders = allow_live_open_orders
        self.live_max_order_notional = live_max_order_notional
        self._balances: dict[str, float] = {}
        self._order_seq = 0

    async def submit(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        amount: float,
        price: float | None = None,
        trigger_price: float | None = None,
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
            trigger_price=trigger_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        if self.kill_switch_checker is not None and self.kill_switch_checker():
            risk_result = RiskGateResult(
                passed=False,
                reason="Kill switch engaged",
                effective_price=price,
            )
            return await self._reject_order(order, strategy_name, risk_result)

        if self.live_safeguards and self.live_state_refresher is not None:
            await self.live_state_refresher(strategy_name, symbol)
        risk_result = self._check_risk_gate(order, strategy_name)
        if not risk_result.passed:
            return await self._reject_order(order, strategy_name, risk_result)

        submitted_order = await self.router.submit(order)
        self._persist_order(submitted_order, strategy_name)
        if (
            self.live_safeguards
            and self.live_state_refresher is not None
            and submitted_order.status == OrderStatus.FILLED
        ):
            await self.live_state_refresher(strategy_name, symbol)
        if self.on_order_update is not None:
            await self._run_callback(self.on_order_update, strategy_name)
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

    def _check_risk_gate(self, order: Order, strategy_name: str) -> RiskGateResult:
        if self.risk_manager is None:
            if self.live_safeguards:
                return RiskGateResult(
                    passed=False,
                    reason="Live safeguards require a configured risk manager",
                )
            return RiskGateResult(passed=True)

        account = None
        position = None
        if self.repository is not None:
            if hasattr(self.repository, "get_account"):
                account = self.repository.get_account(strategy_name)
            if hasattr(self.repository, "get_position"):
                position = self.repository.get_position(strategy_name, order.symbol)

        order_price = order.price
        if order_price is None and self.price_provider is not None:
            order_price = self.price_provider(order.symbol)

        if self.live_safeguards:
            live_result = self._check_live_order_safety(order, account, position, order_price)
            if not live_result.passed:
                return live_result

        current_amount = 0.0
        if position is not None:
            current_amount = abs(position.amount)
            if getattr(position, "side", "long") == "short":
                current_amount = -current_amount
        order_amount = order.amount if order.side == OrderSide.BUY else -order.amount
        current_position_value = abs(current_amount) * (order_price or 0.0)
        if position is not None and order_price is None:
            current_position_value = position_notional(position)
        resulting_position_value = abs(current_amount + order_amount) * (order_price or 0.0)
        current_other_position_value = self._other_position_notional(strategy_name, order.symbol)
        current_total_position_value = current_other_position_value + current_position_value
        resulting_total_position_value = current_other_position_value + resulting_position_value
        if resulting_total_position_value < current_total_position_value:
            risk_position_value = 0.0
            risk_order_value = 0.0
        else:
            risk_position_value = current_other_position_value
            risk_order_value = resulting_position_value

        total_equity = account.equity if account is not None else self.initial_equity
        daily_pnl = account.daily_pnl if account is not None else 0.0
        current_equity = account.equity if account is not None else self.initial_equity
        initial_equity = account.initial_equity if account is not None else self.initial_equity

        result = self.risk_manager.check_order(
            order=order,
            current_position_value=risk_position_value,
            total_equity=total_equity,
            order_value=risk_order_value,
            daily_pnl=daily_pnl,
            peak_equity=max(initial_equity, current_equity),
            current_equity=current_equity,
        )
        return RiskGateResult(
            passed=result.passed,
            reason=result.reason,
            order_value=resulting_position_value,
            effective_price=order_price,
        )

    def _check_live_order_safety(
        self,
        order: Order,
        account: Any,
        position: Any,
        order_price: float | None,
    ) -> RiskGateResult:
        if account is None:
            return RiskGateResult(False, "Live safeguards require account state")
        if order_price is None or order_price <= 0:
            return RiskGateResult(False, "Live safeguards require a current price")

        current_amount = 0.0
        has_position = False
        if position is not None:
            position_amount = abs(float(getattr(position, "amount", 0.0) or 0.0))
            if position_amount > 0:
                has_position = True
                current_amount = position_amount
                if getattr(position, "side", "long") == "short":
                    current_amount = -current_amount

        order_amount = order.amount if order.side == OrderSide.BUY else -order.amount
        resulting_amount = current_amount + order_amount
        is_reducing = has_position and abs(resulting_amount) < abs(current_amount)
        if is_reducing:
            if self.live_market_type != "spot":
                order.reduce_only = True
                order.params = {**order.params, "reduceOnly": True}
            return RiskGateResult(True, effective_price=order_price)

        if not self.allow_live_open_orders:
            return RiskGateResult(
                False,
                "Live opening orders are disabled",
                order_value=abs(resulting_amount) * order_price,
                effective_price=order_price,
            )

        if self.live_market_type == "spot" and order.side == OrderSide.SELL and not has_position:
            return RiskGateResult(
                False,
                "Live spot sell requires existing position",
                order_value=order.amount * order_price,
                effective_price=order_price,
            )

        order_value = order.amount * order_price
        if self.live_max_order_notional > 0 and order_value > self.live_max_order_notional:
            return RiskGateResult(
                False,
                "Live order exceeds configured notional cap",
                order_value=order_value,
                effective_price=order_price,
            )

        return RiskGateResult(True, order_value=order_value, effective_price=order_price)

    def _other_position_notional(self, strategy_name: str, symbol: str) -> float:
        if self.repository is None or not hasattr(self.repository, "get_open_positions"):
            return 0.0
        return sum(
            position_notional(position)
            for position in self.repository.get_open_positions(strategy_name)
            if getattr(position, "symbol", None) != symbol
        )

    async def _reject_order(
        self,
        order: Order,
        strategy_name: str,
        risk_result: RiskGateResult,
    ) -> Order:
        order.status = OrderStatus.REJECTED
        timestamp = self.timestamp_ms()
        self._persist_order(order, strategy_name, timestamp=timestamp)
        try:
            if self.on_risk_event is not None:
                await self._run_callback(
                    self.on_risk_event,
                    self._risk_event_payload(order, strategy_name, risk_result, timestamp),
                )
        finally:
            if self.on_order_update is not None:
                await self._run_callback(self.on_order_update, strategy_name)
        return order

    @staticmethod
    async def _run_callback(callback: Callable[..., Awaitable[None] | None], *args: object) -> None:
        result = callback(*args)
        if inspect.isawaitable(result):
            await result

    def _risk_event_payload(
        self,
        order: Order,
        strategy_name: str,
        result: RiskGateResult,
        timestamp: int,
    ) -> dict[str, object]:
        return {
            "type": "risk_event",
            "strategy": strategy_name,
            "order_id": order.id,
            "symbol": order.symbol,
            "side": order.side.value,
            "order_type": order.type.value,
            "amount": order.amount,
            "price": result.effective_price,
            "requested_price": order.price,
            "order_value": result.order_value,
            "reason": result.reason,
            "reason_code": risk_reason_code(result.reason),
            "timestamp": timestamp,
        }

    def _persist_order(
        self,
        order: Order,
        strategy_name: str,
        timestamp: int | None = None,
    ) -> None:
        if self.repository is None:
            return

        if timestamp is None:
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

        if order.status == OrderStatus.FILLED and getattr(self.router, "mode", None) != "live":
            PaperAccountingService(
                repository=self.repository,
                initial_equity=self.initial_equity,
                fee_rate=self.fee_rate,
            ).process_filled_order(order, strategy_name, timestamp)

    @staticmethod
    def _current_timestamp_ms() -> int:
        return int(time.time() * 1000)
