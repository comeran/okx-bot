"""Balance evidence is scoped, append-only, and cannot fund legacy accounts."""

import hashlib
import json
from dataclasses import replace

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from src.core.execution_scope import ExecutionScope
from src.data.exchange_store import open_exchange_repository
from src.data.repository import Repository
from src.exchange.okx_account_facts import parse_okx_account_config
from tests.unit.test_okx_account_facts import config_payload
from tests.unit.test_okx_balance_facts import balance_payload


@pytest.fixture
def stores(tmp_path):
    master = Repository(db_path=str(tmp_path / "master.db"))
    scopes = [ExecutionScope("okx", "sandbox", c * 64) for c in "ab"]
    return master, [open_exchange_repository(s, master_engine=master.engine) for s in scopes]


def bind(repo, uid="00123"):
    raw = config_payload(uid=uid)
    repo.commit_private_account_config(
        parse_okx_account_config(raw), raw_payload=raw, received_at=1,
    )


def commit(repo, raw=None, *, received_at=100, uid=None):
    from src.exchange.okx_balance_facts import parse_okx_account_balance

    raw = balance_payload() if raw is None else raw
    fact = parse_okx_account_balance(json.loads(raw) if isinstance(raw, str) else raw)
    return repo.commit_private_account_balance(
        fact, uid=uid, raw_payload=raw, received_at=received_at,
    )


def database_state(repo, *, operational_only=False):
    exact = {"private_uid_binding", "private_account_config", "private_account_balance",
             "exact_spot_fill", "exact_spot_fill_page", "exact_spot_fill_cursor"}
    with repo.engine.connect() as connection:
        return {name: connection.exec_driver_sql(f'SELECT * FROM "{name}" ORDER BY rowid').all()
                for name in inspect(repo.engine).get_table_names()
                if not operational_only or name not in exact}


def test_roundtrip_preserves_raw_precision_states_hash_and_original_json(stores):
    _, (repo, _) = stores
    bind(repo)
    raw = balance_payload()
    raw_text = json.dumps(raw, indent=2)
    record = commit(repo, raw_text)
    assert record.uid == "00123" and record.received_at == 100
    assert record.raw_json == raw_text
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    assert record.payload_hash == hashlib.sha256(canonical.encode()).hexdigest()
    assert (record.total_eq, record.total_eq_state) == ("9007199254740993.000100", "value")
    assert (record.adj_eq, record.adj_eq_state) == (None, "null")
    assert (record.avail_eq, record.avail_eq_state) == ("", "value")
    assert json.loads(record.details_json) == raw["data"][0]["details"]
    record.uid = "detached"
    reopened = Repository(db_path=repo.engine.url.database)
    stored, = reopened.get_private_account_balances()
    assert stored.uid == "00123" and stored.raw_json == raw_text
    assert stored.u_time == "001700000000123"
    missing = balance_payload()
    del missing["data"][0]["totalEq"]
    assert commit(repo, missing).total_eq_state == "missing"


def test_identical_replay_is_idempotent_and_changed_balance_is_retained(stores):
    _, (repo, _) = stores
    bind(repo)
    first = commit(repo, json.dumps(balance_payload(), indent=2))
    assert commit(repo, received_at=200).model_dump() == first.model_dump()
    changed = commit(repo, balance_payload(totalEq="1.00"), received_at=300)
    assert first.payload_hash != changed.payload_hash
    assert [r.received_at for r in repo.get_private_account_balances(uid="00123")] == [100, 300]


def test_balance_requires_existing_config_uid_and_never_invents_binding(stores):
    _, (repo, _) = stores
    before = database_state(repo)
    with pytest.raises(ValueError, match="UID"):
        commit(repo)
    assert database_state(repo) == before and repo.get_private_uid() is None
    bind(repo)
    assert commit(repo).uid == "00123"


def test_uid_and_scope_isolation_and_operational_state_unchanged(stores):
    master, (left, right) = stores
    bind(left)
    bind(right, "00456")
    before = [database_state(r, operational_only=True) for r in (master, left, right)]
    commit(left)
    commit(right, balance_payload(totalEq="2.00"), uid="00456")
    assert left.get_private_account_balances()[0].uid == "00123"
    assert right.get_private_account_balances()[0].total_eq == "2.00"
    with pytest.raises(ValueError, match="UID"):
        commit(left, uid="00456")
    with pytest.raises(ValueError, match="UID"):
        left.get_private_account_balances(uid="00456")
    with pytest.raises(ValueError, match="facts store"):
        commit(master)
    assert [database_state(r, operational_only=True) for r in (master, left, right)] == before


@pytest.mark.parametrize("raw", [
    "{bad", "[]", {"padding": "x" * 4_194_305}, {"authorization": "secret"},
    '{"code":"0","code":"0","data":[]}',
    {"code": "0", "data": [{"details": [], "headers": {"apiSecretKey": "secret"}}]},
])
def test_invalid_credential_or_oversize_raw_is_rejected_atomically(stores, raw):
    from src.exchange.okx_balance_facts import parse_okx_account_balance

    _, (repo, _) = stores
    bind(repo)
    before = database_state(repo)
    with pytest.raises(ValueError):
        repo.commit_private_account_balance(
            parse_okx_account_balance(balance_payload()), raw_payload=raw, received_at=100,
        )
    assert database_state(repo) == before


@pytest.mark.parametrize("received_at", [-1, True, 1.5, "1", None])
def test_invalid_receive_time_rolls_back(stores, received_at):
    _, (repo, _) = stores
    bind(repo)
    before = database_state(repo)
    with pytest.raises(ValueError):
        commit(repo, received_at=received_at)
    assert database_state(repo) == before


def test_typed_raw_mismatch_is_rejected(stores):
    from src.exchange.okx_balance_facts import parse_okx_account_balance

    _, (repo, _) = stores
    bind(repo)
    before = database_state(repo)
    with pytest.raises(ValueError, match="typed fact"):
        repo.commit_private_account_balance(
            replace(parse_okx_account_balance(balance_payload()), total_eq="1.00"),
            raw_payload=balance_payload(), received_at=100,
        )
    assert database_state(repo) == before


def test_transaction_failure_rolls_back_balance_and_other_writes(stores):
    _, (repo, _) = stores
    before = database_state(repo)

    def interrupted(tx):
        bind(tx)
        commit(tx)
        assert len(tx.get_private_account_balances()) == 1
        raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError, match="interrupted"):
        repo.atomic_accounting(interrupted)
    assert database_state(repo) == before


@pytest.mark.parametrize("statement", [
    "UPDATE private_account_balance SET total_eq='1'",
    "DELETE FROM private_account_balance",
    "INSERT OR REPLACE INTO private_account_balance SELECT * FROM private_account_balance",
])
def test_observations_are_append_only_even_for_direct_sql(stores, statement):
    _, (repo, _) = stores
    bind(repo)
    commit(repo)
    before = database_state(repo)
    with pytest.raises(IntegrityError):
        with repo.engine.begin() as connection:
            connection.execute(text(statement))
    assert database_state(repo) == before
