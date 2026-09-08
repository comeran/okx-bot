from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from src.exchange.okx_client import OKX_RUNTIME_TIMEFRAME_MILLISECONDS
from src.strategy.base import BaseStrategy
from src.strategy.definitions import (
    NormalizedStrategyConfig,
    StrategyConfigValidationError,
    StrategyDefinition,
    StrategyParameterDefinition,
    StrategyValidationIssue,
)

_ALLOWED_CONFIG_FIELDS = {"name", "strategy_type", "symbol", "timeframe", "enabled", "params"}
_MISSING = object()


class StrategyRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, StrategyDefinition] = {}

    def register(
        self,
        name: str,
        cls: type[BaseStrategy],
        *,
        parameters: tuple[StrategyParameterDefinition, ...] = (),
        validate=None,
        allow_unknown_params: bool = True,
        implicit_instance: bool = True,
    ) -> None:
        self.register_definition(
            StrategyDefinition(
                name=name,
                strategy_cls=cls,
                parameters=parameters,
                validate=validate,
                allow_unknown_params=allow_unknown_params,
                implicit_instance=implicit_instance,
            )
        )

    def register_definition(self, definition: StrategyDefinition) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"Strategy type already registered: {definition.name}")
        self._definitions[definition.name] = definition

    def get_definition(self, name: str) -> StrategyDefinition:
        return self._definitions[name]

    def list_definitions(self) -> list[StrategyDefinition]:
        return list(self._definitions.values())

    def normalize_config(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        name: Any = _MISSING,
        strategy_type: Any = _MISSING,
        symbol: Any = _MISSING,
        timeframe: Any = _MISSING,
        enabled: Any = True,
        params: Mapping[str, Any] | None = None,
    ) -> NormalizedStrategyConfig:
        issues: list[StrategyValidationIssue] = []
        if config is not None:
            unknown_fields = sorted(set(config) - _ALLOWED_CONFIG_FIELDS)
            issues.extend(
                StrategyValidationIssue(
                    path=field,
                    code="unknown_field",
                    message="Unknown strategy config field",
                )
                for field in unknown_fields
            )
            values = dict(config)
        else:
            values = {}
            for field, value in {
                "name": name,
                "strategy_type": strategy_type,
                "symbol": symbol,
                "timeframe": timeframe,
                "enabled": enabled,
            }.items():
                if value is not _MISSING:
                    values[field] = value
            if params is not None:
                values["params"] = params

        normalized_type = self._normalize_required_text(
            values["strategy_type"] if "strategy_type" in values else _MISSING,
            "strategy_type",
            issues,
        )
        definition = self._definitions.get(normalized_type) if normalized_type else None
        if normalized_type and definition is None:
            issues.append(
                StrategyValidationIssue(
                    path="strategy_type",
                    code="unsupported_strategy_type",
                    message="Unsupported strategy type",
                )
            )

        normalized_timeframe = self._normalize_required_text(
            values["timeframe"] if "timeframe" in values else _MISSING,
            "timeframe",
            issues,
        )
        if (
            normalized_timeframe
            and normalized_timeframe not in OKX_RUNTIME_TIMEFRAME_MILLISECONDS
        ):
            issues.append(
                StrategyValidationIssue(
                    path="timeframe",
                    code="unsupported_timeframe",
                    message="Unsupported strategy timeframe",
                )
            )

        raw_params = values.get("params", {})
        if not isinstance(raw_params, Mapping):
            issues.append(
                StrategyValidationIssue(
                    path="params",
                    code="invalid_type",
                    message="Params must be a mapping",
                )
            )
            raw_params = {}
        normalized_params = (
            self._normalize_params(definition, raw_params, issues)
            if definition is not None
            else {}
        )

        if definition is not None and definition.validate is not None:
            parameter_paths = {
                f"params.{parameter.name}" for parameter in definition.parameters
            }
            unsafe_param_errors = any(
                issue.path == "params"
                or (
                    issue.path in parameter_paths
                    and issue.code in {"invalid_type", "missing_required", "empty"}
                )
                for issue in issues
            )
            if not unsafe_param_errors:
                issues.extend(definition.validate(normalized_params))

        normalized = NormalizedStrategyConfig(
            name=self._normalize_required_text(
                values["name"] if "name" in values else _MISSING,
                "name",
                issues,
            ),
            strategy_type=normalized_type,
            symbol=self._normalize_required_text(
                values["symbol"] if "symbol" in values else _MISSING,
                "symbol",
                issues,
            ),
            timeframe=normalized_timeframe,
            enabled=self._normalize_enabled(values.get("enabled", True), issues),
            params=normalized_params,
        )
        if issues:
            raise StrategyConfigValidationError(issues)
        return normalized

    def create_instance(
        self,
        name: str,
        strategy_type: str,
        symbol: str,
        timeframe: str,
        params: Mapping[str, Any] | None = None,
    ) -> BaseStrategy:
        normalized = self.normalize_config(
            name=name,
            strategy_type=strategy_type,
            symbol=symbol,
            timeframe=timeframe,
            params=params or {},
            enabled=True,
        )
        definition = self.get_definition(normalized.strategy_type)
        strategy = definition.strategy_cls(symbol=normalized.symbol, **normalized.params)
        strategy.name = normalized.name
        strategy.timeframe = normalized.timeframe
        return strategy

    def create(self, name: str, **params) -> BaseStrategy:
        definition = self.get_definition(name)
        timeframe = params.pop("timeframe", "1m" if name == "ma_cross" else None)
        strategy = definition.strategy_cls(**params)
        strategy.name = name
        if timeframe is not None:
            strategy.timeframe = timeframe
        return strategy

    def list_strategies(self) -> list[str]:
        return list(self._definitions)

    def list_implicit_strategies(self) -> list[str]:
        return [
            name
            for name, definition in self._definitions.items()
            if definition.implicit_instance
        ]

    def _normalize_params(
        self,
        definition: StrategyDefinition,
        raw_params: Mapping[str, Any],
        issues: list[StrategyValidationIssue],
    ) -> dict[str, Any]:
        params = dict(raw_params)
        parameter_definitions = {parameter.name: parameter for parameter in definition.parameters}
        if not definition.allow_unknown_params:
            for unknown in sorted(set(params) - set(parameter_definitions)):
                issues.append(
                    StrategyValidationIssue(
                        path=f"params.{unknown}",
                        code="unknown_param",
                        message="Unknown strategy parameter",
                    )
                )
        normalized: dict[str, Any] = {}
        for parameter in definition.parameters:
            raw_value = params.get(parameter.name, _MISSING)
            if raw_value is _MISSING:
                if parameter.has_default:
                    raw_value = parameter.default
                elif parameter.required:
                    issues.append(
                        StrategyValidationIssue(
                            path=f"params.{parameter.name}",
                            code="missing_required",
                            message="Missing required strategy parameter",
                        )
                    )
                    continue
                else:
                    continue
            normalized[parameter.name] = self._normalize_param_value(parameter, raw_value, issues)
        if definition.allow_unknown_params:
            for key, value in params.items():
                if key not in normalized:
                    normalized[key] = value
        return normalized

    def _normalize_param_value(
        self,
        parameter: StrategyParameterDefinition,
        value: Any,
        issues: list[StrategyValidationIssue],
    ) -> Any:
        path = f"params.{parameter.name}"
        normalized = value
        issue_count = len(issues)
        if parameter.type == "integer":
            normalized = self._normalize_integer(value, path, issues)
        elif parameter.type == "number":
            normalized = self._normalize_number(value, path, issues)
        elif parameter.type == "boolean":
            if type(value) is not bool:
                issues.append(
                    StrategyValidationIssue(path, "invalid_type", "Value must be a boolean")
                )
                return value
        elif parameter.type == "string":
            normalized = self._normalize_required_text(value, path, issues)
        if (
            len(issues) == issue_count
            and parameter.type in {"integer", "number"}
            and isinstance(normalized, int | float)
        ):
            self._validate_bounds(parameter, normalized, path, issues)
        return normalized

    def _normalize_required_text(
        self,
        value: Any,
        path: str,
        issues: list[StrategyValidationIssue],
    ) -> str:
        if value is _MISSING:
            issues.append(
                StrategyValidationIssue(path, "missing_required", "Missing required value")
            )
            return ""
        if not isinstance(value, str):
            issues.append(StrategyValidationIssue(path, "invalid_type", "Value must be a string"))
            return ""
        normalized = value.strip()
        if not normalized:
            issues.append(StrategyValidationIssue(path, "empty", "Value must be non-empty"))
        return normalized

    def _normalize_enabled(
        self,
        value: Any,
        issues: list[StrategyValidationIssue],
    ) -> bool:
        if type(value) is bool:
            return value
        issues.append(
            StrategyValidationIssue("enabled", "invalid_type", "Enabled must be a boolean")
        )
        return False

    def _normalize_integer(
        self,
        value: Any,
        path: str,
        issues: list[StrategyValidationIssue],
    ) -> int:
        if type(value) is bool:
            issues.append(StrategyValidationIssue(path, "invalid_type", "Value must be an integer"))
            return int(value)
        if type(value) is int:
            return value
        if type(value) is float and math.isfinite(value) and value.is_integer():
            return int(value)
        issues.append(StrategyValidationIssue(path, "invalid_type", "Value must be an integer"))
        return 0

    def _normalize_number(
        self,
        value: Any,
        path: str,
        issues: list[StrategyValidationIssue],
    ) -> float:
        if type(value) is bool or not isinstance(value, int | float) or not math.isfinite(value):
            issues.append(
                StrategyValidationIssue(path, "invalid_type", "Value must be a finite number")
            )
            return 0.0
        return float(value)

    def _validate_bounds(
        self,
        parameter: StrategyParameterDefinition,
        value: int | float,
        path: str,
        issues: list[StrategyValidationIssue],
    ) -> None:
        if parameter.min_value is not None:
            invalid = (
                value <= parameter.min_value
                if parameter.exclusive_min
                else value < parameter.min_value
            )
            if invalid:
                comparator = (
                    "greater than"
                    if parameter.exclusive_min
                    else "greater than or equal to"
                )
                issues.append(
                    StrategyValidationIssue(
                        path,
                        "min_value",
                        f"Value must be {comparator} {parameter.min_value}",
                    )
                )
        if parameter.max_value is not None:
            invalid = (
                value >= parameter.max_value
                if parameter.exclusive_max
                else value > parameter.max_value
            )
            if invalid:
                comparator = "less than" if parameter.exclusive_max else "less than or equal to"
                issues.append(
                    StrategyValidationIssue(
                        path,
                        "max_value",
                        f"Value must be {comparator} {parameter.max_value}",
                    )
                )
