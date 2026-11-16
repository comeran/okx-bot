"""第三阶段剩余三条：有限重试的私有同步、按身份解释未知单、重启前核对运行状态。"""

from src.data.models import OrderRecord
from src.data.repository import Repository
from src.ops.lifecycle import graceful_shutdown, startup_reconcile
from src.ops.phase3_recovery import (
    apply_fill_pages,
    explain_order_by_identity,
    record_runtime_intent,
    resume_allowed,
    run_bounded_sync,
    sync_freshness,
)


def _order(repository: Repository, **overrides) -> None:
    fields = dict(
        order_id="ord-1",
        client_order_id="cid-1",
        exchange_order_id="",
        strategy="alpha",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=2,
        price=10,
        status="pending",
        fill_price=0,
        timestamp=1,
        updated_at=1,
        filled_amount=0,
        remaining_amount=2,
    )
    fields.update(overrides)
    repository.upsert_order(OrderRecord(**fields))


def test_bounded_sync_retries_and_publishes_freshness(tmp_path) -> None:
    repository = Repository(db_path=str(tmp_path / "bot.db"))
    calls = {"count": 0}

    def fetch():
        calls["count"] += 1
        if calls["count"] < 3:
            raise TimeoutError("network interrupted")
        return {"cursor_ms": 50, "observed_at": 1_000_000, "pages": 2}

    result = run_bounded_sync(
        repository, fetch, max_attempts=3, now_ms=1_000_000, max_age_ms=30_000,
    )

    assert calls["count"] == 3
    assert result["status"] == "fresh"
    assert result["attempts"] == 3
    assert result["fresh"] is True
    assert result["cursor_ms"] == 50
    assert result["complete"] is False
    assert sync_freshness(repository)["status"] == "fresh"

    again = run_bounded_sync(
        repository, fetch, max_attempts=3, now_ms=1_000_100, max_age_ms=30_000,
    )
    assert again["attempts"] == 1
    assert again["cursor_ms"] == 50

    def always_fail():
        raise TimeoutError("network interrupted")

    failed = run_bounded_sync(
        repository, always_fail, max_attempts=3, now_ms=1_000_200, max_age_ms=30_000,
    )
    assert failed["status"] == "failed"
    assert failed["attempts"] == 3
    assert failed["fresh"] is False
    assert failed["complete"] is False
    assert failed["cursor_ms"] == 50


def test_unknown_submit_and_cancel_are_explained_without_a_second_order(tmp_path) -> None:
    repository = Repository(db_path=str(tmp_path / "bot.db"))
    _order(repository)
    submits = {"count": 0}
    cancels = {"count": 0}

    def fail_lookup(_client_order_id: str):
        raise TimeoutError("lost response")

    blocked = explain_order_by_identity(
        repository, client_order_id="cid-1", lookup=fail_lookup, kind="submit",
    )
    assert blocked["action"] == "blocked"
    assert blocked["resubmitted"] is False
    assert submits["count"] == 0
    assert len(repository.get_orders()) == 1

    def found(_client_order_id: str):
        return {"exchange_order_id": "ex-9", "status": "partially_filled", "filled_amount": 1}

    adopted = explain_order_by_identity(
        repository, client_order_id="cid-1", lookup=found, kind="submit",
    )
    assert adopted == {
        "action": "explained",
        "order_id": "ord-1",
        "status": "partially_filled",
        "resubmitted": False,
    }
    stored = repository.get_orders()
    assert len(stored) == 1
    assert stored[0].exchange_order_id == "ex-9"
    assert stored[0].filled_amount == 1

    replay = explain_order_by_identity(
        repository,
        client_order_id="cid-1",
        lookup=lambda _client: {
            "exchange_order_id": "ex-9", "status": "filled", "filled_amount": 2,
        },
        kind="submit",
    )
    assert replay["order_id"] == "ord-1"
    assert replay["resubmitted"] is False
    assert len(repository.get_orders()) == 1
    assert repository.get_orders()[0].status == "filled"

    _order(repository, order_id="ord-2", client_order_id="cid-2", status="pending")

    def still_open(_client_order_id: str):
        cancels["count"] += 1
        return {"exchange_order_id": "ex-2", "status": "pending", "filled_amount": 0}

    unclear = explain_order_by_identity(
        repository, client_order_id="cid-2", lookup=still_open, kind="cancel",
    )
    assert unclear["action"] == "blocked"
    assert unclear["resubmitted"] is False
    assert cancels["count"] == 1
    assert repository.get_orders(order_id="ord-2")[0].status == "pending"

    explained = explain_order_by_identity(
        repository,
        client_order_id="cid-2",
        lookup=lambda _client: {
            "exchange_order_id": "ex-2", "status": "cancelled", "filled_amount": 0,
        },
        kind="cancel",
    )
    assert explained["status"] == "cancelled"
    assert explained["resubmitted"] is False
    assert len([item for item in repository.get_orders() if item.client_order_id == "cid-2"]) == 1


def test_fill_pages_and_restart_converge_without_duplicate_orders(tmp_path) -> None:
    repository = Repository(db_path=str(tmp_path / "bot.db"))
    _order(repository)
    pages = [
        [{"trade_id": "t-1", "client_order_id": "cid-1", "amount": 1, "price": 10}],
        [
            {"trade_id": "t-1", "client_order_id": "cid-1", "amount": 1, "price": 10},
            {"trade_id": "t-2", "client_order_id": "cid-1", "amount": 1, "price": 11},
        ],
    ]

    first = apply_fill_pages(repository, pages)
    second = apply_fill_pages(repository, pages)

    assert first["orders"] == 1
    assert first["fills"] == 2
    assert second == first
    order = repository.get_orders()[0]
    assert order.filled_amount == 2
    assert order.status == "filled"
    assert len(repository.get_orders()) == 1

    record_runtime_intent(
        repository, name="alpha", intended="running", actual="running",
        owner="old-process", updated_at=10,
    )
    restarted = startup_reconcile(repository, alive=lambda owner: owner != "old-process")
    assert restarted["paused"] is True
    assert restarted["resume"] is False
    assert resume_allowed(repository, "alpha", "new-process") is False
    assert repository.acquire_strategy_lease(
        "alpha", "new-process", alive=lambda owner: owner != "old-process",
    ) is True
    repository.release_strategy_lease("alpha", "new-process")

    repository.upsert_order(OrderRecord(
        order_id="ord-1", client_order_id="cid-1", strategy="alpha", symbol="BTC-USDT",
        side="buy", type="limit", amount=2, price=10, status="filled", fill_price=11,
        timestamp=1, updated_at=3, filled_amount=2, remaining_amount=0,
    ))
    cleared = startup_reconcile(repository, alive=lambda _owner: False)
    assert cleared["resume"] is True
    assert resume_allowed(repository, "alpha", "new-process") is True
    assert repository.acquire_strategy_lease("alpha", "new-process", alive=lambda _owner: True)
    assert repository.acquire_strategy_lease("beta", "other", alive=lambda _owner: True)
    held = repository.acquire_strategy_lease("beta", "new-process", alive=lambda _owner: True)
    assert held is False

    graceful_shutdown(repository, "new-process")
    assert repository.acquire_strategy_lease(
        "alpha", "replacement", alive=lambda _owner: False,
    ) is True
    assert repository.acquire_strategy_lease(
        "beta", "replacement", alive=lambda _owner: True,
    ) is False
    assert resume_allowed(repository, "alpha", "replacement") is True
