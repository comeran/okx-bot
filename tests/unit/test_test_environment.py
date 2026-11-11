import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("preset_paths", [False, True])
def test_collection_does_not_create_or_migrate_user_database(tmp_path, preset_paths):
    project_root = Path(__file__).resolve().parents[2]
    probe_tests = tmp_path / "tests"
    probe_tests.mkdir()
    (probe_tests / "conftest.py").write_text(
        (project_root / "tests/conftest.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (probe_tests / "test_collection_probe.py").write_text(
        "from src.web.app import app\n\ndef test_probe():\n    assert app is not None\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.pop("OKX_BOT_DB_PATH", None)
    env.pop("OKX_BOT_SETTINGS_PATH", None)
    env["PYTHONPATH"] = str(project_root)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    user_database = tmp_path / "data/bot.db"
    if preset_paths:
        user_database = tmp_path / "user.db"
        with sqlite3.connect(user_database) as connection:
            connection.execute("CREATE TABLE sentinel (value TEXT)")
            connection.execute("INSERT INTO sentinel VALUES ('untouched')")
        env["OKX_BOT_DB_PATH"] = str(user_database)
        env["OKX_BOT_SETTINGS_PATH"] = str(tmp_path / "user-settings.yaml")

    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", "--collect-only", "-q",
            "-p", "no:cacheprovider", str(probe_tests),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 test collected" in result.stdout
    if preset_paths:
        with sqlite3.connect(f"file:{user_database}?mode=ro", uri=True) as connection:
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
            assert tables == [("sentinel",)]
            assert connection.execute("SELECT value FROM sentinel").fetchall() == [("untouched",)]
    else:
        assert not user_database.exists()
