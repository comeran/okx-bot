"""把本地 SQLite 复制成可恢复的备份。"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def backup_sqlite(source: str, destination: str) -> None:
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(source)
    destination_conn = sqlite3.connect(destination)
    try:
        source_conn.execute("PRAGMA wal_checkpoint(FULL)")
        with destination_conn:
            source_conn.backup(destination_conn)
    finally:
        source_conn.close()
        destination_conn.close()
