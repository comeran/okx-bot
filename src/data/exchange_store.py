"""Open account facts only after validating their immutable on-disk binding."""

from __future__ import annotations

import fcntl
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from src.core.execution_scope import ExecutionScope
from src.data.repository import Repository


class ExchangeStoreError(RuntimeError):
    """Account facts storage is unavailable; callers must never use master instead."""


def _header_values(scope: ExecutionScope) -> tuple[int, str, str, str, str]:
    return (
        1, scope.account_scope_id, scope.exchange, scope.environment, scope.account_fingerprint,
    )


def _facts_path(scope: ExecutionScope, master_engine: Engine) -> Path:
    database = master_engine.url.database
    if (
        master_engine.dialect.name != "sqlite" or not database or database == ":memory:"
        or database.startswith("file:")
    ):
        raise ExchangeStoreError("A persistent SQLite master database is required")
    master = Path(database).resolve()
    return master.parent / "account-facts" / master.name / f"{scope.account_scope_id}.db"


def _validate_header(path: Path, scope: ExecutionScope) -> None:
    if path.is_symlink() or not path.is_file():
        raise ExchangeStoreError("Exchange facts binding is missing or invalid")
    # Participate in SQLite locks so validation stays consistent during WAL checkpoints.
    # Read-only connections can create SQLite-managed WAL/SHM sidecars, never facts.
    uri = f"{path.as_uri()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            rows = connection.execute(
                "SELECT schema_version, account_scope_id, exchange, environment, "
                "account_fingerprint FROM execution_scope_header"
            ).fetchall()
    except sqlite3.Error:
        raise ExchangeStoreError("Exchange facts binding is missing or invalid") from None
    if rows != [_header_values(scope)]:
        raise ExchangeStoreError("Exchange facts binding is incompatible")


def _publish_header(path: Path, scope: ExecutionScope) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{scope.account_scope_id}.", dir=path.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        with closing(sqlite3.connect(temporary)) as connection, connection:
            connection.execute(
                "CREATE TABLE execution_scope_header ("
                "singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
                "schema_version INTEGER NOT NULL, account_scope_id TEXT NOT NULL, "
                "exchange TEXT NOT NULL, environment TEXT NOT NULL, "
                "account_fingerprint TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO execution_scope_header VALUES (1, ?, ?, ?, ?, ?)",
                _header_values(scope),
            )
            for action in ("INSERT", "UPDATE", "DELETE"):
                connection.execute(
                    f"CREATE TRIGGER execution_scope_header_no_{action.lower()} "
                    f"BEFORE {action} ON execution_scope_header "
                    "BEGIN SELECT RAISE(ABORT, 'immutable execution scope'); END"
                )
        with temporary.open("rb") as header_file:
            os.fsync(header_file.fileno())
        try:
            # Same-directory hard link publishes a complete DB without replacing a winner.
            os.link(temporary, path)
        except FileExistsError:
            pass
    finally:
        temporary.unlink(missing_ok=True)


def open_exchange_repository(scope: ExecutionScope, *, master_engine: Engine) -> Repository:
    if not isinstance(scope, ExecutionScope):
        raise ExchangeStoreError("A valid execution account scope is required")
    try:
        path = _facts_path(scope, master_engine)
        for directory in (path.parent.parent, path.parent):
            if directory.is_symlink():
                raise ExchangeStoreError("Exchange facts directory is invalid")
            directory.mkdir(mode=0o700, exist_ok=True)
        if not path.exists() and not path.is_symlink():
            _publish_header(path, scope)
        _validate_header(path, scope)
        lock_path = path.with_suffix(".bootstrap.lock")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            # Serialize create_all/migrations for competing threads and processes.
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            _validate_header(path, scope)
            return Repository(db_path=str(path))
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
    except (OSError, sqlite3.Error, SQLAlchemyError):
        raise ExchangeStoreError("Exchange facts storage could not be opened") from None
