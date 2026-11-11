import math
from enum import Enum

import pytest

from src.backtest.engine import BacktestEngine
from src.backtest.matcher import MatchResult, OrderMatcher
from src.core.types import Bar, Order, OrderSide, OrderStatus, OrderType, PositionSide
from src.strategy.base import BaseStrategy


def candle(timestamp, opening=10, close=None):
    close = opening if close is None else close
    return Bar(
        timestamp, opening, max(opening, close) + 1,
        max(0, min(opening, close) - 1), close, 100,
    )


class ScriptedStrategy(BaseStrategy):
    name = "spot_test"
    symbol = "BTC-USDT"

    def __init__(self, schedule):
        super().__init__()
        self.schedule = schedule
        self.orders = []
        self.updates = []

    async def on_bar(self, bar):
        for side, amount, price in self.schedule.get(bar.timestamp, []):
            submit = self.buy if side == "buy" else self.sell
            self.orders.append(await submit(self.symbol, amount, price))

    async def on_order(self, order):
        self.updates.append((order.id, order.status))


@pytest.mark.parametrize(
    ("cash", "amount", "fee_rate", "expected_status", "cash_after"),
    [
        (10, 2, 0, OrderStatus.REJECTED, 10),
        (20, 2, 0, OrderStatus.FILLED, 0),
        (20, 2, 0.05, OrderStatus.REJECTED, 20),
        (21, 2, 0.05, OrderStatus.FILLED, 0),
        (100, 0, 0, OrderStatus.REJECTED, 100),
    ],
)
async def test_spot_buy_checks_actual_cost_including_fee(
    cash, amount, fee_rate, expected_status, cash_after,
):
    strategy = ScriptedStrategy({0: [("buy", amount, None)]})

    report = await BacktestEngine(cash, OrderMatcher(slippage=0, fee_rate=fee_rate)).run(
        strategy, [candle(0), candle(1), candle(2)]
    )

    assert strategy.updates == [(strategy.orders[0].id, expected_status)]
    assert strategy.get_balance() == pytest.approx(cash_after)
    assert len(report.trades) == int(expected_status == OrderStatus.FILLED)
    if expected_status == OrderStatus.REJECTED:
        assert strategy.orders[0].fill_price is None
        assert strategy.orders[0].filled_amount == 0
        assert strategy.get_position("BTC-USDT") is None
        assert report.equity_curve == [cash, cash, cash, cash]
        assert await strategy.cancel(strategy.orders[0].id) is False


@pytest.mark.parametrize("opening,slippage", [(12, 0), (10, 0.1)])
async def test_spot_buy_rechecks_gap_and_slippage_at_fill(opening, slippage):
    strategy = ScriptedStrategy({0: [("buy", 2, None)]})

    report = await BacktestEngine(20, OrderMatcher(slippage=slippage, fee_rate=0)).run(
        strategy, [candle(0), candle(1, opening)]
    )

    assert strategy.updates == [(strategy.orders[0].id, OrderStatus.REJECTED)]
    assert report.trades == []
    assert strategy.get_balance() == 20


async def test_spot_rejects_naked_sell_and_oversell_without_partial_accounting():
    strategy = ScriptedStrategy({
        0: [("sell", 1, None)],
        1: [("buy", 2, None)],
        2: [("sell", 3, None)],
        3: [("sell", 2, None)],
    })

    report = await BacktestEngine(100, OrderMatcher(slippage=0, fee_rate=0.05)).run(
        strategy, [candle(i) for i in range(6)]
    )

    assert [status for _, status in strategy.updates] == [
        OrderStatus.REJECTED, OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.FILLED,
    ]
    assert [trade["side"] for trade in report.trades] == ["buy", "sell"]
    assert strategy.get_balance() == 98
    assert strategy.get_positions("BTC-USDT") == []
    assert report.final_equity == 98


@pytest.mark.parametrize("side", ["buy", "sell"])
async def test_same_bar_competing_orders_use_updated_funds_and_holdings(side):
    schedule = {0: [("buy", 1, None), ("buy", 1, None)]}
    cash = 10
    if side == "sell":
        schedule = {0: [("buy", 1, None)], 1: [("sell", 1, None), ("sell", 1, None)]}
    strategy = ScriptedStrategy(schedule)

    report = await BacktestEngine(cash, OrderMatcher(slippage=0, fee_rate=0)).run(
        strategy, [candle(i) for i in range(4)]
    )

    assert [status for _, status in strategy.updates][-2:] == [
        OrderStatus.FILLED, OrderStatus.REJECTED,
    ]
    assert len(report.trades) == (1 if side == "buy" else 2)
    assert strategy.get_balance() == (0 if side == "buy" else 10)


async def test_sell_then_buy_can_reuse_same_bar_net_proceeds():
    strategy = ScriptedStrategy({
        0: [("buy", 2, None)],
        1: [("sell", 2, None), ("buy", 1, None)],
    })

    report = await BacktestEngine(22, OrderMatcher(slippage=0, fee_rate=0.1)).run(
        strategy, [candle(i) for i in range(4)]
    )

    assert len(report.trades) == 3
    assert strategy.get_balance() == 7
    assert strategy.get_position("BTC-USDT").amount == 1
    assert report.final_equity == 17


async def test_pending_limits_do_not_reserve_cash_and_cancel_has_no_refund():
    class PendingStrategy(ScriptedStrategy):
        async def on_bar(self, bar):
            await super().on_bar(bar)
            if bar.timestamp == 0:
                self.cash_while_pending = self.get_balance()
            if bar.timestamp == 1:
                self.cancelled = await self.cancel(self.orders[0].id)
                self.cash_after_cancel = self.get_balance()

    strategy = PendingStrategy({0: [("buy", 2, 5), ("buy", 1, None)]})
    report = await BacktestEngine(10, OrderMatcher(slippage=0, fee_rate=0)).run(
        strategy, [candle(0), candle(1), candle(2, 5)]
    )

    assert strategy.cash_while_pending == 10
    assert strategy.cancelled is True
    assert strategy.cash_after_cancel == 0
    assert len(report.trades) == 1
    assert strategy.orders[0].status == OrderStatus.CANCELLED


async def test_queries_share_portfolio_but_return_detached_scoped_snapshots():
    class Side(Enum):
        LONG = "long"

    class QueryStrategy(ScriptedStrategy):
        async def on_init(self):
            self.initial = (self.get_balance(), self.get_positions(self.symbol))

        async def on_order(self, order):
            await super().on_order(order)
            position = self.get_position(self.symbol, side=PositionSide.LONG)
            self.post_fill = (
                self.get_balance(), position.amount, position.entry_price,
                position.mark_price, position.unrealized_pnl,
            )
            position.amount = 999
            position.entry_price = 999
            self.get_positions(self.symbol).clear()

        async def on_bar(self, bar):
            await super().on_bar(bar)
            if bar.timestamp == 1:
                position = self.get_position("btc/usdt")
                self.at_close = (
                    self.get_balance(), position.amount, position.entry_price,
                    position.mark_price, position.unrealized_pnl,
                )
                self.wrong_strategy_cash = self._order_manager.get_balance("other")
                self.wrong_strategy_position = self._order_manager.get_position(
                    "other", self.symbol,
                )
                self.wrong_strategy_positions = self._order_manager.get_positions(
                    "other", self.symbol,
                )
                self.wrong_symbol = self.get_position("ETH-USDT")
                self.wrong_side = self.get_position(self.symbol, "short")
                self.enum_position = self.get_position(self.symbol, Side.LONG)

    strategy = QueryStrategy({0: [("buy", 2, None)]})
    report = await BacktestEngine(100, OrderMatcher(slippage=0, fee_rate=0.05)).run(
        strategy, [candle(0), candle(1, 10, close=12)]
    )

    assert strategy.initial == (100, [])
    assert strategy.post_fill == (79, 2, 10, 10, 0)
    assert strategy.at_close == (79, 2, 10, 12, 4)
    assert strategy.wrong_strategy_cash == 0
    assert strategy.wrong_strategy_position is None
    assert strategy.wrong_strategy_positions == []
    assert strategy.wrong_symbol is None
    assert strategy.wrong_side is None
    assert strategy.enum_position.amount == 2
    assert report.final_equity == 103


async def test_query_without_strategy_symbol_binds_first_order():
    class NoSymbolStrategy(BaseStrategy):
        name = "no_symbol"

        async def on_bar(self, bar):
            if bar.timestamp == 0:
                await self.buy("BTC/USDT", 1)

    strategy = NoSymbolStrategy()
    report = await BacktestEngine(20, OrderMatcher(slippage=0, fee_rate=0)).run(
        strategy, [candle(0), candle(1)]
    )

    assert len(report.trades) == 1
    assert strategy.get_balance() == 10
    assert strategy.get_position("BTC-USDT").amount == 1


async def test_plain_custom_strategy_does_not_require_name_or_symbol_attributes():
    class PlainStrategy:
        async def on_init(self):
            pass

        async def on_bar(self, bar):
            if bar.timestamp == 0:
                return Order(
                    id="plain", symbol="BTC-USDT", side=OrderSide.BUY,
                    type=OrderType.MARKET, amount=1,
                )

        async def on_order(self, order):
            self.last_status = order.status

    strategy = PlainStrategy()
    report = await BacktestEngine(20, OrderMatcher(slippage=0, fee_rate=0)).run(
        strategy, [candle(0), candle(1)]
    )

    assert strategy.last_status == OrderStatus.FILLED
    assert len(report.trades) == 1
    assert report.final_equity == 20


@pytest.mark.parametrize("symbol", ["BTC-USDT-SWAP", "BTC/USDT:USDT", "BTC-USDT-261225"])
async def test_derivative_strategy_context_is_rejected(symbol):
    strategy = ScriptedStrategy({})
    strategy.symbol = symbol

    with pytest.raises(ValueError, match="spot"):
        await BacktestEngine(100, OrderMatcher()).run(strategy, [candle(0)])


async def test_multiple_symbols_cannot_share_one_spot_portfolio():
    class MultipleSymbols(BaseStrategy):
        name = "multi"

        def __init__(self):
            super().__init__()
            self.updates = []

        async def on_bar(self, bar):
            if bar.timestamp == 0:
                await self.buy("BTC-USDT", 1)
                await self.buy("ETH-USDT", 1)

        async def on_order(self, order):
            self.updates.append((order.symbol, order.status))

    strategy = MultipleSymbols()
    report = await BacktestEngine(100, OrderMatcher(slippage=0, fee_rate=0)).run(
        strategy, [candle(0), candle(1)]
    )

    assert ("ETH-USDT", OrderStatus.REJECTED) in strategy.updates
    assert [(trade["symbol"], trade["amount"]) for trade in report.trades] == [("BTC-USDT", 1)]
    assert strategy.get_position("ETH-USDT") is None
    assert strategy.get_position("BTC-USDT").amount == 1


@pytest.mark.parametrize("changed_symbol", ["ETH-USDT", "BTC-USDT-SWAP"])
async def test_pending_order_symbol_mutation_is_rejected_before_matching(changed_symbol):
    class MutatingStrategy(ScriptedStrategy):
        async def on_bar(self, bar):
            await super().on_bar(bar)
            if bar.timestamp == 1:
                self.orders[0].symbol = changed_symbol

    strategy = MutatingStrategy({0: [("buy", 1, 5)]})
    report = await BacktestEngine(100, OrderMatcher(slippage=0, fee_rate=0)).run(
        strategy, [candle(0), candle(1), candle(2, 4), candle(3, 4)]
    )

    order = strategy.orders[0]
    assert strategy.updates == [(order.id, OrderStatus.REJECTED)]
    assert order.status == OrderStatus.REJECTED
    assert order.fill_price is None
    assert order.filled_amount == 0
    assert "spot" in order.params["rejection_reason"]
    assert report.trades == []
    assert strategy.get_balance() == 100
    assert strategy.get_positions("BTC-USDT") == []
    assert strategy.get_positions(changed_symbol) == []
    assert report.equity_curve == [100, 100, 100, 100, 100]
    assert await strategy.cancel(order.id) is False


async def test_pending_order_same_symbol_alias_remains_executable():
    class AliasStrategy(ScriptedStrategy):
        async def on_bar(self, bar):
            await super().on_bar(bar)
            if bar.timestamp == 1:
                self.orders[0].symbol = "btc/usdt"

    strategy = AliasStrategy({0: [("buy", 1, 5)]})
    report = await BacktestEngine(100, OrderMatcher(slippage=0, fee_rate=0)).run(
        strategy, [candle(0), candle(1), candle(2, 4), candle(3, 4)]
    )

    assert strategy.updates == [(strategy.orders[0].id, OrderStatus.FILLED)]
    assert len(report.trades) == 1
    assert report.trades[0]["price"] == 4
    assert strategy.get_balance() == 96
    assert strategy.get_position("BTC-USDT").amount == 1


@pytest.mark.parametrize(
    "capital", [0, -1, math.nan, math.inf, True, pytest.param(10**400, id="overflow")],
)
async def test_invalid_initial_cash_cannot_start_backtest(capital):
    with pytest.raises(ValueError, match="capital"):
        await BacktestEngine(capital, OrderMatcher()).run(ScriptedStrategy({}), [candle(0)])


@pytest.mark.parametrize(
    ("price", "fee"), [(None, 0), (math.nan, 0), (math.inf, 0), (0, 0), (-1, 0), (10, math.nan),
                       (10, math.inf), (10, -1)],
)
async def test_invalid_match_values_are_rejected_without_corrupting_cash(price, fee):
    class InvalidMatcher:
        def match(self, order, bar):
            return MatchResult(OrderStatus.FILLED, price, fee)

    strategy = ScriptedStrategy({0: [("buy", 1, None)]})
    report = await BacktestEngine(100, InvalidMatcher()).run(
        strategy, [candle(0), candle(1)]
    )

    assert strategy.updates == [(strategy.orders[0].id, OrderStatus.REJECTED)]
    assert strategy.get_balance() == 100
    assert report.trades == []


async def test_mutated_nonfinite_order_amount_cannot_corrupt_account():
    class MalformedStrategy(ScriptedStrategy):
        async def on_bar(self, bar):
            await super().on_bar(bar)
            if bar.timestamp == 0:
                self.orders[0].amount = math.nan

    strategy = MalformedStrategy({0: [("buy", 1, None)]})
    report = await BacktestEngine(100, OrderMatcher()).run(
        strategy, [candle(0), candle(1)]
    )

    assert strategy.updates == [(strategy.orders[0].id, OrderStatus.REJECTED)]
    assert report.trades == []
    assert strategy.get_balance() == 100


async def test_explicit_nonspot_model_is_not_silently_used_as_spot():
    with pytest.raises(ValueError, match="spot"):
        await BacktestEngine(100, OrderMatcher(), market_type="swap").run(
            ScriptedStrategy({}), [candle(0)]
        )


async def test_exact_affordability_tolerates_binary_roundoff_without_negative_cash():
    strategy = ScriptedStrategy({0: [("buy", 3, None)]})

    report = await BacktestEngine(0.3, OrderMatcher(slippage=0, fee_rate=0)).run(
        strategy, [candle(0, 0.1), candle(1, 0.1)]
    )

    assert strategy.orders[0].status == OrderStatus.FILLED
    assert strategy.get_balance() == 0
    assert len(report.trades) == 1


async def test_rejected_fill_queries_do_not_see_future_close_or_change_position():
    class RejectionQueries(ScriptedStrategy):
        async def on_order(self, order):
            await super().on_order(order)
            if order.status == OrderStatus.REJECTED:
                position = self.get_position(self.symbol)
                self.rejected_state = (
                    self.get_balance(), position.amount, position.mark_price,
                    position.unrealized_pnl,
                )

    strategy = RejectionQueries({0: [("buy", 2, None)], 1: [("sell", 3, None)]})
    report = await BacktestEngine(100, OrderMatcher(slippage=0, fee_rate=0)).run(
        strategy, [candle(0), candle(1), candle(2, 12, close=50)]
    )

    assert strategy.rejected_state == (80, 2, 12, 4)
    assert strategy.get_position(strategy.symbol).mark_price == 50
    assert len(report.trades) == 1


async def test_partial_spot_sale_preserves_average_cost_and_allocates_entry_fee():
    strategy = ScriptedStrategy({0: [("buy", 2, None)], 1: [("sell", 1, None)]})

    report = await BacktestEngine(100, OrderMatcher(slippage=0, fee_rate=0.05)).run(
        strategy, [candle(0), candle(1), candle(2, 12)]
    )

    position = strategy.get_position(strategy.symbol)
    assert strategy.get_balance() == pytest.approx(90.4)
    assert position.amount == 1
    assert position.entry_price == 10
    assert position.unrealized_pnl == 2
    assert position.realized_pnl == pytest.approx(0.9)
    assert report.trades[-1]["pnl"] == pytest.approx(0.9)


async def test_nonfinite_aggregate_quantity_cannot_enter_portfolio():
    strategy = ScriptedStrategy({0: [("buy", 1e308, None), ("buy", 1e308, None)]})

    report = await BacktestEngine(100, OrderMatcher(slippage=0, fee_rate=0)).run(
        strategy, [candle(0, 1e-308), candle(1, 1e-308)]
    )

    assert [status for _, status in strategy.updates] == [OrderStatus.FILLED, OrderStatus.REJECTED]
    assert strategy.get_position(strategy.symbol).amount == 1e308
    assert len(report.trades) == 1


@pytest.mark.parametrize(
    ("cash", "opening", "side", "amount"),
    [(1, 10, "sell", 1e-13), (1e-15, 1e-13, "buy", 1)],
)
async def test_roundoff_tolerance_does_not_fund_orders_without_resources(
    cash, opening, side, amount,
):
    strategy = ScriptedStrategy({0: [(side, amount, None)]})

    report = await BacktestEngine(cash, OrderMatcher(slippage=0, fee_rate=0)).run(
        strategy, [candle(0, opening), candle(1, opening)]
    )

    assert strategy.orders[0].status == OrderStatus.REJECTED
    assert strategy.get_balance() == cash
    assert strategy.get_positions(strategy.symbol) == []
    assert report.trades == []
