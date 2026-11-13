"""Raw account config proves provider identity, never safe trading activation."""

from dataclasses import FrozenInstanceError

import pytest

from src.core.private_facts import MISSING
from src.exchange.errors import MalformedProviderData


@pytest.fixture
def parse():
    from src.exchange.okx_account_facts import parse_okx_account_config

    return parse_okx_account_config


def config_payload(**fields):
    return {"code": "0", "data": [{"uid": "00123", **fields}]}


def test_preserves_raw_fields_and_immutable_uid(parse):
    fact = parse(config_payload(
        acctLv="1", acctStpMode="cancel_maker", posMode="net_mode", autoLoan="0",
        enableSpotBorrow="0", feeType="0", settleCcy="USDT", mainUid="00999",
    ))
    assert fact.uid == "00123"
    assert (fact.acct_lv, fact.acct_stp_mode, fact.pos_mode) == ("1", "cancel_maker", "net_mode")
    assert (fact.auto_loan, fact.enable_spot_borrow, fact.fee_type) == ("0", "0", "0")
    assert (fact.settle_ccy, fact.main_uid) == ("USDT", "00999")
    with pytest.raises(FrozenInstanceError):
        fact.uid = "other"


@pytest.mark.parametrize("field,attribute", [
    ("acctLv", "acct_lv"), ("acctStpMode", "acct_stp_mode"), ("posMode", "pos_mode"),
    ("autoLoan", "auto_loan"), ("enableSpotBorrow", "enable_spot_borrow"),
    ("feeType", "fee_type"), ("settleCcy", "settle_ccy"), ("mainUid", "main_uid"),
])
def test_preserves_missing_null_and_empty(parse, field, attribute):
    assert getattr(parse(config_payload()), attribute) is MISSING
    assert getattr(parse(config_payload(**{field: None})), attribute) is None
    assert getattr(parse(config_payload(**{field: ""})), attribute) == ""


@pytest.mark.parametrize("raw", [
    None, [], {}, {"code": 0, "data": [{"uid": "1"}]},
    {"code": "1", "data": [{"uid": "1"}]}, {"code": "0", "data": []},
    {"code": "0", "data": [{"uid": "1"}, {"uid": "1"}]},
    {"code": "0", "data": {}}, {"code": "0", "data": [None]},
    {"code": "0", "data": [{"mainUid": "123"}]},
])
def test_rejects_malformed_envelope_and_never_falls_back_to_main_uid(parse, raw):
    with pytest.raises(MalformedProviderData):
        parse(raw)


@pytest.mark.parametrize("value", [None, "", " ", " 123", "123 ", "x" * 257, 123, 1.2, True])
def test_rejects_invalid_uid_without_coercion(parse, value):
    with pytest.raises(MalformedProviderData):
        parse(config_payload(uid=value, mainUid="valid-fallback"))


@pytest.mark.parametrize("field", [
    "acctLv", "acctStpMode", "posMode", "autoLoan", "enableSpotBorrow", "feeType",
    "settleCcy", "mainUid",
])
@pytest.mark.parametrize("value", [True, 1, 1.2, " ", " 1", "1 ", "x" * 257, [], {}])
def test_rejects_coerced_or_unbounded_optional_text(parse, field, value):
    with pytest.raises(MalformedProviderData):
        parse(config_payload(**{field: value}))


@pytest.mark.parametrize("fields,auto,spot,status", [
    ({}, None, None, "unknown"),
    ({"acctLv": "1"}, None, None, "unknown"),
    ({"autoLoan": None, "enableSpotBorrow": ""}, None, None, "unknown"),
    ({"autoLoan": "future", "enableSpotBorrow": "0"}, None, False, "unknown"),
    ({"autoLoan": "0", "enableSpotBorrow": "0"}, False, False, "disabled"),
    ({"autoLoan": "1"}, True, None, "enabled"),
    ({"enableSpotBorrow": "1"}, None, True, "enabled"),
    ({"acctLv": "1", "autoLoan": "1", "enableSpotBorrow": "1"}, True, True, "enabled"),
])
def test_conservative_borrow_and_mode_status(parse, fields, auto, spot, status):
    fact = parse(config_payload(**fields))
    assert fact.auto_loan_enabled is auto
    assert fact.spot_borrow_enabled is spot
    assert fact.borrow_status == status
    assert fact.is_unleveraged is None


class RawConfigExchange:
    def __init__(self, response):
        self.response = response
        self.requests = []

    async def private_get_account_config(self, params):
        self.requests.append(params.copy())
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response

    async def fetch_balance(self, *args, **kwargs):
        raise AssertionError("Unified balance must not supply exact config facts")

    async def fetch_my_trades(self, *args, **kwargs):
        raise AssertionError("Unified trades must not supply exact config facts")


async def test_raw_config_adapter_fetches_one_empty_parameter_request():
    from src.exchange.okx_account_facts import OKXAccountConfigFactsAdapter

    exchange = RawConfigExchange(config_payload(autoLoan=None))
    fact = await OKXAccountConfigFactsAdapter(exchange).fetch()
    assert exchange.requests == [{}]
    assert fact.uid == "00123"
    assert fact.auto_loan is None and fact.main_uid is MISSING


@pytest.mark.parametrize("response,error", [
    ({"code": "0", "data": [{"mainUid": "123"}]}, MalformedProviderData),
    (OSError("offline"), OSError),
])
async def test_raw_config_adapter_propagates_provider_and_parser_failures(response, error):
    from src.exchange.okx_account_facts import OKXAccountConfigFactsAdapter

    exchange = RawConfigExchange(response)
    with pytest.raises(error):
        await OKXAccountConfigFactsAdapter(exchange).fetch()
    assert exchange.requests == [{}]
