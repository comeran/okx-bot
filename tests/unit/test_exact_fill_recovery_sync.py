"""Recovery guards the network boundary and rechecks the response commit."""

import sqlite3
from contextlib import closing

import pytest
from sqlalchemy import event
from sqlmodel import create_engine

from src.core.private_facts import MISSING
from src.data.repository import Repository
from src.exchange.okx_private_facts import parse_okx_spot_fill_page
from src.ops.exact_private_sync import catch_up_exact_spot_fills, sync_exact_spot_fill_page
from tests.unit.test_account_balance_store import database_state
from tests.unit.test_exact_facts_store import commit, payload
from tests.unit.test_exact_facts_store import stores as stores
from tests.unit.test_exact_fill_recovery import corrupt, read, seed
from tests.unit.test_exact_private_sync import RawExchange

CORRUPTIONS = (
    "UPDATE exact_spot_fill_cursor SET next_after='wrong'",
    "UPDATE exact_spot_fill_page SET payload_hash='bad'",
    "UPDATE exact_spot_fill SET fill_sz='2'",
)


@pytest.mark.parametrize("catch_up", [False, True])
@pytest.mark.parametrize("statement", CORRUPTIONS)
async def test_reopened_sync_rejects_corruption_before_any_provider_request(
    stores, catch_up, statement,
):
    _, (repo, _) = stores
    seed(repo)
    corrupt(repo, statement)
    before = database_state(repo)
    reopened = Repository(db_path=repo.engine.url.database)
    provider = RawExchange(payload("007"))
    with pytest.raises(ValueError):
        if catch_up:
            await catch_up_exact_spot_fills(
                reopened, provider, uid="00123", received_at_factory=100,
            )
        else:
            await sync_exact_spot_fill_page(reopened, provider, uid="00123", received_at=100)
    assert provider.requests == []
    assert database_state(repo) == before


@pytest.mark.parametrize("statement", CORRUPTIONS)
async def test_corruption_after_cursor_capture_is_rechecked_before_commit(stores, statement):
    _, (repo, _) = stores
    seed(repo)
    provider = RawExchange(payload("007"))
    after_corruption = []

    def corrupt_during_transport():
        corrupt(repo, statement)
        after_corruption.append(database_state(repo))

    provider.before_response = corrupt_during_transport
    with pytest.raises(ValueError):
        await sync_exact_spot_fill_page(repo, provider, uid="00123", received_at=100)
    assert len(provider.requests) == 1
    assert database_state(repo) == after_corruption[0]


@pytest.mark.parametrize("memory", [False, True])
@pytest.mark.parametrize("kind", ["cursor", "pages", "fills"])
def test_recovery_selects_share_one_actual_sqlite_transaction(stores, memory, kind):
    _, (repo, _) = stores
    seed(repo)
    if memory:
        in_memory = Repository(engine=create_engine("sqlite:///:memory:"))
        # Copy the real validated scoped database, including its immutable header.
        with closing(sqlite3.connect(repo.engine.url.database)) as source:
            with closing(in_memory.engine.raw_connection()) as target:
                source.backup(target.driver_connection)
        repo = in_memory
    before = database_state(repo)
    transactions = set()

    def capture(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            assert connection.in_transaction()
            assert connection.connection.in_transaction
            transactions.add(id(connection.get_transaction()))

    event.listen(repo.engine, "before_cursor_execute", capture)
    try:
        read(repo, kind)
    finally:
        event.remove(repo.engine, "before_cursor_execute", capture)
    assert len(transactions) == 1 and database_state(repo) == before


def test_interleaved_traversals_and_overlap_preserve_first_metadata_by_insertion(stores):
    _, (repo, _) = stores
    first = payload("009", ts="100", clOrdId=None)
    page = parse_okx_spot_fill_page(first)
    repo.commit_exact_spot_fill_page(page, uid="00123", raw_payload=first, received_at=300)
    # Same financial fill, different later metadata and an earlier receipt clock.
    other = payload("009", ts="200", clOrdId="later-client-tag")
    page = parse_okx_spot_fill_page(other, inst_id="BTC-USDT", before="upper")
    repo.commit_exact_spot_fill_page(page, uid="00123", raw_payload=other, received_at=100)
    # A request boundary itself cannot be repeated, so use another observed bill
    # for a legal overlap while its tail continues to a new identity.
    root = payload("011")
    root["data"].append(payload("010")["data"][0])
    commit(repo, root, before="other-upper")
    overlap = payload("011", ts="400", clOrdId="changed")
    overlap["data"].append(payload("007")["data"][0])
    commit(repo, overlap, after="010", before="other-upper", version=1)
    before = database_state(repo)
    reopened = Repository(db_path=repo.engine.url.database)
    facts = {fact.bill_id: fact for fact in reopened.get_exact_spot_fills(uid="00123")}
    assert (facts["009"].record_time, facts["009"].cl_ord_id) == ("100", None)
    assert facts["011"].cl_ord_id is MISSING
    assert facts["011"].record_time == "001700000000123"
    assert read(reopened, "cursor").next_after == "009"
    assert len(read(reopened, "pages")) == 4
    assert database_state(repo) == before
