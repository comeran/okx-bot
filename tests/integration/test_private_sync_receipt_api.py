from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.core.config import AppConfig, ExchangeConfig
from src.core.execution_scope import build_execution_context
from src.data.exchange_store import open_exchange_repository
from src.data.repository import Repository
from src.web.api import ops


@pytest.fixture
def configured(monkeypatch):
    config = AppConfig(exchange=ExchangeConfig(
        api_key="receipt-account-a", secret="test-secret", passphrase="test-passphrase",
    ))
    monkeypatch.setattr(ops.settings_api, "_load_settings", lambda: config)
    app = FastAPI()
    app.include_router(ops.create_router(), prefix="/api/ops")
    return config, TestClient(app)


def scope_for(config):
    context, scope = build_execution_context(config.exchange)
    return context, scope


def scoped_repository(config):
    _, scope = scope_for(config)
    return open_exchange_repository(scope, master_engine=Repository().engine)


def start(repository, scope_id, *, started_at=1000):
    return repository.start_private_sync_receipt(
        account_scope_id=scope_id, symbols=["BTC-USDT"], since=10, limit=100,
        started_at=started_at,
    )


def finish(repository, attempt):
    return repository.finalize_private_sync_receipt(
        attempt.attempt_id, state="committed", completed_at=1100, received_at=1090,
        parts={"trades": {"coverage": {"completeness": "partial"}}},
        cursor={"durable": False, "complete": False}, result={"account_upserted": 1},
    )


@pytest.mark.parametrize("scope_id", [None, "wrong-account"])
def test_sync_status_rejects_missing_or_wrong_scope_before_provider(
    configured, monkeypatch, scope_id,
):
    _, client = configured

    def forbidden(*args, **kwargs):
        raise AssertionError("status must never construct a provider or run sync")

    monkeypatch.setattr(ops, "_create_adapter", forbidden)
    monkeypatch.setattr(ops, "sync_private_state", forbidden)
    params = {} if scope_id is None else {"expected_account_scope_id": scope_id}
    response = client.get("/api/ops/sync/private/status", params=params)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "account_scope_changed"


def test_sync_status_is_empty_without_receipt_and_never_uses_master_or_provider(
    configured, monkeypatch,
):
    config, client = configured
    context, _ = scope_for(config)
    bound = scoped_repository(config)
    bound.engine.dispose()
    master_path = Repository().engine.url.database
    original_get_status = Repository.get_private_sync_status

    def checked_read(repository):
        assert repository.engine.url.database != master_path
        return original_get_status(repository)

    def forbidden(*args, **kwargs):
        raise AssertionError("status must never construct a provider or run sync")

    monkeypatch.setattr(Repository, "get_private_sync_status", checked_read)
    monkeypatch.setattr(ops, "_create_adapter", forbidden)
    monkeypatch.setattr(ops, "sync_private_state", forbidden)
    response = client.get("/api/ops/sync/private/status", params={
        "expected_account_scope_id": context["account_scope_id"],
    })
    assert response.status_code == 200
    assert response.json() == {"context": context, "latest_attempt": None, "last_committed": None}


def test_sync_status_retains_committed_receipt_after_failed_attempt_without_refresh(configured):
    config, client = configured
    context, _ = scope_for(config)
    repository = scoped_repository(config)
    committed = finish(repository, start(repository, context["account_scope_id"]))
    attempt = start(repository, context["account_scope_id"], started_at=1200)
    failed = repository.fail_private_sync_receipt(
        attempt.attempt_id, completed_at=1250, error_code="provider_unavailable",
    )
    params = {"expected_account_scope_id": context["account_scope_id"]}
    first = client.get("/api/ops/sync/private/status", params=params)
    second = client.get("/api/ops/sync/private/status", params=params)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == {
        "context": context, "latest_attempt": failed.model_dump(),
        "last_committed": committed.model_dump(),
    }
    assert repository.get_private_sync_status()["last_committed"].received_at == 1090


def test_sync_status_switches_account_without_reusing_previous_receipts(configured):
    config, client = configured
    context_a, _ = scope_for(config)
    repository_a = scoped_repository(config)
    committed = finish(repository_a, start(repository_a, context_a["account_scope_id"]))
    config.exchange.api_key = "receipt-account-b"
    context_b, _ = scope_for(config)
    assert client.get("/api/ops/sync/private/status", params={
        "expected_account_scope_id": context_a["account_scope_id"],
    }).status_code == 409
    response_b = client.get("/api/ops/sync/private/status", params={
        "expected_account_scope_id": context_b["account_scope_id"],
    })
    assert response_b.status_code == 200
    assert response_b.json()["last_committed"] is None
    config.exchange.api_key = "receipt-account-a"
    response_a = client.get("/api/ops/sync/private/status", params={
        "expected_account_scope_id": context_a["account_scope_id"],
    })
    assert response_a.json()["last_committed"] == committed.model_dump()


def test_sync_status_requires_available_account_context(configured):
    config, client = configured
    config.exchange.api_key = ""
    response = client.get("/api/ops/sync/private/status")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "account_context_unavailable"


def test_sync_status_fails_closed_with_bounded_message_for_foreign_receipt(configured):
    config, client = configured
    context, _ = scope_for(config)
    repository = scoped_repository(config)
    start(repository, context["account_scope_id"])
    with repository.engine.begin() as connection:
        connection.execute(text(
            "UPDATE private_sync_receipt SET account_scope_id='foreign-sensitive-account'"
        ))
    response = client.get("/api/ops/sync/private/status", params={
        "expected_account_scope_id": context["account_scope_id"],
    })
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "private_sync_status_unavailable"
    assert "foreign-sensitive-account" not in response.text


@pytest.mark.parametrize("explicit_keyword", [True, False])
def test_manual_sync_passes_captured_account_scope_to_supported_callable(
    configured, monkeypatch, explicit_keyword,
):
    config, client = configured
    context, _ = scope_for(config)
    captured = {}
    result = SimpleNamespace(as_response=lambda: {"account_upserted": 0})

    async def explicit(repository, adapter, *, account_scope_id, **kwargs):
        captured["scope"] = account_scope_id
        return result

    async def variadic(repository, adapter, **kwargs):
        captured["scope"] = kwargs["account_scope_id"]
        return result

    monkeypatch.setattr(ops, "sync_private_state", explicit if explicit_keyword else variadic)
    monkeypatch.setattr(ops, "_create_adapter", lambda settings: SimpleNamespace(close=close))
    response = client.post("/api/ops/sync/private", json={}, params={
        "expected_account_scope_id": context["account_scope_id"],
    })
    assert response.status_code == 200
    assert captured["scope"] == context["account_scope_id"]
    assert response.json() == {"status": "ok", "context": context, "account_upserted": 0}


async def close():
    pass


def test_manual_sync_remains_compatible_with_legacy_callable_signature(configured, monkeypatch):
    config, client = configured
    context, _ = scope_for(config)
    called = []

    async def legacy(repository, adapter, *, symbols, since, timestamp_ms, risk_event_notifier):
        called.append(symbols)
        return SimpleNamespace(as_response=lambda: {"account_upserted": 0})

    monkeypatch.setattr(ops, "sync_private_state", legacy)
    monkeypatch.setattr(ops, "_create_adapter", lambda settings: SimpleNamespace(close=close))
    response = client.post("/api/ops/sync/private", json={}, params={
        "expected_account_scope_id": context["account_scope_id"],
    })
    assert response.status_code == 200
    assert called == [None]


@pytest.mark.parametrize(
    ("error", "status_code", "code", "message"),
    [
        (
            ValueError("Private sync attempt superseded by a newer committed generation"),
            409, "private_sync_superseded",
            "A newer private synchronization has already committed",
        ),
        (
            ValueError("sensitive-audit-payload"), 503, "private_sync_unavailable",
            "Private synchronization is unavailable",
        ),
        (
            SQLAlchemyError("sensitive-database-details"), 503, "private_sync_unavailable",
            "Private synchronization is unavailable",
        ),
    ],
)
def test_manual_sync_maps_audit_and_persistence_errors_to_bounded_responses(
    configured, monkeypatch, error, status_code, code, message,
):
    config, client = configured
    context, _ = scope_for(config)
    calls, closed = [], []

    async def failing_sync(repository, adapter, **kwargs):
        calls.append(kwargs["account_scope_id"])
        raise error

    async def close_adapter():
        closed.append(True)

    monkeypatch.setattr(ops, "sync_private_state", failing_sync)
    monkeypatch.setattr(
        ops, "_create_adapter", lambda settings: SimpleNamespace(close=close_adapter),
    )
    response = client.post("/api/ops/sync/private", json={}, params={
        "expected_account_scope_id": context["account_scope_id"],
    })
    assert response.status_code == status_code
    assert response.json() == {"detail": {"code": code, "message": message}}
    assert "sensitive" not in response.text
    assert calls == [context["account_scope_id"]]
    assert closed == [True]


@pytest.mark.parametrize("since", [-1, True, False, 2**63, 1.0, "1"])
def test_manual_sync_rejects_invalid_since_before_provider(configured, monkeypatch, since):
    config, client = configured
    context, _ = scope_for(config)

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid request must not construct a provider or run sync")

    monkeypatch.setattr(ops, "_create_adapter", forbidden)
    monkeypatch.setattr(ops, "sync_private_state", forbidden)
    response = client.post("/api/ops/sync/private", json={"since": since}, params={
        "expected_account_scope_id": context["account_scope_id"],
    })
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "since"]


@pytest.mark.parametrize("since", [None, 0, 2**63 - 1])
def test_manual_sync_preserves_valid_since_boundaries(configured, monkeypatch, since):
    config, client = configured
    context, _ = scope_for(config)
    captured = []

    async def sync(repository, adapter, **kwargs):
        captured.append(kwargs["since"])
        return SimpleNamespace(as_response=lambda: {"account_upserted": 0})

    monkeypatch.setattr(ops, "sync_private_state", sync)
    monkeypatch.setattr(ops, "_create_adapter", lambda settings: SimpleNamespace(close=close))
    response = client.post("/api/ops/sync/private", json={"since": since}, params={
        "expected_account_scope_id": context["account_scope_id"],
    })
    assert response.status_code == 200
    assert captured == [since]
