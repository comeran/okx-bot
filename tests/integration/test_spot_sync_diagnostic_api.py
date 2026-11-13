"""Synchronization audit metadata cannot certify exact SPOT execution safety."""

import json

import pytest

from src.data.repository import Repository
from src.web.api import ops
from tests.integration.test_exchange_context_api import configured as configured
from tests.integration.test_exchange_context_api import current_scope, scoped_repository
from tests.integration.test_spot_preflight_api import ORDER, checks, request, seed
from tests.unit.test_account_balance_store import database_state
from tests.unit.test_spot_preflight_evidence import tamper


def receipt_parts(*, at=1090, trades_state="available", completeness="unknown"):
    return {
        name: {
            "state": trades_state if name == "trades" else "available",
            "received_at": at,
            "provider_timestamp": at,
            "coverage": {
                "kind": kind, "completeness": completeness,
                "symbols": None if name == "account" else ["BTC-USDT"],
            },
        }
        for name, kind in (("account", "account_snapshot"), ("orders", "open_orders"),
                           ("trades", "recent_trades"))
    }


def start(repo, scope_id, *, at=1000, symbols=None):
    return repo.start_private_sync_receipt(
        account_scope_id=scope_id, symbols=["BTC-USDT"] if symbols is None else symbols,
        since=10, limit=100, started_at=at,
    )


def commit(repo, attempt, *, state="committed", parts=None, **values):
    return repo.finalize_private_sync_receipt(attempt.attempt_id, **{
        "state": state, "completed_at": 1100, "received_at": 1090,
        "parts": receipt_parts() if parts is None else parts,
        "cursor": {"durable": False, "complete": False},
        "result": {"account_upserted": 1}, **values,
    })


def assert_execution_closed(payload):
    assert payload["sync_diagnostic"]["coverage"] == "unknown"
    assert checks(payload)["sync_completeness"] == "unknown"
    assert checks(payload)["reconciliation"] == "unknown"
    assert payload["can_submit"] is payload["can_activate"] is False


def test_empty_receipt_store_reports_missing_audit_without_fetching_or_writing(
    configured, monkeypatch,
):
    settings, client = configured
    repo = scoped_repository(settings)
    master = Repository()
    before = database_state(repo), database_state(master)
    monkeypatch.setattr(ops, "current_timestamp_ms", lambda: 1200)

    def forbidden(*args, **kwargs):
        raise AssertionError("Read-only preflight must never contact an exchange")

    monkeypatch.setattr(ops, "_create_adapter", forbidden)
    monkeypatch.setattr(ops, "sync_private_state", forbidden)
    payload = request(client).json()
    diagnostic = payload["sync_diagnostic"]
    assert diagnostic["latest_attempt"] is diagnostic["last_committed"] is None
    assert_execution_closed(payload)
    assert (database_state(repo), database_state(master)) == before


@pytest.mark.parametrize("state", ["failed", "abandoned", "in_progress"])
def test_new_attempt_never_replaces_or_refreshes_last_committed_receipt(
    configured, monkeypatch, state,
):
    settings, client = configured
    repo = seed(settings)
    scope_id = current_scope(client)
    committed = commit(repo, start(repo, scope_id))
    latest = start(repo, scope_id, at=1200)
    if state != "in_progress":
        repo.fail_private_sync_receipt(
            latest.attempt_id, completed_at=1250, state=state,
            error_code="provider_unavailable",
        )
    before = database_state(repo)
    monkeypatch.setattr(ops, "current_timestamp_ms", lambda: 1300)
    first = request(client)
    second = request(client)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    payload = first.json()
    diagnostic = payload["sync_diagnostic"]
    assert diagnostic["latest_attempt"]["state"] == state
    assert diagnostic["latest_attempt"]["attempt_id"] == latest.attempt_id
    assert diagnostic["last_committed"]["attempt_id"] == committed.attempt_id
    assert diagnostic["last_committed"]["received_at"] == 1090
    assert_execution_closed(payload)
    assert database_state(repo) == before


@pytest.mark.parametrize("state,trades_state", [("committed", "available"),
                                                ("partial", "unavailable")])
def test_even_fresh_commit_and_claimed_complete_parts_never_supply_completeness(
    configured, monkeypatch, state, trades_state,
):
    settings, client = configured
    repo = seed(settings)
    attempt = start(repo, current_scope(client))
    commit(repo, attempt, state=state, parts=receipt_parts(
        trades_state=trades_state, completeness="complete",
    ), cursor={"durable": True, "complete": True})
    monkeypatch.setattr(ops, "current_timestamp_ms", lambda: 1200)
    response = request(client)
    assert response.status_code == 200
    payload = response.json()
    assert payload["sync_diagnostic"]["latest_attempt"]["state"] == state
    assert payload["sync_diagnostic"]["last_committed"]["state"] == state
    assert_execution_closed(payload)


def test_receipt_projection_omits_private_provider_metadata_and_extra_json(
    configured, monkeypatch,
):
    settings, client = configured
    repo = seed(settings)
    parts = receipt_parts()
    parts["account"]["private_payload"] = "secret-provider-account-payload"
    parts["orders"]["provider_error"] = "secret-provider-raw-error"
    commit(repo, start(repo, current_scope(client)), parts=parts,
           cursor={"token": "secret-private-pagination-token"},
           result={"divergence_sample": ["secret-private-order-payload"]})
    before = database_state(repo)
    monkeypatch.setattr(ops, "current_timestamp_ms", lambda: 1200)
    response = request(client)
    assert response.status_code == 200
    assert "sync_diagnostic" in response.json()
    for private in ("secret-provider-account-payload", "secret-provider-raw-error",
                    "secret-private-pagination-token", "secret-private-order-payload"):
        assert private not in response.text
    assert database_state(repo) == before


@pytest.mark.parametrize("field,value", [
    ("sync_diagnostic", {}), ("latest_attempt", {"state": "committed"}),
    ("last_committed", {"received_at": 1200}), ("sync_receipt", {}),
    ("sync_part_coverage", True), ("sync_complete", True),
])
def test_client_cannot_supply_or_override_synchronization_receipts(configured, field, value):
    _, client = configured
    response = request(client, {**ORDER, field: value})
    assert response.status_code == 422


def test_account_switch_cannot_reuse_another_accounts_synchronization_receipts(
    configured, monkeypatch,
):
    settings, client = configured
    repo = seed(settings)
    commit(repo, start(repo, current_scope(client)))
    settings.exchange.api_key = "other-receipt-account"
    monkeypatch.setattr(ops, "current_timestamp_ms", lambda: 1200)
    payload = request(client).json()
    diagnostic = payload["sync_diagnostic"]
    assert diagnostic["latest_attempt"] is diagnostic["last_committed"] is None
    assert_execution_closed(payload)


def test_foreign_receipt_scope_returns_bounded_unavailable_error_without_writes(configured):
    settings, client = configured
    repo = seed(settings)
    start(repo, current_scope(client))
    tamper(repo, "private_sync_receipt", "account_scope_id='private-foreign-receipt-scope'")
    before = database_state(repo)
    response = request(client)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "spot_preflight_evidence_unavailable"
    assert "private-foreign-receipt-scope" not in response.text
    assert database_state(repo) == before


@pytest.mark.parametrize("target", ["receipt", "orders", "trades"])
def test_non_utf8_receipt_symbols_return_bounded_unavailable_without_writes(configured, target):
    settings, client = configured
    repo = seed(settings)
    commit(repo, start(repo, current_scope(client)))
    secret = "private-sensitive-symbol-" + chr(0xD800)
    if target == "receipt":
        assignment = "symbols='" + json.dumps([secret]) + "'"
    else:
        parts = receipt_parts()
        parts[target]["coverage"]["symbols"] = [secret]
        assignment = "parts='" + json.dumps(parts) + "'"
    tamper(repo, "private_sync_receipt", assignment)
    before = database_state(repo)
    response = request(client)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "spot_preflight_evidence_unavailable"
    assert "private-sensitive-symbol" not in response.text
    assert database_state(repo) == before
