"""Account configuration is durable, immutable-identity audit evidence only."""

import hashlib
import json

import pytest
from sqlalchemy import text

from src.core.execution_scope import ExecutionScope
from src.data.exchange_store import open_exchange_repository
from src.data.repository import Repository


@pytest.fixture
def stores(tmp_path):
    master = Repository(db_path=str(tmp_path / "master.db"))
    scopes = [ExecutionScope("okx", "sandbox", c * 64) for c in "ab"]
    return master, [open_exchange_repository(s, master_engine=master.engine) for s in scopes]


def payload(**fields):
    return {"code": "0", "data": [{"uid": "00123", "acctLv": "1", **fields}]}


def commit(repo, raw=None, *, received_at=100):
    from src.exchange.okx_account_facts import parse_okx_account_config

    raw = payload() if raw is None else raw
    fact = parse_okx_account_config(json.loads(raw) if isinstance(raw, str) else raw)
    return repo.commit_private_account_config(fact, raw_payload=raw, received_at=received_at)


def test_durable_raw_roundtrip_states_hash_and_detached_values(stores):
    _, (repo, _) = stores
    raw = payload(autoLoan=None, enableSpotBorrow="", mainUid="00999")
    raw_text = json.dumps(raw, indent=2)
    record = commit(repo, raw_text)
    assert record.uid == "00123" and record.received_at == 100
    assert record.raw_json == raw_text
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    assert record.payload_hash == hashlib.sha256(canonical.encode()).hexdigest()
    assert (record.acct_lv, record.acct_lv_state) == ("1", "value")
    assert (record.auto_loan, record.auto_loan_state) == (None, "null")
    assert (record.enable_spot_borrow, record.enable_spot_borrow_state) == ("", "value")
    assert (record.settle_ccy, record.settle_ccy_state) == (None, "missing")
    assert record.main_uid == "00999"
    record.uid = "mutated-copy"
    reopened = Repository(db_path=repo.engine.url.database)
    stored, = reopened.get_private_account_config()
    assert stored.uid == "00123" and stored.raw_json == raw_text
    assert reopened.get_private_uid() == "00123"


def test_identical_payload_replay_keeps_first_observation_and_changed_payload_is_retained(stores):
    _, (repo, _) = stores
    first = commit(repo, json.dumps(payload(), indent=2))
    replay = commit(repo, payload(), received_at=200)
    assert replay.model_dump() == first.model_dump()
    changed = commit(repo, payload(autoLoan="1"), received_at=300)
    assert changed.payload_hash != first.payload_hash
    assert len(repo.get_private_account_config(uid="00123")) == 2
    assert repo.get_private_uid() == "00123"


@pytest.mark.parametrize("raw", [
    "{bad", "[]", {"x": float("nan")}, {"padding": "x" * 4_194_305},
    {"authorization": "secret"},
    '{"code":"0","code":"0","data":[{"uid":"00123"}]}',
    {"code": "0", "data": [{"uid": "00123", "headers": {"apiSecretKey": "secret"}}]},
])
def test_rejects_invalid_oversize_or_credential_raw_before_uid_binding(stores, raw):
    from src.exchange.okx_account_facts import parse_okx_account_config

    _, (repo, _) = stores
    with pytest.raises(ValueError):
        repo.commit_private_account_config(
            parse_okx_account_config(payload()), raw_payload=raw, received_at=100,
        )
    assert repo.get_private_uid() is None
    assert repo.get_private_account_config() == []


@pytest.mark.parametrize("received_at", [-1, True, 1.5, "1", None])
def test_invalid_observation_time_never_binds(stores, received_at):
    _, (repo, _) = stores
    with pytest.raises(ValueError):
        commit(repo, received_at=received_at)
    assert repo.get_private_uid() is None


def test_typed_raw_mismatch_rejected_before_binding(stores):
    from src.exchange.okx_account_facts import parse_okx_account_config

    _, (repo, _) = stores
    with pytest.raises(ValueError):
        repo.commit_private_account_config(
            parse_okx_account_config(payload()), raw_payload=payload(autoLoan="1"), received_at=100,
        )
    assert repo.get_private_uid() is None
    assert repo.get_private_account_config() == []


def test_uid_immutable_scopes_isolated_and_master_legacy_activation_unchanged(stores):
    master, (left, right) = stores
    commit(left)
    commit(right, payload(uid="00456", autoLoan="1"))
    assert left.get_private_account_config()[0].uid == "00123"
    assert right.get_private_account_config()[0].uid == "00456"
    with pytest.raises(ValueError, match="UID"):
        commit(left, payload(uid="00456"))
    with pytest.raises(ValueError, match="UID"):
        left.get_private_account_config(uid="00456")
    with pytest.raises(ValueError, match="facts store"):
        commit(master)
    with pytest.raises(ValueError, match="facts store"):
        master.get_private_account_config()
    for repo in (master, left, right):
        assert repo.get_orders() == repo.get_trades() == repo.get_positions() == []
        assert repo.get_account("__exchange__") is None
        assert repo.get_cash_ledger() == repo.get_strategy_configs() == []
        assert repo.get_kill_switch().engaged is False
    assert left.get_private_sync_status() == {"latest_attempt": None, "last_committed": None}
    assert left.get_exact_spot_fills(uid="00123") == []


def test_config_and_binding_roll_back_together(stores):
    _, (repo, _) = stores

    def interrupted(transaction):
        commit(transaction)
        assert len(transaction.get_private_account_config()) == 1
        raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        repo.atomic_accounting(interrupted)
    assert repo.get_private_uid() is None
    assert repo.get_private_account_config() == []


def test_reads_fail_closed_on_foreign_scope_record(stores):
    _, (repo, _) = stores
    commit(repo)
    with repo.engine.begin() as connection:
        connection.execute(text("UPDATE private_account_config SET account_scope_id='foreign'"))
    with pytest.raises(ValueError, match="scope"):
        repo.get_private_account_config()
