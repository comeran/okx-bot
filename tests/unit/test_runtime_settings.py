import yaml

from src.core.config import AppConfig


def test_runtime_settings_path_prefers_env(monkeypatch, tmp_path):
    from src.core.runtime_settings import runtime_settings_path

    env_path = tmp_path / "env-settings.yaml"
    monkeypatch.setenv("OKX_BOT_SETTINGS_PATH", str(env_path))

    assert runtime_settings_path() == env_path


def test_runtime_settings_defaults_load_config_settings_yaml_when_present(monkeypatch, tmp_path):
    from src.core.runtime_settings import load_runtime_settings

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.yaml").write_text(
        yaml.safe_dump(
            {
                "mode": "paper",
                "backtest": {"initial_capital": 123456},
                "risk": {"max_total_position_pct": 0.42},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OKX_BOT_SETTINGS_PATH", raising=False)

    settings = load_runtime_settings()

    assert settings.mode == "paper"
    assert settings.backtest.initial_capital == 123456
    assert settings.risk.max_total_position_pct == 0.42


def test_runtime_settings_defaults_use_app_config_when_config_settings_yaml_missing(
    monkeypatch,
    tmp_path,
):
    from src.core.runtime_settings import load_runtime_settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OKX_BOT_SETTINGS_PATH", raising=False)

    settings = load_runtime_settings()

    assert settings == AppConfig()


def test_backtest_api_uses_persisted_runtime_settings(monkeypatch, tmp_path):
    from src.web.api import backtest as backtest_api

    settings_path = tmp_path / "settings.local.yaml"
    settings_path.write_text(
        yaml.safe_dump({"backtest": {"initial_capital": 765432, "fee_rate": 0.0011}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("OKX_BOT_SETTINGS_PATH", str(settings_path))

    backtest = backtest_api.load_backtest_config()

    assert backtest.initial_capital == 765432
    assert backtest.fee_rate == 0.0011


def test_strategy_helpers_use_persisted_runtime_settings(monkeypatch, tmp_path):
    from src.web.api import strategies as strategy_api

    settings_path = tmp_path / "settings.local.yaml"
    settings_path.write_text(
        yaml.safe_dump(
            {
                "mode": "paper",
                "exchange": {
                    "api_key": "persisted-api-key",
                    "secret": "persisted-secret",
                    "passphrase": "persisted-passphrase",
                },
                "backtest": {
                    "initial_capital": 654321,
                    "fee_rate": 0.0009,
                    "slippage": 0.002,
                    "data_cache_dir": "./persisted-data",
                },
                "risk": {
                    "max_daily_loss_pct": 0.01,
                    "max_drawdown_pct": 0.02,
                    "max_total_position_pct": 0.33,
                    "allow_live_open_orders": True,
                    "live_max_order_notional": 1234.0,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OKX_BOT_SETTINGS_PATH", str(settings_path))

    backtest = strategy_api.paper_backtest_config()
    risk_manager = strategy_api.create_risk_manager()

    assert backtest.initial_capital == 654321
    assert backtest.fee_rate == 0.0009
    assert risk_manager.position_rule.max_position_pct == 0.33
    settings = strategy_api.load_runtime_settings()
    assert settings.risk.allow_live_open_orders is True
    assert settings.risk.live_max_order_notional == 1234.0


def test_save_runtime_settings_is_private_and_atomic(monkeypatch, tmp_path):
    import stat

    from src.core.runtime_settings import save_runtime_settings

    settings_path = tmp_path / "settings.local.yaml"
    monkeypatch.setenv("OKX_BOT_SETTINGS_PATH", str(settings_path))

    save_runtime_settings(AppConfig())

    assert stat.S_IMODE(settings_path.stat().st_mode) == 0o600
    assert not (tmp_path / ".settings.local.yaml.tmp").exists()


def test_save_runtime_settings_does_not_copy_env_secrets(monkeypatch, tmp_path):
    from src.core.runtime_settings import save_runtime_settings

    settings_path = tmp_path / "settings.local.yaml"
    monkeypatch.setenv("OKX_BOT_SETTINGS_PATH", str(settings_path))
    monkeypatch.setenv("OKX_API_KEY", "env-api-key")
    monkeypatch.setenv("OKX_SECRET", "env-secret")
    monkeypatch.setenv("OKX_PASSPHRASE", "env-passphrase")
    monkeypatch.setenv("TG_BOT_TOKEN", "env-telegram-token")
    config = AppConfig()
    config.exchange.api_key = "env-api-key"
    config.exchange.secret = "env-secret"
    config.exchange.passphrase = "env-passphrase"
    config.notify.telegram_bot_token = "env-telegram-token"

    save_runtime_settings(config)

    saved = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    assert saved["exchange"]["api_key"] == ""
    assert saved["exchange"]["secret"] == ""
    assert saved["exchange"]["passphrase"] == ""
    assert saved["notify"]["telegram_bot_token"] == ""
