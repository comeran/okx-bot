"""进程启动时对账，退出时释放本进程租约。"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

_UNKNOWN_REASONS = {"live_order_submission_unknown", "live_order_persistence_failed"}
_TERMINAL = {"filled", "cancelled", "canceled", "rejected"}
_RECONCILE_REASON = "unresolved_live_order"


def unresolved_live_order_ids(repository) -> list[str]:
    getter = getattr(repository, "get_risk_events", None)
    if not callable(getter):
        return []
    try:
        events = list(getter())
    except Exception:
        logger.exception("读取未知订单事件失败")
        return ["unknown"]
    clients: list[str] = []
    missing = False
    for event in events:
        reason = str(getattr(event, "reason_code", "") or "")
        payload = getattr(event, "payload", None)
        if not isinstance(payload, dict):
            payload = {}
        client = str(payload.get("client_order_id") or "")
        if reason not in _UNKNOWN_REASONS:
            continue
        if client:
            clients.append(client)
        else:
            missing = True
    if not clients and not missing:
        return []
    try:
        orders = list(repository.get_orders())
    except Exception:
        logger.exception("读取未决订单失败")
        return clients or ["unknown"]
    status_by_client: dict[str, str] = {}
    for order in orders:
        client = str(getattr(order, "client_order_id", "") or "")
        if client:
            status_by_client[client] = str(getattr(order, "status", "") or "").lower()
    if missing:
        return ["unknown"]
    return [client for client in clients if status_by_client.get(client) not in _TERMINAL]


def startup_reconcile(repository) -> dict[str, object]:
    now = int(time.time() * 1000)
    unresolved = unresolved_live_order_ids(repository)
    writer = getattr(repository, "write_runtime_checkpoint", None)
    if unresolved:
        repository.set_opening_pause(True, _RECONCILE_REASON, now)
        if callable(writer):
            writer("paused", now)
        return {"paused": True, "unresolved": len(unresolved)}
    pause = repository.get_opening_pause()
    if getattr(pause, "paused", False) and getattr(pause, "reason", "") == _RECONCILE_REASON:
        repository.set_opening_pause(False, "", now)
    if callable(writer):
        writer("reconciled", now)
    return {"paused": False, "unresolved": 0}


def graceful_shutdown(repository, owner: str) -> None:
    release = getattr(repository, "release_strategy_leases_for_owner", None)
    if callable(release):
        release(owner)
    writer = getattr(repository, "write_runtime_checkpoint", None)
    if callable(writer):
        writer("stopped", int(time.time() * 1000))
