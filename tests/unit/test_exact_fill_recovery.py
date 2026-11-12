"""Restart recovery validates complete committed local evidence before reuse."""

import json

import pytest
from sqlalchemy import text

from src.data.repository import Repository
from tests.unit.test_account_balance_store import database_state
from tests.unit.test_exact_facts_store import commit, payload
from tests.unit.test_exact_facts_store import stores as stores

CORRUPTIONS = [
    "UPDATE exact_spot_fill_page SET payload_hash='" + "a" * 64 + "'",
    "UPDATE exact_spot_fill_page SET raw_json='{bad'",
    "UPDATE exact_spot_fill_page SET page_id='wrong' WHERE requested_after IS NULL",
    "UPDATE exact_spot_fill_page SET traversal_id='wrong'",
    "UPDATE exact_spot_fill_page SET endpoint='wrong'",
    "UPDATE exact_spot_fill_page SET inst_id='ETH-USDT'",
    "UPDATE exact_spot_fill_page SET requested_before='wrong'",
    "UPDATE exact_spot_fill_page SET requested_after='gap' WHERE requested_after IS NOT NULL",
    "UPDATE exact_spot_fill_page SET next_after='wrong'",
    "UPDATE exact_spot_fill_page SET coverage='complete'",
    "UPDATE exact_spot_fill_page SET received_at='invalid'",
    "UPDATE exact_spot_fill_page SET account_scope_id='foreign'",
    "UPDATE exact_spot_fill_page SET uid='other'",
    "DELETE FROM exact_spot_fill_page WHERE requested_after IS NULL",
    "DELETE FROM exact_spot_fill_page",
    "UPDATE exact_spot_fill_cursor SET version=99",
    "UPDATE exact_spot_fill_cursor SET next_after='wrong'",
    "UPDATE exact_spot_fill_cursor SET endpoint='wrong'",
    "UPDATE exact_spot_fill_cursor SET inst_id='ETH-USDT'",
    "UPDATE exact_spot_fill_cursor SET requested_before='wrong'",
    "UPDATE exact_spot_fill_cursor SET traversal_id='wrong'",
    "UPDATE exact_spot_fill_cursor SET uid='other'",
    "DELETE FROM exact_spot_fill_cursor",
    "UPDATE exact_spot_fill SET fill_sz='1' WHERE bill_id='009'",
    "UPDATE exact_spot_fill SET record_time='1' WHERE bill_id='009'",
    "UPDATE exact_spot_fill SET symbol='ETH-USDT' WHERE bill_id='009'",
    "UPDATE exact_spot_fill SET cl_ord_id='',cl_ord_id_state='value' WHERE bill_id='009'",
    "UPDATE exact_spot_fill SET fee=NULL,fee_state='missing' WHERE bill_id='009'",
    "UPDATE exact_spot_fill SET account_scope_id='foreign' WHERE bill_id='009'",
    "UPDATE exact_spot_fill SET uid='other' WHERE bill_id='009'",
    "UPDATE exact_spot_fill SET bill_id='orphan' WHERE bill_id='009'",
    "DELETE FROM exact_spot_fill WHERE bill_id='009'",
    "DELETE FROM exact_spot_fill",
]


def seed(repo):
    commit(repo)
    commit(repo, payload("008"), after="009", version=1)


def corrupt(repo, statement):
    with repo.engine.begin() as connection:
        connection.execute(text(statement))


def read(repo, kind):
    if kind == "cursor":
        return repo.get_exact_spot_fill_cursor(uid="00123")
    if kind == "pages":
        return repo.get_exact_spot_fill_pages(uid="00123")
    return repo.get_exact_spot_fills(uid="00123")


@pytest.mark.parametrize("kind", ["cursor", "pages", "fills"])
@pytest.mark.parametrize("statement", CORRUPTIONS)
def test_each_read_rejects_corrupt_or_orphan_local_state_without_repair(stores, kind, statement):
    _, (repo, _) = stores
    seed(repo)
    corrupt(repo, statement)
    before = database_state(repo)
    reopened = Repository(db_path=repo.engine.url.database)
    with pytest.raises(ValueError):
        read(reopened, kind)
    assert database_state(repo) == before


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("statement", [CORRUPTIONS[i] for i in (0, 3, 14, 15, 22, 23, 24, 27, 32)])
def test_append_and_advanced_replay_both_validate_state_before_mutation(stores, replay, statement):
    _, (repo, _) = stores
    seed(repo)
    corrupt(repo, statement)
    before = database_state(repo)
    with pytest.raises(ValueError):
        if replay:
            commit(repo)
        else:
            commit(repo, payload("007"), after="008", version=2)
    assert database_state(repo) == before


@pytest.mark.parametrize("kind", ["cursor", "pages", "fills"])
def test_duplicate_raw_keys_are_not_hidden_by_json_last_value_wins(stores, kind):
    _, (repo, _) = stores
    commit(repo)
    raw = json.dumps(payload()).replace('"code": "0"', '"code": "1", "code": "0"')
    with repo.engine.begin() as connection:
        connection.execute(text("UPDATE exact_spot_fill_page SET raw_json=:raw"), {"raw": raw})
    before = database_state(repo)
    with pytest.raises(ValueError):
        read(repo, kind)
    assert database_state(repo) == before


def test_new_traversal_cannot_start_at_an_unproven_non_root_cursor(stores):
    _, (repo, _) = stores
    before = database_state(repo)
    with pytest.raises(ValueError, match="cursor|root|initial"):
        commit(repo, after="external-seed")
    assert database_state(repo) == before and repo.get_private_uid() is None


def test_valid_advanced_replay_and_empty_tail_never_claim_completeness(stores):
    _, (repo, _) = stores
    seed(repo)
    commit(repo, {"code": "0", "data": []}, after="008", version=2)
    before = database_state(repo)
    reopened = Repository(db_path=repo.engine.url.database)
    cursor = read(reopened, "cursor")
    assert (cursor.version, cursor.next_after, cursor.complete) == (3, None, False)
    replay = commit(reopened)
    assert replay == cursor
    assert {fact.bill_id for fact in read(reopened, "fills")} == {"009", "008"}
    assert len(read(reopened, "pages")) == 3
    assert database_state(repo) == before


def test_corrupt_other_traversal_cannot_hide_behind_a_valid_requested_cursor(stores):
    _, (repo, _) = stores
    commit(repo)
    commit(repo, payload("007"), inst_id="BTC-USDT", before="upper")
    corrupt(repo, "UPDATE exact_spot_fill_page SET payload_hash='bad' WHERE inst_id IS NOT NULL")
    with pytest.raises(ValueError):
        read(repo, "cursor")
