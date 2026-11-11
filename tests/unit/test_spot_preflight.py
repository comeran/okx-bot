"""Conservative order diagnostics from immutable exact observations only."""

import builtins
import copy
import socket
import sqlite3
from dataclasses import FrozenInstanceError, replace
from decimal import localcontext

import pytest

from src.core.private_facts import MISSING
from src.exchange.okx_account_facts import AccountConfigFact
from src.exchange.okx_balance_facts import AccountBalanceFact, AssetBalanceFact
from src.exchange.okx_instrument_facts import SpotInstrumentFact


def evaluate(**overrides):
    # Import inside the consumer so missing implementation produces test failures.
    from src.ops.spot_preflight import evaluate_spot_order_preflight

    inputs = dict(
        config=AccountConfigFact(uid="42", acct_lv="1", auto_loan="0", enable_spot_borrow="0"),
        balance=AccountBalanceFact(details=(
            AssetBalanceFact(ccy="USDT", avail_bal="100.0000"),
            AssetBalanceFact(ccy="BTC", avail_bal="2.0000"),
        )),
        instrument=SpotInstrumentFact(
            inst_id="BTC-USDT", base_ccy="BTC", quote_ccy="USDT", tick_sz="0.01",
            lot_sz="0.001", min_sz="0.010", state="live",
        ),
        symbol="BTC-USDT", side="buy", amount="1.000", price="10.00", now_ms=1000,
        config_received_at=900, balance_received_at=900, instrument_received_at=900,
        max_age_ms=100, expected_uid="42", balance_uid="42", fee_reserve="0.0100",
        liability_free=True, reconciliation_complete=True, sync_complete=True,
    )
    inputs.update(overrides)
    return evaluate_spot_order_preflight(**inputs)


def check(result, code):
    matches = [item for item in result.checks if item.code == code]
    assert len(matches) == 1
    assert matches[0].reason
    assert matches[0].status in {"pass", "fail", "unknown"}
    return matches[0].status


def test_complete_exact_evidence_passes_without_changing_source_strings():
    result = evaluate()
    assert all(item.status == "pass" for item in result.checks)
    assert result.can_submit and result.can_activate
    assert len({item.code for item in result.checks}) == len(result.checks)
    with pytest.raises(FrozenInstanceError):
        result.can_submit = False
    with pytest.raises(FrozenInstanceError):
        result.checks[0].status = "fail"


def test_empty_account_is_insufficient_even_with_usd_equity():
    result = evaluate(balance=AccountBalanceFact(details=(), total_eq="1000000"))
    assert check(result, "available_funds") == "unknown"
    assert not result.can_submit and not result.can_activate


def test_usd_cash_and_total_equity_never_substitute_for_usdt():
    result = evaluate(balance=AccountBalanceFact(
        details=(AssetBalanceFact(ccy="USD", avail_bal="1000000"),), total_eq="1000000",
    ))
    assert check(result, "available_funds") == "unknown"
    assert not result.can_submit


@pytest.mark.parametrize("available", [MISSING, None, "", "garbage", "NaN", "1e9", "-1"])
def test_missing_or_invalid_available_balance_cannot_pass(available):
    result = evaluate(balance=AccountBalanceFact(details=(
        AssetBalanceFact(ccy="USDT", avail_bal=available, cash_bal="1000000", eq="1000000"),
    )))
    assert check(result, "available_funds") in {"fail", "unknown"}
    assert not result.can_submit


@pytest.mark.parametrize("field", ["auto_loan", "enable_spot_borrow"])
def test_either_borrow_flag_enabled_blocks(field):
    config = replace(AccountConfigFact(uid="42", auto_loan="0", enable_spot_borrow="0"),
                     **{field: "1"})
    result = evaluate(config=config)
    assert check(result, "borrow_policy") == "fail"
    assert not result.can_submit


@pytest.mark.parametrize("value", [MISSING, None, "", "false", "False", "true", "2"])
def test_borrow_policy_requires_both_exact_zero_flags(value):
    result = evaluate(config=AccountConfigFact(uid="42", auto_loan="0", enable_spot_borrow=value))
    assert check(result, "borrow_policy") == "unknown"
    assert not result.can_submit


def test_account_level_alone_does_not_prove_unleveraged_safety():
    result = evaluate(config=AccountConfigFact(uid="42", acct_lv="1"), liability_free=None)
    assert check(result, "borrow_policy") == "unknown"
    assert check(result, "liability_free") == "unknown"
    assert not result.can_activate


def test_disabled_borrow_flags_are_only_policy_evidence():
    result = evaluate(liability_free=None)
    assert check(result, "borrow_policy") == "pass"
    assert check(result, "liability_free") == "unknown"
    assert not result.can_submit


@pytest.mark.parametrize("state,want", [
    ("live", "pass"), ("suspend", "fail"), ("preopen", "fail"),
    ("unexpected", "fail"), (MISSING, "unknown"), (None, "unknown"), ("", "unknown"),
])
def test_only_exact_live_instrument_passes(state, want):
    fact = SpotInstrumentFact("BTC-USDT", "BTC", "USDT", "0.01", "0.001", "0.01", state)
    result = evaluate(instrument=fact)
    assert check(result, "instrument_state") == want
    assert result.can_submit is (want == "pass")


@pytest.mark.parametrize("field,value,code", [
    ("amount", "1.0001", "amount_increment"), ("price", "10.001", "price_increment"),
    ("amount", "0.009", "minimum_size"),
])
def test_order_must_align_exactly_and_meet_minimum(field, value, code):
    result = evaluate(**{field: value})
    assert check(result, code) == "fail"
    assert not result.can_submit


def test_decimal_precision_never_rounds_an_off_grid_order_into_alignment():
    with localcontext() as context:
        context.prec = 2
        result = evaluate(amount="1.000000000000000000000000000000000000000000001")
    assert check(result, "amount_increment") == "fail"
    assert not result.can_submit


def test_exact_notional_and_fee_reserve_cannot_round_down_to_available_cash():
    with localcontext() as context:
        context.prec = 2
        result = evaluate(fee_reserve="90.000000000000000000000000000000000000000000001")
    assert check(result, "available_funds") == "fail"
    assert not result.can_submit


@pytest.mark.parametrize("side,ccy,available,want", [
    ("buy", "USDT", "10.01", "pass"), ("buy", "USDT", "10.009", "fail"),
    ("sell", "BTC", "1.01", "pass"), ("sell", "BTC", "1.009", "fail"),
    ("buy", "BTC", "1000000", "unknown"), ("sell", "USDT", "1000000", "unknown"),
])
def test_balance_requirement_uses_side_currency_with_fee_reserve(side, ccy, available, want):
    result = evaluate(side=side, balance=AccountBalanceFact(details=(
        AssetBalanceFact(ccy=ccy, avail_bal=available),
    )))
    assert check(result, "available_funds") == want
    assert result.can_submit is (want == "pass")


@pytest.mark.parametrize("timestamp_field,code", [
    ("config_received_at", "config_freshness"),
    ("balance_received_at", "balance_freshness"),
    ("instrument_received_at", "instrument_freshness"),
])
@pytest.mark.parametrize("timestamp", [899, 1001, None, True, -1, "900"])
def test_old_future_or_unknown_observation_times_block(timestamp_field, code, timestamp):
    result = evaluate(**{timestamp_field: timestamp})
    assert check(result, code) == "unknown"
    assert not result.can_submit and not result.can_activate


@pytest.mark.parametrize("field,code", [
    ("config", "identity"), ("balance", "available_funds"),
    ("instrument", "instrument_identity"),
])
def test_absent_fact_blocks_with_explainable_unknown(field, code):
    result = evaluate(**{field: None})
    assert check(result, code) == "unknown"
    assert not result.can_submit


@pytest.mark.parametrize("field,value", [
    ("expected_uid", None), ("balance_uid", None), ("expected_uid", "other"),
    ("balance_uid", "other"),
])
def test_account_identity_and_balance_uid_binding_are_required(field, value):
    result = evaluate(**{field: value})
    assert check(result, "identity") in {"unknown", "fail"}
    assert not result.can_submit


def test_instrument_must_match_requested_native_symbol():
    result = evaluate(symbol="ETH-USDT")
    assert check(result, "instrument_identity") == "fail"
    assert not result.can_submit


@pytest.mark.parametrize("field,value,code", [
    ("amount", "0", "amount"), ("amount", "-1", "amount"),
    ("price", "0", "price"), ("price", "NaN", "price"),
    ("amount", "Infinity", "amount"), ("amount", "1e4", "amount"),
    ("amount", " 1", "amount"), ("amount", 1.0, "amount"),
    ("price", True, "price"), ("amount", "9" * 129, "amount"),
    ("side", "BUY", "side"), ("side", "long", "side"),
    ("symbol", "BTC/USDT", "symbol"), ("symbol", None, "symbol"),
    ("now_ms", -1, "freshness_policy"), ("now_ms", True, "freshness_policy"),
    ("max_age_ms", None, "freshness_policy"), ("max_age_ms", -1, "freshness_policy"),
    ("max_age_ms", True, "freshness_policy"),
])
def test_invalid_requests_fail_closed_with_reason(field, value, code):
    result = evaluate(**{field: value})
    assert check(result, code) in {"fail", "unknown"}
    assert not result.can_submit and not result.can_activate


@pytest.mark.parametrize("field,code", [
    ("fee_reserve", "fee_reserve"), ("liability_free", "liability_free"),
    ("reconciliation_complete", "reconciliation"), ("sync_complete", "sync_completeness"),
])
@pytest.mark.parametrize("value", [None, False, "true", 1])
def test_missing_or_non_boolean_completeness_cannot_authorize(field, code, value):
    result = evaluate(**{field: value})
    assert check(result, code) in {"unknown", "fail"}
    assert not result.can_submit and not result.can_activate


@pytest.mark.parametrize("reserve", ["", "-1", "NaN", "1e6", 0.0])
def test_invalid_fee_reserve_is_not_zero(reserve):
    result = evaluate(fee_reserve=reserve)
    assert check(result, "fee_reserve") in {"unknown", "fail"}
    assert not result.can_submit


def test_evaluator_has_no_storage_network_or_input_mutation(monkeypatch):
    # Warm import before rejecting I/O so this verifies evaluation, not import machinery.
    evaluate()
    balance = AccountBalanceFact(details=(AssetBalanceFact(ccy="USDT", avail_bal="100.0000"),))
    before = copy.deepcopy(balance)

    def reject(*args, **kwargs):
        raise AssertionError("preflight attempted external I/O")

    monkeypatch.setattr(builtins, "open", reject)
    monkeypatch.setattr(sqlite3, "connect", reject)
    monkeypatch.setattr(socket.socket, "connect", reject)
    result = evaluate(balance=balance)
    assert result.can_submit
    assert balance == before
    assert balance.details[0].avail_bal == "100.0000"
