"""持续风控、分开的值班动作、来源新鲜度，以及不依赖界面的告警重试。

默认不连接交易所，也不因为界面断开而停止监督。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from sqlalchemy import text

logger = logging.getLogger(__name__)

_DERIVATIVES = {"swap", "futures", "future", "option"}
_RESTART_FIELDS = ("mode", "market_type", "demo", "web_host", "web_port")
_CONTROLS = ("pause_opening", "stop_engine", "cancel_orders", "flatten", "recover")


def ensure_control_tables(engine) -> None:
    if getattr(getattr(engine, "dialect", None), "name", None) != "sqlite":
        return
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS runtime_risk ("
            "id INTEGER PRIMARY KEY, blocked BOOLEAN NOT NULL, reason VARCHAR NOT NULL, "
            "derivative_margin VARCHAR NOT NULL, exposure FLOAT NOT NULL, "
            "updated_at INTEGER NOT NULL)"
        ))
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS source_heartbeat ("
            "source VARCHAR PRIMARY KEY, observed_at INTEGER NOT NULL, "
            "status VARCHAR NOT NULL)"
        ))
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS alert_outbox ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, kind VARCHAR NOT NULL, "
            "payload_json VARCHAR NOT NULL, status VARCHAR NOT NULL, "
            "attempts INTEGER NOT NULL, updated_at INTEGER NOT NULL)"
        ))
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS config_apply_state ("
            "id INTEGER PRIMARY KEY, state VARCHAR NOT NULL, updated_at INTEGER NOT NULL)"
        ))


def assess_runtime_risk(
    *,
    price_status: str,
    account_status: str,
    position_notional: float,
    pending_notional: float,
    equity: float,
    peak_equity: float,
    daily_pnl: float,
    market_type: str,
    margin_ratio: float | None,
    max_total_position_pct: float,
    max_daily_loss_pct: float,
    max_drawdown_pct: float,
) -> dict[str, object]:
    """未知或过期的事实会拦住新敞口。现货不把保证金当成风险。"""
    exposure = float(position_notional) + float(pending_notional)
    derivative = _derivative_margin(market_type, margin_ratio)
    reason = _risk_reason(
        price_status=price_status,
        account_status=account_status,
        exposure=exposure,
        equity=float(equity),
        peak_equity=float(peak_equity),
        daily_pnl=float(daily_pnl),
        derivative=derivative,
        max_total_position_pct=max_total_position_pct,
        max_daily_loss_pct=max_daily_loss_pct,
        max_drawdown_pct=max_drawdown_pct,
    )
    return {
        "blocked": reason is not None,
        "reason": reason,
        "exposure": exposure,
        "derivative_margin": derivative,
    }


def publish_runtime_risk(repository, assessment: dict[str, object], *, now_ms: int) -> None:
    _require_sqlite(repository)
    reason = "" if assessment.get("reason") is None else str(assessment["reason"])
    with repository.engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO runtime_risk("
            "id, blocked, reason, derivative_margin, exposure, updated_at) "
            "VALUES (1, :blocked, :reason, :derivative, :exposure, :updated) "
            "ON CONFLICT(id) DO UPDATE SET "
            "blocked = excluded.blocked, reason = excluded.reason, "
            "derivative_margin = excluded.derivative_margin, "
            "exposure = excluded.exposure, updated_at = excluded.updated_at"
        ), {
            "blocked": 1 if assessment.get("blocked") else 0,
            "reason": reason,
            "derivative": str(assessment.get("derivative_margin") or ""),
            "exposure": float(assessment.get("exposure") or 0),
            "updated": int(now_ms),
        })


def runtime_risk_reason(repository) -> str | None:
    if not _sqlite(repository):
        return None
    with repository.engine.connect() as connection:
        row = connection.execute(text(
            "SELECT blocked, reason FROM runtime_risk WHERE id = 1"
        )).first()
    if row is None or not bool(row[0]) or not row[1]:
        return None
    return str(row[1])


def apply_control(
    repository,
    action: str,
    *,
    now_ms: int,
    reason: str = "",
    names: list[str] | None = None,
    stop: Callable[[str], None] | None = None,
    worker: Callable[[], dict] | None = None,
) -> dict[str, object]:
    """每个动作只做自己的事。恢复不会下单，风险仍在时拒绝恢复。"""
    if action not in _CONTROLS:
        raise ValueError(f"unknown control: {action}")
    result: dict[str, object] = {
        "action": action,
        "engine_stopped": [],
        "orders_cancelled": [],
        "positions_closed": [],
        "recovered": False,
    }
    if action == "pause_opening":
        repository.set_opening_pause(True, reason or "pause_opening", now_ms)
    elif action == "stop_engine":
        stopped = []
        for name in names or []:
            if stop is not None:
                stop(name)
            stopped.append(name)
        result["engine_stopped"] = stopped
    elif action in {"cancel_orders", "flatten"}:
        payload = worker() if worker is not None else {}
        result["orders_cancelled"] = list(payload.get("cancelled") or [])
        result["positions_closed"] = list(payload.get("closed") or [])
    elif action == "recover":
        blocking = runtime_risk_reason(repository)
        if blocking is not None:
            result["reason"] = blocking
        else:
            repository.set_opening_pause(False, "", now_ms)
            repository.set_kill_switch(False, "", now_ms)
            result["recovered"] = True
    _record(repository, action, result, now_ms)
    return result


def record_source_heartbeat(repository, source: str, *, observed_at: int, status: str) -> None:
    _require_sqlite(repository)
    with repository.engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO source_heartbeat(source, observed_at, status) "
            "VALUES (:source, :observed_at, :status) "
            "ON CONFLICT(source) DO UPDATE SET "
            "observed_at = excluded.observed_at, status = excluded.status"
        ), {"source": source, "observed_at": int(observed_at), "status": status})


def source_freshness(repository, *, now_ms: int, max_age_ms: int) -> dict[str, str]:
    if not _sqlite(repository):
        return {"market": "unknown", "account": "unknown", "private_sync": "unknown"}
    found: dict[str, str] = {}
    with repository.engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT source, observed_at, status FROM source_heartbeat"
        )).all()
    for source, observed_at, status in rows:
        age = int(now_ms) - int(observed_at)
        if status != "fresh" or age > max_age_ms:
            found[str(source)] = "stale"
        else:
            found[str(source)] = "fresh"
    return {
        "market": found.get("market", "unknown"),
        "account": found.get("account", "unknown"),
        "private_sync": found.get("private_sync", "unknown"),
    }


def classify_config(startup: dict, saved: dict, *, applied: bool) -> str:
    if not applied:
        return "saved"
    if any(startup.get(field) != saved.get(field) for field in _RESTART_FIELDS):
        return "restart_required"
    return "effective"


def record_config_state(repository, state: str, now_ms: int) -> None:
    if not _sqlite(repository):
        return
    with repository.engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO config_apply_state(id, state, updated_at) "
            "VALUES (1, :state, :updated) "
            "ON CONFLICT(id) DO UPDATE SET "
            "state = excluded.state, updated_at = excluded.updated_at"
        ), {"state": state, "updated": int(now_ms)})


def config_state(repository) -> str:
    if not _sqlite(repository):
        return "effective"
    with repository.engine.connect() as connection:
        row = connection.execute(text(
            "SELECT state FROM config_apply_state WHERE id = 1"
        )).first()
    if row is None:
        return "effective"
    return str(row[0])


class PushQueue:
    def __init__(self, max_size: int) -> None:
        self.max_size = max_size
        self.items: list[object] = []
        self.dropped = 0

    def push(self, item: object) -> bool:
        if len(self.items) >= self.max_size:
            self.dropped += 1
            return False
        self.items.append(item)
        return True


def supervise_observation(assessment: dict[str, object], *, push: Callable[[object], None]) -> dict:
    published = True
    try:
        push(assessment)
    except Exception:
        logger.warning("界面推送失败，监督继续")
        published = False
    return {
        "reason": assessment.get("reason"),
        "published": published,
        "supervision_continued": True,
    }


def enqueue_alert(repository, kind: str, payload: dict, *, now_ms: int) -> None:
    _require_sqlite(repository)
    with repository.engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO alert_outbox(kind, payload_json, status, attempts, updated_at) "
            "VALUES (:kind, :payload, 'pending', 0, :updated)"
        ), {"kind": kind, "payload": json.dumps(payload), "updated": int(now_ms)})


def deliver_alert(
    repository,
    sender: Callable[[dict], None],
    *,
    now_ms: int,
    max_attempts: int,
) -> dict:
    _require_sqlite(repository)
    with repository.engine.begin() as connection:
        row = connection.execute(text(
            "SELECT id, payload_json, attempts FROM alert_outbox "
            "WHERE status = 'pending' ORDER BY id LIMIT 1"
        )).first()
        if row is None:
            latest = connection.execute(text(
                "SELECT status, attempts FROM alert_outbox ORDER BY id DESC LIMIT 1"
            )).first()
            if latest is None:
                return {"status": "empty", "attempts": 0}
            return {"status": str(latest[0]), "attempts": int(latest[1])}
        alert_id, payload_json, attempts = row
        payload = json.loads(payload_json)
        try:
            sender(payload)
        except Exception:
            logger.warning("告警发送失败，稍后重试")
            updated_attempts = int(attempts) + 1
            status = "failed" if updated_attempts >= max_attempts else "pending"
        else:
            updated_attempts = int(attempts) + 1
            status = "delivered"
        connection.execute(text(
            "UPDATE alert_outbox SET status = :status, attempts = :attempts, "
            "updated_at = :updated WHERE id = :id"
        ), {
            "status": status,
            "attempts": updated_attempts,
            "updated": int(now_ms),
            "id": alert_id,
        })
    return {"status": status, "attempts": updated_attempts}


class AlertOutbox:
    def __init__(self, repository) -> None:
        self.repository = repository

    def pending(self, *, now_ms: int) -> list[int]:
        del now_ms
        if not _sqlite(self.repository):
            return []
        with self.repository.engine.connect() as connection:
            rows = connection.execute(text(
                "SELECT id FROM alert_outbox WHERE status = 'pending' ORDER BY id"
            )).all()
        return [int(row[0]) for row in rows]


def readiness_payload(repository, *, now_ms: int, max_price_age_ms: int) -> dict[str, object]:
    freshness = source_freshness(repository, now_ms=now_ms, max_age_ms=max_price_age_ms)
    price_status = freshness["market"]
    if price_status == "unknown":
        latest = _latest_price(repository)
        if latest is None:
            price_status = "unknown"
        elif int(now_ms) - int(latest) > max_price_age_ms:
            price_status = "stale"
        else:
            price_status = "fresh"
    assessment = assess_runtime_risk(
        price_status=price_status,
        account_status=freshness["account"],
        position_notional=0,
        pending_notional=0,
        equity=1,
        peak_equity=1,
        daily_pnl=0,
        market_type="spot",
        margin_ratio=None,
        max_total_position_pct=0.8,
        max_daily_loss_pct=0.05,
        max_drawdown_pct=0.15,
    )
    reason = assessment["reason"]
    return {
        "ready": not assessment["blocked"],
        "reasons": [] if reason is None else [reason],
    }


def refresh_runtime_risk(repository, *, now_ms: int, max_price_age_ms: int) -> dict[str, object]:
    """根据本地心跳写下一份风险结论。还没有心跳时不暂停开仓。"""
    payload = readiness_payload(
        repository, now_ms=now_ms, max_price_age_ms=max_price_age_ms,
    )
    reason = None if not payload["reasons"] else payload["reasons"][0]
    if reason in {"unknown_price", "unknown_account"}:
        reason = None
    assessment = {
        "blocked": reason is not None,
        "reason": reason,
        "exposure": 0,
        "derivative_margin": "not_applicable",
    }
    if _sqlite(repository):
        publish_runtime_risk(repository, assessment, now_ms=now_ms)
        if assessment["blocked"]:
            repository.set_opening_pause(True, str(reason), now_ms)
    return assessment


def _derivative_margin(market_type: str, margin_ratio: float | None) -> str:
    if str(market_type or "").strip().lower() not in _DERIVATIVES:
        return "not_applicable"
    if margin_ratio is None:
        return "unknown"
    if float(margin_ratio) < 1:
        return "insufficient"
    return "ok"


def _risk_reason(
    *,
    price_status: str,
    account_status: str,
    exposure: float,
    equity: float,
    peak_equity: float,
    daily_pnl: float,
    derivative: str,
    max_total_position_pct: float,
    max_daily_loss_pct: float,
    max_drawdown_pct: float,
) -> str | None:
    if price_status == "unknown":
        return "unknown_price"
    if price_status == "stale":
        return "stale_price"
    if account_status == "unknown":
        return "unknown_account"
    if account_status == "stale":
        return "stale_account"
    if equity > 0 and daily_pnl <= -(equity * max_daily_loss_pct):
        return "daily_loss"
    if peak_equity > 0 and (peak_equity - equity) / peak_equity > max_drawdown_pct:
        return "drawdown"
    if equity > 0 and exposure > equity * max_total_position_pct:
        return "exposure_limit"
    if derivative in {"unknown", "insufficient"}:
        return "derivative_margin"
    return None


def _record(repository, action: str, result: dict, now_ms: int) -> None:
    writer = getattr(repository, "record_operator_action", None)
    if not callable(writer):
        return
    writer(action, result, int(now_ms))


def _sqlite(repository) -> bool:
    engine = getattr(repository, "engine", None)
    if getattr(getattr(engine, "dialect", None), "name", None) != "sqlite":
        return False
    ensure_control_tables(engine)
    return True


def _require_sqlite(repository) -> None:
    if not _sqlite(repository):
        raise RuntimeError("phase4 controls require sqlite")


def _latest_price(repository) -> int | None:
    getter = getattr(repository, "latest_market_timestamp", None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:
        logger.warning("读取行情时间失败")
        return None
