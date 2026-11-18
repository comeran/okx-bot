"""用比较条件描述一笔买卖，不执行任意代码。"""

from __future__ import annotations

import re

from src.strategy.definitions import (
    StrategyDefinition,
    StrategyParameterDefinition,
    StrategyValidationIssue,
)
from src.strategy.registry import StrategyRegistry
from src.strategy.yaml_strategy import YAMLStrategy

_CALL = re.compile(
    r"(sma|ema|rsi)\s*\(\s*(close|open|high|low|volume)\s*,\s*([1-9][0-9]*)\s*\)",
    re.IGNORECASE,
)
_OPERATORS = (">=", "<=", "==", "!=", ">", "<")


def _replace_calls(expression: str, indicators: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        kind = match.group(1).lower()
        source = match.group(2).lower()
        period = match.group(3)
        key = f"{kind}_{source}_{period}"
        indicators[key] = f"{kind}({source}, {period})"
        return key

    return _CALL.sub(replace, expression)


def compile_rule(symbol: str, buy: str, sell: str, amount: float) -> dict:
    indicators: dict[str, str] = {}
    buy_expr = _replace_calls(buy, indicators).strip()
    sell_expr = _replace_calls(sell, indicators).strip()
    return {
        "name": "rule",
        "symbol": symbol,
        "timeframe": "1m",
        "params": {"amount": float(amount)},
        "indicators": indicators,
        "conditions": {
            "buy": [buy_expr] if buy_expr else [],
            "sell": [sell_expr] if sell_expr else [],
        },
    }


def validate_rule(params: dict) -> list[StrategyValidationIssue]:
    issues = []
    for key in ("buy", "sell"):
        text = str(params.get(key, ""))
        if not any(operator in text for operator in _OPERATORS):
            issues.append(StrategyValidationIssue(
                f"params.{key}",
                "invalid_condition",
                "条件需要一个比较符号",
            ))
    return issues


class RuleStrategy(YAMLStrategy):
    """买入和卖出各写一条 sma、ema 或 rsi 比较。"""

    def __init__(self, symbol: str, buy: str, sell: str, amount: float = 0.0001) -> None:
        super().__init__(compile_rule(symbol, buy, sell, amount))


def rule_definition() -> StrategyDefinition:
    return StrategyDefinition(
        strategy_type="rule",
        label="Rule",
        description="用一条买入条件和一条卖出条件交易。可用 sma、ema、rsi 和比较符号。",
        strategy_cls=RuleStrategy,
        parameters=(
            StrategyParameterDefinition(
                "buy",
                "string",
                label="Buy condition",
                description="例如 sma(close, 5) > sma(close, 20)",
                default="sma(close, 5) > sma(close, 20)",
            ),
            StrategyParameterDefinition(
                "sell",
                "string",
                label="Sell condition",
                description="例如 sma(close, 5) < sma(close, 20)",
                default="sma(close, 5) < sma(close, 20)",
            ),
            StrategyParameterDefinition(
                "amount",
                "number",
                label="Order amount",
                description="每次信号提交的基础资产数量。",
                default=0.0001,
                minimum=0,
                exclusive_min=True,
                step=0.0001,
            ),
        ),
        validate=validate_rule,
        allow_unknown_params=False,
        implicit_instance=False,
    )


def register_rule(registry: StrategyRegistry) -> None:
    registry.register_definition(rule_definition())
