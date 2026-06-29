import textwrap

from src.core.config import AppConfig, load_config


def test_app_config_defaults_keep_demo_enabled():
    config = AppConfig()

    assert config.exchange.demo is True
    assert config.exchange.market_type == "spot"
    assert config.risk.allow_live_open_orders is False
    assert config.risk.live_max_order_notional == 0.0


def test_load_config_accepts_live_exchange_and_risk_keys(tmp_path):
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(
        textwrap.dedent(
            """
            mode: live
            exchange:
              market_type: swap
              demo: true
            risk:
              max_daily_loss_pct: 0.03
              max_drawdown_pct: 0.12
              max_total_position_pct: 0.7
              allow_live_open_orders: false
              live_max_order_notional: 250.0
            """
        )
    )

    config = load_config(str(config_file))

    assert config.mode == "live"
    assert config.exchange.market_type == "swap"
    assert config.exchange.demo is True
    assert config.risk.allow_live_open_orders is False
    assert config.risk.live_max_order_notional == 250.0


def test_loads_explicit_config_values(tmp_path):
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(
        textwrap.dedent(
            """
            mode: live
            exchange:
              api_key: explicit-key
              secret: explicit-secret
              passphrase: explicit-passphrase
              market_type: swap
              demo: false
            backtest:
              initial_capital: 250000
              fee_rate: 0.001
              slippage: 0.002
              data_cache_dir: ./cache
            risk:
              max_daily_loss_pct: 0.03
              max_drawdown_pct: 0.12
              max_total_position_pct: 0.6
              allow_live_open_orders: true
              live_max_order_notional: 2500
            web:
              host: 127.0.0.1
              port: 9000
            """
        )
    )

    config = load_config(str(config_file))

    assert config.mode == "live"
    assert config.exchange.api_key == "explicit-key"
    assert config.exchange.secret == "explicit-secret"
    assert config.exchange.passphrase == "explicit-passphrase"
    assert config.exchange.market_type == "swap"
    assert config.exchange.demo is False
    assert config.backtest.initial_capital == 250000
    assert config.backtest.fee_rate == 0.001
    assert config.backtest.slippage == 0.002
    assert config.backtest.data_cache_dir == "./cache"
    assert config.risk.max_daily_loss_pct == 0.03
    assert config.risk.max_drawdown_pct == 0.12
    assert config.risk.max_total_position_pct == 0.6
    assert config.risk.allow_live_open_orders is True
    assert config.risk.live_max_order_notional == 2500
    assert config.web.host == "127.0.0.1"
    assert config.web.port == 9000


def test_substitutes_environment_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_OKX_KEY", "env-key")
    config_file = tmp_path / "settings.yaml"
    config_file.write_text("exchange:\n  api_key: ${TEST_OKX_KEY}\n")

    config = load_config(str(config_file))

    assert config.exchange.api_key == "env-key"


def test_uses_defaults_for_missing_optional_values(tmp_path):
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(
        textwrap.dedent(
            """
            mode: backtest
            backtest:
              initial_capital: 50000
            risk:
              max_daily_loss_pct: 0.02
            """
        )
    )

    config = load_config(str(config_file))

    assert config.backtest.initial_capital == 50000
    assert config.backtest.fee_rate == 0.0005
    assert config.backtest.slippage == 0.001
    assert config.exchange.market_type == "spot"
    assert config.exchange.demo is True
    assert config.risk.max_daily_loss_pct == 0.02
    assert config.risk.max_drawdown_pct == 0.15
    assert config.risk.allow_live_open_orders is False
    assert config.risk.live_max_order_notional == 0.0
