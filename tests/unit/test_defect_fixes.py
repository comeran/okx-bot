"""默认下单量、实盘名义上限、独占账户和私有同步。"""

from pathlib import Path

from src.core.config import AppConfig, ExchangeConfig
from src.core.runtime_settings import save_runtime_settings
from src.data.repository import Repository
from src.ops.phase3_recovery import build_live_identity_lookup, default_private_fetch
from src.ops.serve import serve_command
from src.research.phase5 import ResearchBar, backtest_manifest_id, remember_backtest_manifest
from src.web.api.strategies import claim_live_symbol, create_order_manager


def test_live_configuration_uses_a_small_notional_cap_when_unset():
    save_runtime_settings(AppConfig(
        mode="live",
        exchange=ExchangeConfig(
            api_key="test-key", secret="test-secret", passphrase="test-passphrase",
            demo=False, market_type="spot",
        ),
    ))
    manager = create_order_manager(repository=Repository())
    assert manager.live_max_order_notional == 20


def test_second_live_strategy_cannot_claim_the_same_symbol(tmp_path):
    repository = Repository(db_path=str(tmp_path / "bot.db"))
    claim_live_symbol(repository, strategy="alpha", symbol="BTC-USDT", scope_id="acct")
    try:
        claim_live_symbol(repository, strategy="beta", symbol="BTC-USDT", scope_id="acct")
    except ValueError as exc:
        assert str(exc) == "symbol_owned"
    else:
        raise AssertionError("第二份策略不应占用同一现货")
    claim_live_symbol(repository, strategy="alpha", symbol="BTC-USDT", scope_id="acct")


def test_private_fetch_skips_the_exchange_outside_demo_and_live():
    calls = []

    def loader():
        return AppConfig(mode="paper")

    def factory(_settings):
        calls.append("factory")
        raise AssertionError("纸面模式不应连接交易所")

    page = default_private_fetch(loader=loader, factory=factory)()
    assert calls == []
    assert page["cursor_ms"] is None
    assert page["complete"] is False


def test_private_fetch_reads_sandbox_balance_when_the_header_is_simulated():
    class Client:
        def __init__(self):
            self.headers = {"x-simulated-trading": "1"}
            self.closed = False

        def fetch_balance(self):
            return {"USDT": {"free": "5"}}

        def close(self):
            self.closed = True

    client = Client()

    def loader():
        return AppConfig(
            mode="demo",
            exchange=ExchangeConfig(
                api_key="k", secret="s", passphrase="p", demo=True,
            ),
        )

    page = default_private_fetch(loader=loader, factory=lambda _settings: client)()
    assert page["complete"] is False
    assert page["cursor_ms"] == page["observed_at"]
    assert client.closed is True


def test_private_fetch_timestamps_success_after_balance_read(monkeypatch):
    import src.ops.phase3_recovery as recovery

    class Client:
        headers = {"x-simulated-trading": "1"}

        def fetch_balance(self):
            return {}

        def close(self):
            pass

    monkeypatch.setattr(recovery.time, "time", lambda: 100.1)
    page = default_private_fetch(
        loader=lambda: AppConfig(
            mode="demo",
            exchange=ExchangeConfig(api_key="k", secret="s", passphrase="p"),
        ),
        factory=lambda _settings: Client(),
    )()
    assert page["observed_at"] == 100100
    assert page["cursor_ms"] == 100100


def test_serve_command_restarts_the_app_factory():
    command = serve_command()
    assert command[0:3] == ["uv", "run", "uvicorn"]
    assert "src.web.app:create_app" in command
    assert "--factory" in command
    image = Path("Dockerfile").read_text(encoding="utf-8")
    assert "src.ops.serve" in image


def test_live_identity_lookup_reads_the_existing_order_without_placing_another():
    class Client:
        def __init__(self):
            self.headers = {"x-simulated-trading": "1"}
            self.closed = False

        def fetch_order(self, order_id, symbol, params):
            assert order_id == "cid-1"
            assert symbol == "BTC/USDT"
            assert params["clOrdId"] == "cid-1"
            return {"id": "ex-1", "status": "closed", "filled": 0.0001}

        def close(self):
            self.closed = True

    repository = type("Repo", (), {})()
    repository.get_orders = lambda: [type("Order", (), {
        "client_order_id": "cid-1", "symbol": "BTC-USDT",
    })()]
    settings = AppConfig(
        mode="demo",
        exchange=ExchangeConfig(api_key="k", secret="s", passphrase="p", demo=True),
    )
    client = Client()
    found = build_live_identity_lookup(
        settings, repository, factory=lambda _settings: client,
    )("cid-1")
    assert found["status"] == "filled"
    assert found["exchange_order_id"] == "ex-1"
    assert client.closed is True


def test_backtest_result_keeps_the_research_manifest(tmp_path):
    repository = Repository(db_path=str(tmp_path / "bot.db"))
    bars = (
        ResearchBar(1, 1, 2, 1, 2, 1),
        ResearchBar(2, 2, 3, 2, 3, 1),
    )
    manifest_id = remember_backtest_manifest(repository, "bt-1", bars)
    assert backtest_manifest_id(repository, "bt-1") == manifest_id
    assert backtest_manifest_id(repository, "missing") is None
