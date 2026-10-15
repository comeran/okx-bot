from __future__ import annotations

import math

from src.core.types import Bar
from src.strategy.base import BaseStrategy
from src.strategy.definitions import StrategyDefinition, StrategyParameterDefinition
from src.strategy.registry import StrategyRegistry


class BollingerMeanReversionStrategy(BaseStrategy):
    name = "bollinger_mean_reversion"

    def __init__(
        self,
        symbol: str = "BTC-USDT",
        window: int = 20,
        stddev_multiplier: float = 2.0,
        amount: float = 0.1,
    ) -> None:
        super().__init__()
        if not _is_integral(window) or window < 2:
            raise ValueError("window must be at least 2")
        if not _is_positive_number(stddev_multiplier):
            raise ValueError("stddev_multiplier must be positive")
        if not _is_positive_number(amount):
            raise ValueError("amount must be positive")
        window = int(window)
        self.symbol = symbol
        self.window = window
        self.stddev_multiplier = float(stddev_multiplier)
        self.amount = float(amount)
        self._closes: list[float] = []

    def required_warmup_bars(self) -> int:
        return self.window

    async def on_bar(self, bar: Bar) -> None:
        self._closes.append(bar.close)
        self._closes = self._closes[-(self.window + 1) :]
        if len(self._closes) < self.window + 1:
            return None

        previous_close = self._closes[-2]
        current_close = self._closes[-1]
        previous_lower, previous_upper = self._bands(self._closes[:-1])
        current_lower, current_upper = self._bands(self._closes[1:])

        if previous_close >= previous_lower and current_close < current_lower:
            return await self.buy(self.symbol, self.amount)
        if previous_close <= previous_upper and current_close > current_upper:
            return await self.sell(self.symbol, self.amount)
        return None

    def _bands(self, closes: list[float]) -> tuple[float, float]:
        mean = sum(closes) / self.window
        variance = sum((close - mean) ** 2 for close in closes) / self.window
        stddev = math.sqrt(variance)
        distance = self.stddev_multiplier * stddev
        return mean - distance, mean + distance


def _is_integral(value: object) -> bool:
    return type(value) is int or (
        type(value) is float and math.isfinite(value) and value.is_integer()
    )


def _is_positive_number(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and value > 0


def bollinger_mean_reversion_definition() -> StrategyDefinition:
    return StrategyDefinition(
        strategy_type="bollinger_mean_reversion",
        label="Bollinger Mean Reversion",
        description="Trade when price crosses outside rolling Bollinger bands.",
        strategy_cls=BollingerMeanReversionStrategy,
        parameters=(
            StrategyParameterDefinition(
                "window",
                "integer",
                label="Band window",
                description="Number of closes used for the rolling bands.",
                default=20,
                minimum=2,
                step=1,
            ),
            StrategyParameterDefinition(
                "stddev_multiplier",
                "number",
                label="Standard deviation multiplier",
                description="Population standard deviations from the rolling mean.",
                default=2.0,
                minimum=0,
                step=0.1,
                exclusive_min=True,
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
        allow_unknown_params=False,
        implicit_instance=False,
    )


def register_bollinger_mean_reversion(registry: StrategyRegistry) -> None:
    registry.register_definition(bollinger_mean_reversion_definition())
