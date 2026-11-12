"""Bounded immutable projections of synchronization audit receipts.

The original JSON remains an audit journal. Only these allowlisted fields cross
into spot preflight; no provider payload, cursor, or operation result is retained.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from src.data.models import PrivateSyncReceiptRecord

_PART_KINDS = {"account": "account_snapshot", "orders": "open_orders", "trades": "recent_trades"}
_RECEIPT_STATES = frozenset({"in_progress", "committed", "partial", "failed", "abandoned"})
_PART_STATES = frozenset({"pending", "available", "skipped", "unavailable", "failed"})
_COMPLETENESS = frozenset({"unknown", "partial", "complete"})


def _utf8_text(value: str) -> bool:
    try:
        value.encode("utf-8")
        return True
    except UnicodeEncodeError:
        return False


def valid_receipt_time(value: object) -> bool:
    return type(value) is int and 0 <= value <= 2**63 - 1


@dataclass(frozen=True)
class PrivateSyncPartSnapshot:
    name: str
    state: str | None
    received_at: int | None
    coverage_kind: str | None
    completeness: str | None
    symbols: tuple[str, ...] | None


@dataclass(frozen=True)
class PrivateSyncReceiptSnapshot:
    attempt_id: str
    generation: int
    account_scope_id: str
    state: str
    symbols: tuple[str, ...] | None
    started_at: int
    completed_at: int | None
    received_at: int | None
    error_code: str | None
    parts: tuple[PrivateSyncPartSnapshot, ...]


def snapshot_private_sync_receipt(
    record: PrivateSyncReceiptRecord, *, account_scope_id: str, strict: bool = True,
) -> PrivateSyncReceiptSnapshot:
    """Validate stored metadata; relaxed input merely leaves bad part fields unknown.

    Relaxed mode supports pure diagnostic callers with untrusted observations. The
    repository always uses strict mode before returning its detached snapshot.
    Missing legacy part metadata remains unknown in either mode.
    """
    def invalid():
        raise ValueError("Invalid stored private sync receipt")

    def optional_time(value):
        if value is None or valid_receipt_time(value):
            return value
        if strict:
            invalid()
        return None

    def symbols(value):
        if value is None:
            return None
        if (not isinstance(value, (list, tuple)) or len(value) > 100
                or any(not isinstance(item, str) or not item or len(item) > 128
                       or item != item.strip() or not _utf8_text(item)
                       or any(ord(char) < 33 for char in item)
                       for item in value)):
            if strict:
                invalid()
            return None
        return tuple(value)

    def label(value, allowed):
        if value is None or isinstance(value, str) and value in allowed:
            return value
        if strict:
            invalid()
        return None

    if (record.account_scope_id != account_scope_id
            or not isinstance(record.attempt_id, str) or not record.attempt_id
            or len(record.attempt_id) > 128 or record.attempt_id != record.attempt_id.strip()
            or not _utf8_text(record.attempt_id)
            or any(ord(char) < 33 for char in record.attempt_id)
            or type(record.generation) is not int or not 0 < record.generation <= 2**63 - 1
            or not isinstance(record.state, str) or record.state not in _RECEIPT_STATES
            or not valid_receipt_time(record.started_at)):
        invalid()
    completed_at = optional_time(record.completed_at)
    received_at = optional_time(record.received_at)
    error_code = record.error_code
    if error_code is not None and (
        not isinstance(error_code, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error_code) is None
    ):
        invalid()
    if strict:
        if (type(record.limit) is not int or not 0 < record.limit <= 1000
                or record.since is not None and not valid_receipt_time(record.since)):
            invalid()
        if record.state == "in_progress":
            if completed_at is not None or received_at is not None or error_code is not None:
                invalid()
        elif completed_at is None or completed_at < record.started_at:
            invalid()
        if record.state in {"committed", "partial"}:
            if (received_at is None or error_code is not None
                    or not record.started_at <= received_at <= completed_at):
                invalid()
        elif record.state in {"failed", "abandoned"}:
            if received_at is not None or error_code is None:
                invalid()
        # Bound even ignored journal fields without exposing them in the snapshot.
        for value in (record.parts, record.cursor, record.result):
            if not isinstance(value, dict):
                invalid()
            try:
                encoded = json.dumps(value, allow_nan=False, separators=(",", ":"))
                if len(encoded.encode("utf-8")) > 65_536:
                    invalid()
            except (TypeError, ValueError, OverflowError, RecursionError):
                invalid()
    parts = []
    raw_parts = record.parts if isinstance(record.parts, dict) else {}
    for name, expected_kind in _PART_KINDS.items():
        raw = raw_parts.get(name)
        if raw is None:
            continue
        if not isinstance(raw, dict):
            if strict:
                invalid()
            continue
        coverage = raw.get("coverage", {})
        if not isinstance(coverage, dict):
            if strict:
                invalid()
            coverage = {}
        part_time = optional_time(raw.get("received_at"))
        if part_time is not None and (
            part_time < record.started_at
            or completed_at is not None and part_time > completed_at
            or received_at is not None and part_time > received_at
        ):
            if strict:
                invalid()
            part_time = None
        parts.append(PrivateSyncPartSnapshot(
            name=name, state=label(raw.get("state"), _PART_STATES), received_at=part_time,
            coverage_kind=label(coverage.get("kind"), {expected_kind}),
            completeness=label(coverage.get("completeness"), _COMPLETENESS),
            symbols=symbols(coverage.get("symbols")),
        ))
    return PrivateSyncReceiptSnapshot(
        attempt_id=record.attempt_id, generation=record.generation,
        account_scope_id=account_scope_id, state=record.state, symbols=symbols(record.symbols),
        started_at=record.started_at, completed_at=completed_at, received_at=received_at,
        error_code=error_code, parts=tuple(parts),
    )
