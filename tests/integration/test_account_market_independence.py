"""Credential configuration and per-operation market selection are independent."""

from types import SimpleNamespace

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.config import AppConfig, ExchangeConfig
from src.core.execution_scope import build_execution_context
from src.core.runtime_settings import load_runtime_settings, save_runtime_settings
from src.web.api import settings, strategies, trading
from src.web.exchange_auth import probe_saved_exchange_auth


def configured(market="spot"):
    return AppConfig(exchange=ExchangeConfig(
        api_key="synthetic-key", secret="synthetic-secret",
        passphrase="synthetic-passphrase", market_type=market,
    ))


def test_legacy_market_does_not_limit_account_context_or_change_identity():
    first, _ = build_execution_context(configured().exchange)
    for legacy_market in ("swap", "future", "option", "margin", "", None):
        context, scope = build_execution_context(configured(legacy_market).exchange)
        assert scope is not None
        assert context["available"] is True
        assert context["reason"] is None
        assert context["account_scope_id"] == first["account_scope_id"]
        assert context["market_type"] == "all"
        assert context["market_types"] == ["spot", "swap", "future", "option"]


def test_old_settings_load_but_new_saves_omit_global_market(monkeypatch, tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump({"exchange": {
        "api_key": "synthetic-key", "secret": "synthetic-secret",
        "passphrase": "synthetic-passphrase", "market_type": "swap", "demo": True,
    }}))
    monkeypatch.setenv("OKX_BOT_SETTINGS_PATH", str(path))
    config = load_runtime_settings()
    assert config.exchange.api_key == "synthetic-key"
    save_runtime_settings(config)
    persisted = yaml.safe_load(path.read_text())
    assert "market_type" not in persisted["exchange"]
    assert persisted["exchange"]["api_key"] == "synthetic-key"
    assert persisted["exchange"]["demo"] is True


def test_settings_save_keeps_credentials_without_market_configuration(monkeypatch):
    config = configured("swap")
    saved = []
    monkeypatch.setattr(settings, "load_runtime_settings", lambda: config)
    monkeypatch.setattr(settings, "save_runtime_settings", saved.append)
    app = FastAPI()
    app.include_router(settings.create_router(), prefix="/settings")
    client = TestClient(app)
    view = client.get("/settings").json()
    assert "market_type" not in view["exchange"]
    assert view["exchange"]["api_key_set"] is True
    view["exchange"]["demo"] = False
    response = client.put("/settings", json=view)
    assert response.status_code == 200
    assert "market_type" not in response.json()["exchange"]
    assert saved[0].exchange.api_key == "synthetic-key"
    assert saved[0].exchange.demo is False


@pytest.mark.asyncio
async def test_authentication_observation_is_independent_of_legacy_market(monkeypatch):
    from src.exchange import okx_client

    config = configured("swap")
    scope_id = build_execution_context(config.exchange)[0]["account_scope_id"]
    constructions = []

    class Client:
        async def private_get_account_config(self, params):
            config.exchange.market_type = "option"
            return {"code": "0", "data": [{"uid": "synthetic-uid"}]}

        async def close(self):
            pass

    def create_client(**kwargs):
        constructions.append(kwargs)
        return Client()

    monkeypatch.setattr(okx_client, "create_okx_client", create_client)
    result = await probe_saved_exchange_auth(lambda: config, scope_id)
    assert result["status"] == "authenticated"
    assert constructions[0]["default_type"] == "spot"


@pytest.fixture
def manual_client(monkeypatch):
    markets = []
    submitted = []

    class Manager:
        async def submit(self, **kwargs):
            submitted.append(kwargs)
            return SimpleNamespace(id="synthetic-order", status="open", **{
                key: kwargs[key] for key in ("symbol", "amount", "price")
            })

        async def cancel(self, order_id, symbol):
            submitted.append({"order_id": order_id, "symbol": symbol})
            return True

    def create_manager(*, market_type=None):
        markets.append(market_type)
        return Manager()

    monkeypatch.setattr(strategies, "create_order_manager", create_manager)
    app = FastAPI()
    app.include_router(trading.router, prefix="/trading")
    return TestClient(app), markets, submitted


@pytest.mark.parametrize("symbol,market", [
    ("BTC-USDT", "spot"),
    ("BTC-USDT-SWAP", "swap"),
    ("BTC-USDT-271231", "future"),
    ("BTC-USD-271231-100000-C", "option"),
])
def test_manual_orders_route_by_requested_instrument(manual_client, symbol, market):
    client, markets, submitted = manual_client
    response = client.post("/trading/orders", json={
        "symbol": symbol, "market_type": market, "side": "buy", "order_type": "limit",
        "amount": 1, "price": 100,
    })
    assert response.status_code == 200
    assert markets == [market]
    assert submitted[0]["symbol"] == symbol


def test_legacy_manual_payload_infers_market_without_global_setting(manual_client):
    client, markets, _ = manual_client
    response = client.post("/trading/orders", json={
        "symbol": "BTC-USDT-SWAP", "side": "buy", "amount": 1, "price": 100,
    })
    assert response.status_code == 200
    assert markets == ["swap"]


def test_market_mismatch_is_rejected_before_manager_creation(manual_client):
    client, markets, submitted = manual_client
    response = client.post("/trading/orders", json={
        "symbol": "BTC-USDT", "market_type": "swap", "side": "buy",
        "amount": 1, "price": 100,
    })
    assert response.status_code == 422
    assert markets == submitted == []


def test_cancellation_infers_market_from_order_symbol(manual_client):
    client, markets, submitted = manual_client
    response = client.post("/trading/orders/synthetic-order/cancel", params={
        "symbol": "BTC-USDT-SWAP",
    })
    assert response.status_code == 200
    assert markets == ["swap"]
    assert submitted == [{"order_id": "synthetic-order", "symbol": "BTC-USDT-SWAP"}]
