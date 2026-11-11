from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from typing import Any

from src.backtest.matcher import OrderMatcher
from src.backtest.report import BacktestReport, generate_report
from src.core.types import Bar, Order, OrderSide, OrderStatus, OrderType, Position, PositionSide
from src.exchange.base import canonical_symbol, is_supported_canonical_symbol


def spot_backtest_symbol(symbol: str) -> str:
    normalized = canonical_symbol(symbol, market_type="spot")
    if not is_supported_canonical_symbol(normalized, market_type="spot"):
        raise ValueError("backtest supports a single spot symbol only")
    return normalized


def _finite_number(value: object, *, positive: bool) -> bool:
    try:
        return (
            type(value) in {int, float}
            and math.isfinite(value)
            and (value > 0 if positive else value >= 0)
        )
    except OverflowError:
        return False


def _exceeds(value: float, available: float) -> bool:
    return value > available and not math.isclose(
        value, available, rel_tol=1e-12, abs_tol=0
    )


class BacktestOrderManager:
    def __init__(
        self,
        portfolio: _Portfolio | None = None,
        *,
        strategy_name: str = "",
        symbol: str | None = None,
    ) -> None:
        self._next_id = 0
        self._submitted: list[Order] = []
        self._orders: dict[str, Order] = {}
        self._portfolio = portfolio if portfolio is not None else _Portfolio(cash=0)
        self._strategy_name = strategy_name
        self._symbol = symbol

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
        self._next_id += 1
        order = Order(
            id=f"{strategy_name}-{self._next_id}",
            symbol=symbol,
            side=side,
            type=order_type,
            amount=amount,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        self.track_order(order)
        self._submitted.append(order)
        return order

    async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
        order = self._orders.get(order_id)
        if (
            order is None
            or order.status != OrderStatus.PENDING
            or (symbol is not None and order.symbol != symbol)
        ):
            return False
        order.status = OrderStatus.CANCELLED
        self._submitted = [item for item in self._submitted if item is not order]
        return True

    def track_order(self, order: Order) -> None:
        self._orders[order.id] = order

    def take_submitted(self) -> list[Order]:
        submitted = self._submitted
        self._submitted = []
        return submitted

    def bind_symbol(self, symbol: str) -> None:
        normalized = spot_backtest_symbol(symbol)
        if self._symbol is not None and self._symbol != normalized:
            raise ValueError("backtest supports a single spot symbol only")
        self._symbol = normalized

    def get_balance(self, strategy_name: str) -> float:
        """Cash balance; pending orders do not reserve cash in this model."""
        return self._portfolio.cash if strategy_name == self._strategy_name else 0.0

    def get_position(
        self, strategy_name: str, symbol: str, side: str | None = None,
    ) -> Position | None:
        if (
            strategy_name != self._strategy_name
            or canonical_symbol(symbol, market_type="spot") != self._symbol
            or self._portfolio.quantity <= 0
            or (
                side is not None
                and str(getattr(side, "value", side)).lower() != PositionSide.LONG.value
            )
        ):
            return None
        portfolio = self._portfolio
        mark = portfolio.mark_price
        return Position(
            symbol=self._symbol,
            side=PositionSide.LONG,
            amount=portfolio.quantity,
            entry_price=portfolio.average_price,
            unrealized_pnl=(mark - portfolio.average_price) * portfolio.quantity,
            realized_pnl=portfolio.realized_pnl,
            mark_price=mark,
            timestamp=portfolio.timestamp,
        )

    def get_positions(self, strategy_name: str, symbol: str) -> list[Position]:
        position = self.get_position(strategy_name, symbol)
        return [] if position is None else [position]


@dataclass
class _Portfolio:
    cash: float
    quantity: float = 0.0
    average_price: float = 0.0
    open_fees: float = 0.0
    round_trip_pnls: list[float] = field(default_factory=list)
    realized_pnl: float = 0.0
    mark_price: float = 0.0
    timestamp: int = 0

    def fill_rejection(self, order: Order, price: float, fee: float) -> str | None:
        if not (
            _finite_number(order.amount, positive=True)
            and _finite_number(price, positive=True)
            and _finite_number(fee, positive=False)
        ):
            return "invalid spot fill amount, price or fee"
        notional = order.amount * price
        if not math.isfinite(notional):
            return "invalid spot fill notional"
        if order.side == OrderSide.BUY:
            cost = notional + fee
            if not math.isfinite(self.quantity + order.amount):
                return "invalid spot position quantity"
            if not math.isfinite(cost) or _exceeds(cost, self.cash):
                return "insufficient spot cash including fees"
        elif order.side == OrderSide.SELL:
            if _exceeds(order.amount, self.quantity):
                return "insufficient spot holdings"
            proceeds = self.cash + notional - fee
            if not math.isfinite(proceeds) or proceeds < 0:
                return "insufficient spot cash for fees"
        else:
            return "unsupported spot order side"
        return None

    def apply_fill(self, side: OrderSide, amount: float, price: float, fee: float) -> float:
        realized = 0.0
        if side == OrderSide.BUY:
            total = self.quantity + amount
            self.average_price = (
                (self.quantity / total) * self.average_price + (amount / total) * price
            )
            self.open_fees += fee
            self.quantity = total
            self.cash = max(0.0, self.cash - (amount * price + fee))
        else:
            entry_fee_share = self.open_fees * (amount / self.quantity)
            realized = (price - self.average_price) * amount - entry_fee_share - fee
            self.quantity = max(0.0, self.quantity - amount)
            self.cash = self.cash + amount * price - fee
            self.open_fees -= entry_fee_share
            self.round_trip_pnls.append(realized)
            self.realized_pnl += realized
            if self.quantity == 0:
                self.average_price = 0
                self.open_fees = 0
        return realized

    def equity(self, mark_price: float) -> float:
        return self.cash + self.quantity * mark_price


def _bars_per_year(bars: list[Bar]) -> float | None:
    intervals = sorted(
        current.timestamp - previous.timestamp
        for previous, current in zip(bars, bars[1:], strict=False)
        if current.timestamp > previous.timestamp
    )
    if not intervals:
        return None
    interval = intervals[len(intervals) // 2]
    if interval <= 0:
        return None
    return 365 * 24 * 60 * 60 * 1000 / interval


class BacktestEngine:
    def __init__(
        self, initial_capital: float, matcher: OrderMatcher, *,
        market_type: str = "spot", symbol: str | None = None,
    ) -> None:
        if market_type != "spot":
            raise ValueError("backtest supports the unleveraged spot model only")
        if not _finite_number(initial_capital, positive=True):
            raise ValueError("initial_capital must be positive and finite")
        self.initial_capital = initial_capital
        self.matcher = matcher
        self.symbol = spot_backtest_symbol(symbol) if symbol is not None else None

    async def run(self, strategy: Any, bars: list[Bar]) -> BacktestReport:
        portfolio = _Portfolio(cash=float(self.initial_capital))
        trades: list[dict[str, Any]] = []
        equity_curve = [portfolio.cash]
        pending: list[Order] = []

        strategy_symbol = getattr(strategy, "symbol", None)
        symbol = self.symbol
        if symbol is None and strategy_symbol is not None:
            symbol = spot_backtest_symbol(strategy_symbol)
        order_manager = BacktestOrderManager(
            portfolio, strategy_name=getattr(strategy, "name", ""), symbol=symbol,
        )
        if hasattr(strategy, "set_order_manager"):
            strategy.set_order_manager(order_manager)

        await strategy.on_init()

        for index, bar in enumerate(bars):
            if index % 1000 == 0:
                await asyncio.sleep(0)
            portfolio.mark_price = bar.open
            portfolio.timestamp = bar.timestamp
            resting: list[Order] = []
            for order in pending:
                if order.status != OrderStatus.PENDING:
                    continue
                try:
                    order_manager.bind_symbol(order.symbol)
                except ValueError as exc:
                    await self._reject_order(strategy, order, str(exc))
                    continue
                match = self.matcher.match(order, bar)
                if match.status == OrderStatus.FILLED:
                    reason = portfolio.fill_rejection(order, match.fill_price, match.fee)
                    if reason is not None:
                        await self._reject_order(strategy, order, reason)
                        continue
                    order.status = OrderStatus.FILLED
                    order.fill_price = match.fill_price
                    order.fill_time = bar.timestamp
                    order.filled_amount = order.amount
                    order.remaining_amount = 0
                    realized = portfolio.apply_fill(
                        order.side, order.amount, match.fill_price, match.fee
                    )
                    trades.append(
                        {
                            "symbol": order.symbol,
                            "side": order.side.value,
                            "amount": order.amount,
                            "price": match.fill_price,
                            "pnl": realized,
                            "fee": match.fee,
                            "timestamp": bar.timestamp,
                        }
                    )
                    await strategy.on_order(order)
                else:
                    order.status = match.status
                    resting.append(order)
            pending = [order for order in resting if order.status == OrderStatus.PENDING]

            portfolio.mark_price = bar.close
            equity_curve.append(portfolio.equity(bar.close))

            returned_order = await strategy.on_bar(bar)
            submitted = order_manager.take_submitted()
            if isinstance(returned_order, Order) and all(
                returned_order is not item for item in submitted
            ):
                submitted.append(returned_order)
            for order in submitted:
                if order.status == OrderStatus.PENDING:
                    try:
                        order_manager.bind_symbol(order.symbol)
                    except ValueError as exc:
                        await self._reject_order(strategy, order, str(exc))
                        continue
                    order_manager.track_order(order)
                    pending.append(order)

        return generate_report(
            initial_capital=self.initial_capital,
            trades=trades,
            equity_curve=equity_curve,
            round_trip_pnls=portfolio.round_trip_pnls,
            bars_per_year=_bars_per_year(bars),
        )

    async def _reject_order(self, strategy: Any, order: Order, reason: str) -> None:
        order.status = OrderStatus.REJECTED
        order.fill_price = None
        order.fill_time = None
        order.filled_amount = 0
        order.remaining_amount = order.amount
        order.params = {**order.params, "rejection_reason": reason}
        await strategy.on_order(order)
