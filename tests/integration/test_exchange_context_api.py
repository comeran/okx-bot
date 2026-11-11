import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from starlette.websockets import WebSocketDisconnect

from src.core.config import AppConfig, ExchangeConfig
from src.data.models import AccountRecord, OrderRecord
from src.data.repository import Repository
from src.web import app as web_app
from src.web.api import ops, trading


def account(equity=123, strategy="__exchange__", source="live_exchange", runtime="live"):
    return AccountRecord(
        strategy=strategy, source=source, runtime_mode=runtime, initial_equity=equity,
        cash_balance=equity, available_balance=equity, equity=equity, realized_pnl=0,
        unrealized_pnl=0, daily_pnl=0, fees_paid=0, updated_at=1,
    )


@pytest.fixture
def configured(monkeypatch):
    config = AppConfig(mode="live", exchange=ExchangeConfig(
        api_key="test-account-a", secret="test-secret", passphrase="test-passphrase",
    ))
    monkeypatch.setattr(trading, "load_runtime_settings", lambda: config)
    monkeypatch.setattr(ops.settings_api, "_load_settings", lambda: config)
    monkeypatch.setattr(web_app, "load_runtime_settings", lambda: config, raising=False)
    web_app.ws_manager = web_app.WebSocketManager()
    return config, TestClient(web_app.create_app())


def current_scope(client):
    response = client.get("/api/trading/context")
    assert response.status_code == 200
    return response.json()["account_scope_id"]


def scoped_repository(config):
    from src.core.execution_scope import build_execution_context
    from src.data.exchange_store import open_exchange_repository

    _, scope = build_execution_context(config.exchange)
    return open_exchange_repository(scope, master_engine=Repository().engine)


def test_exchange_snapshot_requires_binding_and_excludes_master_legacy(configured):
    _, client = configured
    Repository().upsert_account(account(999))
    scope_id = current_scope(client)
    for route in ("snapshot", "account", "positions", "orders", "trades"):
        params = {} if route == "snapshot" else {"scope": "exchange"}
        assert client.get(f"/api/trading/{route}", params=params).status_code == 409
        params["expected_account_scope_id"] = "wrong"
        assert client.get(f"/api/trading/{route}", params=params).status_code == 409
    response = client.get("/api/trading/snapshot", params={
        "expected_account_scope_id": scope_id,
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["context"]["account_scope_id"] == scope_id
    assert payload["data"]["account"] is None
    assert payload["data"]["orders"] == payload["data"]["positions"] == []
    assert payload["context"]["execution_ready"] is False


def test_live_unowned_views_are_empty_while_paper_stays_on_master(configured):
    _, client = configured
    master = Repository()
    master.upsert_account(account(999, "old-live"))
    master.upsert_account(account(42, "paper-one", "paper", "paper"))
    for route in ("positions", "orders", "trades", "strategy-performance"):
        assert client.get(f"/api/trading/{route}", params={
            "runtime_mode": "live",
        }).json() == []
    assert client.get("/api/trading/account", params={
        "runtime_mode": "live",
    }).json()["equity"] == 0
    assert client.get("/api/trading/account", params={
        "runtime_mode": "paper",
    }).json()["equity"] == 42


def test_same_external_order_ids_are_read_from_only_current_account(configured):
    config, client = configured
    for key, equity in (("test-account-a", 10), ("test-account-b", 20)):
        config.exchange.api_key = key
        repository = scoped_repository(config)
        repository.upsert_account(account(equity))
        repository.upsert_order(OrderRecord(
            order_id="same-local", exchange_order_id="same-external", strategy="__exchange__",
            source="live_exchange", runtime_mode="live", symbol="BTC/USDT", side="buy",
            type="limit", amount=1, price=equity, status="submitted", fill_price=0, timestamp=1,
        ))
    scope_id = current_scope(client)
    result = client.get("/api/trading/snapshot", params={
        "expected_account_scope_id": scope_id,
    }).json()
    assert result["data"]["account"]["equity"] == 20
    assert [order["price"] for order in result["data"]["orders"]] == [20]


@pytest.mark.parametrize("global_kill,account_kill", [(False, False), (False, True),
                                                       (True, False), (True, True)])
def test_snapshot_exposes_independent_and_effective_kill_states(
    configured, global_kill, account_kill,
):
    config, client = configured
    Repository().set_kill_switch(global_kill, "global", 1)
    repository = scoped_repository(config)
    repository.set_kill_switch(account_kill, "account", 2)
    payload = client.get("/api/trading/snapshot", params={
        "expected_account_scope_id": current_scope(client),
    }).json()["data"]["kill_switch"]
    assert payload["global"]["engaged"] is global_kill
    assert payload["account"]["engaged"] is account_kill
    assert payload["effective"]["engaged"] is (global_kill or account_kill)
    client.put("/api/ops/kill-switch", json={"engaged": False})
    assert repository.get_kill_switch().engaged is account_kill


def test_unavailable_context_has_empty_facts_without_precondition(configured):
    config, client = configured
    config.exchange.api_key = ""
    Repository().upsert_account(account(999))
    response = client.get("/api/trading/snapshot")
    assert response.status_code == 200
    assert response.json()["context"]["available"] is False
    assert response.json()["data"]["account"] is None


@pytest.mark.asyncio
async def test_delayed_private_sync_keeps_original_repository_and_broadcast_context(
    configured, monkeypatch,
):
    config, client = configured
    original_scope = current_scope(client)
    started, release = asyncio.Event(), asyncio.Event()
    broadcasts = []
    adapter = SimpleNamespace(close=lambda: asyncio.sleep(0))
    monkeypatch.setattr(ops, "_create_adapter", lambda settings: adapter)

    async def sync(repository, captured_adapter, **kwargs):
        assert captured_adapter is adapter
        started.set()
        await release.wait()
        repository.upsert_account(account(77))
        await kwargs["risk_event_notifier"]({
            "type": "risk_event", "strategy": "__exchange__", "severity": "high",
        })
        return SimpleNamespace(as_response=lambda: {"account_upserted": 1})

    async def broadcast(payload):
        broadcasts.append(payload)

    monkeypatch.setattr(ops, "sync_private_state", sync)
    app = FastAPI()
    app.include_router(ops.create_router(broadcast), prefix="/api/ops")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        pending = asyncio.create_task(http.post("/api/ops/sync/private", json={}, params={
            "expected_account_scope_id": original_scope,
        }))
        await asyncio.wait_for(started.wait(), 2)
        config.exchange.api_key = "test-account-b"
        release.set()
        result = await pending
    assert result.status_code == 200
    assert result.json()["context"]["account_scope_id"] == original_scope
    assert len(broadcasts) == 2
    assert all(item["context"]["account_scope_id"] == original_scope for item in broadcasts)
    assert scoped_repository(config).get_account("__exchange__") is None
    config.exchange.api_key = "test-account-a"
    assert scoped_repository(config).get_account("__exchange__").equity == 77
    assert Repository().get_account("__exchange__") is None


def test_exchange_websocket_requires_binding_and_snapshot_excludes_master(configured):
    _, client = configured
    Repository().upsert_account(account(999))
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?scope=exchange"):
            pass
    scope_id = current_scope(client)
    with client.websocket_connect(
        f"/ws?scope=exchange&expected_account_scope_id={scope_id}"
    ) as ws:
        snapshot = ws.receive_json()
        assert snapshot["context"]["account_scope_id"] == scope_id
        assert snapshot["data"]["account"] is None


def test_invalid_facts_header_fails_closed_without_touching_file_or_master(configured):
    config, client = configured
    repository = scoped_repository(config)
    path = Path(repository.engine.url.database)
    repository.engine.dispose()
    path.write_bytes(b"not a SQLite facts file")
    before = path.read_bytes()
    Repository().upsert_account(account(999))
    response = client.get("/api/trading/snapshot", params={
        "expected_account_scope_id": current_scope(client),
    })
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "account_store_unavailable"
    assert path.read_bytes() == before
    assert Repository().get_account("__exchange__").equity == 999


@pytest.mark.asyncio
async def test_delayed_refresh_keeps_copied_exchange_and_facts_binding(configured, monkeypatch):
    config, client = configured
    original_scope = current_scope(client)
    started, release = asyncio.Event(), asyncio.Event()

    async def refresh(exchange, repository, strategy, symbols, timestamp_ms):
        started.set()
        await release.wait()
        assert exchange.api_key == "test-account-a"
        repository.upsert_account(account(88))
        return SimpleNamespace(account=account(88), positions=[])

    monkeypatch.setattr(trading, "refresh_okx_live_state", refresh)
    async with AsyncClient(transport=ASGITransport(app=client.app), base_url="http://test") as http:
        pending = asyncio.create_task(http.post("/api/trading/live-state/refresh", params={
            "strategy": "__exchange__", "expected_account_scope_id": original_scope,
        }))
        await asyncio.wait_for(started.wait(), 2)
        config.exchange.api_key = "test-account-b"
        release.set()
        result = await pending
    assert result.status_code == 200
    assert result.json()["context"]["account_scope_id"] == original_scope
    assert scoped_repository(config).get_account("__exchange__") is None


def test_manual_sync_requires_binding_before_constructing_adapter(configured, monkeypatch):
    _, client = configured

    def forbidden(*args, **kwargs):
        raise AssertionError("provider must not be constructed without account binding")

    monkeypatch.setattr(ops, "_create_adapter", forbidden)
    monkeypatch.setattr(trading, "refresh_okx_live_state", forbidden)
    assert client.post("/api/ops/sync/private", json={}).status_code == 409
    assert client.post("/api/trading/live-state/refresh", params={
        "strategy": "__exchange__",
    }).status_code == 409


def test_settings_save_notifies_strategy_socket_without_account_credentials():
    with TestClient(web_app.create_app()) as client:
        settings = client.get("/api/settings").json()
        with client.websocket_connect("/ws?scope=strategy&strategy=*") as ws:
            assert ws.receive_json()["type"] == "snapshot"
            response = client.put("/api/settings", json=settings)
            assert response.status_code == 200
            assert ws.receive_json() == {"type": "context_changed"}


def test_websocket_endpoint_sends_no_snapshot_after_rejected_handshake(configured, monkeypatch):
    _, client = configured

    async def reject(ws, **kwargs):
        await ws.accept()
        await ws.send_json({"type": "context_changed"})
        await ws.close(code=1000)
        return False

    def forbidden_snapshot(*args, **kwargs):
        raise AssertionError("a rejected handshake must not read or send financial facts")

    monkeypatch.setattr(web_app.ws_manager, "connect", reject)
    monkeypatch.setattr(web_app, "runtime_snapshot", forbidden_snapshot)
    with client.websocket_connect(
        f"/ws?scope=exchange&expected_account_scope_id={current_scope(client)}"
    ) as ws:
        assert ws.receive_json() == {"type": "context_changed"}
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
