"""Adverse exact observations reject safety; zero observations never certify it."""

from dataclasses import FrozenInstanceError
from decimal import Inexact, Rounded, localcontext

import pytest

from src.core.private_facts import MISSING
from src.exchange.okx_account_facts import AccountConfigFact
from src.exchange.okx_balance_facts import parse_okx_account_balance
from tests.unit.test_okx_balance_facts import balance_payload

FIELDS = ("liab", "crossLiab", "isoLiab", "uplLiab", "interest")


def asset(**fields):
    return {"ccy": "USDT", "cashBal": "1", **dict.fromkeys(FIELDS, "0"), **fields}


def evaluate(rows=None, **overrides):
    from src.ops.spot_liability_diagnostic import evaluate_spot_liability_diagnostic

    return evaluate_spot_liability_diagnostic(**{
        "config": AccountConfigFact("00123"),
        "balance": parse_okx_account_balance(balance_payload(
            details=[asset()] if rows is None else rows,
        )),
        "now_ms": 100, "config_received_at": 100, "balance_received_at": 100,
        "expected_uid": "00123", "balance_uid": "00123", **overrides,
    })


def statuses(result):
    return {check.code: check.status for check in result.checks}


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", ["1.000", "0." + "0" * 124 + "1", "9" * 128])
def test_any_exact_positive_liability_in_any_currency_rejects_liability_freedom(field, value):
    result = evaluate([asset(), asset(ccy="PRIVATE-THIRD-CCY", **{field: value})])
    assert result.liability_free is False
    assert result.observed_currency_count == 2
    assert result.reported_liability_currency_count == 1
    assert result.negative_cash_currency_count == 0
    assert statuses(result)["reported_liabilities"] == "fail"
    assert statuses(result)["liability_evidence_identity"] == "pass"
    with pytest.raises(FrozenInstanceError):
        result.liability_free = True


def test_overlapping_liabilities_count_currency_once_and_never_offset_positive_evidence():
    result = evaluate([asset(liab="0", crossLiab="2", isoLiab="3", uplLiab="-100",
                             interest="", cashBal="9" * 128)])
    assert result.liability_free is False
    assert result.reported_liability_currency_count == 1
    assert result.uninterpretable_liability_field_count == 2
    assert statuses(result)["reported_liabilities"] == "fail"


@pytest.mark.parametrize("value", [MISSING, None, "", "NaN", "Infinity", "1e-8", "-1",
                                  "1" * 129])
def test_uninterpretable_fields_never_certify_safety_or_cancel_other_debt(value):
    row = asset(liab=value)
    if value is MISSING:
        del row["liab"]
    result = evaluate([row])
    assert result.liability_free is None
    assert result.uninterpretable_liability_field_count == 1
    assert result.reported_liability_currency_count == 0
    assert statuses(result)["reported_liabilities"] == "unknown"
    row["interest"] = "0.001"
    assert evaluate([row]).liability_free is False


@pytest.mark.parametrize("rows,uncertain", [([], 0), ([{"ccy": "BTC"}], 5),
                                          ([asset()], 0),
                                          ([asset(**dict.fromkeys(FIELDS, "-0.000"))], 0)])
def test_zero_missing_and_empty_observations_never_prove_account_wide_absence(rows, uncertain):
    result = evaluate(rows)
    assert result.liability_free is None
    assert result.reported_liability_currency_count == 0
    assert result.uninterpretable_liability_field_count == uncertain
    assert statuses(result)["reported_liabilities"] == "unknown"


@pytest.mark.parametrize("cash,want,negative,unknown", [
    ("-0.0000000000000000001", "fail", 1, 0), ("-1", "fail", 1, 0),
    ("0", "pass", 0, 0), ("-0", "pass", 0, 0), ("9007199254740993", "pass", 0, 0),
    (None, "unknown", 0, 1), ("", "unknown", 0, 1), ("NaN", "unknown", 0, 1),
])
def test_negative_cash_is_an_independent_shortfall_and_not_a_loan_assertion(
    cash, want, negative, unknown,
):
    result = evaluate([asset(cashBal=cash)])
    assert statuses(result)["cash_balance_sign"] == want
    assert result.negative_cash_currency_count == negative
    assert result.uninterpretable_cash_field_count == unknown
    assert result.liability_free is None


def test_adverse_cash_and_debt_take_precedence_over_missing_fields_in_other_rows():
    result = evaluate([{"ccy": "BTC"}, asset(liab="1", cashBal="-1")])
    assert result.liability_free is False
    assert statuses(result)["cash_balance_sign"] == "fail"
    assert result.uninterpretable_liability_field_count == 5
    assert result.uninterpretable_cash_field_count == 1


@pytest.mark.parametrize("overrides,freshness,identity", [
    ({"now_ms": 30_100}, "pass", "pass"),
    ({"now_ms": 30_101}, "unknown", "unknown"),
    ({"now_ms": 99}, "unknown", "unknown"),
    ({"now_ms": True}, "unknown", "unknown"),
    ({"balance_received_at": True}, "unknown", "unknown"),
    ({"config_received_at": 101}, "unknown", "unknown"),
    ({"max_age_ms": None}, "unknown", "unknown"),
    ({"max_age_ms": -1}, "unknown", "unknown"),
    ({"config": None}, "unknown", "unknown"),
    ({"balance": None}, "unknown", "unknown"),
    ({"expected_uid": None}, "pass", "unknown"),
    ({"balance_uid": None}, "pass", "unknown"),
    ({"expected_uid": " padded "}, "pass", "unknown"),
    ({"balance_uid": "other"}, "pass", "fail"),
    ({"config": AccountConfigFact("other")}, "pass", "fail"),
])
def test_only_fresh_identity_bound_context_can_interpret_liability_evidence(
    overrides, freshness, identity,
):
    result = evaluate([asset(liab="1", cashBal="-1")], **overrides)
    assert statuses(result)["liability_evidence_freshness"] == freshness
    assert statuses(result)["liability_evidence_identity"] == identity
    if identity != "pass":
        assert result.liability_free is None
        assert result.observed_currency_count is None
        assert result.reported_liability_currency_count is None
        assert statuses(result)["reported_liabilities"] == "unknown"
        assert statuses(result)["cash_balance_sign"] == "unknown"
    else:
        assert result.liability_free is False


def test_ambient_decimal_precision_rounding_traps_cannot_hide_tiny_or_large_debt():
    with localcontext() as context:
        context.prec = 1
        context.traps[Inexact] = context.traps[Rounded] = True
        context.Emin = -1
        context.Emax = 1
        result = evaluate([asset(liab="0." + "0" * 124 + "1", interest="9" * 128)])
    assert result.liability_free is False
    assert result.reported_liability_currency_count == 1
