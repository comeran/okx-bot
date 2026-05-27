from typing import Any

from src.backtest.matcher import OrderMatcher
from src.backtest.report import BacktestReport, generate_report
from src.core.types import Bar, Order, OrderSide, OrderStatus


class BacktestEngine:
    def __init__(self, initial_capital: float, matcher: OrderMatcher) -> None:
        self.initial_capital = initial_capital
        self.matcher = matcher

    async def run(self, strategy: Any, bars: list[Bar]) -> BacktestReport:
        equity = self.initial_capital
        trades: list[dict[str, Any]] = []
        equity_curve = [equity]

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
