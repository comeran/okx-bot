"""Raw signed fee rates are observations, not normalized costs or coverage."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace

import pytest

from src.core.private_facts import MISSING
from src.exchange.errors import MalformedProviderData

ROOT_FIELDS = ["level", "maker", "taker", "makerUSDC", "takerUSDC", "ts", "ruleType", "category"]
ROOT_NAMES = ["level", "maker", "taker", "maker_usdc", "taker_usdc", "ts", "rule_type", "category"]


def payload(**fields):
    return {"code": "0", "data": [{
        "instType": "SPOT", "level": "Lv1", "maker": "+0.00000000000000000100",
        "taker": "-0.00100000000000000001", "makerUSDC": None, "takerUSDC": "",
        "ts": "001763979985847", "ruleType": "normal", "feeGroup": [{
            "groupId": "001", "maker": "0.000100", "taker": "-0.00200", "rpiMaker": "",
        }], **fields,
    }]}


def parse(raw, inst_id="BTC-USDT"):
    from src.exchange.okx_trade_fee_facts import parse_okx_spot_trade_fee

    return parse_okx_spot_trade_fee(raw, inst_id=inst_id)


def test_exact_signed_strings_request_context_and_nested_facts_are_immutable():
    raw = payload()
    before = deepcopy(raw)
    fact = parse(raw)
    assert fact.requested_inst_id == "BTC-USDT" and fact.inst_type == "SPOT"
    assert fact.maker == "+0.00000000000000000100"
    assert fact.taker == "-0.00100000000000000001"
    assert fact.maker_usdc is None and fact.taker_usdc == "" and fact.category is MISSING
    assert fact.ts == "001763979985847"
    group, = fact.fee_group
    assert (group.group_id, group.maker, group.taker, group.rpi_maker) == (
        "001", "0.000100", "-0.00200", "",
    )
    with pytest.raises(FrozenInstanceError):
        fact.maker = "0"
    with pytest.raises(FrozenInstanceError):
        group.maker = "0"
    raw["data"][0]["feeGroup"][0]["maker"] = "changed-response"
    assert group.maker == "0.000100"
    assert before["data"][0]["maker"] == fact.maker


@pytest.mark.parametrize("raw_name,name", list(zip(ROOT_FIELDS, ROOT_NAMES, strict=True)))
@pytest.mark.parametrize("value", [MISSING, None, ""])
def test_optional_root_missing_null_empty_states_stay_distinct(raw_name, name, value):
    raw = payload(**{raw_name: value})
    if value is MISSING:
        del raw["data"][0][raw_name]
    actual = getattr(parse(raw), name)
    if value is MISSING or value is None:
        assert actual is value
    else:
        assert actual == value


@pytest.mark.parametrize("raw_name,name", [("maker", "maker"), ("taker", "taker"),
                                          ("rpiMaker", "rpi_maker")])
@pytest.mark.parametrize("value", [MISSING, None, ""])
def test_group_optional_states_stay_distinct(raw_name, name, value):
    group = {"groupId": "1", raw_name: value}
    if value is MISSING:
        del group[raw_name]
    fact = parse(payload(feeGroup=[group]))
    assert getattr(fact.fee_group[0], name) == value


@pytest.mark.parametrize("value,want", [(None, None), ([], ()), (MISSING, MISSING)])
def test_whole_group_missing_null_empty_states_are_preserved(value, want):
    raw = payload(feeGroup=value)
    if value is MISSING:
        del raw["data"][0]["feeGroup"]
    assert parse(raw).fee_group == want


@pytest.mark.parametrize("raw", [None, [], {}, {"code": 0, "data": [{}]},
                                {"code": "1", "data": [{}]}, {"code": "0", "data": []},
                                {"code": "0", "data": [{}, {}]},
                                {"code": "0", "data": [None]}, payload(instType="SWAP"),
                                payload(instType=None)])
def test_non_spot_or_malformed_success_envelopes_are_rejected(raw):
    with pytest.raises(MalformedProviderData):
        parse(raw)


@pytest.mark.parametrize("raw_name", ROOT_FIELDS)
@pytest.mark.parametrize("value", [0.1, True, 1, [], {}, "x" * 257])
def test_root_text_never_coerces_numbers_booleans_or_containers(raw_name, value):
    with pytest.raises(MalformedProviderData):
        parse(payload(**{raw_name: value}))


@pytest.mark.parametrize("groups", [
    "bad", {}, [None], [{}], [{"groupId": ""}], [{"groupId": 1}],
    [{"groupId": "1", "maker": 0.1}], [{"groupId": "1", "taker": True}],
    [{"groupId": "1"}, {"groupId": "1"}],
])
def test_malformed_or_duplicate_group_evidence_is_rejected(groups):
    with pytest.raises(MalformedProviderData):
        parse(payload(feeGroup=groups))


@pytest.mark.parametrize("symbol", ["BTC/USDT", "btc-usdt", "BTC-USDT-SWAP", "", None, 1])
def test_requested_instrument_is_validated_as_native_spot(symbol):
    with pytest.raises(ValueError):
        parse(payload(), symbol)


@pytest.mark.parametrize("echo", ["ETH-USDT", None, ""])
def test_response_instrument_echo_cannot_contradict_request(echo):
    with pytest.raises(MalformedProviderData):
        parse(payload(instId=echo))


def test_matching_echo_and_no_echo_keep_the_explicit_request_binding():
    assert parse(payload(instId="BTC-USDT")).requested_inst_id == "BTC-USDT"
    assert parse(payload(), "ETH-USDT").requested_inst_id == "ETH-USDT"


def test_direct_dto_construction_cannot_bypass_group_type_and_spot_validation():
    from src.exchange.okx_trade_fee_facts import SpotFeeGroupFact

    fact = parse(payload())
    for changed in ({"inst_type": "MARGIN"}, {"maker": 0.1}, {"fee_group": []},
                    {"fee_group": (object(),)}, {"fee_group": fact.fee_group * 2},
                    {"requested_inst_id": "BTC/USDT"}):
        with pytest.raises(ValueError):
            replace(fact, **changed)
    for arguments in ({"group_id": ""}, {"group_id": "1", "taker": False}):
        with pytest.raises(ValueError):
            SpotFeeGroupFact(**arguments)


class RawExchange:
    def __init__(self, response):
        self.response = response
        self.requests = []

    async def private_get_account_trade_fee(self, params):
        self.requests.append(params.copy())
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


async def test_adapter_uses_one_exact_symbol_bound_raw_request():
    from src.exchange.okx_trade_fee_facts import OKXSpotTradeFeeFactsAdapter

    exchange = RawExchange(payload())
    fact = await OKXSpotTradeFeeFactsAdapter(exchange).fetch("BTC-USDT")
    assert exchange.requests == [{"instType": "SPOT", "instId": "BTC-USDT"}]
    assert fact.taker == "-0.00100000000000000001"


async def test_adapter_checks_request_before_transport_and_does_not_retry_errors():
    from src.exchange.okx_trade_fee_facts import OKXSpotTradeFeeFactsAdapter

    exchange = RawExchange(ConnectionError("unavailable"))
    adapter = OKXSpotTradeFeeFactsAdapter(exchange)
    with pytest.raises(ValueError):
        await adapter.fetch("BTC/USDT")
    assert exchange.requests == []
    with pytest.raises(ConnectionError, match="unavailable"):
        await adapter.fetch("BTC-USDT")
    assert len(exchange.requests) == 1
