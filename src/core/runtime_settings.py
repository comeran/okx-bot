from __future__ import annotations

import os
from pathlib import Path

import yaml

from src.core.config import AppConfig, load_config

SETTINGS_PATH_ENV = "OKX_BOT_SETTINGS_PATH"
DEFAULT_SETTINGS_PATH = Path("data/settings.local.yaml")
DEFAULT_CONFIG_PATH = Path("config/settings.yaml")


def runtime_settings_path() -> Path:
    return Path(os.environ.get(SETTINGS_PATH_ENV, DEFAULT_SETTINGS_PATH))


def default_runtime_settings() -> AppConfig:
    if DEFAULT_CONFIG_PATH.exists():
        return load_config(str(DEFAULT_CONFIG_PATH))
    return AppConfig()


def load_runtime_settings() -> AppConfig:
    settings_path = runtime_settings_path()
    if settings_path.exists():
        return load_config(str(settings_path))
    return default_runtime_settings()


def save_runtime_settings(settings: AppConfig) -> None:
    settings_path = runtime_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        yaml.safe_dump(
            {
                "mode": settings.mode,
                "exchange": {
                    "api_key": settings.exchange.api_key,
                    "secret": settings.exchange.secret,
                    "passphrase": settings.exchange.passphrase,
                    "market_type": settings.exchange.market_type,
                    "demo": settings.exchange.demo,
                },
                "backtest": {
                    "initial_capital": settings.backtest.initial_capital,
                    "fee_rate": settings.backtest.fee_rate,
                    "slippage": settings.backtest.slippage,
                    "data_cache_dir": settings.backtest.data_cache_dir,
                },
                "risk": {
                    "max_daily_loss_pct": settings.risk.max_daily_loss_pct,
                    "max_drawdown_pct": settings.risk.max_drawdown_pct,
                    "max_total_position_pct": settings.risk.max_total_position_pct,
                    "allow_live_open_orders": settings.risk.allow_live_open_orders,
                    "live_max_order_notional": settings.risk.live_max_order_notional,
                },
                "notify": {
                    "telegram_bot_token": settings.notify.telegram_bot_token,
                    "telegram_chat_id": settings.notify.telegram_chat_id,
                },
                "web": {
                    "host": settings.web.host,
                    "port": settings.web.port,
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
