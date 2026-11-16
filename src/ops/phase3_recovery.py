"""有限重试的私有同步、未知单身份解释，以及重启前的运行状态核对。

默认不访问交易所。同步函数由调用方注入。解释未知单只更新已有订单，不会另下一笔。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable

from sqlalchemy import text

from src.data.models import OrderRecord

logger = logging.getLogger(__name__)

_TERMINAL = {"filled", "cancelled", "canceled", "rejected"}
_STATUSES = _TERMINAL | {"pending", "partially_filled"}
_PAUSE_REASONS = {"unresolved_live_order", "reconcile_required"}


def ensure_recovery_tables(engine) -> None:
    if getattr(getattr(engine, "dialect", None), "name", None) != "sqlite":
        return
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS private_sync_freshness ("
            "id INTEGER PRIMARY KEY, status VARCHAR NOT NULL, attempts INTEGER NOT NULL, "
            "fresh BOOLEAN NOT NULL, cursor_ms INTEGER, observed_at INTEGER, "
            "complete BOOLEAN NOT NULL, updated_at INTEGER NOT NULL)"
        ))
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS recovered_fill ("
            "trade_id VARCHAR PRIMARY KEY, client_order_id VARCHAR NOT NULL, "
            "amount FLOAT NOT NULL, price FLOAT NOT NULL)"
        ))
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS runtime_intent ("
            "name VARCHAR PRIMARY KEY, intended VARCHAR NOT NULL, actual VARCHAR NOT NULL, "
            "owner VARCHAR NOT NULL, updated_at INTEGER NOT NULL)"
        ))


def run_bounded_sync(
    repository,
    fetch: Callable[[], dict],
    *,
    max_attempts: int = 3,
    now_ms: int,
    max_age_ms: int = 30_000,
) -> dict[str, object]:
    """失败时最多重试 max_attempts 次。成功也不把覆盖标成完整。"""
    ensure_recovery_tables(repository.engine)
    last_error = "sync_failed"
    for attempt in range(1, max_attempts + 1):
        try:
            page = fetch()
        except Exception:
            last_error = "sync_failed"
            logger.warning("私有同步第 %s 次失败", attempt)
            _write_freshness(
                repository, status="retrying", attempts=attempt, fresh=False,
                cursor_ms=_stored_cursor(repository), observed_at=None,
                updated_at=now_ms,
            )
            continue
        observed = page.get("observed_at")
        cursor = page.get("cursor_ms")
        fresh = (
            type(observed) is int
            and type(now_ms) is int
            and 0 <= now_ms - observed <= max_age_ms
        )
        status = "fresh" if fresh else "stale"
        _write_freshness(
            repository, status=status, attempts=attempt, fresh=fresh,
            cursor_ms=None if cursor is None else int(cursor),
            observed_at=None if observed is None else int(observed),
            updated_at=now_ms,
        )
        return {
            "status": status,
            "attempts": attempt,
            "fresh": fresh,
            "cursor_ms": None if cursor is None else int(cursor),
            "complete": False,
        }
    cursor = _stored_cursor(repository)
    _write_freshness(
        repository, status="failed", attempts=max_attempts, fresh=False,
        cursor_ms=cursor, observed_at=None, updated_at=now_ms,
    )
    return {
        "status": "failed",
        "attempts": max_attempts,
        "fresh": False,
        "cursor_ms": cursor,
        "complete": False,
        "error": last_error,
    }


def sync_freshness(repository) -> dict[str, object]:
    ensure_recovery_tables(repository.engine)
    with repository.engine.connect() as connection:
        row = connection.execute(text(
            "SELECT status, attempts, fresh, cursor_ms FROM private_sync_freshness WHERE id = 1"
        )).first()
    if row is None:
        return {
            "status": "unknown",
            "attempts": 0,
            "fresh": False,
            "cursor_ms": None,
            "complete": False,
        }
    return {
        "status": str(row[0]),
        "attempts": int(row[1]),
        "fresh": bool(row[2]),
        "cursor_ms": None if row[3] is None else int(row[3]),
        "complete": False,
    }


def explain_order_by_identity(
    repository,
    *,
    client_order_id: str,
    lookup: Callable[[str], dict | None],
    kind: str,
) -> dict[str, object]:
    """用已有客户单号采纳交易所结果。不创建第二笔订单，也不重新提交。"""
    matches = [
        order for order in repository.get_orders()
        if str(getattr(order, "client_order_id", "") or "") == client_order_id
    ]
    if len(matches) != 1:
        return {"action": "blocked", "resubmitted": False, "orders": len(matches)}
    order = matches[0]
    status = str(order.status or "").lower()
    if status in _TERMINAL:
        return {
            "action": "explained",
            "order_id": order.order_id,
            "status": status,
            "resubmitted": False,
        }
    try:
        found = lookup(client_order_id)
    except Exception:
        logger.warning("按客户单号核对订单失败")
        return {
            "action": "blocked",
            "order_id": order.order_id,
            "resubmitted": False,
        }
    if not isinstance(found, dict):
        if kind == "cancel":
            return {"action": "blocked", "order_id": order.order_id, "resubmitted": False}
        _save_order(repository, order, status="rejected", updated_at=_now(order))
        return {
            "action": "explained",
            "order_id": order.order_id,
            "status": "rejected",
            "resubmitted": False,
        }
    reported = str(found.get("status") or "").lower()
    if reported not in _STATUSES:
        return {"action": "blocked", "order_id": order.order_id, "resubmitted": False}
    if kind == "cancel" and reported not in _TERMINAL:
        return {"action": "blocked", "order_id": order.order_id, "resubmitted": False}
    filled = found.get("filled_amount", order.filled_amount)
    _save_order(
        repository,
        order,
        status=reported,
        exchange_order_id=str(found.get("exchange_order_id") or order.exchange_order_id or ""),
        filled_amount=float(filled or 0),
        updated_at=_now(order) + 1,
    )
    return {
        "action": "explained",
        "order_id": order.order_id,
        "status": reported,
        "resubmitted": False,
    }


def apply_fill_pages(repository, pages: list[list[dict]]) -> dict[str, int]:
    """重放成交页。同一 trade_id 只记一次，不会多出订单。"""
    ensure_recovery_tables(repository.engine)
    with repository.engine.begin() as connection:
        for page in pages:
            for fill in page:
                connection.execute(text(
                    "INSERT INTO recovered_fill(trade_id, client_order_id, amount, price) "
                    "VALUES (:trade_id, :client_order_id, :amount, :price) "
                    "ON CONFLICT(trade_id) DO NOTHING"
                ), {
                    "trade_id": str(fill["trade_id"]),
                    "client_order_id": str(fill["client_order_id"]),
                    "amount": float(fill["amount"]),
                    "price": float(fill["price"]),
                })
        rows = connection.execute(text(
            "SELECT client_order_id, COALESCE(SUM(amount), 0), COUNT(*) "
            "FROM recovered_fill GROUP BY client_order_id"
        )).all()
    orders = 0
    fills = 0
    for client_order_id, amount, count in rows:
        fills += int(count)
        matches = [
            order for order in repository.get_orders()
            if str(order.client_order_id or "") == str(client_order_id)
        ]
        orders += len(matches)
        if len(matches) != 1:
            continue
        order = matches[0]
        filled = float(amount)
        if filled >= float(order.amount):
            status = "filled"
        elif filled > 0:
            status = "partially_filled"
        else:
            status = order.status
        _save_order(
            repository, order, status=status, filled_amount=filled, updated_at=_now(order) + 1,
        )
    return {"orders": orders, "fills": fills}


def record_runtime_intent(
    repository, *, name: str, intended: str, actual: str, owner: str, updated_at: int,
) -> None:
    ensure_recovery_tables(repository.engine)
    with repository.engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO runtime_intent(name, intended, actual, owner, updated_at) "
            "VALUES (:name, :intended, :actual, :owner, :updated_at) "
            "ON CONFLICT(name) DO UPDATE SET "
            "intended = excluded.intended, actual = excluded.actual, "
            "owner = excluded.owner, updated_at = excluded.updated_at"
        ), {
            "name": name,
            "intended": intended,
            "actual": actual,
            "owner": owner,
            "updated_at": int(updated_at),
        })


def mark_crashed_actual(repository, *, alive: Callable[[str], bool] | None, now_ms: int) -> bool:
    """进程已退出但记录仍是 running 时，标成未知，要求先对账再恢复。"""
    ensure_recovery_tables(repository.engine)
    checker = alive or _alive
    crashed = False
    with repository.engine.begin() as connection:
        rows = connection.execute(text(
            "SELECT name, owner FROM runtime_intent "
            "WHERE intended = 'running' AND actual = 'running'"
        )).all()
        for name, owner in rows:
            if checker(str(owner)):
                continue
            crashed = True
            connection.execute(text(
                "UPDATE runtime_intent SET actual = 'unknown', updated_at = :updated "
                "WHERE name = :name"
            ), {"name": name, "updated": int(now_ms)})
    return crashed


def mark_owner_stopped(repository, owner: str) -> None:
    ensure_recovery_tables(repository.engine)
    with repository.engine.begin() as connection:
        connection.execute(text(
            "UPDATE runtime_intent SET intended = 'stopped', actual = 'stopped', "
            "updated_at = :updated WHERE owner = :owner"
        ), {"owner": owner, "updated": int(time.time() * 1000)})


def resume_allowed(repository, name: str, owner: str) -> bool:
    del name, owner
    engine = getattr(repository, "engine", None)
    dialect = getattr(getattr(engine, "dialect", None), "name", None)
    if dialect != "sqlite":
        return True
    pause = repository.get_opening_pause()
    if getattr(pause, "paused", False) and getattr(pause, "reason", "") in _PAUSE_REASONS:
        return False
    from src.ops.lifecycle import unresolved_live_order_ids

    return not unresolved_live_order_ids(repository)


async def sync_forever(
    interval_s: float | None = None,
    fetch: Callable[[], dict] | None = None,
) -> None:
    """先等待，再按间隔做有限次同步。没有注入函数时不访问交易所。"""
    if interval_s is None:
        interval = float(os.environ.get("OKX_BOT_PRIVATE_SYNC_SECONDS", "30"))
    else:
        interval = interval_s
    await asyncio.sleep(interval)
    while True:
        try:
            if fetch is not None:
                from src.data.repository import Repository

                run_bounded_sync(
                    Repository(), fetch, now_ms=int(time.time() * 1000),
                )
        except Exception:
            logger.exception("私有同步失败")
        await asyncio.sleep(interval)


def _write_freshness(
    repository, *, status: str, attempts: int, fresh: bool, cursor_ms: int | None,
    observed_at: int | None, updated_at: int,
) -> None:
    with repository.engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO private_sync_freshness("
            "id, status, attempts, fresh, cursor_ms, observed_at, complete, updated_at) "
            "VALUES (1, :status, :attempts, :fresh, :cursor_ms, :observed_at, 0, :updated_at) "
            "ON CONFLICT(id) DO UPDATE SET "
            "status = excluded.status, attempts = excluded.attempts, fresh = excluded.fresh, "
            "cursor_ms = excluded.cursor_ms, observed_at = excluded.observed_at, "
            "complete = 0, updated_at = excluded.updated_at"
        ), {
            "status": status,
            "attempts": attempts,
            "fresh": fresh,
            "cursor_ms": cursor_ms,
            "observed_at": observed_at,
            "updated_at": int(updated_at),
        })


def _save_order(repository, order, **changes) -> None:
    payload = {
        "order_id": order.order_id,
        "client_order_id": order.client_order_id or "",
        "exchange_order_id": order.exchange_order_id or "",
        "strategy": order.strategy,
        "symbol": order.symbol,
        "side": order.side,
        "type": order.type,
        "amount": order.amount,
        "price": order.price,
        "status": order.status,
        "fill_price": order.fill_price,
        "timestamp": order.timestamp,
        "updated_at": order.updated_at,
        "filled_amount": order.filled_amount,
        "remaining_amount": order.remaining_amount,
        "source": getattr(order, "source", "unknown"),
        "runtime_mode": getattr(order, "runtime_mode", "unknown"),
    }
    payload.update(changes)
    filled = float(payload["filled_amount"] or 0)
    amount = float(payload["amount"] or 0)
    payload["remaining_amount"] = max(0.0, amount - filled)
    repository.upsert_order(OrderRecord(**payload))


def _stored_cursor(repository) -> int | None:
    with repository.engine.connect() as connection:
        row = connection.execute(text(
            "SELECT cursor_ms FROM private_sync_freshness WHERE id = 1"
        )).first()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def _now(order) -> int:
    return int(getattr(order, "updated_at", 0) or 0)


def _alive(owner: str) -> bool:
    return owner == str(os.getpid())
