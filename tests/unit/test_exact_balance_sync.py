"""Exactly one raw balance response is committed without operational effects."""

import asyncio
import json

import pytest

from src.exchange.errors import MalformedProviderData
from tests.unit.test_account_balance_store import bind, database_state
from tests.unit.test_account_balance_store import stores as stores
from tests.unit.test_okx_balance_facts import RawBalanceExchange, balance_payload


@pytest.fixture
def sync():
    async def synchronize(*args, **kwargs):
        from src.ops.exact_private_sync import sync_exact_account_balance

        return await sync_exact_account_balance(*args, **kwargs)

    return synchronize


async def test_single_raw_sync_persists_same_response_and_keeps_legacy_state(stores, sync):
    master, (repo, _) = stores
    bind(repo)
    before = [database_state(r, operational_only=True) for r in (master, repo)]
    raw = balance_payload()
    exchange = RawBalanceExchange(raw)
    result = await sync(repo, exchange, 1234)
    assert exchange.requests == [{}]
    assert result.uid == "00123" and result.received_at == 1234
    assert json.loads(result.raw_json) == raw
    assert [database_state(r, operational_only=True) for r in (master, repo)] == before


@pytest.mark.parametrize("response,error", [
    (OSError("offline"), OSError), (asyncio.CancelledError(), asyncio.CancelledError),
    ({"code": "0", "data": [None]}, MalformedProviderData),
    (balance_payload(headers={"apiSecretKey": "secret"}), ValueError),
])
async def test_provider_parser_and_storage_validation_failures_preserve_all_state(
    stores, sync, response, error,
):
    master, (repo, _) = stores
    bind(repo)
    await sync(repo, RawBalanceExchange(balance_payload()), 1)
    before = database_state(master), database_state(repo)
    exchange = RawBalanceExchange(response)
    with pytest.raises(error):
        await sync(repo, exchange, 2)
    assert (database_state(master), database_state(repo)) == before
    assert exchange.requests == [{}]


async def test_storage_failure_rolls_back_observation(stores, sync, monkeypatch):
    _, (repo, _) = stores
    bind(repo)
    before = database_state(repo)
    atomic = repo.atomic_accounting

    def interrupted(callback):
        def fail_after_writes(tx):
            callback(tx)
            raise RuntimeError("storage interrupted")
        return atomic(fail_after_writes)

    monkeypatch.setattr(repo, "atomic_accounting", interrupted)
    with pytest.raises(RuntimeError, match="storage interrupted"):
        await sync(repo, RawBalanceExchange(balance_payload()), 1)
    assert database_state(repo) == before


async def test_pending_cancellation_after_raw_response_prevents_commit(stores, sync):
    _, (repo, _) = stores
    bind(repo)
    before = database_state(repo)
    exchange = RawBalanceExchange(balance_payload())
    exchange.before_response = lambda: asyncio.current_task().cancel()
    task = asyncio.create_task(sync(repo, exchange, 1))
    with pytest.raises(asyncio.CancelledError):
        await task
    assert database_state(repo) == before


@pytest.mark.parametrize("uid", [None, "other"])
async def test_unbound_sync_never_fetches_or_binds_uid(stores, sync, uid):
    _, (repo, _) = stores
    exchange = RawBalanceExchange(balance_payload())
    before = database_state(repo)
    with pytest.raises(ValueError, match="UID"):
        await sync(repo, exchange, 1, uid=uid)
    assert exchange.requests == [] and database_state(repo) == before


async def test_explicit_uid_cannot_override_existing_binding(stores, sync):
    _, (repo, _) = stores
    bind(repo)
    exchange = RawBalanceExchange(balance_payload())
    with pytest.raises(ValueError, match="UID"):
        await sync(repo, exchange, 1, uid="other")
    assert exchange.requests == []
