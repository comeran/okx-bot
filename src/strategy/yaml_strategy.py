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

_INDICATOR_SOURCES = {"close", "open", "high", "low", "volume"}
_INDICATOR_FUNCTIONS = {"sma", "ema", "rsi"}
_INDICATOR_PATTERN = re.compile(
    r"^\s*(sma|ema|rsi)\s*\(\s*(close|open|high|low|volume)\s*,\s*([1-9][0-9]*)\s*\)\s*$",
    re.IGNORECASE,
)


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
            name: self._resolve_params(str(expression))
            for name, expression in config.get("indicators", {}).items()
        }
        self.conditions = config.get("conditions", {})
        self._compiled_indicators = {
            name: self._compile_indicator(name, expression)
            for name, expression in config.get("indicators", {}).items()
        }
        self._max_period = max(
            (period for _, _, period in self._compiled_indicators.values()),
            default=0,
        )
        self._history: dict[str, list[float]] = {
            source: []
            for source in {kind[1] for kind in self._compiled_indicators.values()}
        }
        self._ema_state: dict[str, float | None] = {
            name: None
            for name, (kind, _, _) in self._compiled_indicators.items()
            if kind == "ema"
        }
        self._indicator_values: dict[str, float] = {}

    def required_warmup_bars(self) -> int:
        return max(self._max_period, 0)

    def _resolve_params(self, value: str) -> str:
        def replace(match: re.Match[str]) -> str:
            return str(self.params.get(match.group(1).strip(), match.group(0)))

        return re.sub(r"\{\{\s*([^}]+?)\s*\}\}", replace, value)

    def _compile_indicator(self, name: str, expression: Any) -> tuple[str, str, int]:
        if isinstance(expression, dict):
            kind = str(expression.get("type", "")).strip().lower()
            source = str(expression.get("source", "close")).strip().lower()
            period_value = expression.get("period")
        else:
            match = _INDICATOR_PATTERN.match(self._resolve_params(str(expression)))
            if match is None:
                raise ValueError(
                    f"Unsupported indicator expression for {name}: {expression!r}; "
                    "expected sma(close, N), ema(close, N) or rsi(close, N)"
                )
            kind, source, period_value = match.groups()
        try:
            period = int(period_value)
        except (TypeError, ValueError):
            raise ValueError(
                f"Indicator {name} requires an integer period, got {period_value!r}"
            ) from None
        if kind not in _INDICATOR_FUNCTIONS:
            raise ValueError(f"Unsupported indicator function for {name}: {kind!r}")
        if source not in _INDICATOR_SOURCES:
            raise ValueError(f"Unsupported indicator source for {name}: {source!r}")
        if period < 1:
            raise ValueError(f"Indicator {name} requires a positive period")
        return kind, source, period

    async def on_bar(self, bar: Bar) -> None:
        raw_values = {
            "close": bar.close,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "volume": bar.volume,
        }
        for source, history in self._history.items():
            history.append(raw_values[source])
            del history[: max(0, len(history) - (self._max_period + 1))]
        self._indicator_values.update(raw_values)

        for name, (kind, source, period) in self._compiled_indicators.items():
            value = self._indicator_value(name, kind, self._history[source], period)
            if value is None:
                self._indicator_values.pop(name, None)
            else:
                self._indicator_values[name] = value

        order = None
        buy_conditions = self.conditions.get("buy", [])
        if buy_conditions and all(
            parse_condition(expr, self._indicator_values) for expr in buy_conditions
        ):
            order = await self.buy(self.symbol, 0.1)

        sell_conditions = self.conditions.get("sell", [])
        if sell_conditions and all(
            parse_condition(expr, self._indicator_values) for expr in sell_conditions
        ):
            sell_order = await self.sell(self.symbol, 0.1)
            order = order or sell_order

        return order

    def _indicator_value(
        self,
        name: str,
        kind: str,
        history: list[float],
        period: int,
    ) -> float | None:
        if kind == "sma":
            if len(history) < period:
                return None
            window = history[-period:]
            return sum(window) / period
        if kind == "ema":
            if not history:
                return None
            alpha = 2 / (period + 1)
            previous = self._ema_state.get(name)
            current = (
                history[-1]
                if previous is None
                else alpha * history[-1] + (1 - alpha) * previous
            )
            self._ema_state[name] = current
            return current if len(history) >= period else None
        if len(history) < period + 1:
            return None
        changes = [
            current - previous
            for previous, current in zip(history[-period - 1 :], history[-period:], strict=False)
        ]
        gains = sum(change for change in changes if change > 0) / period
        losses = sum(-change for change in changes if change < 0) / period
        if losses == 0:
            return 100.0 if gains > 0 else 50.0
        return 100 - 100 / (1 + gains / losses)
