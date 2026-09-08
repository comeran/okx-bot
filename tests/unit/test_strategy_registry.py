import math

import pytest

from src.strategy.builtin import register_builtin_strategies
from src.strategy.builtin.bollinger_mean_reversion import BollingerMeanReversionStrategy
from src.strategy.builtin.donchian_breakout import DonchianBreakoutStrategy
from src.strategy.builtin.ma_cross import MACrossStrategy, register_ma_cross
from src.strategy.builtin.rsi_mean_reversion import RSIMeanReversionStrategy
from src.strategy.definitions import (
    StrategyConfigValidationError,
    StrategyParameterDefinition,
)
from src.strategy.registry import StrategyRegistry


def make_registry() -> StrategyRegistry:
    registry = StrategyRegistry()
    register_builtin_strategies(registry)
    return registry


def issue_paths(exc: StrategyConfigValidationError) -> set[str]:
    return {issue.path for issue in exc.issues}


def issue_codes(exc: StrategyConfigValidationError) -> set[str]:
    return {issue.code for issue in exc.issues}


def test_builtin_definition_metadata_is_canonical_and_stably_ordered() -> None:
    registry = make_registry()

    assert [definition.to_dict() for definition in registry.list_definitions()] == [
        {
            "strategy_type": "ma_cross",
            "label": "Moving Average Cross",
            "description": "Trade when fast and slow moving averages cross.",
            "params": [
                {
                    "key": "fast_window",
                    "label": "Fast window",
                    "description": "Number of bars in the fast moving average.",
                    "value_type": "integer",
                    "required": True,
                    "default": 10,
                    "minimum": 1,
                    "maximum": None,
                    "step": 1,
                },
                {
                    "key": "slow_window",
                    "label": "Slow window",
                    "description": "Number of bars in the slow moving average.",
                    "value_type": "integer",
                    "required": True,
                    "default": 30,
                    "minimum": 1,
                    "maximum": None,
                    "step": 1,
                },
                {
                    "key": "amount",
                    "label": "Order amount",
                    "description": "Base asset amount submitted for each signal.",
                    "value_type": "number",
                    "required": True,
                    "default": 0.1,
                    "minimum": 0,
                    "maximum": None,
                    "step": 0.01,
                },
            ],
        },
        {
            "strategy_type": "rsi_mean_reversion",
            "label": "RSI Mean Reversion",
            "description": "Trade reversals when RSI crosses configured thresholds.",
            "params": [
                {
                    "key": "period",
                    "label": "RSI period",
                    "description": "Number of close-to-close changes used by Wilder RSI.",
                    "value_type": "integer",
                    "required": True,
                    "default": 14,
                    "minimum": 2,
                    "maximum": None,
                    "step": 1,
                },
                {
                    "key": "oversold",
                    "label": "Oversold threshold",
                    "description": "Buy threshold crossed from above.",
                    "value_type": "number",
                    "required": True,
                    "default": 30,
                    "minimum": 0,
                    "maximum": 100,
                    "step": 0.1,
                },
                {
                    "key": "overbought",
                    "label": "Overbought threshold",
                    "description": "Sell threshold crossed from below.",
                    "value_type": "number",
                    "required": True,
                    "default": 70,
                    "minimum": 0,
                    "maximum": 100,
                    "step": 0.1,
                },
                {
                    "key": "amount",
                    "label": "Order amount",
                    "description": "Base asset amount submitted for each signal.",
                    "value_type": "number",
                    "required": True,
                    "default": 0.1,
                    "minimum": 0,
                    "maximum": None,
                    "step": 0.01,
                },
            ],
        },
        {
            "strategy_type": "bollinger_mean_reversion",
            "label": "Bollinger Mean Reversion",
            "description": "Trade when price crosses outside rolling Bollinger bands.",
            "params": [
                {
                    "key": "window",
                    "label": "Band window",
                    "description": "Number of closes used for the rolling bands.",
                    "value_type": "integer",
                    "required": True,
                    "default": 20,
                    "minimum": 2,
                    "maximum": None,
                    "step": 1,
                },
                {
                    "key": "stddev_multiplier",
                    "label": "Standard deviation multiplier",
                    "description": "Population standard deviations from the rolling mean.",
                    "value_type": "number",
                    "required": True,
                    "default": 2.0,
                    "minimum": 0,
                    "maximum": None,
                    "step": 0.1,
                },
                {
                    "key": "amount",
                    "label": "Order amount",
                    "description": "Base asset amount submitted for each signal.",
                    "value_type": "number",
                    "required": True,
                    "default": 0.1,
                    "minimum": 0,
                    "maximum": None,
                    "step": 0.01,
                },
            ],
        },
        {
            "strategy_type": "donchian_breakout",
            "label": "Donchian Breakout",
            "description": "Trade breakouts beyond prior high and low channels.",
            "params": [
                {
                    "key": "entry_window",
                    "label": "Entry window",
                    "description": "Number of prior highs used for entry breakouts.",
                    "value_type": "integer",
                    "required": True,
                    "default": 20,
                    "minimum": 1,
                    "maximum": None,
                    "step": 1,
                },
                {
                    "key": "exit_window",
                    "label": "Exit window",
                    "description": "Number of prior lows used for exit breakouts.",
                    "value_type": "integer",
                    "required": True,
                    "default": 10,
                    "minimum": 1,
                    "maximum": None,
                    "step": 1,
                },
                {
                    "key": "amount",
                    "label": "Order amount",
                    "description": "Base asset amount submitted for each signal.",
                    "value_type": "number",
                    "required": True,
                    "default": 0.1,
                    "minimum": 0,
                    "maximum": None,
                    "step": 0.01,
                },
            ],
        },
    ]
    assert registry.list_implicit_strategies() == ["ma_cross"]


def test_parameter_definition_distinguishes_no_default_from_explicit_none() -> None:
    without_default = StrategyParameterDefinition(
        key="optional_text",
        label="Optional text",
        description="Optional text value.",
        value_type="string",
    )
    explicit_none = StrategyParameterDefinition(
        key="nullable_text",
        label="Nullable text",
        description="Nullable text value.",
        value_type="string",
        default=None,
    )

    assert without_default.has_default is False
    assert "default" not in without_default.to_dict()
    assert explicit_none.has_default is True
    assert explicit_none.to_dict()["default"] is None


def test_duplicate_registration_cannot_replace_a_strict_definition() -> None:
    registry = make_registry()

    with pytest.raises(ValueError, match="Strategy type already registered: ma_cross"):
        register_ma_cross(registry)


def test_normalize_config_trims_fields_applies_defaults_and_coerces_numbers() -> None:
    normalized = make_registry().normalize_config(
        {
            "name": "  persisted ma  ",
            "strategy_type": "ma_cross",
            "symbol": "  ETH-USDT  ",
            "timeframe": "  1h  ",
            "enabled": False,
            "params": {"fast_window": 5.0, "amount": 1},
        }
    )

    assert normalized.name == "persisted ma"
    assert normalized.strategy_type == "ma_cross"
    assert normalized.symbol == "ETH-USDT"
    assert normalized.timeframe == "1h"
    assert normalized.enabled is False
    assert normalized.params == {"fast_window": 5, "slow_window": 30, "amount": 1.0}


def test_normalize_config_accepts_runtime_two_hour_timeframe() -> None:
    normalized = make_registry().normalize_config(
        name="two-hour",
        strategy_type="ma_cross",
        symbol="BTC-USDT-SWAP",
        timeframe="  2h  ",
        params={},
    )

    assert normalized.timeframe == "2h"


@pytest.mark.parametrize("timeframe", ["7m", "invalid", "1M", "3M"])
def test_normalize_config_rejects_unsupported_runtime_timeframes(timeframe: str) -> None:
    with pytest.raises(StrategyConfigValidationError) as error:
        make_registry().normalize_config(
            name="unsupported-timeframe",
            strategy_type="ma_cross",
            symbol="BTC-USDT-SWAP",
            timeframe=timeframe,
            params={},
        )

    assert [
        (issue.path, issue.code, issue.message)
        for issue in error.value.issues
        if issue.path == "timeframe"
    ] == [
        ("timeframe", "unsupported_timeframe", "Unsupported strategy timeframe")
    ]


@pytest.mark.parametrize(
    ("config", "expected_code"),
    [
        (
            {
                "name": "missing-timeframe",
                "strategy_type": "ma_cross",
                "symbol": "BTC-USDT-SWAP",
                "params": {},
            },
            "missing_required",
        ),
        (
            {
                "name": "empty-timeframe",
                "strategy_type": "ma_cross",
                "symbol": "BTC-USDT-SWAP",
                "timeframe": " ",
                "params": {},
            },
            "empty",
        ),
        (
            {
                "name": "invalid-timeframe-type",
                "strategy_type": "ma_cross",
                "symbol": "BTC-USDT-SWAP",
                "timeframe": 2,
                "params": {},
            },
            "invalid_type",
        ),
    ],
)
def test_normalize_config_does_not_duplicate_required_timeframe_issues(
    config: dict[str, object], expected_code: str
) -> None:
    with pytest.raises(StrategyConfigValidationError) as error:
        make_registry().normalize_config(config)

    assert [
        (issue.path, issue.code)
        for issue in error.value.issues
        if issue.path == "timeframe"
    ] == [("timeframe", expected_code)]


@pytest.mark.parametrize("timeframe", ["7m", "invalid", "1M", "3M"])
def test_create_instance_rejects_unsupported_runtime_timeframe(timeframe: str) -> None:
    with pytest.raises(StrategyConfigValidationError) as error:
        make_registry().create_instance(
            name="unsupported-timeframe",
            strategy_type="ma_cross",
            symbol="BTC-USDT-SWAP",
            timeframe=timeframe,
            params={},
        )

    assert {(issue.path, issue.code) for issue in error.value.issues} == {
        ("timeframe", "unsupported_timeframe")
    }


@pytest.mark.parametrize("value", [True, 3.5, math.nan, math.inf, -math.inf, "3"])
def test_normalize_config_rejects_non_integral_or_non_finite_integer_values(value) -> None:
    with pytest.raises(StrategyConfigValidationError) as error:
        make_registry().normalize_config(
            name="bad",
            strategy_type="ma_cross",
            symbol="BTC-USDT",
            timeframe="1h",
            params={"fast_window": value},
        )

    matching = [issue for issue in error.value.issues if issue.path == "params.fast_window"]
    assert [(issue.code, issue.message) for issue in matching] == [
        ("invalid_type", "Value must be an integer")
    ]


@pytest.mark.parametrize("value", [True, math.nan, math.inf, -math.inf, "1"])
def test_normalize_config_rejects_boolean_non_finite_or_non_numeric_numbers(value) -> None:
    with pytest.raises(StrategyConfigValidationError) as error:
        make_registry().normalize_config(
            name="bad",
            strategy_type="ma_cross",
            symbol="BTC-USDT",
            timeframe="1h",
            params={"amount": value},
        )

    matching = [issue for issue in error.value.issues if issue.path == "params.amount"]
    assert [(issue.code, issue.message) for issue in matching] == [
        ("invalid_type", "Value must be a finite number")
    ]


def test_normalize_config_distinguishes_missing_and_empty_required_text() -> None:
    with pytest.raises(StrategyConfigValidationError) as missing:
        make_registry().normalize_config(
            {
                "strategy_type": "ma_cross",
                "params": {},
            }
        )

    assert {
        (issue.path, issue.code)
        for issue in missing.value.issues
        if issue.path in {"name", "symbol", "timeframe"}
    } == {
        ("name", "missing_required"),
        ("symbol", "missing_required"),
        ("timeframe", "missing_required"),
    }

    with pytest.raises(StrategyConfigValidationError) as empty:
        make_registry().normalize_config(
            name=" ",
            strategy_type="ma_cross",
            symbol=" ",
            timeframe=" ",
            params={},
        )

    assert {
        (issue.path, issue.code)
        for issue in empty.value.issues
        if issue.path in {"name", "symbol", "timeframe"}
    } == {
        ("name", "empty"),
        ("symbol", "empty"),
        ("timeframe", "empty"),
    }


def test_normalize_config_reports_unknown_fields_and_params() -> None:
    with pytest.raises(StrategyConfigValidationError) as error:
        make_registry().normalize_config(
            {
                "name": "ma",
                "strategy_type": "ma_cross",
                "symbol": "BTC-USDT",
                "timeframe": "1h",
                "enabled": "yes",
                "params": {"unknown": 1},
                "extra": "not allowed",
            }
        )

    assert {
        (issue.path, issue.code)
        for issue in error.value.issues
    } == {
        ("enabled", "invalid_type"),
        ("extra", "unknown_field"),
        ("params.unknown", "unknown_param"),
    }


def test_unrelated_top_level_error_does_not_hide_semantic_validation() -> None:
    with pytest.raises(StrategyConfigValidationError) as error:
        make_registry().normalize_config(
            {
                "name": "bad",
                "strategy_type": "ma_cross",
                "symbol": "BTC-USDT",
                "timeframe": "1h",
                "params": {"fast_window": 31, "slow_window": 30},
                "extra": "not allowed",
            }
        )

    assert {(issue.path, issue.code) for issue in error.value.issues} == {
        ("extra", "unknown_field"),
        ("params.fast_window", "invalid_window_order"),
    }


def test_unknown_param_does_not_hide_semantic_validation() -> None:
    with pytest.raises(StrategyConfigValidationError) as error:
        make_registry().normalize_config(
            name="bad",
            strategy_type="ma_cross",
            symbol="BTC-USDT",
            timeframe="1h",
            params={"fast_window": 31, "slow_window": 30, "unknown": 1},
        )

    assert {(issue.path, issue.code) for issue in error.value.issues} == {
        ("params.unknown", "unknown_param"),
        ("params.fast_window", "invalid_window_order"),
    }


def test_normalize_config_rejects_unsupported_strategy_type() -> None:
    with pytest.raises(StrategyConfigValidationError) as unsupported:
        make_registry().normalize_config(
            name="bad",
            strategy_type="missing",
            symbol="BTC-USDT",
            timeframe="1h",
            params={},
        )

    assert issue_paths(unsupported.value) == {"strategy_type"}
    assert issue_codes(unsupported.value) == {"unsupported_strategy_type"}


@pytest.mark.parametrize(
    ("strategy_type", "params", "expected_params", "expected_types", "expected_class"),
    [
        (
            "ma_cross",
            {"fast_window": 3.0, "slow_window": 4.0, "amount": 0.25},
            {"fast_window": 3, "slow_window": 4, "amount": 0.25},
            {"fast_window": int, "slow_window": int, "amount": float},
            MACrossStrategy,
        ),
        (
            "rsi_mean_reversion",
            {"period": 14.0, "oversold": 28, "overbought": 72.5, "amount": 0.15},
            {"period": 14, "oversold": 28.0, "overbought": 72.5, "amount": 0.15},
            {"period": int, "oversold": float, "overbought": float, "amount": float},
            RSIMeanReversionStrategy,
        ),
        (
            "bollinger_mean_reversion",
            {"window": 21.0, "stddev_multiplier": 2, "amount": 0.2},
            {"window": 21, "stddev_multiplier": 2.0, "amount": 0.2},
            {"window": int, "stddev_multiplier": float, "amount": float},
            BollingerMeanReversionStrategy,
        ),
        (
            "donchian_breakout",
            {"entry_window": 20.0, "exit_window": 9.0, "amount": 0.3},
            {"entry_window": 20, "exit_window": 9, "amount": 0.3},
            {"entry_window": int, "exit_window": int, "amount": float},
            DonchianBreakoutStrategy,
        ),
    ],
)
def test_create_instance_supports_all_builtin_strategy_types_with_normalized_identity(
    strategy_type, params, expected_params, expected_types, expected_class
) -> None:
    registry = make_registry()

    strategy = registry.create_instance(
        name=f"  persisted_{strategy_type}  ",
        strategy_type=strategy_type,
        symbol="  ETH-USDT  ",
        timeframe="  15m  ",
        params=params,
    )

    assert isinstance(strategy, expected_class)
    assert strategy.name == f"persisted_{strategy_type}"
    assert strategy.symbol == "ETH-USDT"
    assert strategy.timeframe == "15m"
    for key, expected_value in expected_params.items():
        actual = getattr(strategy, key)
        assert actual == expected_value
        assert type(actual) is expected_types[key]
    assert registry.list_implicit_strategies() == ["ma_cross"]
