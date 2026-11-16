"""发布检查、迁移回滚和故障演练。这里不打开实盘。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from sqlalchemy import text

from src.ops.backup import backup_sqlite

_MIGRATION_ID = "0003_release_controls"
_SECRET_KEYS = {"api_key", "secret", "passphrase", "token", "password"}


def package_fingerprint(root: Path) -> str:
    """锁定文件、镜像和检查工作流的摘要。内容变了，摘要就变。"""
    digest = hashlib.sha256()
    for relative in ("Dockerfile", "uv.lock", ".github/workflows/test.yml"):
        digest.update(relative.encode("utf-8"))
        digest.update((root / relative).read_bytes())
    return digest.hexdigest()


def supervise_process(command: list[str], *, max_restarts: int, ui) -> dict[str, Any]:
    """子进程退出后按次数重启。界面断开只记失败，监督继续。"""
    exits = 0
    restarts = 0
    ui_failures = 0
    last_code = 1
    while True:
        completed = subprocess.run(command, capture_output=True, check=False)
        exits += 1
        last_code = completed.returncode
        try:
            ui({"returncode": last_code})
        except Exception:
            ui_failures += 1
        if last_code == 0 or restarts >= max_restarts:
            break
        restarts += 1
    return {
        "restarts": restarts,
        "exits": exits,
        "ui_failures": ui_failures,
        "supervision_continued": True,
        "last_code": last_code,
    }


def apply_migrations(repository: Any = None, *, engine: Any = None) -> list[str]:
    """记录已经执行的迁移。兼容版本号仍保持 2。"""
    target = engine if engine is not None else repository.engine
    if target.dialect.name != "sqlite":
        return []
    applied: list[str] = []
    with target.begin() as connection:
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS applied_migration ("
            "id VARCHAR PRIMARY KEY, applied_at INTEGER NOT NULL)"
        ))
        present = {
            row[0] for row in connection.execute(text("SELECT id FROM applied_migration")).all()
        }
        if _MIGRATION_ID not in present:
            connection.execute(text(
                "INSERT INTO applied_migration(id, applied_at) VALUES (:id, 0)"
            ), {"id": _MIGRATION_ID})
            applied.append(_MIGRATION_ID)
    return applied


def restore_sqlite(backup: str, destination: str) -> None:
    """用备份整库替换目标，并清掉旧的预写日志。"""
    temporary = f"{destination}.restore"
    backup_sqlite(backup, temporary)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{destination}{suffix}").unlink(missing_ok=True)
    Path(temporary).replace(destination)


def rehearse_rollback(database: str, backup_path: str) -> dict[str, Any]:
    """先备份，再写入探测行，然后恢复。探测行应该消失。"""
    backup_sqlite(database, backup_path)
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE rollback_probe (id INTEGER PRIMARY KEY)")
    connection.execute("INSERT INTO rollback_probe(id) VALUES (1)")
    connection.commit()
    connection.close()
    restore_sqlite(backup_path, database)
    connection = sqlite3.connect(database)
    found = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'rollback_probe'"
    ).fetchone()
    connection.close()
    return {"restored": found is None, "backup": backup_path}


def apply_retention(repository: Any, *, now_ms: int, keep_ms: int) -> dict[str, int]:
    """删掉过期的操作记录。订单不在保留清理范围内。"""
    cutoff = now_ms - keep_ms
    with repository.engine.begin() as connection:
        result = connection.execute(
            text("DELETE FROM operator_action WHERE created_at < :cutoff"),
            {"cutoff": cutoff},
        )
    return {"actions_removed": int(result.rowcount or 0)}


def export_audit(repository: Any, destination: str) -> dict[str, Any]:
    """导出操作记录和迁移编号。密钥字段不写进文件。"""
    with repository.engine.connect() as connection:
        migrations = [
            row[0] for row in connection.execute(
                text("SELECT id FROM applied_migration ORDER BY id")
            ).all()
        ]
    payload = {
        "actions": _redact(repository.list_operator_actions(limit=1000)),
        "migrations": migrations,
    }
    Path(destination).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8",
    )
    return payload


def run_release_drills(root: Path) -> dict[str, Any]:
    """本地演练重启、断线、重复行情和恢复。这不是交易所长跑。"""
    from src.data.repository import Repository

    database = root / "drill.db"
    repository = Repository(db_path=str(database))
    return {
        "long_running_exchange": False,
        "live_open_orders": False,
        "drills": [
            _drill_restart(repository),
            _drill_disconnect(),
            _drill_duplicate(),
            _drill_restore(repository, database),
        ],
    }


def evaluate_live_acceptance(
    *, prerequisites_passed: bool, authorization: str, funded_quote: str,
) -> dict[str, Any]:
    """记录实盘验收为什么不能开始。这个函数不会打开实盘。"""
    if not prerequisites_passed:
        reason = "prerequisites_missing"
    elif not authorization.strip():
        reason = "live_not_authorized"
    else:
        reason = "live_execution_not_enabled"
    return {
        "accepted": False,
        "allow_live_open_orders": False,
        "reason": reason,
        "funded_quote": funded_quote,
    }


def _drill_restart(repository: Any) -> dict[str, Any]:
    from src.ops.lifecycle import startup_reconcile
    from src.ops.phase3_recovery import record_runtime_intent

    record_runtime_intent(
        repository, name="alpha", intended="running", actual="running",
        owner="old-process", updated_at=1,
    )
    result = startup_reconcile(repository, alive=lambda owner: owner != "old-process")
    return {
        "name": "restart",
        "passed": result["paused"] is True and result["resume"] is False,
        "explanation": "进程已退出，恢复前先暂停",
    }


def _drill_disconnect() -> dict[str, Any]:
    from src.ops.phase4_controls import supervise_observation

    result = supervise_observation(
        {"reason": "stale_price", "blocked": True},
        push=lambda _item: (_ for _ in ()).throw(ConnectionError("ui closed")),
    )
    return {
        "name": "disconnect",
        "passed": result["supervision_continued"] is True and result["published"] is False,
        "explanation": "界面断开后，监督仍在继续",
    }


def _drill_duplicate() -> dict[str, Any]:
    from src.research.phase5 import prepare_dataset

    bar = {"open": 10, "high": 10, "low": 10, "volume": 1, "closed": True}
    revised = {"open": 11, "high": 11, "low": 11, "volume": 1, "closed": True}
    prepared = prepare_dataset([
        {"timestamp": 0, "close": 10, **bar},
        {"timestamp": 0, "close": 11, **revised},
    ], timeframe_ms=60_000)
    return {
        "name": "duplicate",
        "passed": len(prepared.bars) == 1 and prepared.bars[0]["close"] == 10,
        "explanation": "重复行情保留第一根收盘",
    }


def _drill_restore(repository: Any, database: Path) -> dict[str, Any]:
    repository.set_opening_pause(True, "drill", 5)
    rehearsal = rehearse_rollback(str(database), str(database.with_suffix(".backup.db")))
    from src.data.repository import Repository

    restored = Repository(db_path=str(database))
    return {
        "name": "restore",
        "passed": rehearsal["restored"] is True and restored.get_opening_pause().paused is True,
        "explanation": "恢复后探测写入消失，暂停状态还在",
    }


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items() if key not in _SECRET_KEYS}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value
