from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from threading import Barrier

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from src.data.models import StrategyConfigRecord
from src.data.repository import Repository


def allocation_module():
    return import_module("src.data.allocations")


def make_scope(**overrides):
    return allocation_module().AllocationScope(**{
        "account_fingerprint": "a" * 64,
        "environment": "sandbox",
        "market_type": "spot",
        **overrides,
    })


def make_store(path):
    return allocation_module().AllocationStore(Repository(db_path=str(path)).engine)


def audit_rows(store):
    with Session(store.engine) as session:
        return list(session.exec(select(allocation_module().AllocationAuditRecord)).all())


def save(store, scope, **overrides):
    return store.save_draft(scope, **{
        "strategy": "alpha", "symbol": "BTC-USDT", "budget": 100,
        "expected_revision": 0, "timestamp": 1000, **overrides,
    })


def test_draft_survives_store_and_repository_reconstruction(tmp_path):
    path = tmp_path / "allocations.db"
    store = make_store(path)
    first = save(store, make_scope())
    assert (first.budget, first.revision, first.status) == (100, 1, "draft")
    loaded = make_store(path).get_draft(make_scope(), "alpha")
    assert (loaded.symbol, loaded.currency, loaded.created_at) == ("BTC-USDT", "USDT", 1000)
    assert len(audit_rows(store)) == 1


@pytest.mark.parametrize("budget", [0, -1, float("nan"), float("inf"), True, "100"])
def test_invalid_budgets_leave_no_draft_or_audit(tmp_path, budget):
    store = make_store(tmp_path / "allocations.db")
    with pytest.raises(ValueError):
        save(store, make_scope(), budget=budget)
    assert store.get_draft(make_scope(), "alpha") is None
    assert audit_rows(store) == []


@pytest.mark.parametrize("overrides", [
    {"environment": "live"},
    {"account_fingerprint": "b" * 64},
])
def test_distinct_account_scopes_do_not_share_drafts(tmp_path, overrides):
    store = make_store(tmp_path / "allocations.db")
    save(store, make_scope())
    scope = make_scope(**overrides)
    assert store.get_draft(scope, "alpha") is None
    assert save(store, scope, budget=200).budget == 200
    assert store.get_draft(make_scope(), "alpha").budget == 100


def test_scope_is_immutable_and_rejects_non_spot_market():
    scope = make_scope()
    with pytest.raises(AttributeError):
        scope.environment = "live"
    with pytest.raises(ValueError):
        make_scope(market_type="swap")


def test_conflicting_symbol_is_exclusive_across_stores(tmp_path):
    path = tmp_path / "allocations.db"
    store = make_store(path)
    save(store, make_scope())
    with pytest.raises(allocation_module().AllocationConflict) as error:
        save(make_store(path), make_scope(), strategy="beta")
    assert error.value.code == "symbol_conflict"
    assert len(audit_rows(store)) == 1
    assert store.get_draft(make_scope(), "beta") is None


def test_update_delete_and_recreation_keep_monotonic_revision(tmp_path):
    store = make_store(tmp_path / "allocations.db")
    scope = make_scope()
    save(store, scope)
    updated = save(store, scope, budget=150, expected_revision=1, timestamp=2000)
    assert (updated.revision, updated.created_at, updated.updated_at) == (2, 1000, 2000)
    with pytest.raises(allocation_module().AllocationConflict):
        store.delete_draft(scope, "alpha", expected_revision=1, timestamp=2500)
    store.delete_draft(scope, "alpha", expected_revision=2, timestamp=3000)
    assert store.get_draft(scope, "alpha") is None
    rebuilt = save(store, scope, budget=200, timestamp=4000)
    assert rebuilt.revision == 4
    with pytest.raises(allocation_module().AllocationConflict):
        save(store, scope, budget=999, expected_revision=2, timestamp=5000)
    assert store.get_draft(scope, "alpha").budget == 200
    audits = audit_rows(store)
    assert [row.action for row in audits] == ["create", "update", "delete", "create"]
    assert audits[1].before["budget"] == 100
    assert audits[1].after["budget"] == 150
    assert audits[2].after is None


def test_symbol_change_requires_explicit_delete(tmp_path):
    store = make_store(tmp_path / "allocations.db")
    save(store, make_scope())
    with pytest.raises(allocation_module().AllocationConflict) as error:
        save(store, make_scope(), symbol="ETH-USDT", expected_revision=1)
    assert error.value.code == "symbol_changed"
    assert store.get_draft(make_scope(), "alpha").symbol == "BTC-USDT"


def test_audit_failure_rolls_back_draft_mutation(tmp_path):
    store = make_store(tmp_path / "allocations.db")
    save(store, make_scope())
    with store.engine.begin() as connection:
        connection.execute(text(
            "CREATE TRIGGER reject_allocation_audit BEFORE INSERT ON allocation_audit "
            "BEGIN SELECT RAISE(FAIL, 'audit unavailable'); END"
        ))
    with pytest.raises(IntegrityError):
        save(store, make_scope(), budget=200, expected_revision=1)
    assert store.get_draft(make_scope(), "alpha").budget == 100
    assert store.get_draft(make_scope(), "alpha").revision == 1
    assert len(audit_rows(store)) == 1


def test_simultaneous_symbol_claims_commit_one_draft_and_one_audit(tmp_path):
    path = tmp_path / "allocations.db"
    stores = [make_store(path), make_store(path)]
    barrier = Barrier(2)

    def claim(index):
        barrier.wait(timeout=5)
        try:
            save(stores[index], make_scope(), strategy=("alpha", "beta")[index])
            return "saved"
        except allocation_module().AllocationConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(claim, [0, 1]))
    assert sorted(outcomes) == ["conflict", "saved"]
    assert len(audit_rows(stores[0])) == 1


def test_stale_concurrent_updates_do_not_overwrite_a_new_revision(tmp_path):
    path = tmp_path / "allocations.db"
    first, second = make_store(path), make_store(path)
    save(first, make_scope())
    save(second, make_scope(), budget=120, expected_revision=1, timestamp=2000)
    with pytest.raises(allocation_module().AllocationConflict):
        save(first, make_scope(), budget=90, expected_revision=1, timestamp=3000)
    assert first.get_draft(make_scope(), "alpha").budget == 120
    assert len(audit_rows(first)) == 2


def test_simultaneous_updates_commit_only_one_expected_revision(tmp_path):
    path = tmp_path / "allocations.db"
    stores = [make_store(path), make_store(path)]
    save(stores[0], make_scope())
    barrier = Barrier(2)

    def update(index):
        barrier.wait(timeout=5)
        try:
            save(stores[index], make_scope(), budget=110 + index, expected_revision=1)
            return "saved"
        except allocation_module().AllocationConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(update, [0, 1]))
    assert sorted(outcomes) == ["conflict", "saved"]
    assert stores[0].get_draft(make_scope(), "alpha").revision == 2
    assert len(audit_rows(stores[0])) == 2


def test_delete_audit_failure_preserves_active_draft_and_revision(tmp_path):
    store = make_store(tmp_path / "allocations.db")
    save(store, make_scope())
    with store.engine.begin() as connection:
        connection.execute(text(
            "CREATE TRIGGER reject_allocation_delete_audit BEFORE INSERT ON allocation_audit "
            "WHEN NEW.action = 'delete' BEGIN SELECT RAISE(FAIL, 'audit unavailable'); END"
        ))
    with pytest.raises(IntegrityError):
        store.delete_draft(make_scope(), "alpha", expected_revision=1, timestamp=2000)
    assert store.get_draft(make_scope(), "alpha").revision == 1
    assert len(audit_rows(store)) == 1


def test_deleting_strategy_releases_every_scope_and_allows_new_symbol_claim(tmp_path):
    path = tmp_path / "allocations.db"
    repository = Repository(db_path=str(path))
    store = make_store(path)
    repository.upsert_strategy_config(StrategyConfigRecord(
        name="alpha", strategy_type="ma_cross", symbol="BTC-USDT", timeframe="1h",
        params={}, created_at=1, updated_at=1,
    ))
    sandbox, live = make_scope(), make_scope(environment="live")
    save(store, sandbox)
    save(store, live)
    assert repository.delete_strategy_config("alpha") is True
    assert store.get_draft(sandbox, "alpha") is None
    assert store.get_draft(live, "alpha") is None
    assert [row.action for row in audit_rows(store)].count("strategy_deleted") == 2
    assert save(store, sandbox, strategy="beta").symbol == "BTC-USDT"


def test_strategy_delete_audit_failure_rolls_back_config_and_all_drafts(tmp_path):
    path = tmp_path / "allocations.db"
    repository = Repository(db_path=str(path))
    store = make_store(path)
    repository.upsert_strategy_config(StrategyConfigRecord(
        name="alpha", strategy_type="ma_cross", symbol="BTC-USDT", timeframe="1h",
        params={}, created_at=1, updated_at=1,
    ))
    save(store, make_scope())
    with store.engine.begin() as connection:
        connection.execute(text(
            "CREATE TRIGGER reject_strategy_delete_audit BEFORE INSERT ON allocation_audit "
            "WHEN NEW.action = 'strategy_deleted' "
            "BEGIN SELECT RAISE(FAIL, 'audit unavailable'); END"
        ))
    with pytest.raises(IntegrityError):
        repository.delete_strategy_config("alpha")
    assert repository.get_strategy_config("alpha") is not None
    assert store.get_draft(make_scope(), "alpha").revision == 1
    assert len(audit_rows(store)) == 1
