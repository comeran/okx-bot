import math
import os
from dataclasses import dataclass, field
from typing import Any

import yaml


@dataclass
class ExchangeConfig:
    api_key: str = ""
    secret: str = ""
    passphrase: str = ""
    market_type: str = "spot"
    demo: bool = True


@dataclass
class BacktestConfig:
    initial_capital: float = 100000
    fee_rate: float = 0.0005
    slippage: float = 0.001
    data_cache_dir: str = "./data"


@dataclass
class RiskConfig:
    max_daily_loss_pct: float = 0.05
    max_drawdown_pct: float = 0.15
    max_total_position_pct: float = 0.8
    allow_live_open_orders: bool = False
    live_max_order_notional: float = 0.0
    max_price_age_ms: int = 15000


@dataclass
class NotifyConfig:
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


@dataclass
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 8080


@dataclass
class AppConfig:
    mode: str = "backtest"
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    web: WebConfig = field(default_factory=WebConfig)


def _substitute_env(value: Any) -> Any:
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return os.environ.get(value[2:-1], "")
    if isinstance(value, dict):
        return {key: _substitute_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute_env(item) for item in value]
    return value


def _parse_str(value: Any, default: str) -> str:
    if value is None:
        return default
    return str(value)


def _parse_bool(value: Any, default: bool, field_name: str) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"{field_name} must be a boolean, got {value!r}")


def _parse_float(value: Any, default: float, field_name: str) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a number, got {value!r}")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number, got {value!r}") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be finite, got {value!r}")
    return parsed


def _parse_int(value: Any, default: int, field_name: str) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer, got {value!r}")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer, got {value!r}") from exc


def _section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a mapping, got {type(value).__name__}")
    return value


def load_config(path: str) -> AppConfig:
    with open(path) as config_file:
        raw_config = yaml.safe_load(config_file) or {}

    config = _substitute_env(raw_config)
    exchange = _section(config, "exchange")
    backtest = _section(config, "backtest")
    risk = _section(config, "risk")
    notify = _section(config, "notify")
    web = _section(config, "web")

    return AppConfig(
        mode=_parse_str(config.get("mode"), "backtest"),
        exchange=ExchangeConfig(
            api_key=_parse_str(exchange.get("api_key"), ""),
            secret=_parse_str(exchange.get("secret"), ""),
            passphrase=_parse_str(exchange.get("passphrase"), ""),
            market_type=_parse_str(exchange.get("market_type"), "spot"),
            demo=_parse_bool(exchange.get("demo"), True, "exchange.demo"),
        ),
        backtest=BacktestConfig(
            initial_capital=_parse_float(
                backtest.get("initial_capital"), 100000, "backtest.initial_capital"
            ),
            fee_rate=_parse_float(backtest.get("fee_rate"), 0.0005, "backtest.fee_rate"),
            slippage=_parse_float(backtest.get("slippage"), 0.001, "backtest.slippage"),
            data_cache_dir=_parse_str(backtest.get("data_cache_dir"), "./data"),
        ),
        risk=RiskConfig(
            max_daily_loss_pct=_parse_float(
                risk.get("max_daily_loss_pct"), 0.05, "risk.max_daily_loss_pct"
            ),
            max_drawdown_pct=_parse_float(
                risk.get("max_drawdown_pct"), 0.15, "risk.max_drawdown_pct"
            ),
            max_total_position_pct=_parse_float(
                risk.get("max_total_position_pct"), 0.8, "risk.max_total_position_pct"
            ),
            allow_live_open_orders=_parse_bool(
                risk.get("allow_live_open_orders"),
                False,
                "risk.allow_live_open_orders",
            ),
            live_max_order_notional=_parse_float(
                risk.get("live_max_order_notional"),
                0.0,
                "risk.live_max_order_notional",
            ),
            max_price_age_ms=_parse_int(
                risk.get("max_price_age_ms"),
                15000,
                "risk.max_price_age_ms",
            ),
        ),
        notify=NotifyConfig(
            telegram_bot_token=_parse_str(notify.get("telegram_bot_token"), ""),
            telegram_chat_id=_parse_str(notify.get("telegram_chat_id"), ""),
        ),
        web=WebConfig(
            host=_parse_str(web.get("host"), "127.0.0.1"),
            port=_parse_int(web.get("port"), 8080, "web.port"),
        ),
    )
