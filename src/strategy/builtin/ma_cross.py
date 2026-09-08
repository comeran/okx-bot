from __future__ import annotations

import math

from src.core.types import Bar
from src.strategy.base import BaseStrategy
from src.strategy.definitions import (
    StrategyDefinition,
    StrategyParameterDefinition,
    StrategyValidationIssue,
)
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
        if not _is_integral_window(fast_window) or fast_window < 1:
            raise ValueError("fast_window must be positive")
        if not _is_integral_window(slow_window) or slow_window < 1:
            raise ValueError("slow_window must be positive")
        if not _is_positive_number(amount):
            raise ValueError("amount must be positive")
        fast_window = int(fast_window)
        slow_window = int(slow_window)
        amount = float(amount)
        if fast_window > slow_window:
            raise ValueError("fast_window must be less than or equal to slow_window")
        self.symbol = symbol
        self.fast_window = fast_window
        self.slow_window = slow_window
        self.amount = amount
        self._closes: list[float] = []
        self._previous_fast: float | None = None
        self._previous_slow: float | None = None

    def required_warmup_bars(self) -> int:
        return self.slow_window

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


def _is_integral_window(value: object) -> bool:
    return type(value) is int or (
        type(value) is float and math.isfinite(value) and value.is_integer()
    )


def _is_positive_number(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and value > 0


def validate_ma_cross(params: dict[str, object]) -> list[StrategyValidationIssue]:
    if params["fast_window"] > params["slow_window"]:
        return [
            StrategyValidationIssue(
                path="params.fast_window",
                code="invalid_window_order",
                message="fast_window must be less than or equal to slow_window",
            )
        ]
    return []


def ma_cross_definition() -> StrategyDefinition:
    return StrategyDefinition(
        strategy_type="ma_cross",
        label="Moving Average Cross",
        description="Trade when fast and slow moving averages cross.",
        strategy_cls=MACrossStrategy,
        parameters=(
            StrategyParameterDefinition(
                "fast_window",
                "integer",
                label="Fast window",
                description="Number of bars in the fast moving average.",
                default=10,
                minimum=1,
                step=1,
            ),
            StrategyParameterDefinition(
                "slow_window",
                "integer",
                label="Slow window",
                description="Number of bars in the slow moving average.",
                default=30,
                minimum=1,
                step=1,
            ),
            StrategyParameterDefinition(
                "amount",
                "number",
                label="Order amount",
                description="Base asset amount submitted for each signal.",
                default=0.1,
                minimum=0,
                step=0.01,
                exclusive_min=True,
            ),
        ),
        validate=validate_ma_cross,
        allow_unknown_params=False,
        implicit_instance=True,
    )


def register_ma_cross(registry: StrategyRegistry) -> None:
    registry.register_definition(ma_cross_definition())
