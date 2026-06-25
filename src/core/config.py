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


@dataclass
class NotifyConfig:
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


@dataclass
class WebConfig:
    host: str = "0.0.0.0"
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


def load_config(path: str) -> AppConfig:
    with open(path) as config_file:
        raw_config = yaml.safe_load(config_file) or {}

    config = _substitute_env(raw_config)

    return AppConfig(
        mode=config.get("mode", "backtest"),
        exchange=ExchangeConfig(**config.get("exchange", {})),
        backtest=BacktestConfig(**config.get("backtest", {})),
        risk=RiskConfig(**config.get("risk", {})),
        notify=NotifyConfig(**config.get("notify", {})),
        web=WebConfig(**config.get("web", {})),
    )
