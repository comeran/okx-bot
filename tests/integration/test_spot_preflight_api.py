"""HTTP diagnostics read server-owned exact facts and never grant execution."""

import pytest

from src.data.repository import Repository
from src.web.api import ops
from tests.integration.test_exchange_context_api import (
    configured as configured,
)
from tests.integration.test_exchange_context_api import current_scope, scoped_repository
from tests.unit.test_account_balance_store import database_state
from tests.unit.test_okx_instrument_facts import instrument_payload, instrument_row
from tests.unit.test_spot_preflight_evidence import balance, config, instruments, tamper

ORDER = {"symbol": "BTC-USDT", "side": "buy", "amount": "0.01000", "price": "100.10"}
ROUTE = "/api/ops/spot/preflight"


def seed(settings, *, at=100):
    repo = scoped_repository(settings)
    config(repo, at=at, mainUid="private-main-uid")
    balance(repo, at=at, details=[{"ccy": "USDT", "availBal": "100000.00000000000001"}])
    instruments(repo, at=at)
    return repo


def request(client, body=None, **kwargs):
    return client.post(ROUTE, json=ORDER if body is None else body, params={
        "expected_account_scope_id": current_scope(client), **kwargs,
    })


def checks(payload):
    return {check["code"]: check["status"] for check in payload["checks"]}


def test_saved_fact_diagnostic_has_provenance_unknown_blockers_and_no_side_effects(
    configured, monkeypatch,
):
    settings, client = configured
    repo = seed(settings)
    master = Repository()
    before = database_state(repo), database_state(master)
    monkeypatch.setattr(ops, "current_timestamp_ms", lambda: 100)

    def forbidden(*args, **kwargs):
        raise AssertionError("Diagnostic must never construct an exchange adapter")

    monkeypatch.setattr(ops, "_create_adapter", forbidden)
    response = request(client)
    assert response.status_code == 200
    payload = response.json()
    assert payload["diagnostic_only"] is True and payload["evaluated_at"] == 100
    assert payload["context"]["account_scope_id"] == current_scope(client)
    assert payload["context"]["execution_ready"] is False
    assert payload["can_submit"] is payload["can_activate"] is False
    statuses = checks(payload)
    for code in ("identity", "borrow_policy", "instrument_state", "amount_increment",
                 "price_increment", "minimum_size"):
        assert statuses[code] == "pass"
    for code in ("fee_reserve", "available_funds", "liability_free", "reconciliation",
                 "sync_completeness"):
        assert statuses[code] == "unknown"
    evidence = payload["evidence"]
    assert evidence["uid"] == "00123"
    assert evidence["account_scope_id"] == current_scope(client)
    for name in ("config", "balance", "instrument"):
        assert len(evidence[name]["payload_hash"]) == 64
        assert evidence[name]["received_at"] == 100 and evidence[name]["fact_present"] is True
    assert "raw_json" not in response.text and "private-main-uid" not in response.text
    assert "100000.00000000000001" not in response.text
    assert (database_state(repo), database_state(master)) == before


def test_empty_scoped_store_returns_unknown_without_fetching_or_binding_uid(configured):
    settings, client = configured
    repo = scoped_repository(settings)
    before = database_state(repo)
    response = request(client)
    assert response.status_code == 200
    payload = response.json()
    assert payload["evidence"] == {"account_scope_id": current_scope(client), "uid": None,
                                    "config": None, "balance": None, "instrument": None}
    assert checks(payload)["identity"] == "unknown"
    assert payload["can_submit"] is payload["can_activate"] is False
    assert database_state(repo) == before


@pytest.mark.parametrize("expected", [None, "wrong"])
def test_requires_current_account_scope_handshake(configured, expected):
    _, client = configured
    params = {} if expected is None else {"expected_account_scope_id": expected}
    response = client.post(ROUTE, json=ORDER, params=params)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "account_scope_changed"


def test_account_or_environment_switch_cannot_reuse_saved_evidence(configured):
    settings, client = configured
    seed(settings)
    original_scope = current_scope(client)
    settings.exchange.api_key = "test-account-b"
    response = request(client, expected_account_scope_id=original_scope)
    assert response.status_code == 409
    assert request(client).json()["evidence"]["config"] is None
    settings.exchange.api_key = "test-account-a"
    settings.exchange.demo = not settings.exchange.demo
    assert request(client).json()["evidence"]["balance"] is None


@pytest.mark.parametrize("update", [
    {"amount": 1}, {"price": 100.1}, {"amount": True}, {"symbol": "BTC/USDT"},
    {"side": "BUY"}, {"price": "1" * 129}, {"symbol": "B" * 257},
    {"fee_reserve": "0"}, {"liability_free": True}, {"sync_complete": True},
    {"reconciliation_complete": True}, {"now_ms": 1}, {"max_age_ms": 10**9},
    {"expected_uid": "00123"}, {"config_received_at": 100},
])
def test_rejects_coercion_and_client_evidence_overrides(configured, update):
    _, client = configured
    assert request(client, {**ORDER, **update}).status_code == 422


@pytest.mark.parametrize("now,want", [(30_100, "pass"), (30_101, "unknown"), (99, "unknown")])
def test_server_clock_enforces_inclusive_receipt_age_and_rejects_future_receipts(
    configured, monkeypatch, now, want,
):
    settings, client = configured
    seed(settings)
    monkeypatch.setattr(ops, "current_timestamp_ms", lambda: now)
    payload = request(client).json()
    statuses = checks(payload)
    for name in ("config", "balance", "instrument"):
        assert statuses[f"{name}_freshness"] == want
    assert payload["can_submit"] is False


def test_newer_unsafe_config_and_missing_rules_are_not_replaced_by_safe_history(
    configured, monkeypatch,
):
    settings, client = configured
    repo = seed(settings)
    config(repo, at=200, autoLoan="1")
    instruments(repo, at=200, raw={"code": "0", "data": []})
    monkeypatch.setattr(ops, "current_timestamp_ms", lambda: 200)
    payload = request(client).json()
    assert checks(payload)["borrow_policy"] == "fail"
    assert checks(payload)["instrument_state"] == "unknown"
    assert payload["evidence"]["instrument"]["fact_present"] is False
    assert payload["evidence"]["instrument"]["received_at"] == 200


def test_matching_exact_order_failure_is_visible_even_while_execution_stays_closed(
    configured, monkeypatch,
):
    settings, client = configured
    seed(settings)
    monkeypatch.setattr(ops, "current_timestamp_ms", lambda: 100)
    payload = request(client, {**ORDER, "price": "100.11"}).json()
    assert checks(payload)["price_increment"] == "fail"
    assert payload["can_submit"] is False


def test_corrupt_stored_evidence_returns_sanitized_unavailable_error(configured):
    settings, client = configured
    repo = seed(settings)
    tamper(repo, "private_account_config", "raw_json='private-sensitive-raw'")
    before = database_state(repo)
    response = request(client)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "spot_preflight_evidence_unavailable"
    assert "private-sensitive-raw" not in response.text
    assert database_state(repo) == before


def test_suspended_instrument_is_reported_as_failed(configured, monkeypatch):
    settings, client = configured
    repo = seed(settings)
    instruments(repo, at=200, raw=instrument_payload(instrument_row(state="suspend")))
    monkeypatch.setattr(ops, "current_timestamp_ms", lambda: 200)
    assert checks(request(client).json())["instrument_state"] == "fail"
