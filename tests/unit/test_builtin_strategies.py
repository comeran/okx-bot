import math

import pytest

from src.core.types import Bar, OrderSide, OrderType
from src.strategy.base import BaseStrategy
from src.strategy.builtin.bollinger_mean_reversion import BollingerMeanReversionStrategy
from src.strategy.builtin.donchian_breakout import DonchianBreakoutStrategy
from src.strategy.builtin.rsi_mean_reversion import RSIMeanReversionStrategy


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


def make_bar(
    close: float,
    timestamp: int,
    high: float | None = None,
    low: float | None = None,
) -> Bar:
    return Bar(
        timestamp=timestamp,
        open=close,
        high=close if high is None else high,
        low=close if low is None else low,
        close=close,
        volume=1.0,
    )


@pytest.mark.parametrize(
    ("strategy", "expected"),
    [
        (RSIMeanReversionStrategy(period=14), 15),
        (BollingerMeanReversionStrategy(window=20), 20),
        (DonchianBreakoutStrategy(entry_window=20, exit_window=10), 20),
    ],
)
def test_builtin_required_warmup_bars_match_indicator_lookback(strategy, expected) -> None:
    assert strategy.required_warmup_bars() == expected


@pytest.mark.asyncio
async def test_warmup_updates_state_without_submitting_orders() -> None:
    strategy = DonchianBreakoutStrategy(entry_window=2, exit_window=1, amount=0.4)
    manager = RecordingOrderManager()
    strategy.set_order_manager(manager)

    bars = [
        make_bar(9, 1, high=10, low=8),
        make_bar(7, 2, high=9, low=7),
        make_bar(12, 3, high=12, low=8),
    ]
    await strategy.warmup(bars)

    assert len(strategy._highs) == 2
    assert manager.submitted == []


@pytest.mark.asyncio
async def test_warmup_restores_order_submission_after_on_bar_raises() -> None:
    class FailingWarmupStrategy(BaseStrategy):
        name = "failing_warmup"

        async def on_bar(self, bar: Bar) -> None:
            if not self._orders_enabled:
                raise RuntimeError("warmup failed")
            await self.buy("BTC-USDT", 0.1)

    strategy = FailingWarmupStrategy()
    manager = RecordingOrderManager()
    strategy.set_order_manager(manager)

    with pytest.raises(RuntimeError, match="warmup failed"):
        await strategy.warmup([make_bar(100, 1)])
    await strategy.on_bar(make_bar(101, 2))

    assert len(manager.submitted) == 1


@pytest.mark.asyncio
async def test_warmup_does_not_leave_order_submission_disabled() -> None:
    strategy = RSIMeanReversionStrategy(period=2, amount=0.25)
    manager = RecordingOrderManager()
    strategy.set_order_manager(manager)

    await strategy.warmup(
        [make_bar(close, index) for index, close in enumerate([100, 101, 102], 1)]
    )
    await strategy.on_bar(make_bar(90, 4))

    assert manager.submitted


@pytest.mark.asyncio
async def test_rsi_mean_reversion_warmup_crossing_and_no_repeated_orders() -> None:
    strategy = RSIMeanReversionStrategy(
        symbol="ETH-USDT",
        period=2,
        oversold=30,
        overbought=70,
        amount=0.25,
    )
    strategy.name = "rsi_eth"
    manager = RecordingOrderManager()
    strategy.set_order_manager(manager)

    for index, close in enumerate([100, 101, 102, 90, 80], start=1):
        await strategy.on_bar(make_bar(close, index))

    assert manager.submitted == [
        {
            "symbol": "ETH-USDT",
            "side": OrderSide.BUY,
            "order_type": OrderType.MARKET,
            "amount": 0.25,
            "price": None,
            "stop_loss": None,
            "take_profit": None,
            "strategy_name": "rsi_eth",
        }
    ]


@pytest.mark.asyncio
async def test_rsi_mean_reversion_sells_on_overbought_cross() -> None:
    strategy = RSIMeanReversionStrategy(symbol="ETH-USDT", period=2, amount=0.25)
    strategy.name = "rsi_eth"
    manager = RecordingOrderManager()
    strategy.set_order_manager(manager)

    for index, close in enumerate([100, 99, 98, 120], start=1):
        await strategy.on_bar(make_bar(close, index))

    assert [order["side"] for order in manager.submitted] == [OrderSide.SELL]
    assert manager.submitted[0]["symbol"] == "ETH-USDT"
    assert manager.submitted[0]["amount"] == 0.25
    assert manager.submitted[0]["strategy_name"] == "rsi_eth"


@pytest.mark.asyncio
async def test_rsi_uses_wilder_initialization_and_recurrence() -> None:
    strategy = RSIMeanReversionStrategy(period=3)

    for index, close in enumerate([10, 11, 10, 12], start=1):
        await strategy.on_bar(make_bar(close, index))

    assert strategy._avg_gain == pytest.approx(1.0)
    assert strategy._avg_loss == pytest.approx(1 / 3)
    assert strategy._previous_rsi == pytest.approx(75.0)

    await strategy.on_bar(make_bar(11, 5))

    assert strategy._avg_gain == pytest.approx(2 / 3)
    assert strategy._avg_loss == pytest.approx(5 / 9)
    assert strategy._previous_rsi == pytest.approx(54.54545454545455)


def test_rsi_boundary_values_are_deterministic() -> None:
    strategy = RSIMeanReversionStrategy(period=2)

    assert strategy._rsi(1.0, 0.0) == 100.0
    assert strategy._rsi(0.0, 1.0) == 0.0
    assert strategy._rsi(0.0, 0.0) == 50.0


@pytest.mark.asyncio
async def test_first_valid_rsi_only_arms_crossing_state() -> None:
    strategy = RSIMeanReversionStrategy(period=2)
    manager = RecordingOrderManager()
    strategy.set_order_manager(manager)

    for index, close in enumerate([100, 90, 80, 70], start=1):
        await strategy.on_bar(make_bar(close, index))

    assert strategy._previous_rsi == 0.0
    assert manager.submitted == []


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"period": True}, "period must be at least 2"),
        ({"period": 2.5}, "period must be at least 2"),
        ({"period": math.nan}, "period must be at least 2"),
        ({"period": math.inf}, "period must be at least 2"),
        ({"period": "2"}, "period must be at least 2"),
        ({"oversold": True}, "oversold must be greater than 0 and less than 100"),
        ({"oversold": math.nan}, "oversold must be greater than 0 and less than 100"),
        ({"oversold": math.inf}, "oversold must be greater than 0 and less than 100"),
        ({"oversold": "30"}, "oversold must be greater than 0 and less than 100"),
        ({"overbought": True}, "overbought must be greater than 0 and less than 100"),
        ({"overbought": math.nan}, "overbought must be greater than 0 and less than 100"),
        ({"overbought": math.inf}, "overbought must be greater than 0 and less than 100"),
        ({"overbought": "70"}, "overbought must be greater than 0 and less than 100"),
        ({"amount": True}, "amount must be positive"),
        ({"amount": math.nan}, "amount must be positive"),
        ({"amount": math.inf}, "amount must be positive"),
        ({"amount": "1"}, "amount must be positive"),
    ],
)
def test_rsi_mean_reversion_rejects_invalid_constructor_inputs(kwargs, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        RSIMeanReversionStrategy(**kwargs)


def test_rsi_mean_reversion_normalizes_integral_period_and_numbers() -> None:
    strategy = RSIMeanReversionStrategy(
        period=3.0,
        oversold=25,
        overbought=75,
        amount=1,
    )

    assert strategy.period == 3
    assert type(strategy.period) is int
    assert strategy.oversold == 25.0
    assert strategy.overbought == 75.0
    assert strategy.amount == 1.0


def test_rsi_mean_reversion_rejects_invalid_threshold_order() -> None:
    with pytest.raises(ValueError, match="oversold must be less than overbought"):
        RSIMeanReversionStrategy(oversold=80, overbought=70)


@pytest.mark.asyncio
async def test_bollinger_mean_reversion_warmup_crossing_and_no_repeated_orders() -> None:
    strategy = BollingerMeanReversionStrategy(
        symbol="ETH-USDT",
        window=2,
        stddev_multiplier=0.5,
        amount=0.3,
    )
    strategy.name = "boll_eth"
    manager = RecordingOrderManager()
    strategy.set_order_manager(manager)

    for index, close in enumerate([10, 10, 5, 4], start=1):
        await strategy.on_bar(make_bar(close, index))

    assert manager.submitted == [
        {
            "symbol": "ETH-USDT",
            "side": OrderSide.BUY,
            "order_type": OrderType.MARKET,
            "amount": 0.3,
            "price": None,
            "stop_loss": None,
            "take_profit": None,
            "strategy_name": "boll_eth",
        }
    ]


@pytest.mark.asyncio
async def test_bollinger_mean_reversion_sells_on_upper_band_cross() -> None:
    strategy = BollingerMeanReversionStrategy(
        symbol="ETH-USDT",
        window=2,
        stddev_multiplier=0.5,
        amount=0.3,
    )
    strategy.name = "boll_eth"
    manager = RecordingOrderManager()
    strategy.set_order_manager(manager)

    for index, close in enumerate([10, 10, 15], start=1):
        await strategy.on_bar(make_bar(close, index))

    assert [order["side"] for order in manager.submitted] == [OrderSide.SELL]
    assert manager.submitted[0]["symbol"] == "ETH-USDT"
    assert manager.submitted[0]["amount"] == 0.3
    assert manager.submitted[0]["strategy_name"] == "boll_eth"


def test_bollinger_uses_population_standard_deviation() -> None:
    strategy = BollingerMeanReversionStrategy(window=3, stddev_multiplier=2)

    lower, upper = strategy._bands([1, 2, 3])

    distance = 2 * math.sqrt(2 / 3)
    assert lower == pytest.approx(2 - distance)
    assert upper == pytest.approx(2 + distance)


@pytest.mark.asyncio
async def test_bollinger_requires_window_plus_one_and_uses_adjacent_windows() -> None:
    strategy = BollingerMeanReversionStrategy(window=3)
    observed_windows = []

    def record_bands(closes):
        observed_windows.append(list(closes))
        return (-math.inf, math.inf)

    strategy._bands = record_bands

    for index, close in enumerate([1, 2, 3], start=1):
        await strategy.on_bar(make_bar(close, index))
    assert observed_windows == []

    await strategy.on_bar(make_bar(4, 4))

    assert observed_windows == [[1, 2, 3], [2, 3, 4]]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"window": True}, "window must be at least 2"),
        ({"window": 2.5}, "window must be at least 2"),
        ({"window": math.nan}, "window must be at least 2"),
        ({"window": math.inf}, "window must be at least 2"),
        ({"window": "2"}, "window must be at least 2"),
        ({"stddev_multiplier": True}, "stddev_multiplier must be positive"),
        ({"stddev_multiplier": math.nan}, "stddev_multiplier must be positive"),
        ({"stddev_multiplier": math.inf}, "stddev_multiplier must be positive"),
        ({"stddev_multiplier": "2"}, "stddev_multiplier must be positive"),
        ({"amount": True}, "amount must be positive"),
        ({"amount": math.nan}, "amount must be positive"),
        ({"amount": math.inf}, "amount must be positive"),
        ({"amount": "1"}, "amount must be positive"),
    ],
)
def test_bollinger_mean_reversion_rejects_invalid_constructor_inputs(
    kwargs,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        BollingerMeanReversionStrategy(**kwargs)


def test_bollinger_mean_reversion_normalizes_integral_window_and_numbers() -> None:
    strategy = BollingerMeanReversionStrategy(
        window=3.0,
        stddev_multiplier=2,
        amount=1,
    )

    assert strategy.window == 3
    assert type(strategy.window) is int
    assert strategy.stddev_multiplier == 2.0
    assert strategy.amount == 1.0


@pytest.mark.asyncio
async def test_donchian_breakout_warmup_crossing_and_no_repeated_orders() -> None:
    strategy = DonchianBreakoutStrategy(
        symbol="ETH-USDT",
        entry_window=2,
        exit_window=2,
        amount=0.4,
    )
    strategy.name = "don_eth"
    manager = RecordingOrderManager()
    strategy.set_order_manager(manager)

    bars = [
        make_bar(9, 1, high=10, low=8),
        make_bar(10, 2, high=11, low=9),
        make_bar(12, 3, high=12, low=10),
        make_bar(13, 4, high=13, low=11),
    ]
    for bar in bars:
        await strategy.on_bar(bar)

    assert manager.submitted == [
        {
            "symbol": "ETH-USDT",
            "side": OrderSide.BUY,
            "order_type": OrderType.MARKET,
            "amount": 0.4,
            "price": None,
            "stop_loss": None,
            "take_profit": None,
            "strategy_name": "don_eth",
        }
    ]


@pytest.mark.asyncio
async def test_donchian_breakout_sells_on_exit_breakout() -> None:
    strategy = DonchianBreakoutStrategy(
        symbol="ETH-USDT",
        entry_window=2,
        exit_window=2,
        amount=0.4,
    )
    strategy.name = "don_eth"
    manager = RecordingOrderManager()
    strategy.set_order_manager(manager)

    bars = [
        make_bar(10, 1, high=11, low=10),
        make_bar(9, 2, high=10, low=9),
        make_bar(8, 3, high=9, low=8),
        make_bar(7, 4, high=8, low=7),
    ]
    for bar in bars:
        await strategy.on_bar(bar)

    assert [order["side"] for order in manager.submitted] == [OrderSide.SELL]
    assert manager.submitted[0]["symbol"] == "ETH-USDT"
    assert manager.submitted[0]["amount"] == 0.4
    assert manager.submitted[0]["strategy_name"] == "don_eth"


@pytest.mark.asyncio
async def test_donchian_entry_and_exit_windows_warm_up_independently() -> None:
    strategy = DonchianBreakoutStrategy(entry_window=3, exit_window=1)
    manager = RecordingOrderManager()
    strategy.set_order_manager(manager)

    await strategy.on_bar(make_bar(9, 1, high=10, low=8))
    await strategy.on_bar(make_bar(7, 2, high=9, low=7))

    assert [order["side"] for order in manager.submitted] == [OrderSide.SELL]


@pytest.mark.asyncio
async def test_donchian_channel_excludes_current_bar() -> None:
    strategy = DonchianBreakoutStrategy(entry_window=1, exit_window=2)
    manager = RecordingOrderManager()
    strategy.set_order_manager(manager)

    await strategy.on_bar(make_bar(9, 1, high=10, low=8))
    await strategy.on_bar(make_bar(11, 2, high=100, low=9))

    assert [order["side"] for order in manager.submitted] == [OrderSide.BUY]


@pytest.mark.asyncio
async def test_donchian_breakout_resets_and_rearms() -> None:
    strategy = DonchianBreakoutStrategy(entry_window=1, exit_window=2)
    manager = RecordingOrderManager()
    strategy.set_order_manager(manager)

    bars = [
        make_bar(9, 1, high=10, low=8),
        make_bar(11, 2, high=12, low=9),
        make_bar(11, 3, high=11, low=9),
        make_bar(12, 4, high=13, low=10),
    ]
    for bar in bars:
        await strategy.on_bar(bar)

    assert [order["side"] for order in manager.submitted] == [
        OrderSide.BUY,
        OrderSide.BUY,
    ]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"entry_window": True}, "entry_window must be positive"),
        ({"entry_window": 2.5}, "entry_window must be positive"),
        ({"entry_window": math.nan}, "entry_window must be positive"),
        ({"entry_window": math.inf}, "entry_window must be positive"),
        ({"entry_window": "2"}, "entry_window must be positive"),
        ({"exit_window": True}, "exit_window must be positive"),
        ({"exit_window": 2.5}, "exit_window must be positive"),
        ({"exit_window": math.nan}, "exit_window must be positive"),
        ({"exit_window": math.inf}, "exit_window must be positive"),
        ({"exit_window": "2"}, "exit_window must be positive"),
        ({"amount": True}, "amount must be positive"),
        ({"amount": math.nan}, "amount must be positive"),
        ({"amount": math.inf}, "amount must be positive"),
        ({"amount": "1"}, "amount must be positive"),
    ],
)
def test_donchian_breakout_rejects_invalid_constructor_inputs(kwargs, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        DonchianBreakoutStrategy(**kwargs)


def test_donchian_breakout_normalizes_integral_windows_and_amount() -> None:
    strategy = DonchianBreakoutStrategy(
        entry_window=3.0,
        exit_window=2.0,
        amount=1,
    )

    assert strategy.entry_window == 3
    assert type(strategy.entry_window) is int
    assert strategy.exit_window == 2
    assert type(strategy.exit_window) is int
    assert strategy.amount == 1.0
