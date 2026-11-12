"""Stored adverse debt/cash facts are visible without opening execution."""

import pytest

from src.data.repository import Repository
from src.web.api import ops
from tests.integration.test_exchange_context_api import configured as configured
from tests.integration.test_spot_preflight_api import checks, request, seed
from tests.unit.test_account_balance_store import database_state
from tests.unit.test_spot_liability_diagnostic import FIELDS, asset
from tests.unit.test_spot_preflight_evidence import balance, tamper


@pytest.mark.parametrize("field", FIELDS)
def test_any_currency_debt_changes_existing_gate_to_fail_without_leaks_or_writes(
    configured, monkeypatch, field,
):
    settings, client = configured
    repo = seed(settings)
    balance(repo, at=200, details=[asset(), asset(ccy="PRIVATE-CCY", **{field: "1.23456789"})])
    master = Repository()
    before = database_state(repo), database_state(master)
    monkeypatch.setattr(ops, "current_timestamp_ms", lambda: 200)
    response = request(client)
    assert response.status_code == 200
    result = response.json()
    diagnostic = result["liability_diagnostic"]
    assert diagnostic == {
        "coverage": "unknown", "observed_currency_count": 2,
        "reported_liability_currency_count": 1, "negative_cash_currency_count": 0,
        "uninterpretable_liability_field_count": 0, "uninterpretable_cash_field_count": 0,
    }
    assert checks(result)["liability_free"] == checks(result)["reported_liabilities"] == "fail"
    assert "PRIVATE-CCY" not in response.text and "1.23456789" not in response.text
    assert result["can_submit"] is result["can_activate"] is False
    assert result["evidence"]["balance"]["received_at"] == 200
    assert (database_state(repo), database_state(master)) == before


@pytest.mark.parametrize("rows", [[], [asset()], [{"ccy": "BTC"}]])
def test_zero_or_missing_debt_keeps_existing_gate_and_completeness_unknown(
    configured, monkeypatch, rows,
):
    settings, client = configured
    repo = seed(settings)
    balance(repo, at=200, details=rows)
    monkeypatch.setattr(ops, "current_timestamp_ms", lambda: 200)
    result = request(client).json()
    for code in ("liability_free", "fee_reserve", "reconciliation", "sync_completeness"):
        assert checks(result)[code] == "unknown"
    assert result["liability_diagnostic"]["coverage"] == "unknown"
    assert result["can_submit"] is result["can_activate"] is False


def test_negative_cash_is_reported_separately_from_confirmed_liabilities(configured, monkeypatch):
    settings, client = configured
    repo = seed(settings)
    balance(repo, at=200, details=[asset(cashBal="-0.0001")])
    monkeypatch.setattr(ops, "current_timestamp_ms", lambda: 200)
    result = request(client).json()
    assert checks(result)["cash_balance_sign"] == "fail"
    assert checks(result)["liability_free"] == "unknown"
    assert result["liability_diagnostic"]["negative_cash_currency_count"] == 1


def test_latest_observation_is_used_and_replay_does_not_refresh_or_revert_history(
    configured, monkeypatch,
):
    settings, client = configured
    repo = seed(settings)
    balance(repo, at=200, details=[asset(liab="1")])
    balance(repo, at=300, details=[asset()])
    balance(repo, at=400, details=[asset(liab="1")])
    monkeypatch.setattr(ops, "current_timestamp_ms", lambda: 300)
    result = request(client).json()
    assert result["evidence"]["balance"]["received_at"] == 300
    assert result["liability_diagnostic"]["reported_liability_currency_count"] == 0
    assert checks(result)["liability_free"] == "unknown"


@pytest.mark.parametrize("now", [199, 30_101])
def test_future_or_stale_context_never_falls_back_or_interprets_debt(configured, monkeypatch, now):
    settings, client = configured
    repo = seed(settings)
    balance(repo, at=200, details=[asset(liab="1")])
    monkeypatch.setattr(ops, "current_timestamp_ms", lambda: now)
    result = request(client).json()
    assert result["evidence"]["balance"]["received_at"] == 200
    assert checks(result)["liability_evidence_freshness"] == "unknown"
    assert result["liability_diagnostic"]["reported_liability_currency_count"] is None
    assert checks(result)["liability_free"] == "unknown"


def test_corrupt_balance_projection_fails_closed_with_sanitized_error(configured):
    settings, client = configured
    repo = seed(settings)
    tamper(repo, "private_account_balance", "details_json='private-balance-details'")
    before = database_state(repo)
    response = request(client)
    assert response.status_code == 503
    assert "private-balance-details" not in response.text
    assert database_state(repo) == before


@pytest.mark.parametrize("field,value", [("liability_diagnostic", {}), ("liab", "0"),
                                         ("liability_free", True), ("balance_uid", "00123")])
def test_client_cannot_supply_liability_proofs(configured, field, value):
    _, client = configured
    body = {"symbol": "BTC-USDT", "side": "buy", "amount": "1", "price": "1", field: value}
    assert request(client, body).status_code == 422
