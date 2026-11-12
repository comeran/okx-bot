"""One-shot exact SPOT order sync captures one response and commits one fact."""

import json

import pytest

from src.exchange.errors import MalformedProviderData
from src.ops.exact_private_sync import sync_exact_spot_order
from tests.unit.test_account_balance_store import bind, database_state
from tests.unit.test_account_balance_store import stores as stores
from tests.unit.test_okx_order_facts import envelope

ORD_ID = "00012345678901234567"
CLIENT_ID = "client-order-0001"


class RawExchange:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    async def private_get_trade_order(self, params):
        self.requests.append(dict(params))
        if not self.responses:
            raise AssertionError("unexpected retry")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class NoIOExchange:
    def __init__(self):
        self.requests = []

    async def private_get_trade_order(self, params):
        self.requests.append(dict(params))
        raise AssertionError("validation must fail before provider access")


class NoIORepository:
    def __getattr__(self, name):
        raise AssertionError(f"validation must fail before repository access: {name}")


def operational_state(repo):
    return {
        name: rows
        for name, rows in database_state(repo).items()
        if name != "private_spot_order"
    }


@pytest.mark.asyncio
async def test_ord_id_sync_sends_exact_native_request_and_stores_response(stores):
    _, (repo, _) = stores
    bind(repo)
    exchange = RawExchange(envelope())

    result = await sync_exact_spot_order(
        repo, exchange, 100, inst_id="BTC-USDT", ord_id=ORD_ID,
    )

    assert exchange.requests == [{
        "instType": "SPOT", "instId": "BTC-USDT", "ordId": ORD_ID,
    }]
    assert result.uid == "00123"
    assert result.lookup_kind == "ordId" and result.lookup_value == ORD_ID
    assert json.loads(result.raw_json)["data"][0]["state"] == "partially_filled"


@pytest.mark.asyncio
async def test_client_order_id_sync_sends_client_lookup_without_substitution(stores):
    _, (repo, _) = stores
    bind(repo)
    exchange = RawExchange(envelope())

    result = await sync_exact_spot_order(
        repo, exchange, 100, inst_id="BTC-USDT", cl_ord_id=CLIENT_ID,
    )

    assert exchange.requests == [{
        "instType": "SPOT", "instId": "BTC-USDT", "clOrdId": CLIENT_ID,
    }]
    assert result.lookup_kind == "clOrdId" and result.lookup_value == CLIENT_ID


@pytest.mark.asyncio
@pytest.mark.parametrize("received_at", [-1, True, 1.5, "1", None, 2**63])
async def test_invalid_timestamp_fails_before_repository_or_provider(received_at):
    exchange = NoIOExchange()
    with pytest.raises(ValueError):
        await sync_exact_spot_order(
            NoIORepository(), exchange, received_at,
            inst_id="BTC-USDT", ord_id=ORD_ID,
        )
    assert exchange.requests == []


@pytest.mark.asyncio
async def test_invalid_lookup_fails_before_repository_or_provider():
    exchange = NoIOExchange()
    with pytest.raises(ValueError):
        await sync_exact_spot_order(
            NoIORepository(), exchange, 100,
            inst_id="BTC-USDT", ord_id="",
        )
    assert exchange.requests == []


@pytest.mark.asyncio
async def test_invalid_uid_fails_before_repository_or_provider():
    exchange = NoIOExchange()
    with pytest.raises(ValueError):
        await sync_exact_spot_order(
            NoIORepository(), exchange, 100,
            inst_id="BTC-USDT", ord_id=ORD_ID, uid="",
        )
    assert exchange.requests == []


@pytest.mark.asyncio
async def test_unbound_or_conflicting_uid_fails_without_provider_request(stores):
    _, (repo, _) = stores
    exchange = RawExchange(envelope())
    with pytest.raises(ValueError, match="UID"):
        await sync_exact_spot_order(
            repo, exchange, 100, inst_id="BTC-USDT", ord_id=ORD_ID,
        )
    assert exchange.requests == []

    bind(repo, "00123")
    exchange = RawExchange(envelope())
    with pytest.raises(ValueError, match="UID"):
        await sync_exact_spot_order(
            repo, exchange, 100, inst_id="BTC-USDT", ord_id=ORD_ID, uid="00456",
        )
    assert exchange.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("response,error", [
    (RuntimeError("provider unavailable"), RuntimeError),
    ({"code": "0", "data": []}, MalformedProviderData),
])
async def test_provider_or_malformed_response_is_one_shot_and_leaves_storage_unchanged(
    stores, response, error,
):
    _, (repo, _) = stores
    bind(repo)
    exchange = RawExchange(response, envelope())
    before = database_state(repo)
    with pytest.raises(error):
        await sync_exact_spot_order(
            repo, exchange, 100, inst_id="BTC-USDT", ord_id=ORD_ID,
        )
    assert len(exchange.requests) == 1
    assert database_state(repo) == before


@pytest.mark.asyncio
async def test_storage_error_rolls_back_captured_order_without_retry(stores, monkeypatch):
    _, (repo, _) = stores
    bind(repo)
    before = database_state(repo)
    atomic = repo.atomic_accounting

    def interrupted(callback):
        def fail_after_writes(transaction):
            callback(transaction)
            raise RuntimeError("storage interrupted")
        return atomic(fail_after_writes)

    monkeypatch.setattr(repo, "atomic_accounting", interrupted)
    exchange = RawExchange(envelope(), envelope())
    with pytest.raises(RuntimeError, match="storage interrupted"):
        await sync_exact_spot_order(
            repo, exchange, 100, inst_id="BTC-USDT", ord_id=ORD_ID,
        )
    assert len(exchange.requests) == 1
    assert database_state(repo) == before


@pytest.mark.asyncio
async def test_replay_is_idempotent_and_changed_state_appends_without_operational_writes(stores):
    _, (repo, _) = stores
    bind(repo)
    before = operational_state(repo)
    changed = envelope(
        state="filled", accFillSz="0.010000000000000001",
        avgPx="100.099999999999999999",
    )
    exchange = RawExchange(envelope(), envelope(), changed)

    first = await sync_exact_spot_order(
        repo, exchange, 100, inst_id="BTC-USDT", ord_id=ORD_ID,
    )
    replay = await sync_exact_spot_order(
        repo, exchange, 200, inst_id="BTC-USDT", ord_id=ORD_ID,
    )
    later = await sync_exact_spot_order(
        repo, exchange, 300, inst_id="BTC-USDT", ord_id=ORD_ID,
    )

    assert replay.model_dump() == first.model_dump()
    history = repo.get_private_spot_orders(
        "BTC-USDT", uid="00123", lookup_kind="ordId", lookup_value=ORD_ID,
    )
    assert [record.received_at for record in history] == [100, 300]
    assert later.state == "filled"
    assert operational_state(repo) == before
    assert len(exchange.requests) == 3
