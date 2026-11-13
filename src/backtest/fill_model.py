"""按点差、档位和可成交量成交，并把合约面值与资金费计入成本。"""

from __future__ import annotations

from dataclasses import dataclass

from src.backtest.matcher import OrderMatcher
from src.core.types import Order, OrderSide, OrderStatus, OrderType


@dataclass(frozen=True)
class PriceLevel:
    price: float
    size: float


@dataclass(frozen=True)
class ContractSpec:
    contract_size: float = 1.0
    funding_rate: float = 0.0


@dataclass
class BookMatch:
    status: OrderStatus
    fill_price: float | None = None
    fee: float = 0.0
    filled_amount: float = 0.0


class BookFillModel:
    def __init__(
        self,
        slippage: float = 0.001,
        fee_rate: float = 0.0005,
        spread: float = 0.0,
        levels: tuple[PriceLevel, ...] | list[PriceLevel] = (),
        contract: ContractSpec | None = None,
        use_volume: bool = False,
    ) -> None:
        self.slippage = slippage
        self.fee_rate = fee_rate
        self.spread = spread
        self.levels = tuple(levels)
        self.contract = contract
        self.use_volume = use_volume

    def funding_charge(self, *, quantity: float, mark: float) -> float:
        if self.contract is None or quantity <= 0 or mark <= 0:
            return 0.0
        return quantity * mark * self.contract.contract_size * self.contract.funding_rate

    def match(self, order: Order, bar: object) -> BookMatch:
        size = 1.0 if self.contract is None else self.contract.contract_size
        if self.levels:
            return self._walk_levels(order, size)
        touch = OrderMatcher(self.slippage, self.fee_rate).match(order, bar)
        if touch.fill_price is None:
            return BookMatch(status=OrderStatus.PENDING)
        price = self._with_spread(touch.fill_price, order.side)
        filled = order.amount
        if self.use_volume:
            filled = min(order.amount, getattr(bar, "volume", 0.0))
        return self._result(order, price, filled, size)

    def _with_spread(self, price: float, side: OrderSide) -> float:
        half = self.spread / 2
        if side == OrderSide.BUY:
            return price * (1 + half)
        return price * (1 - half)

    def _walk_levels(self, order: Order, contract_size: float) -> BookMatch:
        remaining = order.amount
        filled = 0.0
        cost = 0.0
        for level in self.levels:
            if remaining <= 0:
                break
            if level.size <= 0 or level.price <= 0:
                continue
            if not self._level_crosses(order, level.price):
                continue
            take = min(remaining, level.size)
            cost += take * level.price
            filled += take
            remaining -= take
        if filled <= 0:
            return BookMatch(status=OrderStatus.PENDING)
        return self._result(order, cost / filled, filled, contract_size)

    def _level_crosses(self, order: Order, price: float) -> bool:
        if order.type != OrderType.LIMIT or order.price is None:
            return True
        if order.side == OrderSide.BUY:
            return price <= order.price
        return price >= order.price

    def _result(self, order: Order, price: float, filled: float, contract_size: float) -> BookMatch:
        if filled <= 0 or price <= 0:
            return BookMatch(status=OrderStatus.PENDING)
        status = (
            OrderStatus.FILLED if filled >= order.amount else OrderStatus.PARTIALLY_FILLED
        )
        fee = price * filled * self.fee_rate * contract_size
        return BookMatch(status=status, fill_price=price, fee=fee, filled_amount=filled)
