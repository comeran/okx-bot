"""Raw SPOT rules are exact evidence, without trading or storage side effects."""

from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from src.core.private_facts import MISSING
from src.exchange.errors import MalformedProviderData


@pytest.fixture
def parse():
    def parse_payload(raw, **kwargs):
        from src.exchange.okx_instrument_facts import parse_okx_spot_instruments

        return parse_okx_spot_instruments(raw, **kwargs)

    return parse_payload


def instrument_row(**fields):
    return {
        "instType": "SPOT", "instId": "BTC-USDT", "baseCcy": "BTC", "quoteCcy": "USDT",
        "tickSz": "0.00000000000000000100", "lotSz": "9007199254740993.000100",
        "minSz": "0000.00001000", "state": "live", "ruleType": "normal",
        "tradeQuoteCcy": "USDT", "maxLmtSz": "999999999999999999999.00",
        "maxMktSz": "100.000", "lever": "", **fields,
    }


def instrument_payload(*rows):
    return {"code": "0", "data": list(rows) if rows else [instrument_row()]}


OPTIONAL_FIELDS = [
    ("state", "state"), ("ruleType", "rule_type"), ("tradeQuoteCcy", "trade_quote_ccy"),
    ("maxLmtSz", "max_lmt_sz"), ("maxMktSz", "max_mkt_sz"), ("lever", "lever"),
]


def test_exact_rules_beyond_float_remain_immutable_provider_strings(parse):
    raw = instrument_payload()
    original = deepcopy(raw)
    facts = parse(raw)
    assert isinstance(facts, tuple)
    assert len(facts) == 1
    fact = facts[0]
    assert (fact.inst_id, fact.base_ccy, fact.quote_ccy) == ("BTC-USDT", "BTC", "USDT")
    assert fact.tick_sz == "0.00000000000000000100"
    assert fact.lot_sz == "9007199254740993.000100"
    assert fact.min_sz == "0000.00001000"
    assert (fact.state, fact.rule_type, fact.trade_quote_ccy) == ("live", "normal", "USDT")
    assert (fact.max_lmt_sz, fact.max_mkt_sz, fact.lever) == (
        "999999999999999999999.00", "100.000", "",
    )
    assert raw == original
    with pytest.raises(FrozenInstanceError):
        fact.tick_sz = "1"


@pytest.mark.parametrize("raw_name,name", OPTIONAL_FIELDS)
@pytest.mark.parametrize("value", [MISSING, None, ""])
def test_optional_missing_null_empty_states_are_distinct(parse, raw_name, name, value):
    row = instrument_row()
    if value is MISSING:
        del row[raw_name]
    else:
        row[raw_name] = value
    assert getattr(parse(instrument_payload(row))[0], name) is value


@pytest.mark.parametrize("field", ["tickSz", "lotSz", "minSz"])
@pytest.mark.parametrize("value", [
    None, True, False, 1, 0.1, "", "0", "0.000", "-0", "-1", "NaN", "Infinity",
    "1e-8", "1e999999999", " 1", "1 ", "\t1", "1 0", "1_000", "1,000", "1..0",
    "1" * 129, [], {},
])
def test_required_rule_rejects_nonpositive_coerced_or_malformed_decimal(parse, field, value):
    with pytest.raises(MalformedProviderData):
        parse(instrument_payload(instrument_row(**{field: value})))


@pytest.mark.parametrize("field", ["tickSz", "lotSz", "minSz", "baseCcy", "quoteCcy", "instId",
                                   "instType"])
def test_required_fields_cannot_be_missing(parse, field):
    row = instrument_row()
    del row[field]
    with pytest.raises(MalformedProviderData):
        parse(instrument_payload(row))


@pytest.mark.parametrize("raw_name,name", OPTIONAL_FIELDS)
@pytest.mark.parametrize("value", [True, False, 1, 1.5, " ", " live", "live ", "x" * 257, [], {}])
def test_optional_fields_reject_coercion_whitespace_and_unbounded_text(
    parse, raw_name, name, value,
):
    with pytest.raises(MalformedProviderData):
        parse(instrument_payload(instrument_row(**{raw_name: value})))


@pytest.mark.parametrize("fields", [
    {"instType": "SWAP"}, {"instType": "MARGIN"}, {"instType": "FUTURES"},
    {"instType": None}, {"instType": True}, {"instId": "BTC-USDT-SWAP"},
    {"instId": "BTC/USDT"}, {"instId": "btc-usdt"}, {"instId": " BTC-USDT"},
    {"instId": "BTC-USDT "}, {"instId": "BTC-"}, {"instId": "-USDT"},
    {"instId": "X" * 257}, {"baseCcy": ""}, {"quoteCcy": ""},
    {"baseCcy": " "}, {"quoteCcy": None}, {"baseCcy": True}, {"quoteCcy": 1},
    {"baseCcy": "ETH"}, {"quoteCcy": "USD"},
])
def test_rejects_nonspot_or_inconsistent_native_identity(parse, fields):
    with pytest.raises(MalformedProviderData):
        parse(instrument_payload(instrument_row(**fields)))


@pytest.mark.parametrize("raw", [
    None, [], {}, {"code": 0, "data": []}, {"code": True, "data": []},
    {"code": "1", "data": []}, {"code": "0"}, {"code": "0", "data": None},
    {"code": "0", "data": {}}, {"code": "0", "data": ()},
    {"code": "0", "data": [None]}, {"code": "0", "data": [1]},
])
def test_rejects_malformed_success_envelopes_and_rows(parse, raw):
    with pytest.raises(MalformedProviderData):
        parse(raw)


def test_empty_response_is_only_an_empty_observation(parse):
    assert parse({"code": "0", "data": []}) == ()


def test_multiple_distinct_spot_rows_preserve_response_order(parse):
    facts = parse(instrument_payload(
        instrument_row(), instrument_row(instId="ETH-USDT", baseCcy="ETH"),
    ))
    assert tuple(fact.inst_id for fact in facts) == ("BTC-USDT", "ETH-USDT")


@pytest.mark.parametrize("changes", [{}, {"tickSz": "0.0100"}, {"state": "suspend"},
                                     {"lever": None}, {"lotSz": "9007199254740993.00010"}])
def test_duplicate_and_conflicting_observations_are_rejected(parse, changes):
    with pytest.raises(MalformedProviderData, match="duplicate|conflict"):
        parse(instrument_payload(instrument_row(), instrument_row(**changes)))


def test_direct_dto_construction_enforces_the_same_rule_contract():
    from src.exchange.okx_instrument_facts import SpotInstrumentFact

    with pytest.raises(ValueError):
        SpotInstrumentFact("BTC-USDT", "BTC", "USDT", "0", "1", "1")
    with pytest.raises(ValueError):
        SpotInstrumentFact("BTC-USDT-SWAP", "BTC", "USDT", "1", "1", "1")


class RawInstrumentExchange:
    def __init__(self, response):
        self.response = response
        self.requests = []

    async def public_get_public_instruments(self, params):
        self.requests.append(params.copy())
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response

    def __getattr__(self, name):
        raise AssertionError(f"Unexpected unified, private or order call: {name}")


@pytest.mark.parametrize("inst_id,expected", [
    (None, {"instType": "SPOT"}),
    ("BTC-USDT", {"instType": "SPOT", "instId": "BTC-USDT"}),
])
async def test_adapter_calls_only_raw_endpoint_once_with_exact_params(inst_id, expected):
    from src.exchange.okx_instrument_facts import OKXSpotInstrumentFactsAdapter

    exchange = RawInstrumentExchange(instrument_payload())
    facts = await OKXSpotInstrumentFactsAdapter(exchange).fetch(inst_id)
    assert exchange.requests == [expected]
    assert facts[0].tick_sz == "0.00000000000000000100"


@pytest.mark.parametrize("inst_id", ["", "BTC/USDT", "BTC-USDT-SWAP", "BTC-USDT ",
                                    "btc-usdt", True, 1, "x" * 257])
async def test_invalid_request_fails_before_transport(inst_id):
    from src.exchange.okx_instrument_facts import OKXSpotInstrumentFactsAdapter

    exchange = RawInstrumentExchange(instrument_payload())
    with pytest.raises(ValueError):
        await OKXSpotInstrumentFactsAdapter(exchange).fetch(inst_id)
    assert exchange.requests == []


async def test_requested_instrument_cannot_accept_another_row(parse):
    from src.exchange.okx_instrument_facts import OKXSpotInstrumentFactsAdapter

    raw = instrument_payload(instrument_row(instId="ETH-USDT", baseCcy="ETH"))
    with pytest.raises(MalformedProviderData, match="request"):
        parse(raw, inst_id="BTC-USDT")
    exchange = RawInstrumentExchange(raw)
    with pytest.raises(MalformedProviderData, match="request"):
        await OKXSpotInstrumentFactsAdapter(exchange).fetch("BTC-USDT")
    assert exchange.requests == [{"instType": "SPOT", "instId": "BTC-USDT"}]


@pytest.mark.parametrize("response,error", [
    (OSError("offline"), OSError), ({"code": "1"}, MalformedProviderData),
    (instrument_payload(instrument_row(instType="SWAP")), MalformedProviderData),
])
async def test_provider_and_parser_errors_propagate_without_retry(response, error):
    from src.exchange.okx_instrument_facts import OKXSpotInstrumentFactsAdapter

    exchange = RawInstrumentExchange(response)
    with pytest.raises(error):
        await OKXSpotInstrumentFactsAdapter(exchange).fetch()
    assert exchange.requests == [{"instType": "SPOT"}]


async def test_parser_and_adapter_never_open_storage_or_mutate_input(parse, monkeypatch, tmp_path):
    import sqlite3

    from src.exchange.okx_instrument_facts import OKXSpotInstrumentFactsAdapter

    def refuse_storage(*args, **kwargs):
        pytest.fail("Instrument evidence must not touch legacy, ledger or activation storage")

    monkeypatch.setattr(sqlite3, "connect", refuse_storage)
    raw = instrument_payload()
    original = deepcopy(raw)
    direct = parse(raw)
    fetched = await OKXSpotInstrumentFactsAdapter(RawInstrumentExchange(raw)).fetch()
    assert fetched == direct
    assert raw == original
    assert list(tmp_path.iterdir()) == []
