import operator
import re
from collections.abc import Callable
from typing import Any

from src.core.types import Bar
from src.strategy.base import BaseStrategy

_OPERATORS: dict[str, Callable[[float, float], bool]] = {
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    "<": operator.lt,
}


def _resolve_operand(operand: str, values: dict[str, float]) -> float | None:
    if operand in values:
        return values[operand]
    try:
        return float(operand)
    except ValueError:
        return None


def parse_condition(expr: str, values: dict[str, float]) -> bool:
    for symbol, compare in _OPERATORS.items():
        left, separator, right = expr.partition(symbol)
        if separator:
            left_value = _resolve_operand(left.strip(), values)
            right_value = _resolve_operand(right.strip(), values)
            if left_value is None or right_value is None:
                return False
            return compare(left_value, right_value)
    return False


class YAMLStrategy(BaseStrategy):
    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__()
        self.name = config["name"]
        self.symbol = config["symbol"]
        self.timeframe = config["timeframe"]
        self.params = config.get("params", {})
        self.indicators = {
            name: self._resolve_params(expression)
            for name, expression in config.get("indicators", {}).items()
        }
        self.conditions = config.get("conditions", {})
        self._indicator_values: dict[str, float] = {}

    def _resolve_params(self, value: str) -> str:
        def replace(match: re.Match[str]) -> str:
            return str(self.params.get(match.group(1).strip(), match.group(0)))

        return re.sub(r"\{\{\s*([^}]+?)\s*\}\}", replace, value)

    async def on_bar(self, bar: Bar) -> None:
        self._indicator_values.update(
            {
                "close": bar.close,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "volume": bar.volume,
            }
        )

        buy_conditions = self.conditions.get("buy", [])
        if buy_conditions and all(
            parse_condition(expr, self._indicator_values) for expr in buy_conditions
        ):
            await self.buy(self.symbol, 0.1)

        sell_conditions = self.conditions.get("sell", [])
        if sell_conditions and all(
            parse_condition(expr, self._indicator_values) for expr in sell_conditions
        ):
            await self.sell(self.symbol, 0.1)
