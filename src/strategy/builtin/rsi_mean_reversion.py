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


class RSIMeanReversionStrategy(BaseStrategy):
    name = "rsi_mean_reversion"

    def __init__(
        self,
        symbol: str = "BTC-USDT",
        period: int = 14,
        oversold: float = 30,
        overbought: float = 70,
        amount: float = 0.1,
    ) -> None:
        super().__init__()
        if not _is_integral(period) or period < 2:
            raise ValueError("period must be at least 2")
        if not _is_number_between(oversold, 0, 100):
            raise ValueError("oversold must be greater than 0 and less than 100")
        if not _is_number_between(overbought, 0, 100):
            raise ValueError("overbought must be greater than 0 and less than 100")
        if oversold >= overbought:
            raise ValueError("oversold must be less than overbought")
        if not _is_positive_number(amount):
            raise ValueError("amount must be positive")
        period = int(period)
        self.symbol = symbol
        self.period = period
        self.oversold = float(oversold)
        self.overbought = float(overbought)
        self.amount = float(amount)
        self._previous_close: float | None = None
        self._initial_gains: list[float] = []
        self._initial_losses: list[float] = []
        self._avg_gain: float | None = None
        self._avg_loss: float | None = None
        self._previous_rsi: float | None = None

    async def on_bar(self, bar: Bar) -> None:
        if self._previous_close is None:
            self._previous_close = bar.close
            return None

        delta = bar.close - self._previous_close
        self._previous_close = bar.close
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)

        if self._avg_gain is None or self._avg_loss is None:
            self._initial_gains.append(gain)
            self._initial_losses.append(loss)
            if len(self._initial_gains) < self.period:
                return None
            self._avg_gain = sum(self._initial_gains) / self.period
            self._avg_loss = sum(self._initial_losses) / self.period
        else:
            self._avg_gain = ((self._avg_gain * (self.period - 1)) + gain) / self.period
            self._avg_loss = ((self._avg_loss * (self.period - 1)) + loss) / self.period

        rsi = self._rsi(self._avg_gain, self._avg_loss)
        previous_rsi = self._previous_rsi
        self._previous_rsi = rsi
        if previous_rsi is None:
            return None
        if previous_rsi > self.oversold and rsi <= self.oversold:
            return await self.buy(self.symbol, self.amount)
        if previous_rsi < self.overbought and rsi >= self.overbought:
            return await self.sell(self.symbol, self.amount)
        return None

    def _rsi(self, avg_gain: float, avg_loss: float) -> float:
        if avg_loss == 0 and avg_gain > 0:
            return 100.0
        if avg_gain == 0 and avg_loss > 0:
            return 0.0
        if avg_gain == 0 and avg_loss == 0:
            return 50.0
        relative_strength = avg_gain / avg_loss
        return 100 - (100 / (1 + relative_strength))


def _is_integral(value: object) -> bool:
    return type(value) is int or (
        type(value) is float and math.isfinite(value) and value.is_integer()
    )


def _is_positive_number(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and value > 0


def _is_number_between(value: object, lower: float, upper: float) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and lower < value < upper


def validate_rsi_mean_reversion(params: dict[str, object]) -> list[StrategyValidationIssue]:
    if params["oversold"] >= params["overbought"]:
        return [
            StrategyValidationIssue(
                path="params.oversold",
                code="invalid_threshold_order",
                message="oversold must be less than overbought",
            )
        ]
    return []


def rsi_mean_reversion_definition() -> StrategyDefinition:
    return StrategyDefinition(
        strategy_type="rsi_mean_reversion",
        label="RSI Mean Reversion",
        description="Trade reversals when RSI crosses configured thresholds.",
        strategy_cls=RSIMeanReversionStrategy,
        parameters=(
            StrategyParameterDefinition(
                "period",
                "integer",
                label="RSI period",
                description="Number of close-to-close changes used by Wilder RSI.",
                default=14,
                minimum=2,
                step=1,
            ),
            StrategyParameterDefinition(
                "oversold",
                "number",
                label="Oversold threshold",
                description="Buy threshold crossed from above.",
                default=30,
                minimum=0,
                maximum=100,
                step=0.1,
                exclusive_min=True,
                exclusive_max=True,
            ),
            StrategyParameterDefinition(
                "overbought",
                "number",
                label="Overbought threshold",
                description="Sell threshold crossed from below.",
                default=70,
                minimum=0,
                maximum=100,
                step=0.1,
                exclusive_min=True,
                exclusive_max=True,
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
        validate=validate_rsi_mean_reversion,
        allow_unknown_params=False,
        implicit_instance=False,
    )


def register_rsi_mean_reversion(registry: StrategyRegistry) -> None:
    registry.register_definition(rsi_mean_reversion_definition())
