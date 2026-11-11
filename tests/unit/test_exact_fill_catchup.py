"""Bounded exact catch-up keeps each committed page independent of later failure."""

import asyncio
from dataclasses import FrozenInstanceError

import pytest

from src.data.repository import Repository
from src.exchange.errors import MalformedProviderData
from src.ops import exact_private_sync as sync
from tests.unit.test_exact_facts_store import payload
from tests.unit.test_exact_private_sync import RawExchange, operational_state
from tests.unit.test_exact_private_sync import stores as stores


async def test_two_nonempty_pages_and_empty_tail_commit_sequentially(stores):
    master, repo = stores
    before = [operational_state(r) for r in (master, repo)]
    exchange = RawExchange(payload("009"), payload("008"), {"code": "0", "data": []})
    times = iter((100, 200, 300))
    result = await sync.catch_up_exact_spot_fills(
        repo, exchange, uid="00123", received_at_factory=lambda: next(times),
    )
    assert exchange.requests == [
        ("fills", {"instType": "SPOT", "limit": "100"}),
        ("fills", {"instType": "SPOT", "limit": "100", "after": "009"}),
        ("fills", {"instType": "SPOT", "limit": "100", "after": "008"}),
    ]
    assert (result.pages_committed, result.coverage, result.stop_reason) == (
        3, "unknown", "empty_page",
    )
    assert (result.last_cursor.version, result.last_cursor.next_after) == (3, None)
    assert result.last_cursor.complete is False
    reopened = Repository(engine=repo.engine)
    assert {f.bill_id for f in reopened.get_exact_spot_fills(uid="00123")} == {"009", "008"}
    assert [p.received_at for p in reopened.get_exact_spot_fill_pages(uid="00123")] == [
        100, 200, 300,
    ]
    assert reopened.get_exact_spot_fill_cursor(uid="00123").version == 3
    assert [operational_state(r) for r in (master, repo)] == before
    with pytest.raises(FrozenInstanceError):
        result.pages_committed = 99
    with pytest.raises(FrozenInstanceError):
        result.last_cursor.version = 99


@pytest.mark.parametrize("max_pages", [1, 2])
async def test_short_nonempty_pages_continue_until_exact_page_bound(stores, max_pages):
    _, repo = stores
    exchange = RawExchange(payload("009"), payload("008"), payload("007"))
    result = await sync.catch_up_exact_spot_fills(
        repo, exchange, uid="00123", max_pages=max_pages, received_at_factory=2**63 - 1,
    )
    assert len(exchange.requests) == max_pages
    assert (result.pages_committed, result.coverage, result.stop_reason) == (
        max_pages, "partial", "max_pages",
    )
    assert (result.last_cursor.version, result.last_cursor.next_after) == (
        max_pages, "009" if max_pages == 1 else "008",
    )
    assert all(p.received_at == 2**63 - 1 for p in repo.get_exact_spot_fill_pages(uid="00123"))


@pytest.mark.parametrize("changes", [
    {"max_pages": 0}, {"max_pages": -1}, {"max_pages": True}, {"max_pages": 1.5},
    {"max_pages": "10"}, {"max_pages": None},
    {"limit": 0}, {"limit": 101}, {"limit": True}, {"limit": "10"},
    {"uid": ""}, {"uid": True}, {"inst_id": "BTC/USDT"}, {"before": ""},
    {"received_at_factory": -1}, {"received_at_factory": True},
    {"received_at_factory": 2**63}, {"received_at_factory": "1"},
    {"received_at_factory": None}, {"received_at_factory": 1.5},
])
async def test_invalid_arguments_fail_before_any_provider_call_or_write(stores, changes):
    _, repo = stores
    exchange = RawExchange(payload())
    args = {"uid": "00123", "received_at_factory": 0, **changes}
    with pytest.raises(ValueError):
        await sync.catch_up_exact_spot_fills(repo, exchange, **args)
    assert exchange.requests == []
    assert repo.get_private_uid() is None


async def test_filter_bound_cursor_resumes_across_reopen_and_preserves_other_traversal(stores):
    _, repo = stores
    await sync.sync_exact_spot_fill_page(
        repo, RawExchange(payload("011")), uid="00123", received_at=0,
    )
    await sync.sync_exact_spot_fill_page(
        repo, RawExchange(payload("010")), uid="00123", inst_id="BTC-USDT",
        before="upper-bound", received_at=0,
    )
    reopened = Repository(engine=repo.engine)
    exchange = RawExchange(payload("009"), payload("008"))
    result = await sync.catch_up_exact_spot_fills(
        reopened, exchange, uid="00123", inst_id="BTC-USDT", before="upper-bound",
        limit=37, max_pages=2, received_at_factory=100,
    )
    assert exchange.requests == [
        ("fills", {"instType": "SPOT", "limit": "37", "instId": "BTC-USDT",
                   "before": "upper-bound", "after": "010"}),
        ("fills", {"instType": "SPOT", "limit": "37", "instId": "BTC-USDT",
                   "before": "upper-bound", "after": "009"}),
    ]
    assert (result.last_cursor.uid, result.last_cursor.inst_id,
            result.last_cursor.requested_before) == ("00123", "BTC-USDT", "upper-bound")
    assert (result.last_cursor.version, result.last_cursor.next_after) == (3, "008")
    assert reopened.get_exact_spot_fill_cursor(uid="00123").next_after == "011"
    with pytest.raises(ValueError, match="UID"):
        await sync.catch_up_exact_spot_fills(
            reopened, exchange, uid="other", received_at_factory=100,
        )
    assert len(exchange.requests) == 2


@pytest.mark.parametrize("response,error", [
    (OSError("offline"), OSError),
    ({"code": "0", "data": [None]}, MalformedProviderData),
    (asyncio.CancelledError(), asyncio.CancelledError),
])
async def test_later_provider_failure_preserves_prior_committed_page(stores, response, error):
    _, repo = stores
    exchange = RawExchange(payload("009"), response)
    with pytest.raises(error):
        await sync.catch_up_exact_spot_fills(
            repo, exchange, uid="00123", received_at_factory=100,
        )
    reopened = Repository(engine=repo.engine)
    assert len(exchange.requests) == 2
    assert [f.bill_id for f in reopened.get_exact_spot_fills(uid="00123")] == ["009"]
    assert reopened.get_exact_spot_fill_cursor(uid="00123").version == 1
    assert len(reopened.get_exact_spot_fill_pages(uid="00123")) == 1


async def test_pending_cancellation_prevents_second_page_commit(stores):
    _, repo = stores
    exchange = RawExchange(payload("009"), payload("008"))

    def cancel_second_response():
        if len(exchange.requests) == 2:
            asyncio.current_task().cancel()

    exchange.before_response = cancel_second_response
    task = asyncio.create_task(sync.catch_up_exact_spot_fills(
        repo, exchange, uid="00123", received_at_factory=100,
    ))
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(exchange.requests) == 2
    assert repo.get_exact_spot_fill_cursor(uid="00123").next_after == "009"
    assert len(repo.get_exact_spot_fill_pages(uid="00123")) == 1


async def test_second_page_storage_failure_rolls_back_only_that_page(stores, monkeypatch):
    _, repo = stores
    exchange = RawExchange(payload("009"), payload("008"))
    atomic = repo.atomic_accounting
    commits = 0

    def interrupted(callback):
        nonlocal commits
        commits += 1

        def fail_second(transaction):
            result = callback(transaction)
            if commits == 2:
                raise RuntimeError("storage interrupted")
            return result

        return atomic(fail_second)

    monkeypatch.setattr(repo, "atomic_accounting", interrupted)
    with pytest.raises(RuntimeError, match="storage interrupted"):
        await sync.catch_up_exact_spot_fills(
            repo, exchange, uid="00123", received_at_factory=100,
        )
    assert len(exchange.requests) == 2
    assert repo.get_exact_spot_fill_cursor(uid="00123").next_after == "009"
    assert [f.bill_id for f in repo.get_exact_spot_fills(uid="00123")] == ["009"]
    assert len(repo.get_exact_spot_fill_pages(uid="00123")) == 1


@pytest.mark.parametrize("bad_time", [-1, True, 2**63, "200", None])
async def test_invalid_factory_timestamp_preserves_prior_page_without_next_request(
    stores, bad_time,
):
    _, repo = stores
    times = iter((0, bad_time))
    exchange = RawExchange(payload("009"), payload("008"))
    with pytest.raises(ValueError, match="received"):
        await sync.catch_up_exact_spot_fills(
            repo, exchange, uid="00123", received_at_factory=lambda: next(times),
        )
    assert len(exchange.requests) == 1
    assert repo.get_exact_spot_fill_cursor(uid="00123").next_after == "009"
    assert [p.received_at for p in repo.get_exact_spot_fill_pages(uid="00123")] == [0]


@pytest.mark.parametrize("field,value", [("version", 1), ("next_after", "009")])
async def test_nonadvancing_commit_cursor_fails_before_another_request(
    stores, monkeypatch, field, value,
):
    _, repo = stores
    await sync.sync_exact_spot_fill_page(
        repo, RawExchange(payload("009")), uid="00123", received_at=0,
    )
    commit = repo.commit_exact_spot_fill_page

    def stale_result(*args, **kwargs):
        cursor = commit(*args, **kwargs)
        setattr(cursor, field, value)
        return cursor

    monkeypatch.setattr(repo, "commit_exact_spot_fill_page", stale_result)
    exchange = RawExchange(payload("008"), payload("007"))
    with pytest.raises(ValueError, match="cursor"):
        await sync.catch_up_exact_spot_fills(
            repo, exchange, uid="00123", received_at_factory=100,
        )
    assert len(exchange.requests) == 1
    assert repo.get_exact_spot_fill_cursor(uid="00123").next_after == "008"


async def test_stale_durable_cursor_read_fails_before_second_request(stores, monkeypatch):
    _, repo = stores
    await sync.sync_exact_spot_fill_page(
        repo, RawExchange(payload("009")), uid="00123", received_at=0,
    )
    stale = repo.get_exact_spot_fill_cursor(uid="00123")
    monkeypatch.setattr(repo, "get_exact_spot_fill_cursor", lambda **kwargs: stale)
    exchange = RawExchange(payload("008"), payload("007"))
    with pytest.raises(ValueError, match="cursor"):
        await sync.catch_up_exact_spot_fills(
            repo, exchange, uid="00123", received_at_factory=100,
        )
    assert len(exchange.requests) == 1
    assert Repository(engine=repo.engine).get_exact_spot_fill_cursor(uid="00123").version == 2


async def test_persisted_cursor_cycle_fails_without_following_cycle(stores):
    _, repo = stores
    exchange = RawExchange(payload("009"), payload("008"), payload("009"), payload("007"))
    with pytest.raises(MalformedProviderData, match="cursor|duplicate"):
        await sync.catch_up_exact_spot_fills(
            repo, exchange, uid="00123", received_at_factory=100,
        )
    assert len(exchange.requests) == 3
    assert repo.get_exact_spot_fill_cursor(uid="00123").next_after == "008"
    assert len(repo.get_exact_spot_fill_pages(uid="00123")) == 2
