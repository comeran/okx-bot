"""Stored fee scenarios remain diagnostic even with complete rate evidence."""

import pytest

from src.data.repository import Repository
from src.web.api import ops
from tests.integration.test_exchange_context_api import configured as configured
from tests.integration.test_exchange_context_api import scoped_repository
from tests.integration.test_spot_preflight_api import checks, request
from tests.unit.test_account_balance_store import database_state
from tests.unit.test_okx_instrument_facts import instrument_payload, instrument_row
from tests.unit.test_spot_preflight_evidence import balance, config, instruments, tamper
from tests.unit.test_spot_trade_fee_store import commit as commit_fee
from tests.unit.test_spot_trade_fee_store import payload as fee_payload

ORDER = {"symbol": "BTC-USDT", "side": "buy", "amount": "2.000", "price": "100.10"}


def rates(maker="+0.0001", taker="-0.00100"):
    return fee_payload(maker="-0.9", taker="-0.9", feeGroup=[
        {"groupId": "private-other-group", "maker": "-0.8", "taker": "-0.8"},
        {"groupId": "001", "maker": maker, "taker": taker},
    ])


def seed(settings, fee_type="0"):
    repo = scoped_repository(settings)
    config(repo, feeType=fee_type, mainUid="private-main-uid")
    balance(repo, details=[{"ccy": "BTC", "availBal": "10"},
                          {"ccy": "USDT", "availBal": "10000"}])
    instruments(repo, raw=instrument_payload(instrument_row(
        groupId="001", tickSz="0.1", lotSz="0.001", minSz="0.001",
    )))
    commit_fee(repo, rates())
    return repo


@pytest.mark.parametrize("fee_type,side,currency,maker,taker,worst", [
    ("0", "buy", "BTC", "0.0002", "-0.002", "0.002"),
    ("0", "sell", "USDT", "0.02002", "-0.2002", "0.2002"),
    ("1", "buy", "USDT", "0.02002", "-0.2002", "0.2002"),
    ("1", "sell", "USDT", "0.02002", "-0.2002", "0.2002"),
])
def test_group_rates_currency_and_exact_unrounded_scenarios_do_not_grant_coverage(
    configured, monkeypatch, fee_type, side, currency, maker, taker, worst,
):
    settings, client = configured
    repo = seed(settings, fee_type)
    master = Repository()
    before = database_state(repo), database_state(master)
    monkeypatch.setattr(ops, "current_timestamp_ms", lambda: 100)
    response = request(client, {**ORDER, "side": side})
    assert response.status_code == 200
    result = response.json()
    fee = result["fee_diagnostic"]
    assert fee["group_id"] == "001" and fee["currency"] == currency
    assert (fee["maker_rate"], fee["taker_rate"]) == ("+0.0001", "-0.00100")
    assert (fee["maker_signed_amount"], fee["taker_signed_amount"]) == (maker, taker)
    assert fee["worst_commission_unrounded"] == worst
    assert fee["amounts_unrounded"] is True and fee["coverage"] == "unknown"
    assert fee["sign_convention"] == "positive_rebate_negative_commission"
    assert result["evidence"]["trade_fee"]["received_at"] == 100
    assert len(result["evidence"]["trade_fee"]["payload_hash"]) == 64
    statuses = checks(result)
    for code in ("trade_fee_freshness", "fee_identity", "fee_rule_type", "fee_group_mapping",
                 "fee_rate_values", "fee_currency", "fee_scenarios"):
        assert statuses[code] == "pass"
    for code in ("fee_rounding", "fee_reserve", "available_funds", "liability_free",
                 "reconciliation", "sync_completeness"):
        assert statuses[code] == "unknown"
    assert result["can_submit"] is result["can_activate"] is False
    assert "private-main-uid" not in response.text and "private-other-group" not in response.text
    assert "raw_json" not in response.text
    assert (database_state(repo), database_state(master)) == before


def test_another_symbol_does_not_replace_matching_fee_and_replay_never_refreshes(configured,
                                                                               monkeypatch):
    settings, client = configured
    repo = seed(settings)
    commit_fee(repo, rates(taker="-0.003"), at=200)
    commit_fee(repo, rates(taker="-0.8"), at=300, inst_id="ETH-USDT")
    commit_fee(repo, rates(), at=400)
    monkeypatch.setattr(ops, "current_timestamp_ms", lambda: 300)
    result = request(client, ORDER).json()
    assert result["fee_diagnostic"]["taker_signed_amount"] == "-0.006"
    assert result["evidence"]["trade_fee"]["received_at"] == 200


def test_future_newest_fee_does_not_fall_back_to_older_fresh_schedule(configured, monkeypatch):
    settings, client = configured
    repo = seed(settings)
    commit_fee(repo, rates(taker="-0.003"), at=1000)
    monkeypatch.setattr(ops, "current_timestamp_ms", lambda: 200)
    result = request(client, ORDER).json()
    assert result["evidence"]["trade_fee"]["received_at"] == 1000
    assert checks(result)["trade_fee_freshness"] == "unknown"
    assert result["fee_diagnostic"]["taker_signed_amount"] is None


def test_mismatching_group_never_uses_root_fallback(configured, monkeypatch):
    settings, client = configured
    repo = seed(settings)
    commit_fee(repo, fee_payload(maker="0", taker="0", feeGroup=[
        {"groupId": "different", "maker": "0", "taker": "0"},
    ]), at=200)
    monkeypatch.setattr(ops, "current_timestamp_ms", lambda: 200)
    result = request(client, ORDER).json()
    assert checks(result)["fee_group_mapping"] == "fail"
    assert result["fee_diagnostic"]["maker_signed_amount"] is None
    assert result["can_submit"] is False


@pytest.mark.parametrize("field,value", [("fee_type", "1"), ("fee_currency", "USDT"),
                                         ("rounding_quantum", "0.00000001"),
                                         ("maker_rate", "0"), ("trade_fee", {})])
def test_fee_or_rounding_evidence_cannot_be_supplied_by_client(configured, field, value):
    _, client = configured
    assert request(client, {**ORDER, field: value}).status_code == 422


def test_fee_journal_corruption_is_sanitized_and_does_not_write(configured):
    settings, client = configured
    repo = seed(settings)
    tamper(repo, "private_spot_trade_fee", "raw_json='private-sensitive-fee'")
    before = database_state(repo)
    response = request(client, ORDER)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "spot_preflight_evidence_unavailable"
    assert "private-sensitive-fee" not in response.text
    assert database_state(repo) == before


def test_all_rebate_or_zero_rates_still_do_not_prove_rounding_or_coverage(configured, monkeypatch):
    settings, client = configured
    repo = seed(settings)
    commit_fee(repo, rates(maker="0", taker="0.0001"), at=200)
    monkeypatch.setattr(ops, "current_timestamp_ms", lambda: 200)
    result = request(client, ORDER).json()
    assert result["fee_diagnostic"]["worst_commission_unrounded"] == "0"
    assert checks(result)["fee_reserve"] == checks(result)["fee_rounding"] == "unknown"
    assert result["can_submit"] is result["can_activate"] is False
