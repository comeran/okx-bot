from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from src.core.config import (
    AppConfig,
    BacktestConfig,
    ExchangeConfig,
    NotifyConfig,
    RiskConfig,
    WebConfig,
)
from src.core.runtime_settings import load_runtime_settings, save_runtime_settings
from src.web.exchange_auth import probe_saved_exchange_auth

MarketType = Literal["spot", "swap", "future", "futures", "option", "options"]
RuntimeMode = Literal["backtest", "paper", "demo", "live"]


class ExchangeSettingsUpdate(BaseModel):
    api_key: str = ""
    secret: str = ""
    passphrase: str = ""
    market_type: MarketType = "spot"
    demo: bool = True

    @field_validator("market_type", mode="before")
    @classmethod
    def normalize_market_type(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class BacktestSettings(BaseModel):
    initial_capital: float = Field(default=100000, gt=0, allow_inf_nan=False)
    fee_rate: float = Field(default=0.0005, ge=0, lt=1, allow_inf_nan=False)
    slippage: float = Field(default=0.001, ge=0, lt=1, allow_inf_nan=False)
    data_cache_dir: str = "./data"


class RiskSettings(BaseModel):
    max_daily_loss_pct: float = Field(default=0.05, ge=0, le=1, allow_inf_nan=False)
    max_drawdown_pct: float = Field(default=0.15, ge=0, le=1, allow_inf_nan=False)
    max_total_position_pct: float = Field(default=0.8, ge=0, le=1, allow_inf_nan=False)
    allow_live_open_orders: bool = False
    live_max_order_notional: float = Field(default=0.0, ge=0, allow_inf_nan=False)


class NotifySettingsUpdate(BaseModel):
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


class WebSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)


class SettingsUpdate(BaseModel):
    mode: RuntimeMode = "backtest"
    exchange: ExchangeSettingsUpdate = ExchangeSettingsUpdate()
    backtest: BacktestSettings = BacktestSettings()
    risk: RiskSettings = RiskSettings()
    notify: NotifySettingsUpdate = NotifySettingsUpdate()
    web: WebSettings = WebSettings()

    @field_validator("mode", mode="before")
    @classmethod
    def normalize_mode(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class SecretSetting(BaseModel):
    value: str = ""


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"


def _merge_secret(current: str, incoming: str) -> str:
    if not incoming:
        return current
    if current and incoming == _mask_secret(current):
        return current
    return incoming


def _serialize_settings(settings: SettingsUpdate) -> dict[str, object]:
    return {
        "mode": settings.mode,
        "exchange": {
            "api_key": "",
            "api_key_set": bool(settings.exchange.api_key),
            "secret": "",
            "secret_set": bool(settings.exchange.secret),
            "passphrase": "",
            "passphrase_set": bool(settings.exchange.passphrase),
            "market_type": settings.exchange.market_type,
            "demo": settings.exchange.demo,
        },
        "backtest": settings.backtest.model_dump(),
        "risk": settings.risk.model_dump(),
        "notify": {
            "telegram_bot_token": "",
            "telegram_bot_token_set": bool(settings.notify.telegram_bot_token),
            "telegram_chat_id": settings.notify.telegram_chat_id,
        },
        "web": settings.web.model_dump(),
    }


def _settings_from_config(config: AppConfig) -> SettingsUpdate:
    return SettingsUpdate(
        mode=config.mode,
        exchange=ExchangeSettingsUpdate(
            api_key=config.exchange.api_key,
            secret=config.exchange.secret,
            passphrase=config.exchange.passphrase,
            market_type=config.exchange.market_type,
            demo=config.exchange.demo,
        ),
        backtest=BacktestSettings(
            initial_capital=config.backtest.initial_capital,
            fee_rate=config.backtest.fee_rate,
            slippage=config.backtest.slippage,
            data_cache_dir=config.backtest.data_cache_dir,
        ),
        risk=RiskSettings(
            max_daily_loss_pct=config.risk.max_daily_loss_pct,
            max_drawdown_pct=config.risk.max_drawdown_pct,
            max_total_position_pct=config.risk.max_total_position_pct,
            allow_live_open_orders=config.risk.allow_live_open_orders,
            live_max_order_notional=config.risk.live_max_order_notional,
        ),
        notify=NotifySettingsUpdate(
            telegram_bot_token=config.notify.telegram_bot_token,
            telegram_chat_id=config.notify.telegram_chat_id,
        ),
        web=WebSettings(host=config.web.host, port=config.web.port),
    )


def _config_from_settings(settings: SettingsUpdate) -> AppConfig:
    return AppConfig(
        mode=settings.mode,
        exchange=ExchangeConfig(
            api_key=settings.exchange.api_key,
            secret=settings.exchange.secret,
            passphrase=settings.exchange.passphrase,
            market_type=settings.exchange.market_type,
            demo=settings.exchange.demo,
        ),
        backtest=BacktestConfig(
            initial_capital=settings.backtest.initial_capital,
            fee_rate=settings.backtest.fee_rate,
            slippage=settings.backtest.slippage,
            data_cache_dir=settings.backtest.data_cache_dir,
        ),
        risk=RiskConfig(
            max_daily_loss_pct=settings.risk.max_daily_loss_pct,
            max_drawdown_pct=settings.risk.max_drawdown_pct,
            max_total_position_pct=settings.risk.max_total_position_pct,
            allow_live_open_orders=settings.risk.allow_live_open_orders,
            live_max_order_notional=settings.risk.live_max_order_notional,
        ),
        notify=NotifyConfig(
            telegram_bot_token=settings.notify.telegram_bot_token,
            telegram_chat_id=settings.notify.telegram_chat_id,
        ),
        web=WebConfig(host=settings.web.host, port=settings.web.port),
    )


def _load_settings() -> SettingsUpdate:
    return _settings_from_config(load_runtime_settings())


def _save_settings(settings: SettingsUpdate) -> None:
    save_runtime_settings(_config_from_settings(settings))


def create_router(broadcast=None) -> APIRouter:
    router = APIRouter()
    settings = _load_settings()

    @router.get("")
    async def get_settings() -> dict[str, object]:
        return _serialize_settings(settings)

    @router.post("/exchange-auth")
    async def exchange_auth(
        expected_account_scope_id: str | None = None,
    ) -> dict[str, object]:
        return await probe_saved_exchange_auth(
            load_runtime_settings, expected_account_scope_id,
        )

    @router.put("")
    async def update_settings(update: SettingsUpdate) -> dict[str, object]:
        settings.mode = update.mode
        settings.exchange.api_key = _merge_secret(
            settings.exchange.api_key,
            update.exchange.api_key,
        )
        settings.exchange.secret = _merge_secret(
            settings.exchange.secret,
            update.exchange.secret,
        )
        settings.exchange.passphrase = _merge_secret(
            settings.exchange.passphrase,
            update.exchange.passphrase,
        )
        settings.exchange.market_type = update.exchange.market_type
        settings.exchange.demo = update.exchange.demo
        settings.backtest = update.backtest
        settings.risk = update.risk
        settings.notify.telegram_bot_token = _merge_secret(
            settings.notify.telegram_bot_token,
            update.notify.telegram_bot_token,
        )
        settings.notify.telegram_chat_id = update.notify.telegram_chat_id
        settings.web = update.web
        _save_settings(settings)
        if broadcast is not None:
            await broadcast({"type": "context_changed"})
        return _serialize_settings(settings)

    return router
