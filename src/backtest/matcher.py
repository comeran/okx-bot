from dataclasses import dataclass

from src.core.types import Bar, Order, OrderSide, OrderStatus, OrderType


@dataclass
class MatchResult:
    status: OrderStatus
    fill_price: float | None = None
    fee: float = 0.0


class OrderMatcher:
    def __init__(self, slippage: float = 0.001, fee_rate: float = 0.0005) -> None:
        self.slippage = slippage
        self.fee_rate = fee_rate

    def match(self, order: Order, bar: Bar) -> MatchResult:
        fill_price = self._fill_price(order, bar)
        if fill_price is None:
            return MatchResult(status=OrderStatus.PENDING)

        return MatchResult(
            status=OrderStatus.FILLED,
            fill_price=fill_price,
            fee=fill_price * order.amount * self.fee_rate,
        )

    def _fill_price(self, order: Order, bar: Bar) -> float | None:
        if order.type == OrderType.MARKET:
            return self._apply_slippage(bar.open, order.side)

        if order.price is None:
            return None

        if order.type == OrderType.LIMIT and bar.low <= order.price <= bar.high:
            return order.price

        if order.type == OrderType.STOP:
            if order.side == OrderSide.SELL and bar.low <= order.price:
                return order.price
            if order.side == OrderSide.BUY and bar.high >= order.price:
                return order.price

        return None

    def _apply_slippage(self, price: float, side: OrderSide) -> float:
        if side == OrderSide.BUY:
            return price * (1 + self.slippage)
        return price * (1 - self.slippage)
