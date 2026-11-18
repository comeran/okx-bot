import pytest
import yaml
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.core.config import AppConfig, ExchangeConfig, load_config
from src.core.runtime_settings import save_runtime_settings
from src.web.api import settings as settings_api
from src.web.api import strategies as strategy_api
from src.web.api import trading as trading_api


def settings_app():
    app = FastAPI()
    app.include_router(settings_api.create_router(), prefix="/api/settings")
    return app


@pytest.mark.parametrize(("mode", "demo"), [("live", True), ("demo", False)])
async def test_rejects_conflicting_settings_without_mutating_saved_values(mode, demo):
    async with AsyncClient(
        transport=ASGITransport(app=settings_app()), base_url="http://test",
    ) as client:
        before = (await client.get("/api/settings")).json()
        response = await client.put(
            "/api/settings", json={"mode": mode, "exchange": {"demo": demo}},
        )
        assert response.status_code == 422
        assert "exchange.demo" in response.text
        assert (await client.get("/api/settings")).json() == before


@pytest.mark.parametrize(("mode", "demo"), [("live", False), ("demo", True)])
async def test_mode_only_updates_derive_and_persist_environment(mode, demo, monkeypatch, tmp_path):
    path = tmp_path / "settings.yaml"
    monkeypatch.setenv("OKX_BOT_SETTINGS_PATH", str(path))
    async with AsyncClient(
        transport=ASGITransport(app=settings_app()), base_url="http://test",
    ) as client:
        response = await client.put("/api/settings", json={"mode": mode})
        assert response.status_code == 200
        assert response.json()["exchange"]["demo"] is demo
    assert yaml.safe_load(path.read_text())["exchange"]["demo"] is demo
    async with AsyncClient(
        transport=ASGITransport(app=settings_app()), base_url="http://test",
    ) as client:
        assert (await client.get("/api/settings")).json()["exchange"]["demo"] is demo


@pytest.mark.parametrize(("mode", "demo"), [("live", True), ("demo", False)])
async def test_legacy_conflict_remains_readable_and_requires_explicit_repair(
    mode, demo, monkeypatch, tmp_path,
):
    path = tmp_path / "legacy.yaml"
    legacy = {"mode": mode, "exchange": {"demo": demo, "api_key": "saved-key"}}
    path.write_text(yaml.safe_dump(legacy))
    monkeypatch.setenv("OKX_BOT_SETTINGS_PATH", str(path))
    async with AsyncClient(
        transport=ASGITransport(app=settings_app()), base_url="http://test",
    ) as client:
        response = await client.get("/api/settings")
        assert response.status_code == 200
        assert response.json()["mode"] == mode
        assert response.json()["exchange"]["demo"] is demo
        assert yaml.safe_load(path.read_text()) == legacy
        with pytest.raises(ValueError, match="exchange.demo"):
            strategy_api.current_order_router_mode()
        repaired = await client.put("/api/settings", json={"mode": "demo"})
        assert repaired.status_code == 200
        assert repaired.json()["exchange"]["demo"] is True
        assert yaml.safe_load(path.read_text())["exchange"]["api_key"] == "saved-key"


@pytest.mark.parametrize(("mode", "demo"), [("live", True), ("demo", False)])
async def test_legacy_conflict_rejects_strategy_start_before_lookup(mode, demo, monkeypatch):
    config = AppConfig(mode=mode, exchange=ExchangeConfig(demo=demo))
    monkeypatch.setattr(strategy_api, "load_runtime_settings", lambda: config)
    app = FastAPI()
    app.include_router(strategy_api.create_router(), prefix="/strategies")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.post("/strategies/nonexistent/start")
        assert response.status_code == 400
        assert "exchange.demo" in response.json()["detail"]


@pytest.mark.parametrize(("mode", "demo"), [("live", True), ("demo", False)])
@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        ("/orders", {"symbol": "BTC-USDT", "side": "buy", "amount": 1, "price": 100}),
        ("/orders/order-id/cancel?symbol=BTC-USDT", None),
        ("/orders/order-id/amend", {"symbol": "BTC-USDT", "price": 100}),
    ],
)
async def test_legacy_conflict_rejects_manual_orders_before_adapter_creation(
    mode, demo, endpoint, payload, monkeypatch,
):
    monkeypatch.setattr(
        strategy_api, "load_runtime_settings",
        lambda: AppConfig(mode=mode, exchange=ExchangeConfig(demo=demo)),
    )
    monkeypatch.setattr(
        strategy_api, "create_okx_adapter",
        lambda *args, **kwargs: pytest.fail("Must reject before creating an adapter"),
    )
    app = FastAPI()
    app.include_router(trading_api.router, prefix="/trading")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.post(f"/trading{endpoint}", json=payload)
        assert response.status_code == 422
        assert "exchange.demo" in response.json()["detail"]


@pytest.mark.parametrize(("mode", "demo"), [("live", True), ("demo", False)])
def test_direct_save_and_adapter_creation_reject_conflicts(mode, demo, monkeypatch, tmp_path):
    path = tmp_path / "saved.yaml"
    monkeypatch.setenv("OKX_BOT_SETTINGS_PATH", str(path))
    config = AppConfig(mode=mode, exchange=ExchangeConfig(demo=demo))
    with pytest.raises(ValueError, match="exchange.demo"):
        save_runtime_settings(config)
    assert not path.exists()
    monkeypatch.setattr(
        strategy_api, "create_okx_adapter",
        lambda *args, **kwargs: pytest.fail("Must reject before creating any exchange adapter"),
    )
    with pytest.raises(ValueError, match="exchange.demo"):
        strategy_api.create_live_order_handler(config)


@pytest.mark.parametrize(("mode", "demo"), [("live", False), ("demo", True)])
def test_config_without_legacy_demo_field_uses_mode(mode, demo, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"mode": mode}))
    assert load_config(str(path)).exchange.demo is demo
