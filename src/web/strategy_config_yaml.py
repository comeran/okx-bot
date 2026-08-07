from __future__ import annotations

from typing import Any

import yaml

_CONFIG_FIELD_ORDER = ("name", "strategy_type", "symbol", "timeframe", "enabled", "params")


class StrategyConfigYamlError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        line: int | None = None,
        column: int | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.line = line
        self.column = column
        super().__init__(message)


def load_strategy_config_yaml(content: str) -> dict[str, Any]:
    try:
        document = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = None if mark is None else min(mark.line + 1, len(content.splitlines()))
        code = (
            "multiple_documents"
            if "expected a single document" in str(exc)
            else "yaml_syntax_error"
        )
        message = (
            "YAML must contain a single document"
            if code == "multiple_documents"
            else "Invalid YAML syntax"
        )
        raise StrategyConfigYamlError(
            code,
            message,
            line=line,
            column=None if mark is None else mark.column + 1,
        ) from None
    if not isinstance(document, dict):
        raise StrategyConfigYamlError(
            "invalid_root",
            "YAML root must be a mapping",
            line=1 if content.splitlines() else None,
            column=1 if content.splitlines() else None,
        )
    return document


def dump_strategy_config_yaml(config: dict[str, Any]) -> str:
    ordered = {field: config[field] for field in _CONFIG_FIELD_ORDER if field in config}
    return yaml.safe_dump(ordered, sort_keys=False)
