"""第六阶段：可复现打包、迁移回滚、故障演练。实盘开仓保持关闭。"""

import json
import sys
from pathlib import Path

from sqlalchemy import text

from src.data.models import OrderRecord
from src.data.repository import Repository
from src.ops.phase6_release import (
    apply_migrations,
    apply_retention,
    evaluate_live_acceptance,
    export_audit,
    package_fingerprint,
    rehearse_rollback,
    run_release_drills,
    supervise_process,
)


def test_package_fingerprint_is_stable_and_supervisor_survives_a_closed_ui(tmp_path) -> None:
    root = tmp_path / "package"
    root.mkdir()
    (root / "Dockerfile").write_text("uv sync --frozen\n", encoding="utf-8")
    (root / "uv.lock").write_text("lock-a\n", encoding="utf-8")
    workflow = root / ".github/workflows"
    workflow.mkdir(parents=True)
    (workflow / "test.yml").write_text("uv run pytest\nnpm ci\n", encoding="utf-8")

    first = package_fingerprint(root)
    assert first == package_fingerprint(root)
    (root / "uv.lock").write_text("lock-b\n", encoding="utf-8")
    assert package_fingerprint(root) != first

    repo_root = Path(__file__).resolve().parents[2]
    dockerfile = (repo_root / "Dockerfile").read_text(encoding="utf-8")
    workflow_text = (repo_root / ".github/workflows/test.yml").read_text(encoding="utf-8")
    assert "--frozen" in dockerfile
    assert "uv run pytest" in workflow_text
    assert "npm ci" in workflow_text

    child = tmp_path / "child.py"
    child.write_text("import sys\nsys.exit(1)\n", encoding="utf-8")

    def closed_ui(_event: dict) -> None:
        raise ConnectionError("ui closed")

    result = supervise_process(
        [sys.executable, str(child)], max_restarts=2, ui=closed_ui,
    )
    assert result == {
        "restarts": 2,
        "exits": 3,
        "ui_failures": 3,
        "supervision_continued": True,
        "last_code": 1,
    }


def test_migration_rollback_retention_and_audit_export(tmp_path) -> None:
    database = tmp_path / "bot.db"
    repository = Repository(db_path=str(database))
    assert apply_migrations(repository) == []
    with repository.engine.connect() as connection:
        applied = connection.execute(
            text("SELECT id FROM applied_migration ORDER BY id")
        ).all()
    assert [row[0] for row in applied] == ["0003_release_controls"]
    assert repository.get_schema_version() == 2

    repository.record_operator_action("pause_opening", {"ok": True}, 1_000)
    repository.record_operator_action("old_action", {"ok": True}, 1)
    repository.upsert_order(OrderRecord(
        order_id="ord-1", client_order_id="cid-1", strategy="alpha", symbol="BTC-USDT",
        side="buy", type="limit", amount=1, price=10, status="pending", fill_price=0,
        timestamp=1, updated_at=1,
    ))
    rehearsal = rehearse_rollback(str(database), str(tmp_path / "backup.db"))
    assert rehearsal["restored"] is True

    restored = Repository(db_path=str(database))
    assert restored.get_schema_version() == 2
    assert apply_retention(restored, now_ms=1_000, keep_ms=100) == {"actions_removed": 1}
    names = [item["action"] for item in restored.list_operator_actions(limit=10)]
    assert names == ["pause_opening"]
    assert len(restored.get_orders()) == 1

    destination = tmp_path / "audit.json"
    exported = export_audit(restored, str(destination))
    assert exported["actions"][0]["action"] == "pause_opening"
    assert exported["migrations"] == ["0003_release_controls"]
    payload_text = destination.read_text(encoding="utf-8")
    assert "api_key" not in payload_text
    assert json.loads(payload_text)["actions"][0]["action"] == "pause_opening"


def test_drills_explain_residuals_and_live_acceptance_stays_closed(tmp_path) -> None:
    report = run_release_drills(tmp_path)
    assert report["long_running_exchange"] is False
    assert report["live_open_orders"] is False
    assert [item["name"] for item in report["drills"]] == [
        "restart", "disconnect", "duplicate", "restore",
    ]
    assert all(item["passed"] is True and item["explanation"] for item in report["drills"])

    missing = evaluate_live_acceptance(
        prerequisites_passed=False, authorization="signed-by-operator", funded_quote="10",
    )
    assert missing["accepted"] is False
    assert missing["allow_live_open_orders"] is False
    assert missing["reason"] == "prerequisites_missing"

    unsigned = evaluate_live_acceptance(
        prerequisites_passed=True, authorization="", funded_quote="10",
    )
    assert unsigned["reason"] == "live_not_authorized"
    assert unsigned["allow_live_open_orders"] is False

    signed = evaluate_live_acceptance(
        prerequisites_passed=True, authorization="signed-by-operator", funded_quote="10",
    )
    assert signed["accepted"] is False
    assert signed["allow_live_open_orders"] is False
    assert signed["reason"] == "live_execution_not_enabled"
