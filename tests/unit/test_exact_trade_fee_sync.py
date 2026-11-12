"""One exact fee request commits only request-bound audit observations."""

import asyncio
import json

import pytest
from sqlalchemy.exc import IntegrityError

from src.data.repository import Repository
from src.exchange.errors import MalformedProviderData
from tests.unit.test_account_balance_store import bind, database_state
from tests.unit.test_account_balance_store import stores as stores
from tests.unit.test_spot_trade_fee_store import operational_state, payload


async def sync(*args, **kwargs):
    from src.ops.exact_private_sync import sync_exact_spot_trade_fee

    return await sync_exact_spot_trade_fee(*args, **kwargs)


class RawExchange:
    def __init__(self, response=None):
        self.response = payload() if response is None else response
        self.requests = []
        self.before_response = None

    async def private_get_account_trade_fee(self, params):
        self.requests.append(params.copy())
        if self.before_response is not None:
            self.before_response()
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response

    async def fetch_trading_fee(self, *args, **kwargs):
        raise AssertionError("Unified float fees cannot supply exact evidence")


async def test_single_raw_request_is_durable_detached_and_does_not_touch_operational_state(stores):
    master, (repo, _) = stores
    bind(repo)
    before = operational_state(master), operational_state(repo)
    exchange = RawExchange()
    record = await sync(repo, exchange, 100, inst_id="BTC-USDT")
    assert exchange.requests == [{"instType": "SPOT", "instId": "BTC-USDT"}]
    assert (record.uid, record.requested_inst_id, record.received_at) == ("00123", "BTC-USDT", 100)
    assert json.loads(record.raw_json) == payload()
    record.taker = "detached"
    reopened = Repository(db_path=repo.engine.url.database)
    stored, = reopened.get_private_spot_trade_fees("BTC-USDT")
    assert stored.taker == "-0.00100000000000000001"
    assert (operational_state(master), operational_state(repo)) == before


async def test_identical_replay_does_not_refresh_and_other_request_is_retained_separately(stores):
    _, (repo, _) = stores
    bind(repo)
    exchange = RawExchange()
    first = await sync(repo, exchange, 100, inst_id="BTC-USDT", uid="00123")
    second = await sync(repo, exchange, 200, inst_id="BTC-USDT")
    third = await sync(repo, exchange, 300, inst_id="ETH-USDT")
    assert first.model_dump() == second.model_dump()
    assert third.payload_hash == first.payload_hash and third.requested_inst_id == "ETH-USDT"
    assert len(exchange.requests) == 3 and len(repo.get_private_spot_trade_fees()) == 2


async def test_missing_or_conflicting_uid_is_refused_before_transport(stores):
    _, (repo, _) = stores
    exchange = RawExchange()
    before = database_state(repo)
    with pytest.raises(ValueError, match="UID"):
        await sync(repo, exchange, 100, inst_id="BTC-USDT")
    assert database_state(repo) == before
    bind(repo)
    before = database_state(repo)
    with pytest.raises(ValueError, match="UID"):
        await sync(repo, exchange, 100, inst_id="BTC-USDT", uid="other")
    assert exchange.requests == [] and database_state(repo) == before


@pytest.mark.parametrize("at,inst_id,uid", [
    (-1, "BTC-USDT", None), (True, "BTC-USDT", None), (1.5, "BTC-USDT", None),
    ("100", "BTC-USDT", None), (None, "BTC-USDT", None), (2**63, "BTC-USDT", None),
    (100, "BTC/USDT", None), (100, "BTC-USDT-SWAP", None), (100, "", None),
    (100, "BTC-USDT", ""),
])
async def test_bad_context_is_refused_without_provider_work_or_row_changes(
    stores, at, inst_id, uid,
):
    _, (repo, _) = stores
    bind(repo)
    exchange = RawExchange()
    before = database_state(repo)
    with pytest.raises(ValueError):
        await sync(repo, exchange, at, inst_id=inst_id, uid=uid)
    assert exchange.requests == [] and database_state(repo) == before


async def test_provider_failure_propagates_once_without_writes_or_retries(stores):
    _, (repo, _) = stores
    bind(repo)
    exchange = RawExchange(ConnectionError("provider unavailable"))
    before = database_state(repo)
    with pytest.raises(ConnectionError, match="provider unavailable"):
        await sync(repo, exchange, 100, inst_id="BTC-USDT")
    assert len(exchange.requests) == 1 and database_state(repo) == before


@pytest.mark.parametrize("raw", [
    {"code": "1", "data": []}, {"code": "0", "data": []}, payload(instType="SWAP"),
    payload(maker=0.1), payload(feeGroup="bad"), payload(instId="ETH-USDT"),
])
async def test_bad_provider_response_never_commits(stores, raw):
    _, (repo, _) = stores
    bind(repo)
    exchange = RawExchange(raw)
    before = database_state(repo)
    with pytest.raises(MalformedProviderData):
        await sync(repo, exchange, 100, inst_id="BTC-USDT")
    assert len(exchange.requests) == 1 and database_state(repo) == before


async def test_cancellation_after_response_is_honored_before_commit(stores):
    _, (repo, _) = stores
    bind(repo)
    exchange = RawExchange()
    before = database_state(repo)

    async def collect():
        exchange.before_response = asyncio.current_task().cancel
        return await sync(repo, exchange, 100, inst_id="BTC-USDT")

    with pytest.raises(asyncio.CancelledError):
        await asyncio.create_task(collect())
    assert len(exchange.requests) == 1 and database_state(repo) == before


async def test_failed_commit_does_not_retry_or_leave_partial_rows(stores):
    _, (repo, _) = stores
    bind(repo)
    exchange = RawExchange()
    with repo.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER fail_fee_insert BEFORE INSERT ON private_spot_trade_fee "
            "BEGIN SELECT RAISE(ABORT, 'forced failure'); END",
        )
    before = database_state(repo)
    with pytest.raises(IntegrityError, match="forced failure"):
        await sync(repo, exchange, 100, inst_id="BTC-USDT")
    assert len(exchange.requests) == 1 and database_state(repo) == before
