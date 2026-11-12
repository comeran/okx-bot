"""Exact raw OKX SPOT order-detail facts remain diagnostic input only."""

from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from src.core.private_facts import MISSING
from src.exchange.errors import MalformedProviderData

ORDER_FIELDS = [
    ("px", "px"),
    ("accFillSz", "acc_fill_sz"),
    ("avgPx", "avg_px"),
    ("fillTime", "fill_time"),
    ("fee", "fee"),
    ("feeCcy", "fee_ccy"),
    ("tradeId", "trade_id"),
    ("cancelSource", "cancel_source"),
    ("cancelSourceReason", "cancel_source_reason"),
]


def row(**overrides):
    return {
        "instType": "SPOT",
        "instId": "BTC-USDT",
        "ordId": "00012345678901234567",
        "clOrdId": "client-order-0001",
        "tag": "",
        "side": "buy",
        "ordType": "limit",
        "px": "100.100000000000000001",
        "sz": "0.010000000000000001",
        "accFillSz": "0.009000000000000001",
        "avgPx": "100.099999999999999999",
        "state": "partially_filled",
        "cTime": "001763979985847",
        "uTime": "001763979985948",
        "fillTime": "001763979985900",
        "tradeId": "trade-0009",
        "fee": "-0.000000000000000001",
        "feeCcy": "USDT",
        "cancelSource": "",
        "cancelSourceReason": "",
        "rebate": "",
        "rebateCcy": "",
        **overrides,
    }


def envelope(**overrides):
    return {"code": "0", "data": [row(**overrides)]}


def parse(raw, *, inst_id="BTC-USDT", ord_id=None, cl_ord_id=None):
    from src.exchange.okx_order_facts import parse_okx_spot_order

    return parse_okx_spot_order(
        raw, inst_id=inst_id, ord_id=ord_id, cl_ord_id=cl_ord_id,
    )


class RawExchange:
    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.requests = []

    async def private_get_trade_order(self, params):
        self.requests.append(dict(params))
        if not self.payloads:
            raise AssertionError("unexpected retry")
        payload = self.payloads.pop(0)
        if isinstance(payload, BaseException):
            raise payload
        return payload


@pytest.mark.parametrize(
    "identity",
    [{"ord_id": "00012345678901234567"}, {"cl_ord_id": "client-order-0001"}],
)
def test_exact_order_fact_preserves_raw_decimal_text_identity_and_is_immutable(identity):
    raw = envelope()
    before = deepcopy(raw)
    fact = parse(raw, **identity)
    assert fact.inst_type == "SPOT"
    assert fact.inst_id == "BTC-USDT"
    assert fact.ord_id == "00012345678901234567"
    assert fact.cl_ord_id == "client-order-0001"
    assert fact.px == "100.100000000000000001"
    assert fact.sz == "0.010000000000000001"
    assert fact.acc_fill_sz == "0.009000000000000001"
    assert fact.avg_px == "100.099999999999999999"
    assert fact.c_time == "001763979985847"
    assert fact.u_time == "001763979985948"
    assert fact.fee == "-0.000000000000000001"
    assert fact.state == "partially_filled"
    with pytest.raises(FrozenInstanceError):
        fact.state = "filled"
    raw["data"][0]["px"] = "changed"
    assert fact.px == before["data"][0]["px"]


@pytest.mark.parametrize("raw_name,name", ORDER_FIELDS)
@pytest.mark.parametrize("value", [MISSING, None, ""])
def test_optional_order_fields_preserve_missing_null_and_empty_states(raw_name, name, value):
    raw = envelope(**({} if value is MISSING else {raw_name: value}))
    if value is MISSING:
        del raw["data"][0][raw_name]
    fact = parse(raw, ord_id="00012345678901234567")
    actual = getattr(fact, name)
    assert actual is value if value in (MISSING, None) else actual == value


@pytest.mark.parametrize("kwargs", [
    {},
    {"ord_id": "1", "cl_ord_id": "client"},
    {"ord_id": ""},
    {"cl_ord_id": ""},
    {"ord_id": 1},
    {"cl_ord_id": 1},
])
def test_lookup_requires_exactly_one_bounded_identity_before_provider_data(kwargs):
    with pytest.raises((ValueError, MalformedProviderData)):
        parse(envelope(), **kwargs)


@pytest.mark.parametrize("inst_id", ["BTC/USDT", "btc-USDT", "BTC-USDT-SWAP", "", "BTC-USDT "])
def test_non_native_spot_symbol_is_rejected_before_parsing(inst_id):
    with pytest.raises((ValueError, MalformedProviderData)):
        parse(envelope(), inst_id=inst_id, ord_id="00012345678901234567")


@pytest.mark.parametrize("raw", [
    None,
    {},
    {"code": "1", "data": [row()]},
    {"code": 0, "data": [row()]},
    {"code": "0", "data": []},
    {"code": "0", "data": [{}, {}]},
    {"code": "0", "data": [None]},
    {"code": "0", "data": [row(), row(ordId="other")]},
])
def test_empty_not_found_or_malformed_envelopes_fail_closed(raw):
    with pytest.raises(MalformedProviderData):
        parse(raw, ord_id="00012345678901234567")


@pytest.mark.parametrize("field,value", [
    ("instType", "SWAP"), ("instType", None),
    ("instId", "ETH-USDT"), ("ordId", "other-order"),
    ("clOrdId", "other-client"),
])
def test_response_type_instrument_and_requested_identity_must_match(field, value):
    identity = {"cl_ord_id": "client-order-0001"} if field == "clOrdId" else {
        "ord_id": "00012345678901234567",
    }
    with pytest.raises(MalformedProviderData):
        parse(envelope(**{field: value}), **identity)


def test_client_identity_lookup_requires_response_client_identity_match():
    with pytest.raises(MalformedProviderData):
        parse(envelope(clOrdId="different-client"), cl_ord_id="client-order-0001")


@pytest.mark.parametrize("field,value", [
    ("ordId", None), ("ordId", ""), ("ordId", 9),
    ("clOrdId", 9), ("side", "BUY"), ("state", "unknown-state"),
    ("sz", "1e-3"), ("sz", 1), ("px", "NaN"),
    ("cTime", "-1"), ("uTime", True),
])
def test_malformed_required_or_numeric_provider_fields_fail_closed(field, value):
    with pytest.raises(MalformedProviderData):
        parse(envelope(**{field: value}), ord_id="00012345678901234567")


@pytest.mark.asyncio
async def test_adapter_sends_one_native_spot_order_request_and_returns_fact():
    from src.exchange.okx_order_facts import OKXSpotOrderFactsAdapter

    exchange = RawExchange(envelope())
    fact = await OKXSpotOrderFactsAdapter(exchange).fetch(
        inst_id="BTC-USDT", ord_id="00012345678901234567",
    )
    assert fact.ord_id == "00012345678901234567"
    assert exchange.requests == [{
        "instType": "SPOT", "instId": "BTC-USDT", "ordId": "00012345678901234567",
    }]


@pytest.mark.asyncio
async def test_adapter_accepts_client_order_identity_without_exchange_order_id_param():
    from src.exchange.okx_order_facts import OKXSpotOrderFactsAdapter

    exchange = RawExchange(envelope())
    await OKXSpotOrderFactsAdapter(exchange).fetch(
        inst_id="BTC-USDT", cl_ord_id="client-order-0001",
    )
    assert exchange.requests == [{
        "instType": "SPOT", "instId": "BTC-USDT", "clOrdId": "client-order-0001",
    }]


@pytest.mark.asyncio
async def test_adapter_validates_request_before_provider_access_and_never_retries():
    from src.exchange.okx_order_facts import OKXSpotOrderFactsAdapter

    exchange = RawExchange(envelope(), envelope())
    adapter = OKXSpotOrderFactsAdapter(exchange)
    with pytest.raises(ValueError):
        await adapter.fetch(inst_id="BTC-USDT")
    assert exchange.requests == []

    exchange = RawExchange(RuntimeError("provider unavailable"), envelope())
    adapter = OKXSpotOrderFactsAdapter(exchange)
    with pytest.raises(RuntimeError, match="provider unavailable"):
        await adapter.fetch(inst_id="BTC-USDT", ord_id="00012345678901234567")
    assert len(exchange.requests) == 1


@pytest.mark.asyncio
async def test_adapter_surfaces_empty_provider_response_once_without_inference():
    from src.exchange.okx_order_facts import OKXSpotOrderFactsAdapter

    exchange = RawExchange({"code": "0", "data": []})
    with pytest.raises(MalformedProviderData):
        await OKXSpotOrderFactsAdapter(exchange).fetch(
            inst_id="BTC-USDT", ord_id="00012345678901234567",
        )
    assert len(exchange.requests) == 1
