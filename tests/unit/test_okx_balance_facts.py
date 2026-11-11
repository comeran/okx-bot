"""Raw USD equity and per-currency cash remain distinct exact observations."""

from dataclasses import FrozenInstanceError

import pytest

from src.core.private_facts import MISSING
from src.exchange.errors import MalformedProviderData


@pytest.fixture
def parse():
    def parse_payload(raw):
        from src.exchange.okx_balance_facts import parse_okx_account_balance

        return parse_okx_account_balance(raw)

    return parse_payload


def balance_payload(**fields):
    return {"code": "0", "data": [{
        "totalEq": "9007199254740993.000100", "adjEq": None, "availEq": "",
        "uTime": "001700000000123", "details": [{
            "ccy": "USDT", "cashBal": "0.00000000000000000100", "eq": "2.00",
            "availBal": None, "frozenBal": "", "upl": "-0.100", "uTime": "1700000000122",
            "liab": "1.000", "interest": "0.0001", "eqUsd": "2.001",
        }], **fields,
    }]}


def test_exact_usd_equity_is_never_usdt_cash_and_dto_is_immutable(parse):
    fact = parse(balance_payload())
    assert fact.total_eq == "9007199254740993.000100"
    assert fact.equity_currency == "USD"
    assert fact.details[0].cash_bal == "0.00000000000000000100"
    assert (fact.adj_eq, fact.avail_eq, fact.u_time) == (None, "", "001700000000123")
    assert (fact.details[0].liab, fact.details[0].interest) == ("1.000", "0.0001")
    assert fact.details[0].eq_usd == "2.001"
    assert fact.is_unleveraged is None
    with pytest.raises(FrozenInstanceError):
        fact.total_eq = "1"
    with pytest.raises(FrozenInstanceError):
        fact.details[0].ccy = "BTC"


@pytest.mark.parametrize("raw_name,name", [
    ("totalEq", "total_eq"), ("adjEq", "adj_eq"), ("availEq", "avail_eq"), ("uTime", "u_time"),
])
@pytest.mark.parametrize("value", [MISSING, None, ""])
def test_top_level_states_remain_distinct(parse, raw_name, name, value):
    raw = balance_payload()
    if value is MISSING:
        del raw["data"][0][raw_name]
    else:
        raw["data"][0][raw_name] = value
    assert getattr(parse(raw), name) is value


ASSET_FIELDS = [
    ("cashBal", "cash_bal"), ("eq", "eq"), ("availBal", "avail_bal"),
    ("frozenBal", "frozen_bal"), ("upl", "upl"), ("uTime", "u_time"),
    ("liab", "liab"), ("interest", "interest"), ("eqUsd", "eq_usd"),
    ("ordFrozen", "ord_frozen"),
]


@pytest.mark.parametrize("raw_name,name", ASSET_FIELDS)
@pytest.mark.parametrize("value", [MISSING, None, ""])
def test_per_asset_states_remain_distinct(parse, raw_name, name, value):
    row = {"ccy": "BTC"}
    if value is not MISSING:
        row[raw_name] = value
    assert getattr(parse(balance_payload(details=[row])).details[0], name) is value


@pytest.mark.parametrize("value", [True, 123, 1.5, " ", " 1", "1 ", "1" * 257, [], {}])
@pytest.mark.parametrize("location", ["totalEq", "cashBal", "uTime"])
def test_rejects_coerced_whitespace_and_unbounded_fields(parse, location, value):
    raw = balance_payload()
    row = raw["data"][0] if location != "cashBal" else raw["data"][0]["details"][0]
    row[location] = value
    with pytest.raises(MalformedProviderData):
        parse(raw)


@pytest.mark.parametrize("ccy", [None, "", " ", " BTC", "BTC ", True, 3, "x" * 257])
def test_asset_currency_is_required_without_normalization(parse, ccy):
    with pytest.raises(MalformedProviderData):
        parse(balance_payload(details=[{"ccy": ccy}]))


@pytest.mark.parametrize("second", [{"ccy": "BTC", "cashBal": "1"},
                                     {"ccy": "BTC", "cashBal": "2"}])
def test_duplicate_assets_fail_even_if_values_agree(parse, second):
    with pytest.raises(MalformedProviderData, match="duplicate"):
        parse(balance_payload(details=[{"ccy": "BTC", "cashBal": "1"}, second]))


@pytest.mark.parametrize("raw", [
    None, [], {}, {"code": 0, "data": [{}]}, {"code": "1", "data": [{}]},
    {"code": "0", "data": []}, {"code": "0", "data": [{}, {}]},
    {"code": "0", "data": {}}, {"code": "0", "data": [None]},
    {"code": "0", "data": [{}]}, balance_payload(details=None),
    balance_payload(details={}), balance_payload(details=[None]), balance_payload(details=[{}]),
])
def test_rejects_malformed_success_envelope_or_details(parse, raw):
    with pytest.raises(MalformedProviderData):
        parse(raw)


def test_empty_details_never_synthesizes_usdt_cash(parse):
    fact = parse(balance_payload(details=[]))
    assert fact.details == () and fact.is_unleveraged is None


class RawBalanceExchange:
    def __init__(self, response):
        self.response, self.requests = response, []
        self.before_response = None

    async def private_get_account_balance(self, params):
        self.requests.append(params.copy())
        if self.before_response is not None:
            self.before_response()
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response

    async def fetch_balance(self, *args, **kwargs):
        raise AssertionError("Unified balance loses exact provider strings")


async def test_adapter_calls_raw_balance_once_with_empty_parameters():
    from src.exchange.okx_balance_facts import OKXAccountBalanceFactsAdapter

    exchange = RawBalanceExchange(balance_payload())
    fact = await OKXAccountBalanceFactsAdapter(exchange).fetch()
    assert exchange.requests == [{}]
    assert fact.details[0].cash_bal == "0.00000000000000000100"


@pytest.mark.parametrize("response,error", [(OSError("offline"), OSError),
                                          ({"code": "1"}, MalformedProviderData)])
async def test_adapter_propagates_provider_and_parser_errors(response, error):
    from src.exchange.okx_balance_facts import OKXAccountBalanceFactsAdapter

    exchange = RawBalanceExchange(response)
    with pytest.raises(error):
        await OKXAccountBalanceFactsAdapter(exchange).fetch()
    assert exchange.requests == [{}]
