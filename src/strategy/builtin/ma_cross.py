from __future__ import annotations

from src.core.types import Bar
from src.strategy.base import BaseStrategy
from src.strategy.registry import StrategyRegistry


class MACrossStrategy(BaseStrategy):
    name = "ma_cross"

    def __init__(
        self,
        symbol: str = "BTC-USDT",
        fast_window: int = 10,
        slow_window: int = 30,
        amount: float = 0.1,
    ) -> None:
        super().__init__()
        if fast_window <= 0:
            raise ValueError("fast_window must be positive")
        if slow_window <= 0:
            raise ValueError("slow_window must be positive")
        if fast_window > slow_window:
            raise ValueError("fast_window must be less than or equal to slow_window")
        self.symbol = symbol
        self.fast_window = fast_window
        self.slow_window = slow_window
        self.amount = amount
        self._closes: list[float] = []
        self._previous_fast: float | None = None
        self._previous_slow: float | None = None

    async def on_bar(self, bar: Bar) -> None:
        self._closes.append(bar.close)
        self._closes = self._closes[-self.slow_window :]
        if len(self._closes) < self.slow_window:
            return

        fast = self._average(self.fast_window)
        slow = self._average(self.slow_window)
        previous_fast = self._previous_fast
        previous_slow = self._previous_slow
        self._previous_fast = fast
        self._previous_slow = slow

        if previous_fast is None or previous_slow is None:
            return
        if previous_fast <= previous_slow and fast > slow:
            return await self.buy(self.symbol, self.amount)
        if previous_fast >= previous_slow and fast < slow:
            return await self.sell(self.symbol, self.amount)
        return None

    def _average(self, window: int) -> float:
        values = self._closes[-window:]
        return sum(values) / window


def register_ma_cross(registry: StrategyRegistry) -> None:
    registry.register("ma_cross", MACrossStrategy)
