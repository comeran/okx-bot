import pytest

from src.core.types import Bar, Order, OrderSide, OrderType
from src.strategy.yaml_strategy import YAMLStrategy, parse_condition


class RecordingOrderManager:
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
        return Order(
            id=f"{strategy_name}-1",
            symbol=symbol,
            side=side,
            type=order_type,
            amount=amount,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )


def test_parse_simple_condition() -> None:
    values = {"fast_ma": 50500, "slow_ma": 50000, "rsi": 65}

    assert parse_condition("fast_ma > slow_ma", values) is True
    assert parse_condition("fast_ma < slow_ma", values) is False
    assert parse_condition("rsi < 70", values) is True
    assert parse_condition("rsi > 70", values) is False


def test_parse_condition_with_literal() -> None:
    values = {"close": 50500}

    assert parse_condition("close > 50000", values) is True
    assert parse_condition("close < 50000", values) is False


def test_yaml_strategy_creation() -> None:
    config = {
        "name": "MA_Cross",
        "symbol": "BTC-USDT",
        "timeframe": "1h",
        "params": {"fast": 10, "slow": 30},
        "indicators": {
            "fast_ma": "sma(close, {{ fast }})",
            "slow_ma": "sma(close, {{ slow }})",
        },
        "conditions": {
            "buy": ["fast_ma > slow_ma"],
            "sell": ["fast_ma < slow_ma"],
        },
    }

    strategy = YAMLStrategy(config)

    assert strategy.name == "MA_Cross"
    assert strategy.symbol == "BTC-USDT"


@pytest.mark.asyncio
async def test_yaml_strategy_returns_buy_order_for_backtest_engine() -> None:
    strategy = YAMLStrategy(
        {
            "name": "yaml_cross",
            "symbol": "BTC-USDT",
            "timeframe": "1h",
            "conditions": {"buy": ["close > 100"]},
        }
    )
    strategy.set_order_manager(RecordingOrderManager())

    order = await strategy.on_bar(
        Bar(timestamp=1, open=100, high=102, low=99, close=101, volume=10)
    )

    assert isinstance(order, Order)
    assert order.symbol == "BTC-USDT"
    assert order.side == OrderSide.BUY


@pytest.mark.asyncio
async def test_yaml_strategy_returns_sell_order_for_backtest_engine() -> None:
    strategy = YAMLStrategy(
        {
            "name": "yaml_cross",
            "symbol": "BTC-USDT",
            "timeframe": "1h",
            "conditions": {"sell": ["close < 100"]},
        }
    )
    strategy.set_order_manager(RecordingOrderManager())

    order = await strategy.on_bar(Bar(timestamp=1, open=100, high=101, low=98, close=99, volume=10))

    assert isinstance(order, Order)
    assert order.symbol == "BTC-USDT"
    assert order.side == OrderSide.SELL
