import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlmodel import Session, select

from src.core.config import AppConfig, ExchangeConfig
from src.core.execution_scope import build_execution_context
from src.core.runtime_settings import save_runtime_settings
from src.data.models import AccountRecord, PositionRecord, StrategyConfigRecord
from src.data.repository import Repository
from src.web import app as web_app
from src.web.api import allocations, strategies


def scope_id(client):
    return client.get("/api/allocations/context").json()["scope_id"] or "unavailable"


def remove_draft(client, revision):
    return client.delete("/api/allocations/ma_cross", params={
        "revision": revision, "expected_scope_id": scope_id(client), "expected_symbol": "BTC-USDT",
    })


@pytest.fixture
def allocation_client(monkeypatch):
    save_runtime_settings(AppConfig(mode="live", exchange=ExchangeConfig(
        api_key="test-key", secret="test-secret", passphrase="test-passphrase", demo=True,
    )))
    runtime = strategies.StrategyRuntimeState()
    monkeypatch.setattr(strategies, "StrategyRuntimeState", lambda: runtime)
    with TestClient(web_app.create_app()) as client:
        yield client, runtime


def test_allocation_routes_expose_configuration_only_context(allocation_client):
    client, _ = allocation_client
    response = client.get("/api/allocations/context")
    assert response.status_code == 200
    context = response.json()
    assert context["available"] is True
    assert context["environment"] == "sandbox"
    assert context["execution_mode"] == "live"
    assert context["identity_verified"] is False
    assert context["execution_ready"] is False
    assert "test-key" not in response.text
    assert "test-secret" not in response.text
    assert "test-passphrase" not in response.text


@pytest.mark.parametrize("configured", [True, False])
def test_draft_envelope_binds_both_scopes_from_one_settings_capture(
    allocation_client, monkeypatch, configured,
):
    client, _ = allocation_client
    captured = AppConfig(mode="live", exchange=ExchangeConfig(
        api_key="test-key" if configured else "", secret="test-secret" if configured else "",
        passphrase="test-passphrase" if configured else "", demo=True,
    ))
    changed = AppConfig(mode="paper", exchange=ExchangeConfig(
        api_key="other-key", secret="other-secret", passphrase="other-passphrase", demo=False,
    ))
    calls = []

    def changing_settings():
        calls.append(1)
        return captured if len(calls) == 1 else changed

    monkeypatch.setattr(allocations, "load_runtime_settings", changing_settings)
    response = client.get("/api/allocations/ma_cross")
    assert response.status_code == 200
    payload = response.json()
    assert payload["execution_context"] == build_execution_context(captured.exchange)[0]
    assert payload["context"] == allocations.allocation_context(captured)[0]
    assert len(calls) == 1
    assert payload["execution_context"]["available"] is configured
    assert "test-key" not in response.text


def test_save_delete_recreate_is_draft_only_and_keeps_live_gate_closed(allocation_client):
    client, _ = allocation_client
    before = client.get("/api/allocations/ma_cross").json()
    assert before["draft"] is None
    assert before["can_edit"] is True
    saved = client.put("/api/allocations/ma_cross", json={
        "expected_symbol": "BTC-USDT",
        "expected_scope_id": scope_id(client),
        "budget": 100, "expected_revision": 0,
    })
    assert saved.status_code == 200
    draft = saved.json()["draft"]
    assert (draft["strategy"], draft["symbol"], draft["currency"]) == (
        "ma_cross", "BTC-USDT", "USDT",
    )
    assert (draft["budget"], draft["revision"], draft["status"]) == (100, 1, "draft")
    with Session(Repository().engine) as session:
        assert list(session.exec(select(AccountRecord)).all()) == []
        assert list(session.exec(select(PositionRecord)).all()) == []
    assert strategies.has_live_strategy_ownership_mapping(
        Repository(), "ma_cross", "BTC-USDT",
    ) is False
    live_start = client.post("/api/strategies/ma_cross/start")
    assert live_start.status_code == 409
    assert live_start.json()["detail"]["code"] == "live_position_ownership_unavailable"
    removed = remove_draft(client, 1)
    assert removed.status_code == 200
    assert removed.json()["draft"] is None
    recreated = client.put("/api/allocations/ma_cross", json={
        "expected_symbol": "BTC-USDT",
        "expected_scope_id": scope_id(client),
        "budget": 200, "expected_revision": 0,
    })
    assert recreated.status_code == 200
    assert recreated.json()["draft"]["revision"] == 3
    assert client.put("/api/allocations/ma_cross", json={
        "expected_symbol": "BTC-USDT",
        "expected_scope_id": scope_id(client),
        "budget": 999, "expected_revision": 1,
    }).status_code == 409


@pytest.mark.parametrize("status", ["starting", "running", "unknown", "error"])
def test_mutations_reject_non_stopped_lifecycle_state(allocation_client, status):
    client, runtime = allocation_client
    assert client.put("/api/allocations/ma_cross", json={
        "expected_symbol": "BTC-USDT",
        "expected_scope_id": scope_id(client),
        "budget": 100, "expected_revision": 0,
    }).status_code == 200
    runtime.strategy_status["ma_cross"] = status
    assert client.get("/api/allocations/ma_cross").json()["can_edit"] is False
    assert client.put("/api/allocations/ma_cross", json={
        "expected_symbol": "BTC-USDT",
        "expected_scope_id": scope_id(client),
        "budget": 90, "expected_revision": 1,
    }).status_code == 409
    assert remove_draft(client, 1).status_code == 409


@pytest.mark.parametrize("owner_map", ["engines", "starting_engines"])
def test_engine_ownership_overrides_stale_stopped_label(allocation_client, owner_map):
    client, runtime = allocation_client
    getattr(runtime, owner_map)["ma_cross"] = SimpleNamespace()
    response = client.put("/api/allocations/ma_cross", json={
        "expected_symbol": "BTC-USDT",
        "expected_scope_id": scope_id(client),
        "budget": 100, "expected_revision": 0,
    })
    assert response.status_code == 409


def test_unavailable_context_never_fabricates_account_scope(allocation_client):
    client, _ = allocation_client
    save_runtime_settings(AppConfig(exchange=ExchangeConfig()))
    context = client.get("/api/allocations/context").json()
    assert context["available"] is False
    assert context["scope_id"] is None
    assert context["account_fingerprint"] is None
    assert context["reason"] == "credentials_unavailable"
    assert client.get("/api/allocations/ma_cross").json()["draft"] is None
    assert client.put("/api/allocations/ma_cross", json={
        "expected_symbol": "BTC-USDT",
        "expected_scope_id": scope_id(client),
        "budget": 100, "expected_revision": 0,
    }).status_code == 409


@pytest.mark.parametrize("payload", [
    {"budget": 0, "expected_revision": 0},
    {"budget": -1, "expected_revision": 0},
    {"budget": True, "expected_revision": 0},
    {"budget": "100", "expected_revision": 0},
    {"budget": 100, "expected_revision": True},
    {"budget": 100, "expected_revision": 0, "scope_id": "injected"},
])
def test_invalid_payload_does_not_create_draft(allocation_client, payload):
    client, _ = allocation_client
    response = client.put("/api/allocations/ma_cross", json={
        "expected_scope_id": scope_id(client), "expected_symbol": "BTC-USDT", **payload,
    })
    assert response.status_code == 422
    assert client.get("/api/allocations/ma_cross").json()["draft"] is None


def test_context_and_drafts_follow_server_credentials_and_environment(allocation_client):
    client, _ = allocation_client
    first = client.put("/api/allocations/ma_cross", json={
        "expected_symbol": "BTC-USDT",
        "expected_scope_id": scope_id(client),
        "budget": 100, "expected_revision": 0,
    }).json()
    save_runtime_settings(AppConfig(mode="demo", exchange=ExchangeConfig(
        api_key="other-key", secret="secret", passphrase="pass", demo=False,
    )))
    second = client.get("/api/allocations/ma_cross").json()
    assert second["draft"] is None
    assert second["context"]["scope_id"] != first["context"]["scope_id"]
    assert second["context"]["environment"] == "live"
    assert second["context"]["execution_mode"] == "demo"


def test_strategy_registry_and_duplicate_symbol_are_validated(allocation_client):
    client, runtime = allocation_client
    assert client.get("/api/allocations/missing").status_code == 404
    Repository().upsert_strategy_config(StrategyConfigRecord(
        name="alpha", strategy_type="ma_cross", symbol="BTC-USDT", timeframe="1h",
        params={}, created_at=1, updated_at=1,
    ))
    runtime.strategy_status["alpha"] = "stopped"
    assert client.put("/api/allocations/alpha", json={
        "expected_symbol": "BTC-USDT",
        "expected_scope_id": scope_id(client),
        "budget": 100, "expected_revision": 0,
    }).status_code == 200
    response = client.put("/api/allocations/ma_cross", json={
        "expected_symbol": "BTC-USDT",
        "expected_scope_id": scope_id(client),
        "budget": 100, "expected_revision": 0,
    })
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "symbol_conflict"


def test_non_spot_context_is_unavailable(allocation_client):
    client, _ = allocation_client
    save_runtime_settings(AppConfig(exchange=ExchangeConfig(
        api_key="key", secret="secret", passphrase="pass", market_type="swap",
    )))
    context = client.get("/api/allocations/context").json()
    assert context["available"] is False
    assert context["reason"] == "unsupported_market"
    assert client.put("/api/allocations/ma_cross", json={
        "expected_symbol": "BTC-USDT",
        "expected_scope_id": scope_id(client),
        "budget": 100, "expected_revision": 0,
    }).status_code == 409


@pytest.mark.parametrize("change", ["credentials", "environment"])
def test_stale_account_context_cannot_create_or_delete_drafts(allocation_client, change):
    client, _ = allocation_client
    old_scope = scope_id(client)
    assert client.put("/api/allocations/ma_cross", json={
        "expected_symbol": "BTC-USDT",
        "budget": 100, "expected_revision": 0, "expected_scope_id": old_scope,
    }).status_code == 200
    from src.data.allocations import AllocationAuditRecord, AllocationDraftRecord

    with Session(Repository().engine) as session:
        audits_before = len(session.exec(select(AllocationAuditRecord)).all())
    save_runtime_settings(AppConfig(mode="live", exchange=ExchangeConfig(
        api_key="other-key" if change == "credentials" else "test-key",
        secret="test-secret", passphrase="test-passphrase", demo=change != "environment",
    )))
    assert scope_id(client) != old_scope
    stale_save = client.put("/api/allocations/ma_cross", json={
        "expected_symbol": "BTC-USDT",
        "budget": 500, "expected_revision": 0, "expected_scope_id": old_scope,
    })
    stale_delete = client.delete("/api/allocations/ma_cross", params={
        "revision": 1, "expected_scope_id": old_scope, "expected_symbol": "BTC-USDT",
    })
    for response in (stale_save, stale_delete):
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "allocation_scope_changed"
    with Session(Repository().engine) as session:
        assert len(session.exec(select(AllocationAuditRecord)).all()) == audits_before
        records = session.exec(select(AllocationDraftRecord)).all()
        assert len(records) == 1
        assert records[0].budget == 100
        assert records[0].deleted is False


def test_mutations_require_explicit_loaded_scope(allocation_client):
    client, _ = allocation_client
    assert client.put("/api/allocations/ma_cross", json={
        "expected_symbol": "BTC-USDT",
        "budget": 100, "expected_revision": 0,
    }).status_code == 422
    assert client.delete("/api/allocations/ma_cross?revision=1").status_code == 422


@pytest.mark.asyncio
async def test_mutation_rechecks_lifecycle_after_shared_lock(allocation_client, monkeypatch):
    client, runtime = allocation_client
    loaded_scope = scope_id(client)
    lock = runtime.lifecycle_lock("ma_cross")
    requested = asyncio.Event()
    original_lock = runtime.lifecycle_lock

    def observed_lock(name):
        requested.set()
        return original_lock(name)

    monkeypatch.setattr(runtime, "lifecycle_lock", observed_lock)
    async with AsyncClient(transport=ASGITransport(app=client.app), base_url="http://test") as api:
        async with lock:
            mutation = asyncio.create_task(api.put("/api/allocations/ma_cross", json={
        "expected_symbol": "BTC-USDT",
                "budget": 100, "expected_revision": 0, "expected_scope_id": loaded_scope,
            }))
            await asyncio.wait_for(requested.wait(), timeout=2)
            assert not mutation.done()
            runtime.strategy_status["ma_cross"] = "running"
        response = await mutation
    assert response.status_code == 409
    assert client.get("/api/allocations/ma_cross").json()["draft"] is None


def test_stale_strategy_symbol_cannot_create_or_delete_draft(allocation_client):
    client, runtime = allocation_client
    repository = Repository()
    config = StrategyConfigRecord(
        name="alpha", strategy_type="ma_cross", symbol="BTC-USDT", timeframe="1h",
        params={}, created_at=1, updated_at=1,
    )
    repository.upsert_strategy_config(config)
    runtime.strategy_status["alpha"] = "stopped"
    loaded_scope = scope_id(client)
    config.symbol = "ETH-USDT"
    repository.upsert_strategy_config(config)
    stale_create = client.put("/api/allocations/alpha", json={
        "budget": 100, "expected_revision": 0, "expected_scope_id": loaded_scope,
        "expected_symbol": "BTC-USDT",
    })
    assert stale_create.status_code == 409
    assert stale_create.json()["detail"]["code"] == "allocation_symbol_changed"
    assert client.get("/api/allocations/alpha").json()["draft"] is None
    saved = client.put("/api/allocations/alpha", json={
        "budget": 100, "expected_revision": 0, "expected_scope_id": loaded_scope,
        "expected_symbol": "ETH-USDT",
    })
    assert saved.status_code == 200
    config.symbol = "SOL-USDT"
    repository.upsert_strategy_config(config)
    stale_delete = client.delete("/api/allocations/alpha", params={
        "revision": 1, "expected_scope_id": loaded_scope, "expected_symbol": "ETH-USDT",
    })
    assert stale_delete.status_code == 409
    assert stale_delete.json()["detail"]["code"] == "allocation_symbol_changed"
    assert client.get("/api/allocations/alpha").json()["draft"]["symbol"] == "ETH-USDT"


def test_mutations_require_explicit_loaded_symbol(allocation_client):
    client, _ = allocation_client
    assert client.put("/api/allocations/ma_cross", json={
        "budget": 100, "expected_revision": 0, "expected_scope_id": scope_id(client),
    }).status_code == 422
    assert client.delete("/api/allocations/ma_cross", params={
        "revision": 1, "expected_scope_id": scope_id(client),
    }).status_code == 422
