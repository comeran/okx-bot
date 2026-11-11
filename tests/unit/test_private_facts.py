from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest


def raw_fill(**overrides):
    row = {
        "instType": "SPOT",
        "billId": "009007199254740993",
        "instId": "BTC-USDT",
        "tradeId": "00012345678901234567890",
        "ordId": "009007199254740992",
        "clOrdId": "client-0001",
        "side": "buy",
        "fillSz": "0.00000000000000000100",
        "fillPx": "9007199254740993.000000000000000001",
        "fee": "+0.00000000000000000001",
        "feeCcy": "BTC",
        "fillTime": "001730000000001",
        "ts": "001730000000099",
    }
    return row | overrides


def parse(row):
    from src.exchange.okx_private_facts import parse_okx_spot_fill

    return parse_okx_spot_fill(row)


def test_exact_facts_preserve_strings_precision_rebates_and_both_times():
    fact = parse(raw_fill())

    assert fact.bill_id == "009007199254740993"
    assert fact.inst_id == "BTC-USDT"
    assert fact.symbol == "BTC-USDT"
    assert fact.trade_id == "00012345678901234567890"
    assert fact.ord_id == "009007199254740992"
    assert fact.cl_ord_id == "client-0001"
    assert fact.side == "buy"
    assert fact.fill_sz == "0.00000000000000000100"
    assert fact.fill_px == "9007199254740993.000000000000000001"
    assert fact.fee == "+0.00000000000000000001"
    assert fact.fee_ccy == "BTC"
    assert Decimal(fact.fee) > 0  # Positive OKX fees are rebates, not costs to abs().
    assert fact.fill_time == "001730000000001"
    assert fact.record_time == "001730000000099"
    assert fact.fill_time_ms == 1730000000001
    assert fact.record_time_ms == 1730000000099
    with pytest.raises(FrozenInstanceError):
        fact.fee = "0"


@pytest.mark.parametrize(
    "field,attribute", [("clOrdId", "cl_ord_id"), ("fee", "fee"), ("feeCcy", "fee_ccy")]
)
def test_optional_fields_distinguish_absent_null_and_empty(field, attribute):
    from src.core.private_facts import MISSING

    absent = raw_fill()
    del absent[field]
    assert getattr(parse(absent), attribute) is MISSING
    assert getattr(parse(raw_fill(**{field: None})), attribute) is None
    assert getattr(parse(raw_fill(**{field: ""})), attribute) == ""


@pytest.mark.parametrize("field", ["fillSz", "fillPx", "fee", "fillTime", "ts"])
@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        0.1,
        float("nan"),
        float("inf"),
        Decimal("1"),
        1,
        "NaN",
        "Infinity",
        "-Infinity",
        "1e999999999",
        " 1",
        "1 ",
        "1_000",
        "9" * 129,
    ],
)
def test_numeric_fields_reject_lossy_nonfinite_or_unbounded_inputs(field, value):
    with pytest.raises(ValueError):
        parse(raw_fill(**{field: value}))


@pytest.mark.parametrize("field", ["fillSz", "fillPx"])
@pytest.mark.parametrize("value", ["", "0", "-0.1", None, "--1", "1.2.3"])
def test_fill_amount_and_price_must_be_positive_exact_decimals(field, value):
    with pytest.raises(ValueError):
        parse(raw_fill(**{field: value}))


@pytest.mark.parametrize("field", ["fillTime", "ts"])
@pytest.mark.parametrize("value", ["", None, "-1", "1.1", "+1", str(2**63)])
def test_times_are_bounded_unsigned_integer_strings(field, value):
    with pytest.raises(ValueError):
        parse(raw_fill(**{field: value}))


def test_missing_fill_time_never_falls_back_to_record_time():
    row = raw_fill()
    del row["fillTime"]
    with pytest.raises(ValueError):
        parse(row)


@pytest.mark.parametrize("field", ["billId", "instId", "tradeId", "ordId", "side"])
@pytest.mark.parametrize("value", [None, "", True, 123, 1.5, "x" * 257])
def test_required_identifiers_and_side_are_never_coerced(field, value):
    with pytest.raises(ValueError):
        parse(raw_fill(**{field: value}))


@pytest.mark.parametrize(
    "value",
    ["BTC-USDT-SWAP", "BTC-USD-261225", "BTC/USDT", "BTC/USDT:USDT", " BTC-USDT", "BTC--USDT"],
)
def test_canonicalization_rejects_derivatives_and_non_native_spot_ids(value):
    with pytest.raises(ValueError):
        parse(raw_fill(instId=value))


@pytest.mark.parametrize("value", ["MARGIN", "SWAP", "FUTURES", "OPTION", None, ""])
def test_parser_requires_explicit_spot_instrument_type(value):
    with pytest.raises(ValueError):
        parse(raw_fill(instType=value))


@pytest.mark.parametrize("value", ["BUY", "short", " buy"])
def test_side_is_not_normalized_to_hide_provider_errors(value):
    with pytest.raises(ValueError):
        parse(raw_fill(side=value))


def test_raw_input_mutations_do_not_change_facts():
    row = raw_fill(fee="-0.000001")
    fact = parse(row)
    row["fee"] = "100"
    assert fact.fee == "-0.000001"
