from __future__ import annotations

import math

from src.core.types import Bar
from src.strategy.base import BaseStrategy
from src.strategy.definitions import StrategyDefinition, StrategyParameterDefinition
from src.strategy.registry import StrategyRegistry


class DonchianBreakoutStrategy(BaseStrategy):
    name = "donchian_breakout"

    def __init__(
        self,
        symbol: str = "BTC-USDT",
        entry_window: int = 20,
        exit_window: int = 10,
        amount: float = 0.1,
    ) -> None:
        super().__init__()
        if not _is_integral(entry_window) or entry_window < 1:
            raise ValueError("entry_window must be positive")
        if not _is_integral(exit_window) or exit_window < 1:
            raise ValueError("exit_window must be positive")
        if not _is_positive_number(amount):
            raise ValueError("amount must be positive")
        entry_window = int(entry_window)
        exit_window = int(exit_window)
        self.symbol = symbol
        self.entry_window = entry_window
        self.exit_window = exit_window
        self.amount = float(amount)
        self.entry_breakout_active = False
        self.exit_breakout_active = False
        self._highs: list[float] = []
        self._lows: list[float] = []

    def required_warmup_bars(self) -> int:
        return max(self.entry_window, self.exit_window)

    async def on_bar(self, bar: Bar) -> None:
        order = None
        if len(self._highs) >= self.entry_window:
            entry_high = max(self._highs[-self.entry_window :])
            entry_breakout = bar.close > entry_high
            if entry_breakout and not self.entry_breakout_active:
                order = await self.buy(self.symbol, self.amount)
            self.entry_breakout_active = entry_breakout

        if len(self._lows) >= self.exit_window:
            exit_low = min(self._lows[-self.exit_window :])
            exit_breakout = bar.close < exit_low
            if exit_breakout and not self.exit_breakout_active:
                sell_order = await self.sell(self.symbol, self.amount)
                order = order or sell_order
            self.exit_breakout_active = exit_breakout

        self._highs.append(bar.high)
        self._lows.append(bar.low)
        self._highs = self._highs[-self.entry_window :]
        self._lows = self._lows[-self.exit_window :]
        return order


def _is_integral(value: object) -> bool:
    return type(value) is int or (
        type(value) is float and math.isfinite(value) and value.is_integer()
    )


def _is_positive_number(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and value > 0


def donchian_breakout_definition() -> StrategyDefinition:
    return StrategyDefinition(
        strategy_type="donchian_breakout",
        label="Donchian Breakout",
        description="Trade breakouts beyond prior high and low channels.",
        strategy_cls=DonchianBreakoutStrategy,
        parameters=(
            StrategyParameterDefinition(
                "entry_window",
                "integer",
                label="Entry window",
                description="Number of prior highs used for entry breakouts.",
                default=20,
                minimum=1,
                step=1,
            ),
            StrategyParameterDefinition(
                "exit_window",
                "integer",
                label="Exit window",
                description="Number of prior lows used for exit breakouts.",
                default=10,
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
        allow_unknown_params=False,
        implicit_instance=False,
    )


def register_donchian_breakout(registry: StrategyRegistry) -> None:
    registry.register_definition(donchian_breakout_definition())
