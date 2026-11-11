from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from src.core.execution_scope import ExecutionScope
from src.data.exchange_store import open_exchange_repository
from src.data.models import AccountRecord
from src.data.repository import Repository

SCOPE = ExecutionScope("okx", "sandbox", "a" * 64)


def make_repository(path):
    master = Repository(db_path=str(path))
    return open_exchange_repository(SCOPE, master_engine=master.engine)


def start(repository, **values):
    return repository.start_private_sync_receipt(
        **{
            "account_scope_id": SCOPE.account_scope_id,
            "symbols": ["BTC-USDT"],
            "since": 10,
            "limit": 100,
            "started_at": 1000,
            **values,
        }
    )


def finish(repository, attempt_id, **values):
    return repository.finalize_private_sync_receipt(
        attempt_id,
        **{
            "state": "committed",
            "completed_at": 1100,
            "received_at": 1090,
            "parts": {"account": {"received_at": 1010, "completeness": "unknown"}},
            "cursor": {"mode": "recent", "complete": False},
            "result": {"account_upserted": 1},
            **values,
        },
    )


def test_attempt_and_receipt_survive_reopen_without_fabricating_a_commit(tmp_path):
    path = tmp_path / "master.db"
    repo = make_repository(path)
    before = repo.get_private_sync_status()
    assert before == {"latest_attempt": None, "last_committed": None}
    attempt = start(repo)
    assert attempt.state == "in_progress"
    assert attempt.generation == 1
    assert attempt.account_scope_id == SCOPE.account_scope_id
    assert attempt.symbols == ["BTC-USDT"]
    assert attempt.completed_at is None and attempt.received_at is None
    assert attempt.parts == attempt.cursor == attempt.result == {}
    assert attempt.error_code is None
    loaded = make_repository(path).get_private_sync_status()
    assert loaded["latest_attempt"].model_dump() == attempt.model_dump()
    assert loaded["last_committed"] is None
    committed = finish(repo, attempt.attempt_id)
    status = make_repository(path).get_private_sync_status()
    assert status["latest_attempt"].model_dump() == committed.model_dump()
    assert status["last_committed"].attempt_id == attempt.attempt_id


def test_generation_is_monotonic_and_failed_latest_does_not_refresh_last_commit(tmp_path):
    repo = make_repository(tmp_path / "master.db")
    first = start(repo)
    finish(repo, first.attempt_id)
    second = start(repo, started_at=1200, symbols=None, since=None)
    failed = repo.fail_private_sync_receipt(
        second.attempt_id, completed_at=1250, error_code="provider_unavailable"
    )
    assert second.generation == 2
    assert failed.state == "failed"
    assert failed.received_at is None
    assert failed.error_code == "provider_unavailable"
    status = repo.get_private_sync_status()
    assert status["latest_attempt"].attempt_id == second.attempt_id
    assert status["last_committed"].attempt_id == first.attempt_id
    assert status["last_committed"].completed_at == 1100
    assert status["last_committed"].received_at == 1090
    assert repo.get_private_sync_receipt("missing") is None


@pytest.mark.parametrize("newer_state", ["committed", "partial"])
def test_out_of_order_completion_rejects_superseded_success(tmp_path, newer_state):
    repo = make_repository(tmp_path / "master.db")
    slow = start(repo)
    fast = start(repo, started_at=1001)
    finish(repo, fast.attempt_id, state=newer_state, completed_at=1200, received_at=1190)
    with pytest.raises(ValueError, match="superseded"):
        finish(repo, slow.attempt_id, completed_at=1300, received_at=1290)
    status = repo.get_private_sync_status()
    assert status["latest_attempt"].attempt_id == fast.attempt_id
    assert status["last_committed"].attempt_id == fast.attempt_id
    assert repo.get_private_sync_receipt(slow.attempt_id).state == "in_progress"
    failed = repo.fail_private_sync_receipt(
        slow.attempt_id, completed_at=1300, error_code="superseded"
    )
    assert failed.state == "failed"
    assert repo.get_private_sync_status()["last_committed"].attempt_id == fast.attempt_id


def test_partial_commit_is_explicit_and_failure_after_it_retains_its_evidence(tmp_path):
    repo = make_repository(tmp_path / "master.db")
    attempt = start(repo)
    receipt = finish(
        repo,
        attempt.attempt_id,
        state="partial",
        parts={"trades": {"state": "unavailable", "completeness": "unknown"}},
    )
    assert receipt.state == "partial"
    failed = start(repo, started_at=1200)
    repo.fail_private_sync_receipt(
        failed.attempt_id, completed_at=1250, error_code="malformed_data"
    )
    status = repo.get_private_sync_status()
    assert status["last_committed"].attempt_id == attempt.attempt_id
    assert status["last_committed"].state == "partial"


def account():
    return AccountRecord(
        strategy="__exchange__",
        source="live_exchange",
        runtime_mode="live",
        initial_equity=100,
        cash_balance=100,
        equity=100,
        realized_pnl=0,
        unrealized_pnl=0,
        daily_pnl=0,
        fees_paid=0,
        updated_at=10,
    )


def test_projection_and_final_receipt_share_atomic_transaction_and_rollback(tmp_path):
    repo = make_repository(tmp_path / "master.db")
    attempt = start(repo)

    def broken(transaction):
        transaction.upsert_account(account())
        finish(transaction, attempt.attempt_id)
        raise RuntimeError("fail after receipt")

    with pytest.raises(RuntimeError, match="fail after receipt"):
        repo.atomic_private_sync(broken)
    assert repo.get_account("__exchange__") is None
    assert repo.get_private_sync_receipt(attempt.attempt_id).state == "in_progress"
    assert repo.get_private_sync_status()["last_committed"] is None
    repo.atomic_private_sync(
        lambda transaction: (
            transaction.upsert_account(account()),
            finish(transaction, attempt.attempt_id),
        )
    )
    assert repo.get_account("__exchange__").equity == 100
    assert repo.get_private_sync_status()["last_committed"].attempt_id == attempt.attempt_id


@pytest.mark.parametrize("already_terminal", [False, True])
def test_superseded_finalization_rolls_back_all_projection_changes(tmp_path, already_terminal):
    repo = make_repository(tmp_path / "master.db")
    slow = start(repo)
    if already_terminal:
        finish(repo, slow.attempt_id)
    fast = start(repo, started_at=1001)
    repo.atomic_private_sync(
        lambda transaction: (
            transaction.upsert_account(account()),
            finish(transaction, fast.attempt_id),
        )
    )
    changed_account = account().model_copy(update={"equity": 50, "updated_at": 20})

    def stale_commit(transaction):
        transaction.upsert_account(changed_account)
        transaction.set_kill_switch(True, "stale attempt", 1200)
        finish(transaction, slow.attempt_id)

    with pytest.raises(ValueError, match="superseded"):
        repo.atomic_private_sync(stale_commit)
    assert repo.get_account("__exchange__").equity == 100
    assert repo.get_account("__exchange__").updated_at == 10
    assert repo.get_kill_switch().engaged is False
    assert repo.get_private_sync_status()["last_committed"].attempt_id == fast.attempt_id
    assert repo.get_private_sync_receipt(slow.attempt_id).state == (
        "committed" if already_terminal else "in_progress"
    )


def test_receipt_read_returns_detached_payload_and_exact_terminal_retry_is_idempotent(tmp_path):
    repo = make_repository(tmp_path / "master.db")
    attempt = start(repo, attempt_id="stable-attempt")
    assert start(repo, attempt_id="stable-attempt").model_dump() == attempt.model_dump()
    with pytest.raises(ValueError):
        start(repo, attempt_id="stable-attempt", symbols=["ETH-USDT"])
    saved = finish(repo, attempt.attempt_id)
    saved.parts["account"]["completeness"] = "complete"
    assert (
        repo.get_private_sync_receipt(attempt.attempt_id).parts["account"]["completeness"]
        == "unknown"
    )
    assert finish(repo, attempt.attempt_id).parts["account"]["completeness"] == "unknown"
    with pytest.raises(ValueError):
        finish(repo, attempt.attempt_id, state="partial")
    with pytest.raises(ValueError):
        repo.fail_private_sync_receipt(attempt.attempt_id, completed_at=1200, error_code="late")
    with pytest.raises(ValueError):
        finish(repo, "missing")


def test_abandoned_state_preserves_attempt_identity_and_never_is_committed(tmp_path):
    repo = make_repository(tmp_path / "master.db")
    attempt = start(repo)
    result = repo.fail_private_sync_receipt(
        attempt.attempt_id, state="abandoned", completed_at=2000, error_code="process_interrupted"
    )
    assert result.state == "abandoned"
    assert repo.get_private_sync_status()["last_committed"] is None


@pytest.mark.parametrize(
    "values",
    [
        {"limit": 0},
        {"limit": True},
        {"since": -1},
        {"since": True},
        {"started_at": -1},
        {"symbols": [""]},
        {"symbols": ["BTC-USDT"] * 101},
        {"attempt_id": ""},
        {"account_scope_id": "other"},
    ],
)
def test_invalid_attempt_inputs_create_no_row(tmp_path, values):
    repo = make_repository(tmp_path / "master.db")
    with pytest.raises(ValueError):
        start(repo, **values)
    assert repo.get_private_sync_status()["latest_attempt"] is None


@pytest.mark.parametrize(
    "values",
    [
        {"state": "ready"},
        {"completed_at": 999},
        {"received_at": 999},
        {"received_at": 1200},
        {"parts": {"bad": float("nan")}},
        {"result": {"secret": "x" * 70_000}},
    ],
)
def test_invalid_finalization_does_not_modify_in_progress_attempt(tmp_path, values):
    repo = make_repository(tmp_path / "master.db")
    attempt = start(repo)
    with pytest.raises(ValueError):
        finish(repo, attempt.attempt_id, **values)
    assert repo.get_private_sync_receipt(attempt.attempt_id).state == "in_progress"


def test_failure_reason_is_a_bounded_code_not_provider_exception_text(tmp_path):
    repo = make_repository(tmp_path / "master.db")
    attempt = start(repo)
    for code in ["bad token=secret", "x" * 65, "", None]:
        with pytest.raises(ValueError):
            repo.fail_private_sync_receipt(attempt.attempt_id, completed_at=1200, error_code=code)
    assert repo.get_private_sync_receipt(attempt.attempt_id).state == "in_progress"


def test_concurrent_starts_allocate_distinct_contiguous_generations(tmp_path):
    path = tmp_path / "master.db"
    repo = make_repository(path)
    barrier = Barrier(4)

    def compete(_):
        worker = Repository(engine=repo.engine)
        barrier.wait()
        return start(worker).generation

    with ThreadPoolExecutor(max_workers=4) as pool:
        generations = list(pool.map(compete, range(4)))
    assert sorted(generations) == [1, 2, 3, 4]
    assert repo.get_private_sync_status()["latest_attempt"].generation == 4


def test_receipts_reject_master_or_wrong_header_scope_without_legacy_fallback(tmp_path):
    master = Repository(db_path=str(tmp_path / "master.db"))
    with pytest.raises(ValueError):
        start(master)
    with pytest.raises(ValueError):
        master.get_private_sync_status()


@pytest.mark.parametrize("state", ["in_progress", "committed"])
def test_receipt_reads_fail_closed_on_foreign_account_record(tmp_path, state):
    from sqlalchemy import text

    repo = make_repository(tmp_path / "master.db")
    attempt = start(repo)
    if state == "committed":
        finish(repo, attempt.attempt_id)
    with repo.engine.begin() as connection:
        connection.execute(
            text("UPDATE private_sync_receipt SET account_scope_id=:scope "
                 "WHERE attempt_id=:attempt"),
            {"scope": "foreign-account", "attempt": attempt.attempt_id},
        )
    with pytest.raises(ValueError, match="scope"):
        repo.get_private_sync_receipt(attempt.attempt_id)
    with pytest.raises(ValueError, match="scope"):
        repo.get_private_sync_status()


def test_receipt_state_is_bounded_by_database_constraint(tmp_path):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    repo = make_repository(tmp_path / "master.db")
    attempt = start(repo)
    with pytest.raises(IntegrityError):
        with repo.engine.begin() as connection:
            connection.execute(
                text("UPDATE private_sync_receipt SET state='ready' WHERE attempt_id=:attempt"),
                {"attempt": attempt.attempt_id},
            )
    assert repo.get_private_sync_receipt(attempt.attempt_id).state == "in_progress"
