import os
import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from importlib import import_module
from pathlib import Path
from threading import Barrier

import pytest
from sqlmodel import create_engine

from src.data.models import OrderRecord, TradeRecord
from src.data.repository import Repository


def storage_module():
    return import_module("src.data.exchange_store")


def scope(fingerprint="a", environment="sandbox"):
    return import_module("src.core.execution_scope").ExecutionScope(
        exchange="okx", environment=environment, account_fingerprint=fingerprint * 64,
    )


def expected_path(master, identity):
    path = Path(master.engine.url.database)
    return path.parent / "account-facts" / path.name / f"{identity.account_scope_id}.db"


def order(amount=1):
    return OrderRecord(
        order_id="local-order", exchange_order_id="same-ordId", client_order_id="same-clOrdId",
        strategy="__exchange__", symbol="BTC-USDT", side="buy", type="limit",
        amount=amount, price=10, status="pending", fill_price=0, timestamp=1,
        source="live_exchange", runtime_mode="live",
    )


def trade(amount=1):
    return TradeRecord(
        exchange_trade_id="same-tradeId", exchange_order_id="same-ordId",
        client_order_id="same-clOrdId", order_id="local-order", strategy="__exchange__",
        symbol="BTC-USDT", side="buy", amount=amount, price=10, fee=0, timestamp=1,
        source="live_exchange", runtime_mode="live",
    )


def test_accounts_and_environments_keep_identical_external_ids_independent(tmp_path):
    master = Repository(db_path=str(tmp_path / "bot.db"))
    identities = [scope(), scope("b"), scope(environment="live")]
    for amount, identity in enumerate(identities, start=1):
        facts = storage_module().open_exchange_repository(identity, master_engine=master.engine)
        facts.upsert_order(order(amount))
        facts.upsert_trade(trade(amount))
        facts.engine.dispose()
    for amount, identity in enumerate(identities, start=1):
        reopened = storage_module().open_exchange_repository(identity, master_engine=master.engine)
        assert [item.amount for item in reopened.get_orders()] == [amount]
        assert [item.amount for item in reopened.get_trades()] == [amount]
        reopened.upsert_order(order(amount))
        reopened.upsert_trade(trade(amount))
        assert len(reopened.get_orders()) == len(reopened.get_trades()) == 1
        assert Path(reopened.engine.url.database) == expected_path(master, identity)


def test_independent_masters_in_one_directory_do_not_share_facts(tmp_path):
    first = Repository(db_path=str(tmp_path / "first.db"))
    second = Repository(db_path=str(tmp_path / "second.db"))
    identity = scope()
    left = storage_module().open_exchange_repository(identity, master_engine=first.engine)
    right = storage_module().open_exchange_repository(identity, master_engine=second.engine)
    left.upsert_trade(trade(1))
    right.upsert_trade(trade(2))
    assert left.engine.url.database != right.engine.url.database
    assert [item.amount for item in left.get_trades()] == [1]
    assert [item.amount for item in right.get_trades()] == [2]


def test_open_does_not_modify_master_or_database_environment(tmp_path, monkeypatch):
    master = Repository(db_path=str(tmp_path / "bot.db"))
    master.upsert_order(order(99))
    master.upsert_trade(trade(99))
    with sqlite3.connect(master.engine.url.database) as connection:
        before = list(connection.iterdump())
    monkeypatch.setenv("OKX_BOT_DB_PATH", "synthetic-untouched-environment")
    facts = storage_module().open_exchange_repository(scope(), master_engine=master.engine)
    assert facts.get_orders() == facts.get_trades() == []
    facts.upsert_order(order(1))
    facts.upsert_trade(trade(1))
    with sqlite3.connect(master.engine.url.database) as connection:
        assert list(connection.iterdump()) == before
    assert os.environ["OKX_BOT_DB_PATH"] == "synthetic-untouched-environment"
    assert [item.amount for item in master.get_trades()] == [99]


def test_header_contains_only_immutable_binding_and_no_credentials(tmp_path):
    master = Repository(db_path=str(tmp_path / "bot.db"))
    identity = scope()
    facts = storage_module().open_exchange_repository(identity, master_engine=master.engine)
    with sqlite3.connect(facts.engine.url.database) as connection:
        assert connection.execute(
            "SELECT schema_version, account_scope_id, exchange, environment, account_fingerprint "
            "FROM execution_scope_header"
        ).fetchall() == [(1, identity.account_scope_id, "okx", "sandbox", "a" * 64)]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE execution_scope_header SET environment='live'")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM execution_scope_header")


@pytest.mark.parametrize("kind", ["headerless", "corrupt", "mismatch", "wrong-version"])
def test_invalid_existing_files_fail_before_repository_and_remain_unchanged(
    tmp_path, monkeypatch, kind,
):
    module = storage_module()
    master = Repository(db_path=str(tmp_path / "bot.db"))
    identity = scope()
    path = expected_path(master, identity)
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "headerless":
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE old_facts (cash INTEGER)")
            connection.execute("INSERT INTO old_facts VALUES (123)")
    elif kind == "corrupt":
        path.write_bytes(b"synthetic-invalid-sqlite")
    elif kind == "mismatch":
        foreign = module.open_exchange_repository(scope("b"), master_engine=master.engine)
        foreign.engine.dispose()
        shutil.copyfile(foreign.engine.url.database, path)
    else:
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE execution_scope_header (schema_version INTEGER, "
                "account_scope_id TEXT, exchange TEXT, environment TEXT, account_fingerprint TEXT)"
            )
            connection.execute(
                "INSERT INTO execution_scope_header VALUES (2, ?, 'okx', 'sandbox', ?)",
                (identity.account_scope_id, identity.account_fingerprint),
            )
    before = path.read_bytes()
    siblings = sorted(item.name for item in path.parent.iterdir())

    def forbid_repository(*args, **kwargs):
        pytest.fail("Repository must not inspect or migrate an invalid facts file")

    monkeypatch.setattr(module, "Repository", forbid_repository)
    with pytest.raises(module.ExchangeStoreError):
        module.open_exchange_repository(identity, master_engine=master.engine)
    assert path.read_bytes() == before
    after = {item.name for item in path.parent.iterdir()}
    assert set(siblings) <= after
    assert after - set(siblings) <= {f"{path.name}-wal", f"{path.name}-shm"}


def test_competing_initialization_opens_one_valid_database(tmp_path):
    master = Repository(db_path=str(tmp_path / "bot.db"))
    identity = scope()
    barrier = Barrier(6)

    def open_one(_):
        barrier.wait(timeout=10)
        facts = storage_module().open_exchange_repository(identity, master_engine=master.engine)
        path = facts.engine.url.database
        facts.engine.dispose()
        return path

    with ThreadPoolExecutor(max_workers=6) as executor:
        paths = list(executor.map(open_one, range(6)))
    assert len(set(paths)) == 1
    with closing(sqlite3.connect(paths[0])) as connection:
        assert connection.execute("SELECT COUNT(*) FROM execution_scope_header").fetchone() == (1,)
    path = expected_path(master, identity)
    assert {item.name for item in path.parent.iterdir()} == {
        path.name, path.with_suffix(".bootstrap.lock").name,
    }


def test_concurrent_reopen_remains_valid_while_facts_write_and_checkpoint(tmp_path):
    master = Repository(db_path=str(tmp_path / "bot.db"))
    identity = scope()
    facts = storage_module().open_exchange_repository(identity, master_engine=master.engine)
    facts.upsert_order(order())
    facts.upsert_trade(trade())
    barrier = Barrier(4)

    def write():
        barrier.wait(timeout=10)
        for timestamp in range(2, 42):
            update = order()
            update.updated_at = timestamp
            facts.upsert_order(update)

    def checkpoint():
        barrier.wait(timeout=10)
        with closing(sqlite3.connect(facts.engine.url.database)) as connection:
            for _ in range(40):
                connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchall()

    def read():
        barrier.wait(timeout=10)
        for _ in range(20):
            opened = storage_module().open_exchange_repository(
                identity, master_engine=master.engine,
            )
            assert [item.amount for item in opened.get_trades()] == [1]
            opened.engine.dispose()

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(operation) for operation in (write, checkpoint, read, read)]
        for future in futures:
            future.result(timeout=20)
    assert [item.amount for item in facts.get_orders()] == [1]


def test_publish_does_not_overwrite_a_file_that_appears_during_initialization(
    tmp_path, monkeypatch,
):
    module = storage_module()
    master = Repository(db_path=str(tmp_path / "bot.db"))
    identity = scope()
    path = expected_path(master, identity)
    original_link = module.os.link

    def competing_publish(source, destination, **kwargs):
        Path(destination).write_bytes(b"preexisting-audit-file")
        return original_link(source, destination, **kwargs)

    monkeypatch.setattr(module.os, "link", competing_publish)
    with pytest.raises(module.ExchangeStoreError):
        module.open_exchange_repository(identity, master_engine=master.engine)
    assert path.read_bytes() == b"preexisting-audit-file"
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("where", ["file", "directory"])
def test_symlink_storage_is_rejected_without_touching_its_target(tmp_path, where):
    module = storage_module()
    master = Repository(db_path=str(tmp_path / "bot.db"))
    identity = scope()
    path = expected_path(master, identity)
    target = tmp_path / "untouched"
    if where == "file":
        path.parent.mkdir(parents=True)
        target.write_bytes(b"synthetic-legacy-audit")
        path.symlink_to(target)
    else:
        target.mkdir()
        path.parent.parent.mkdir()
        path.parent.symlink_to(target, target_is_directory=True)
    before = target.read_bytes() if target.is_file() else list(target.iterdir())
    with pytest.raises(module.ExchangeStoreError):
        module.open_exchange_repository(identity, master_engine=master.engine)
    assert (target.read_bytes() if target.is_file() else list(target.iterdir())) == before


def test_in_memory_master_fails_closed_without_creating_storage(tmp_path, monkeypatch):
    module = storage_module()
    monkeypatch.chdir(tmp_path)
    with pytest.raises(module.ExchangeStoreError):
        module.open_exchange_repository(scope(), master_engine=create_engine("sqlite:///:memory:"))
    assert list(tmp_path.iterdir()) == []


def test_initialization_failure_is_normalized_and_removes_unpublished_temporary_file(
    tmp_path, monkeypatch,
):
    module = storage_module()
    master = Repository(db_path=str(tmp_path / "bot.db"))
    identity = scope()

    def fail_publish(*args, **kwargs):
        raise PermissionError("synthetic-private-path-or-credential")

    monkeypatch.setattr(module.os, "link", fail_publish)
    with pytest.raises(module.ExchangeStoreError) as failure:
        module.open_exchange_repository(identity, master_engine=master.engine)
    assert "synthetic-private" not in str(failure.value)
    path = expected_path(master, identity)
    assert not path.exists()
    assert list(path.parent.iterdir()) == []
