from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from src.strategy.base import BaseStrategy

StrategyParameterType = Literal["integer", "number", "boolean", "string"]
NO_DEFAULT = object()


@dataclass(frozen=True)
class StrategyValidationIssue:
    path: str
    code: str
    message: str


@dataclass(frozen=True, init=False)
class StrategyParameterDefinition:
    key: str
    label: str
    description: str
    value_type: StrategyParameterType
    required: bool
    default: Any
    minimum: float | int | None
    maximum: float | int | None
    step: float | int | None
    exclusive_min: bool
    exclusive_max: bool

    def __init__(
        self,
        key: str,
        value_type: StrategyParameterType | None = None,
        *,
        label: str | None = None,
        description: str | None = None,
        required: bool | None = None,
        default: Any = NO_DEFAULT,
        minimum: float | int | None = None,
        maximum: float | int | None = None,
        step: float | int | None = None,
        min_value: float | int | None = None,
        max_value: float | int | None = None,
        type: StrategyParameterType | None = None,
        exclusive_min: bool = False,
        exclusive_max: bool = False,
    ) -> None:
        resolved_type = value_type if value_type is not None else type
        if resolved_type is None:
            raise TypeError("value_type is required")
        if minimum is None:
            minimum = min_value
        if maximum is None:
            maximum = max_value
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "label", label or key.replace("_", " ").capitalize())
        object.__setattr__(self, "description", description or "")
        object.__setattr__(self, "value_type", resolved_type)
        object.__setattr__(
            self,
            "required",
            (default is not NO_DEFAULT) if required is None else required,
        )
        object.__setattr__(self, "default", default)
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)
        object.__setattr__(self, "step", step)
        object.__setattr__(self, "exclusive_min", exclusive_min)
        object.__setattr__(self, "exclusive_max", exclusive_max)

    @property
    def name(self) -> str:
        return self.key

    @property
    def type(self) -> StrategyParameterType:
        return self.value_type

    @property
    def min_value(self) -> float | int | None:
        return self.minimum

    @property
    def max_value(self) -> float | int | None:
        return self.maximum

    @property
    def has_default(self) -> bool:
        return self.default is not NO_DEFAULT

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "value_type": self.value_type,
            "required": self.required,
        }
        if self.has_default:
            result["default"] = self.default
        result["minimum"] = self.minimum
        result["maximum"] = self.maximum
        if self.step is not None:
            result["step"] = self.step
        return result


@dataclass(frozen=True)
class NormalizedStrategyConfig:
    name: str
    strategy_type: str
    symbol: str
    timeframe: str
    enabled: bool
    params: dict[str, Any]


SemanticValidator = Callable[[dict[str, Any]], list[StrategyValidationIssue]]


@dataclass(frozen=True, init=False)
class StrategyDefinition:
    strategy_type: str
    label: str
    description: str
    strategy_cls: type[BaseStrategy]
    parameters: tuple[StrategyParameterDefinition, ...] = field(default_factory=tuple)
    validate: SemanticValidator | None = None
    allow_unknown_params: bool = False
    implicit_instance: bool = False

    def __init__(
        self,
        strategy_type: str | None = None,
        *,
        name: str | None = None,
        label: str | None = None,
        description: str = "",
        strategy_cls: type[BaseStrategy],
        parameters: tuple[StrategyParameterDefinition, ...] = (),
        validate: SemanticValidator | None = None,
        allow_unknown_params: bool = False,
        implicit_instance: bool = False,
    ) -> None:
        resolved_type = strategy_type if strategy_type is not None else name
        if resolved_type is None:
            raise TypeError("strategy_type is required")
        object.__setattr__(self, "strategy_type", resolved_type)
        object.__setattr__(self, "label", label or resolved_type.replace("_", " ").title())
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "strategy_cls", strategy_cls)
        object.__setattr__(self, "parameters", tuple(parameters))
        object.__setattr__(self, "validate", validate)
        object.__setattr__(self, "allow_unknown_params", allow_unknown_params)
        object.__setattr__(self, "implicit_instance", implicit_instance)

    @property
    def name(self) -> str:
        return self.strategy_type

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_type": self.strategy_type,
            "label": self.label,
            "description": self.description,
            "params": [parameter.to_dict() for parameter in self.parameters],
        }


class StrategyConfigValidationError(ValueError):
    def __init__(self, issues: list[StrategyValidationIssue]) -> None:
        self.issues = issues
        message = "; ".join(issue.message for issue in issues)
        super().__init__(message)
