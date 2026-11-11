"""Raw ingestion resumes durable cursors without affecting operational state."""

import asyncio
import json

import pytest
from sqlalchemy import inspect

from src.core.execution_scope import ExecutionScope
from src.data.exchange_store import open_exchange_repository
from src.data.repository import Repository
from src.exchange.errors import MalformedProviderData
from src.ops.exact_private_sync import (
    sync_exact_account_config as sync_config,
)
from src.ops.exact_private_sync import (
    sync_exact_spot_fill_page as sync_fill,
)
from tests.unit.test_exact_facts_store import payload as fill_payload
from tests.unit.test_okx_account_facts import config_payload


@pytest.fixture
def stores(tmp_path):
    master = Repository(db_path=str(tmp_path / "master.db"))
    scope = ExecutionScope("okx", "sandbox", "a" * 64)
    return master, open_exchange_repository(scope, master_engine=master.engine)


class RawExchange:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.before_response = None

    async def _respond(self, method, params):
        self.requests.append((method, params.copy()))
        if self.before_response is not None:
            self.before_response()
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    async def private_get_account_config(self, params):
        return await self._respond("config", params)

    async def private_get_trade_fills_history(self, params):
        return await self._respond("fills", params)

    async def fetch_balance(self, *args, **kwargs):
        raise AssertionError("Unified balance must not supply exact facts")

    async def fetch_my_trades(self, *args, **kwargs):
        raise AssertionError("Unified trades must not supply exact facts")


def operational_state(repo):
    """Compare all compatibility, ledger, receipt and readiness tables."""
    exact_tables = {
        "private_uid_binding", "private_account_config", "exact_spot_fill",
        "exact_spot_fill_page", "exact_spot_fill_cursor",
    }
    with repo.engine.connect() as connection:
        return {
            name: connection.exec_driver_sql(f'SELECT * FROM "{name}" ORDER BY rowid').all()
            for name in inspect(repo.engine).get_table_names() if name not in exact_tables
        }


def exact_state(repo):
    return (
        repo.get_private_uid(),
        [r.model_dump() for r in repo.get_private_account_config()],
        repo.get_exact_spot_fills(uid="00123"),
        [r.model_dump() for r in repo.get_exact_spot_fill_pages(uid="00123")],
        repo.get_exact_spot_fill_cursor(uid="00123"),
    )


async def test_config_binds_raw_uid_and_returns_detached_durable_evidence(stores):
    master, repo = stores
    before = [operational_state(r) for r in (master, repo)]
    raw = config_payload(mainUid="different", autoLoan=None, enableSpotBorrow="")
    exchange = RawExchange(raw)
    result = await sync_config(repo, exchange, 1234)
    assert exchange.requests == [("config", {})]
    assert result.uid == repo.get_private_uid() == "00123"
    assert result.received_at == 1234 and json.loads(result.raw_json) == raw
    assert (result.auto_loan_state, result.enable_spot_borrow) == ("null", "")
    result.uid = "detached"
    reopened = Repository(db_path=repo.engine.url.database)
    assert reopened.get_private_account_config()[0].uid == "00123"
    assert [operational_state(r) for r in (master, repo)] == before


async def test_fill_resumes_tail_and_version_after_repository_restart(stores):
    master, repo = stores
    before = [operational_state(r) for r in (master, repo)]
    exchange = RawExchange(fill_payload("009"), fill_payload("008"))
    first = await sync_fill(repo, exchange, uid="00123", received_at=100)
    assert (first.version, first.next_after, first.complete) == (1, "009", False)
    reopened = Repository(db_path=repo.engine.url.database)
    second = await sync_fill(reopened, exchange, uid="00123", received_at=200)
    assert exchange.requests == [
        ("fills", {"instType": "SPOT", "limit": "100"}),
        ("fills", {"instType": "SPOT", "limit": "100", "after": "009"}),
    ]
    assert (second.version, second.next_after, second.complete) == (2, "008", False)
    assert {f.bill_id for f in reopened.get_exact_spot_fills(uid="00123")} == {"009", "008"}
    pages = reopened.get_exact_spot_fill_pages(uid="00123")
    assert [p.received_at for p in pages] == [100, 200]
    assert json.loads(pages[1].raw_json) == fill_payload("008")
    second.version = 99
    assert reopened.get_exact_spot_fill_cursor(uid="00123").version == 2
    assert [operational_state(r) for r in (master, repo)] == before


async def test_instrument_and_before_filters_bind_separate_traversals(stores):
    _, repo = stores
    exchange = RawExchange(*(fill_payload(bill) for bill in ("009", "008", "007", "006")))
    for inst_id, before in [(None, None), ("BTC-USDT", None), ("BTC-USDT", "bound-A")]:
        cursor = await sync_fill(
            repo, exchange, uid="00123", inst_id=inst_id, before=before,
            limit=37, received_at=100,
        )
        assert cursor.version == 1
    cursor = await sync_fill(
        repo, exchange, uid="00123", inst_id="BTC-USDT", before="bound-A",
        limit=37, received_at=200,
    )
    assert exchange.requests == [
        ("fills", {"instType": "SPOT", "limit": "37"}),
        ("fills", {"instType": "SPOT", "limit": "37", "instId": "BTC-USDT"}),
        ("fills", {"instType": "SPOT", "limit": "37", "instId": "BTC-USDT", "before": "bound-A"}),
        ("fills", {"instType": "SPOT", "limit": "37", "instId": "BTC-USDT",
                   "before": "bound-A", "after": "007"}),
    ]
    assert (cursor.version, cursor.next_after) == (2, "006")
    assert repo.get_exact_spot_fill_cursor(uid="00123").next_after == "009"
    assert repo.get_exact_spot_fill_cursor(uid="00123", inst_id="BTC-USDT").next_after == "008"


@pytest.mark.parametrize("response,error", [
    ({"code": "0", "data": [None]}, MalformedProviderData),
    (OSError("offline"), OSError),
    (asyncio.CancelledError(), asyncio.CancelledError),
])
@pytest.mark.parametrize("kind", ["config", "fills"])
async def test_failed_fetch_does_not_change_any_durable_state(
    stores, response, error, kind,
):
    master, repo = stores
    await sync_config(repo, RawExchange(config_payload()), 1)
    await sync_fill(repo, RawExchange(fill_payload()), uid="00123", received_at=2)
    before = exact_state(repo), operational_state(master), operational_state(repo)
    exchange = RawExchange(response)
    with pytest.raises(error):
        if kind == "config":
            await sync_config(repo, exchange, 3)
        else:
            await sync_fill(repo, exchange, uid="00123", received_at=3)
    assert (exact_state(repo), operational_state(master), operational_state(repo)) == before
    assert len(exchange.requests) == 1


@pytest.mark.parametrize("kind", ["config", "fills"])
async def test_storage_exception_rolls_back_all_ingestion(stores, kind, monkeypatch):
    _, repo = stores
    before = exact_state(repo), operational_state(repo)
    atomic = repo.atomic_accounting

    def interrupted(callback):
        def fail_after_writes(transaction):
            callback(transaction)
            raise RuntimeError("storage interrupted")
        return atomic(fail_after_writes)

    monkeypatch.setattr(repo, "atomic_accounting", interrupted)
    with pytest.raises(RuntimeError, match="storage interrupted"):
        if kind == "config":
            await sync_config(repo, RawExchange(config_payload()), 1)
        else:
            await sync_fill(repo, RawExchange(fill_payload()), uid="00123", received_at=1)
    assert (exact_state(repo), operational_state(repo)) == before


async def test_concurrent_cursor_advance_rejects_stale_response(stores):
    _, repo = stores
    await sync_fill(repo, RawExchange(fill_payload("009")), uid="00123", received_at=1)
    fetched, release = asyncio.Event(), asyncio.Event()

    class DelayedExchange(RawExchange):
        async def private_get_trade_fills_history(self, params):
            fetched.set()
            await release.wait()
            return await super().private_get_trade_fills_history(params)

    delayed = DelayedExchange(fill_payload("007"))
    stale = asyncio.create_task(sync_fill(repo, delayed, uid="00123", received_at=2))
    await fetched.wait()
    await sync_fill(repo, RawExchange(fill_payload("008")), uid="00123", received_at=3)
    before = exact_state(repo)
    release.set()
    with pytest.raises(ValueError, match="conflict"):
        await stale
    assert exact_state(repo) == before
    assert delayed.requests == [("fills", {"instType": "SPOT", "limit": "100", "after": "009"})]


async def test_conflicting_financial_fact_rolls_back_page_and_cursor(stores):
    _, repo = stores
    first = fill_payload("010")
    first["data"].append(fill_payload("009")["data"][0])
    await sync_fill(repo, RawExchange(first), uid="00123", received_at=1)
    before = exact_state(repo)
    conflict = fill_payload("008")
    conflict["data"].append(fill_payload("010", fee="-999")["data"][0])
    with pytest.raises(ValueError, match="conflict"):
        await sync_fill(repo, RawExchange(conflict), uid="00123", received_at=2)
    assert exact_state(repo) == before


@pytest.mark.parametrize("kind", ["config", "fills"])
async def test_pending_cancellation_after_provider_response_prevents_commit(
    stores, kind,
):
    _, repo = stores
    exchange = RawExchange(config_payload() if kind == "config" else fill_payload())
    exchange.before_response = lambda: asyncio.current_task().cancel()
    before = exact_state(repo)
    if kind == "config":
        coroutine = sync_config(repo, exchange, 1)
    else:
        coroutine = sync_fill(repo, exchange, uid="00123", received_at=1)
    task = asyncio.create_task(coroutine)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert exact_state(repo) == before


async def test_empty_page_is_one_unknown_observation_without_auto_loop(stores):
    _, repo = stores
    exchange = RawExchange({"code": "0", "data": []})
    cursor = await sync_fill(repo, exchange, uid="00123", received_at=1)
    assert cursor.next_after is None and cursor.complete is False and cursor.version == 1
    assert len(exchange.requests) == 1
    assert repo.get_exact_spot_fill_pages(uid="00123")[0].coverage == "unknown"


@pytest.mark.parametrize("limit", [True, 0, 101, "100"])
async def test_bad_limit_fails_before_provider_or_persistence(stores, limit):
    _, repo = stores
    exchange = RawExchange(fill_payload())
    with pytest.raises(ValueError):
        await sync_fill(repo, exchange, uid="00123", limit=limit, received_at=1)
    assert exchange.requests == [] and repo.get_private_uid() is None
