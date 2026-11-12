"""Account-scoped exact SPOT order observations are append-only and detached."""

import hashlib
import json
from dataclasses import replace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.data.models import PrivateSpotOrderRecord
from src.data.repository import Repository
from src.exchange.okx_order_facts import parse_okx_spot_order
from tests.unit.test_account_balance_store import bind, database_state
from tests.unit.test_account_balance_store import stores as stores
from tests.unit.test_okx_order_facts import envelope

ORD_ID = "00012345678901234567"
CLIENT_ID = "client-order-0001"


def operational_state(repo):
    return {
        name: rows
        for name, rows in database_state(repo).items()
        if name != "private_spot_order"
    }


def commit(repo, raw=None, *, received_at=100, lookup_kind="ordId", lookup_value=ORD_ID,
           uid=None):
    raw = envelope() if raw is None else raw
    decoded = json.loads(raw) if isinstance(raw, str) else raw
    fact = parse_okx_spot_order(
        decoded,
        inst_id="BTC-USDT",
        ord_id=lookup_value if lookup_kind == "ordId" else None,
        cl_ord_id=lookup_value if lookup_kind == "clOrdId" else None,
    )
    return repo.commit_private_spot_order(
        fact,
        raw_payload=raw,
        received_at=received_at,
        lookup_kind=lookup_kind,
        lookup_value=lookup_value,
        uid=uid,
    )


def test_roundtrip_preserves_raw_hash_identity_and_detached_reads(stores):
    _, (repo, _) = stores
    bind(repo)
    raw = envelope()
    raw_text = json.dumps(raw, indent=2)
    before = operational_state(repo)

    record = commit(repo, raw_text)

    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    assert record.uid == "00123"
    assert record.requested_inst_id == "BTC-USDT"
    assert (record.lookup_kind, record.lookup_value) == ("ordId", ORD_ID)
    assert record.raw_json == raw_text
    assert record.payload_hash == hashlib.sha256(canonical.encode()).hexdigest()
    assert (record.ord_id, record.state, record.cl_ord_id) == (
        ORD_ID, "partially_filled", CLIENT_ID,
    )

    record.state = "filled"
    record.raw_json = "detached"
    reopened = Repository(db_path=repo.engine.url.database)
    stored, = reopened.get_private_spot_orders(
        "BTC-USDT", uid="00123", lookup_kind="ordId", lookup_value=ORD_ID,
    )
    assert stored.state == "partially_filled"
    assert stored.raw_json == raw_text
    assert operational_state(repo) == before


def test_identical_replay_is_idempotent_and_later_state_appends(stores):
    _, (repo, _) = stores
    bind(repo)
    first = commit(repo, json.dumps(envelope(), indent=2), received_at=100)
    replay = commit(repo, envelope(), received_at=200)
    assert replay.model_dump() == first.model_dump()
    assert replay.received_at == 100

    changed_raw = envelope(
        state="filled", accFillSz="0.010000000000000001", avgPx="100.099999999999999999",
    )
    changed = commit(repo, changed_raw, received_at=300)
    assert changed.payload_hash != first.payload_hash
    history = repo.get_private_spot_orders(
        "BTC-USDT", uid="00123", lookup_kind="ordId", lookup_value=ORD_ID,
    )
    assert [record.received_at for record in history] == [100, 300]
    assert [record.state for record in history] == ["partially_filled", "filled"]

    by_client = commit(
        repo, envelope(), received_at=400,
        lookup_kind="clOrdId", lookup_value=CLIENT_ID,
    )
    assert by_client.lookup_kind == "clOrdId"
    assert len(repo.get_private_spot_orders("BTC-USDT", uid="00123")) == 3


def test_existing_uid_is_required_and_scope_isolation_is_fail_closed(stores):
    master, (left, right) = stores
    before = database_state(left)
    with pytest.raises(ValueError, match="UID"):
        commit(left)
    assert database_state(left) == before and left.get_private_uid() is None

    bind(left, "00123")
    bind(right, "00456")
    before_operational = [operational_state(left), operational_state(right)]
    commit(left)
    commit(right, envelope(ordId=ORD_ID), uid="00456")
    assert {record.uid for record in left.get_private_spot_orders()} == {"00123"}
    assert {record.uid for record in right.get_private_spot_orders()} == {"00456"}
    with pytest.raises(ValueError, match="UID"):
        commit(left, uid="00456")
    with pytest.raises(ValueError, match="facts store"):
        commit(master)
    assert [operational_state(left), operational_state(right)] == before_operational


@pytest.mark.parametrize("received_at", [-1, True, 1.5, "1", None, 2**63])
def test_invalid_received_at_rolls_back_before_order_write(stores, received_at):
    _, (repo, _) = stores
    bind(repo)
    before = database_state(repo)
    with pytest.raises(ValueError):
        commit(repo, received_at=received_at)
    assert database_state(repo) == before


@pytest.mark.parametrize("lookup_kind,lookup_value", [
    ("ordID", ORD_ID), ("clOrdId", ""), ("ordId", 1), ("ordId", None),
])
def test_invalid_lookup_context_rolls_back_before_order_write(stores, lookup_kind, lookup_value):
    _, (repo, _) = stores
    bind(repo)
    raw = envelope()
    fact = parse_okx_spot_order(raw, inst_id="BTC-USDT", ord_id=ORD_ID)
    before = database_state(repo)
    with pytest.raises((TypeError, ValueError)):
        repo.commit_private_spot_order(
            fact, raw_payload=raw, received_at=100,
            lookup_kind=lookup_kind, lookup_value=lookup_value,
        )
    assert database_state(repo) == before


def test_raw_hash_and_typed_fact_mismatch_is_rejected_atomically(stores):
    _, (repo, _) = stores
    bind(repo)
    raw = envelope()
    fact = parse_okx_spot_order(raw, inst_id="BTC-USDT", ord_id=ORD_ID)
    before = database_state(repo)
    with pytest.raises(ValueError, match="typed"):
        repo.commit_private_spot_order(
            replace(fact, state="filled"), raw_payload=raw, received_at=100,
            lookup_kind="ordId", lookup_value=ORD_ID,
        )
    altered = envelope(state="filled", accFillSz="0.010000000000000001")
    with pytest.raises(ValueError):
        repo.commit_private_spot_order(
            fact, raw_payload=altered, received_at=100,
            lookup_kind="ordId", lookup_value=ORD_ID,
        )
    assert database_state(repo) == before


@pytest.mark.parametrize("raw", [
    "{bad",
    [],
    {"padding": "x" * 4_194_305},
    {"code": "0", "data": [{"authorization": "secret"}]},
])
def test_malformed_oversize_or_credential_raw_is_rejected_atomically(stores, raw):
    _, (repo, _) = stores
    bind(repo)
    fact = parse_okx_spot_order(envelope(), inst_id="BTC-USDT", ord_id=ORD_ID)
    before = database_state(repo)
    with pytest.raises(ValueError):
        repo.commit_private_spot_order(
            fact, raw_payload=raw, received_at=100,
            lookup_kind="ordId", lookup_value=ORD_ID,
        )
    assert database_state(repo) == before


def test_foreign_row_contamination_fails_closed_on_read(stores):
    _, (repo, _) = stores
    bind(repo)
    record = commit(repo)
    with repo.engine.begin() as connection:
        values = dict(connection.execute(
            text("SELECT * FROM private_spot_order WHERE payload_hash = :hash"),
            {"hash": record.payload_hash},
        ).mappings().one())
        values["account_scope_id"] = "f" * 64
        values["uid"] = "foreign-uid"
        connection.execute(PrivateSpotOrderRecord.__table__.insert().values(values))
    with pytest.raises(ValueError, match="scope or UID"):
        repo.get_private_spot_orders()


def test_unbound_reader_rejects_any_existing_order_row(stores):
    _, (left, right) = stores
    bind(left)
    record = commit(left)
    with right.engine.connect() as connection:
        right_scope = connection.execute(text(
            "SELECT account_scope_id FROM execution_scope_header"
        )).scalar_one()
    values = record.model_dump()
    values["account_scope_id"] = right_scope
    with right.engine.begin() as connection:
        connection.execute(PrivateSpotOrderRecord.__table__.insert().values(values))
    with pytest.raises(ValueError, match="scope or UID"):
        right.get_private_spot_orders()


def test_forged_raw_projection_pair_fails_closed_on_read(stores):
    _, (repo, _) = stores
    bind(repo)
    record = commit(repo)
    forged_raw = envelope(state="filled", accFillSz="0.010000000000000001")
    canonical = json.dumps(forged_raw, sort_keys=True, separators=(",", ":"))
    with repo.engine.begin() as connection:
        values = dict(connection.execute(
            text("SELECT * FROM private_spot_order WHERE payload_hash = :hash"),
            {"hash": record.payload_hash},
        ).mappings().one())
        values["raw_json"] = canonical
        values["payload_hash"] = hashlib.sha256(canonical.encode()).hexdigest()
        connection.execute(PrivateSpotOrderRecord.__table__.insert().values(values))
    with pytest.raises(ValueError, match="fact mismatch"):
        repo.get_private_spot_orders()


@pytest.mark.parametrize("statement", [
    "UPDATE private_spot_order SET state='filled'",
    "DELETE FROM private_spot_order",
    "INSERT OR REPLACE INTO private_spot_order SELECT * FROM private_spot_order",
])
def test_order_observations_are_append_only_even_for_direct_sql(stores, statement):
    _, (repo, _) = stores
    bind(repo)
    commit(repo)
    before = database_state(repo)
    with pytest.raises(IntegrityError):
        with repo.engine.begin() as connection:
            connection.execute(text(statement))
    assert database_state(repo) == before
