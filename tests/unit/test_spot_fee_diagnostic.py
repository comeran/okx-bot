"""Group-bound fee scenarios never certify rounded cost or reserve coverage."""

from dataclasses import FrozenInstanceError, replace
from decimal import ROUND_DOWN, Inexact, localcontext

import pytest

from src.core.private_facts import MISSING
from src.exchange.okx_account_facts import AccountConfigFact
from src.exchange.okx_instrument_facts import parse_okx_spot_instruments
from src.exchange.okx_trade_fee_facts import SpotFeeGroupFact, SpotTradeFeeFact
from tests.unit.test_account_balance_store import database_state
from tests.unit.test_account_balance_store import stores as stores
from tests.unit.test_okx_instrument_facts import instrument_payload, instrument_row
from tests.unit.test_spot_instrument_store import commit


def instrument(**fields):
    values = {"groupId": "001", "tickSz": "0.1", "lotSz": "0.001", "minSz": "0.001",
              **fields}
    return parse_okx_spot_instruments(instrument_payload(instrument_row(**values)))[0]


def fee(**fields):
    values = {
        "requested_inst_id": "BTC-USDT", "rule_type": "normal",
        "maker": "-0.9", "taker": "-0.9",
        "fee_group": (SpotFeeGroupFact("001", maker="+0.0001", taker="-0.00100"),),
        **fields,
    }
    return SpotTradeFeeFact(**values)


def evaluate(**overrides):
    from src.ops.spot_fee_diagnostic import evaluate_spot_fee_diagnostic

    arguments = {
        "config": AccountConfigFact("00123", fee_type="0"), "instrument": instrument(),
        "fee": fee(), "symbol": "BTC-USDT", "side": "buy", "amount": "2.000",
        "price": "100.10", "now_ms": 100, "config_received_at": 100,
        "instrument_received_at": 100, "fee_received_at": 100,
        "expected_uid": "00123", "fee_uid": "00123",
        **overrides,
    }
    return evaluate_spot_fee_diagnostic(**arguments)


def statuses(result):
    return {check.code: check.status for check in result.checks}


@pytest.mark.parametrize("group_id", [MISSING, None, "", "001", "future-group"])
def test_group_id_is_a_raw_journal_projection_without_schema_or_row_rewrite(stores, group_id):
    _, (repo, _) = stores
    row = instrument_row(groupId=group_id)
    if group_id is MISSING:
        del row["groupId"]
    raw = instrument_payload(row)
    record, = commit(repo, raw)
    before = database_state(repo)
    fact, = parse_okx_spot_instruments(raw)
    assert fact.group_id == group_id
    evidence = repo.get_spot_preflight_evidence(
        "BTC-USDT", expected_account_scope_id=record.account_scope_id,
    )
    assert evidence.instrument.fact.group_id == group_id
    replay, = commit(repo, raw, received_at=999)
    assert replay.received_at == 100 and replay.raw_json == record.raw_json
    assert not hasattr(record, "group_id")
    assert database_state(repo) == before


@pytest.mark.parametrize("bad", [1, True, [], {}, " padded ", "x" * 257])
def test_invalid_group_id_is_rejected_by_parser_and_direct_dto_validation(bad):
    with pytest.raises(ValueError):
        instrument(groupId=bad)
    with pytest.raises(ValueError):
        replace(instrument(), group_id=bad)


@pytest.mark.parametrize("fee_type,side,currency,maker,taker,worst", [
    ("0", "buy", "BTC", "0.0002", "-0.002", "0.002"),
    ("0", "sell", "USDT", "0.02002", "-0.2002", "0.2002"),
    ("1", "buy", "USDT", "0.02002", "-0.2002", "0.2002"),
    ("1", "sell", "USDT", "0.02002", "-0.2002", "0.2002"),
])
def test_exact_group_selected_rates_and_currency_follow_fee_type_and_side(
    fee_type, side, currency, maker, taker, worst,
):
    result = evaluate(config=AccountConfigFact("00123", fee_type=fee_type), side=side)
    assert result.group_id == "001" and result.currency == currency
    assert (result.maker_rate, result.taker_rate) == ("+0.0001", "-0.00100")
    assert (result.maker_signed_amount, result.taker_signed_amount) == (maker, taker)
    assert result.worst_commission_unrounded == worst
    assert all(status == "pass" for code, status in statuses(result).items()
               if code != "fee_rounding")
    assert statuses(result)["fee_rounding"] == "unknown"
    with pytest.raises(FrozenInstanceError):
        result.currency = "changed"


def test_rebates_are_not_counted_as_available_funds_or_negative_commission():
    result = evaluate(fee=fee(fee_group=(SpotFeeGroupFact("001", maker="0.1", taker="0.2"),)))
    assert (result.maker_signed_amount, result.taker_signed_amount) == ("0.2", "0.4")
    assert result.worst_commission_unrounded == "0"


def test_only_matching_group_is_used_and_root_hints_never_fill_a_missing_group_rate():
    groups = (SpotFeeGroupFact("other", maker="-0.8", taker="-0.9"),
              SpotFeeGroupFact("001", maker="0.0001", taker="-0.001"))
    result = evaluate(fee=fee(fee_group=groups))
    assert result.taker_signed_amount == "-0.002"
    missing = evaluate(fee=fee(fee_group=(SpotFeeGroupFact("001", maker="0.0001"),)))
    assert statuses(missing)["fee_rate_values"] == "unknown"
    assert missing.taker_signed_amount is missing.worst_commission_unrounded is None


@pytest.mark.parametrize("group_id", [MISSING, None, ""])
def test_absent_instrument_group_never_uses_root_rate_fallback(group_id):
    result = evaluate(instrument=instrument(groupId=group_id) if group_id is not MISSING else (
        replace(instrument(), group_id=MISSING)
    ))
    assert statuses(result)["fee_group_mapping"] == "unknown"
    assert result.maker_signed_amount is None


@pytest.mark.parametrize("groups,want", [(MISSING, "unknown"), (None, "unknown"),
                                        ((), "unknown"),
                                        ((SpotFeeGroupFact("1", maker="0", taker="0"),), "fail")])
def test_no_matching_group_does_not_normalize_ids_or_guess_categories(groups, want):
    result = evaluate(fee=fee(fee_group=groups))
    assert statuses(result)["fee_group_mapping"] == want
    assert result.maker_signed_amount is None


@pytest.mark.parametrize("rate", [MISSING, None, "", "NaN", "Infinity", "1e-3", "1", "-1",
                                  "2", "9" * 129])
def test_uninterpretable_or_unsupported_selected_rates_have_no_amounts(rate):
    result = evaluate(fee=fee(fee_group=(SpotFeeGroupFact("001", maker=rate, taker="0"),)))
    assert statuses(result)["fee_rate_values"] == "unknown"
    assert result.maker_signed_amount is result.taker_signed_amount is None


@pytest.mark.parametrize("now,receipt,want", [(30_100, 100, "pass"), (30_101, 100, "unknown"),
                                            (99, 100, "unknown"), (True, 100, "unknown"),
                                            (100, True, "unknown")])
def test_freshness_uses_server_receipt_bound_inclusively_and_rejects_future_or_bool(
    now, receipt, want,
):
    result = evaluate(now_ms=now, fee_received_at=receipt)
    assert statuses(result)["trade_fee_freshness"] == want
    if want == "unknown":
        assert result.maker_signed_amount is None


@pytest.mark.parametrize("override,want", [
    ({"expected_uid": None}, "unknown"), ({"fee_uid": None}, "unknown"),
    ({"fee_uid": "other"}, "fail"), ({"config": AccountConfigFact("other", fee_type="0")}, "fail"),
    ({"fee": fee(requested_inst_id="ETH-USDT")}, "fail"),
    ({"config_received_at": 0, "now_ms": 30_001}, "unknown"),
    ({"instrument_received_at": 0, "now_ms": 30_001}, "unknown"),
    ({"instrument": None}, "unknown"), ({"fee": None}, "unknown"),
])
def test_account_symbol_and_context_binding_are_required_before_fee_interpretation(override, want):
    result = evaluate(**override)
    assert statuses(result)["fee_identity"] == want
    assert result.maker_signed_amount is None


@pytest.mark.parametrize("fee_type", [MISSING, None, "", "2", "unknown"])
def test_unknown_charging_policy_never_guesses_the_fee_currency(fee_type):
    result = evaluate(config=AccountConfigFact("00123", fee_type=fee_type))
    assert statuses(result)["fee_currency"] == "unknown"
    assert result.currency is result.maker_signed_amount is None


@pytest.mark.parametrize("value,want", [(MISSING, "unknown"), (None, "unknown"), ("", "unknown"),
                                      ("pre_market", "fail")])
def test_non_normal_or_missing_rule_type_is_not_used_for_scenarios(value, want):
    result = evaluate(fee=fee(rule_type=value))
    assert statuses(result)["fee_rule_type"] == want
    assert result.maker_signed_amount is None


@pytest.mark.parametrize("overrides", [{"amount": 1.0}, {"amount": "-1"}, {"price": "0"},
                                       {"price": "NaN"}, {"side": "BUY"},
                                       {"symbol": "BTC/USDT"}])
def test_invalid_order_parameters_never_emit_a_fee_amount(overrides):
    result = evaluate(**overrides)
    assert result.maker_signed_amount is result.taker_signed_amount is None


def test_decimal_context_cannot_round_or_trap_exact_scenario_arithmetic():
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_DOWN
        context.traps[Inexact] = True
        result = evaluate(amount="9007199254740993.0001", price="0.123456789",
                          config=AccountConfigFact("00123", fee_type="1"),
                          fee=fee(fee_group=(SpotFeeGroupFact("001", maker="0", taker="-0.001"),)))
    assert result.maker_signed_amount == "0"
    assert result.taker_signed_amount == "-1111999897873.5160224638226789"
    assert result.worst_commission_unrounded == "1111999897873.5160224638226789"
