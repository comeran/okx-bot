from typing import Any

from src.backtest.matcher import OrderMatcher
from src.backtest.report import BacktestReport, generate_report
from src.core.types import Bar, Order, OrderSide, OrderStatus, OrderType


class BacktestOrderManager:
    def __init__(self) -> None:
        self._next_id = 0

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
        return Order(
            id=f"{strategy_name}-{self._next_id}",
            symbol=symbol,
            side=side,
            type=order_type,
            amount=amount,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

    async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
        return True


class BacktestEngine:
    def __init__(self, initial_capital: float, matcher: OrderMatcher) -> None:
        self.initial_capital = initial_capital
        self.matcher = matcher

    async def run(self, strategy: Any, bars: list[Bar]) -> BacktestReport:
        equity = self.initial_capital
        trades: list[dict[str, Any]] = []
        equity_curve = [equity]

        needs_order_manager = getattr(strategy, "_order_manager", None) is None and hasattr(
            strategy, "set_order_manager"
        )
        if needs_order_manager:
            strategy.set_order_manager(BacktestOrderManager())

        await strategy.on_init()

        for bar in bars:
            order = await strategy.on_bar(bar)
            if isinstance(order, Order):
                match = self.matcher.match(order, bar)
                order.status = match.status
                order.fill_price = match.fill_price

                if match.status == OrderStatus.FILLED and match.fill_price is not None:
                    order.fill_time = bar.timestamp
                    trade_value = match.fill_price * order.amount
                    if order.side == OrderSide.BUY:
                        pnl = -(trade_value + match.fee)
                    else:
                        pnl = trade_value - match.fee
                    equity += pnl
                    trades.append(
                        {
                            "pnl": pnl,
                            "fee": match.fee,
                            "timestamp": bar.timestamp,
                        }
                    )

                await strategy.on_order(order)

            equity_curve.append(equity)

        return generate_report(
            initial_capital=self.initial_capital,
            trades=trades,
            equity_curve=equity_curve,
        )
