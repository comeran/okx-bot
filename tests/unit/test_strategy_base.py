import pytest

from src.core.types import Bar
from src.strategy.base import BaseStrategy
from src.strategy.registry import StrategyRegistry


class DummyStrategy(BaseStrategy):
    name = "dummy"

    def __init__(self) -> None:
        super().__init__()
        self.bars: list[Bar] = []

    async def on_bar(self, bar: Bar) -> None:
        self.bars.append(bar)
        if bar.close > 50000:
            await self.buy("BTC-USDT", 0.1, price=bar.close)


def make_bar(close: float) -> Bar:
    return Bar(
        timestamp=1,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1.0,
    )


async def test_strategy_receives_bars() -> None:
    strategy = DummyStrategy()
    bar = make_bar(close=50000)

    await strategy.on_bar(bar)

    assert strategy.bars == [bar]


async def test_strategy_buy_requires_order_manager() -> None:
    strategy = DummyStrategy()

    with pytest.raises(RuntimeError, match="Order manager not set"):
        await strategy.on_bar(make_bar(close=50001))


async def test_strategy_cancel_forwards_symbol() -> None:
    class Manager:
        def __init__(self) -> None:
            self.cancelled = []

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            self.cancelled.append((order_id, symbol))
            return True

    manager = Manager()
    strategy = DummyStrategy()
    strategy.set_order_manager(manager)

    assert await strategy.cancel("exchange-order-1", symbol="BTC-USDT") is True

    assert manager.cancelled == [("exchange-order-1", "BTC-USDT")]


async def test_strategy_registry() -> None:
    registry = StrategyRegistry()

    registry.register("dummy", DummyStrategy)

    assert "dummy" in registry.list_strategies()
    strategy = registry.create("dummy")
    assert isinstance(strategy, DummyStrategy)
    assert strategy.name == "dummy"


async def test_strategy_registry_unknown() -> None:
    registry = StrategyRegistry()

    with pytest.raises(KeyError):
        registry.create("unknown")
