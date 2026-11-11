from __future__ import annotations

import os
from pathlib import Path

import yaml

from src.core.config import AppConfig, load_config

SETTINGS_PATH_ENV = "OKX_BOT_SETTINGS_PATH"
DEFAULT_SETTINGS_PATH = Path("data/settings.local.yaml")
DEFAULT_CONFIG_PATH = Path("config/settings.yaml")
ENV_CREDENTIALS = (
    ("exchange", "api_key", "OKX_API_KEY"),
    ("exchange", "secret", "OKX_SECRET"),
    ("exchange", "passphrase", "OKX_PASSPHRASE"),
    ("notify", "telegram_bot_token", "TG_BOT_TOKEN"),
)


def runtime_settings_path() -> Path:
    return Path(os.environ.get(SETTINGS_PATH_ENV, DEFAULT_SETTINGS_PATH))


def default_runtime_settings() -> AppConfig:
    if DEFAULT_CONFIG_PATH.exists():
        return load_config(str(DEFAULT_CONFIG_PATH))
    return AppConfig()


def load_runtime_settings() -> AppConfig:
    settings_path = runtime_settings_path()
    if settings_path.exists():
        settings = load_config(str(settings_path))
        # Older versions saved environment credentials as empty strings.
        for section, field, env_name in ENV_CREDENTIALS:
            credentials = getattr(settings, section)
            if not getattr(credentials, field):
                setattr(credentials, field, os.environ.get(env_name, ""))
        return settings
    return default_runtime_settings()


def _persisted_secret(value: str, env_name: str) -> str:
    """Do not copy a value that already comes from the environment into the file."""
    env_value = os.environ.get(env_name)
    if env_value and value == env_value:
        return "${" + env_name + "}"
    return value


def save_runtime_settings(settings: AppConfig) -> None:
    settings_path = runtime_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "mode": settings.mode,
        "exchange": {
            "api_key": _persisted_secret(settings.exchange.api_key, "OKX_API_KEY"),
            "secret": _persisted_secret(settings.exchange.secret, "OKX_SECRET"),
            "passphrase": _persisted_secret(settings.exchange.passphrase, "OKX_PASSPHRASE"),
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
            "telegram_bot_token": _persisted_secret(
                settings.notify.telegram_bot_token, "TG_BOT_TOKEN"
            ),
            "telegram_chat_id": settings.notify.telegram_chat_id,
        },
        "web": {
            "host": settings.web.host,
            "port": settings.web.port,
        },
    }
    serialized = yaml.safe_dump(document, sort_keys=False)
    temp_path = settings_path.with_name(f".{settings_path.name}.tmp")
    try:
        descriptor = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as temp_file:
            temp_file.write(serialized)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, settings_path)
        os.chmod(settings_path, 0o600)
    finally:
        temp_path.unlink(missing_ok=True)
