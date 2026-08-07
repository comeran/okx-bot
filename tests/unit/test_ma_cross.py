import math

import pytest

from src.core.types import Bar, OrderSide, OrderType
from src.strategy.builtin.ma_cross import MACrossStrategy, register_ma_cross
from src.strategy.registry import StrategyRegistry


class RecordingOrderManager:
    def __init__(self) -> None:
        self.submitted = []

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
    ) -> None:
        self.submitted.append(
            {
                "symbol": symbol,
                "side": side,
                "order_type": order_type,
                "amount": amount,
                "price": price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "strategy_name": strategy_name,
            }
        )


def make_bar(close: float, timestamp: int) -> Bar:
    return Bar(
        timestamp=timestamp,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1.0,
    )


@pytest.mark.asyncio
async def test_ma_cross_buys_once_when_fast_ma_crosses_above_slow_ma() -> None:
    strategy = MACrossStrategy(symbol="BTC-USDT", fast_window=2, slow_window=3, amount=0.5)
    manager = RecordingOrderManager()
    strategy.set_order_manager(manager)

    for index, close in enumerate([10, 10, 10, 13, 14], start=1):
        await strategy.on_bar(make_bar(close, index))

    assert manager.submitted == [
        {
            "symbol": "BTC-USDT",
            "side": OrderSide.BUY,
            "order_type": OrderType.MARKET,
            "amount": 0.5,
            "price": None,
            "stop_loss": None,
            "take_profit": None,
            "strategy_name": "ma_cross",
        }
    ]


@pytest.mark.asyncio
async def test_ma_cross_sells_when_fast_ma_crosses_below_slow_ma() -> None:
    strategy = MACrossStrategy(symbol="BTC-USDT", fast_window=2, slow_window=3, amount=0.5)
    manager = RecordingOrderManager()
    strategy.set_order_manager(manager)

    for index, close in enumerate([10, 10, 10, 13, 14, 5], start=1):
        await strategy.on_bar(make_bar(close, index))

    assert [order["side"] for order in manager.submitted] == [OrderSide.BUY, OrderSide.SELL]
    assert manager.submitted[1]["symbol"] == "BTC-USDT"
    assert manager.submitted[1]["amount"] == 0.5
    assert manager.submitted[1]["strategy_name"] == "ma_cross"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"fast_window": True, "slow_window": 3}, "fast_window must be positive"),
        ({"fast_window": 2.5, "slow_window": 3}, "fast_window must be positive"),
        ({"fast_window": math.nan, "slow_window": 3}, "fast_window must be positive"),
        ({"fast_window": math.inf, "slow_window": 3}, "fast_window must be positive"),
        ({"fast_window": "2", "slow_window": 3}, "fast_window must be positive"),
        ({"fast_window": 2, "slow_window": True}, "slow_window must be positive"),
        ({"fast_window": 2, "slow_window": 3.5}, "slow_window must be positive"),
        ({"fast_window": 2, "slow_window": math.nan}, "slow_window must be positive"),
        ({"fast_window": 2, "slow_window": math.inf}, "slow_window must be positive"),
        ({"fast_window": 2, "slow_window": "3"}, "slow_window must be positive"),
        ({"fast_window": 2, "slow_window": 3, "amount": True}, "amount must be positive"),
        ({"fast_window": 2, "slow_window": 3, "amount": math.nan}, "amount must be positive"),
        ({"fast_window": 2, "slow_window": 3, "amount": math.inf}, "amount must be positive"),
        ({"fast_window": 2, "slow_window": 3, "amount": "1"}, "amount must be positive"),
    ],
)
def test_ma_cross_rejects_invalid_constructor_inputs(kwargs, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        MACrossStrategy(**kwargs)


def test_ma_cross_normalizes_integral_float_windows_and_numeric_amount() -> None:
    strategy = MACrossStrategy(fast_window=2.0, slow_window=3.0, amount=1)

    assert strategy.fast_window == 2
    assert type(strategy.fast_window) is int
    assert strategy.slow_window == 3
    assert type(strategy.slow_window) is int
    assert strategy.amount == 1.0
    assert type(strategy.amount) is float


def test_ma_cross_rejects_invalid_window_order() -> None:
    with pytest.raises(ValueError, match="fast_window must be less than or equal to slow_window"):
        MACrossStrategy(fast_window=5, slow_window=3)


def test_register_ma_cross_adds_strategy_to_registry() -> None:
    registry = StrategyRegistry()

    register_ma_cross(registry)

    strategy = registry.create("ma_cross")
    assert isinstance(strategy, MACrossStrategy)
    assert strategy.name == "ma_cross"
    assert strategy.symbol == "BTC-USDT"
    assert strategy.timeframe == "1m"
