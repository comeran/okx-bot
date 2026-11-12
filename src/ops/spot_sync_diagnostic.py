"""Pure receipt diagnostics; compatibility synchronization never proves coverage."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.private_facts import canonical_spot_symbol
from src.data.models import PrivateSyncReceiptRecord
from src.data.private_sync_snapshot import (
    PrivateSyncReceiptSnapshot,
    snapshot_private_sync_receipt,
    valid_receipt_time,
)
from src.ops.spot_preflight import CheckStatus, SpotPreflightCheck


@dataclass(frozen=True)
class SpotSyncDiagnostic:
    checks: tuple[SpotPreflightCheck, ...]
    latest_attempt: dict[str, object] | None = None
    last_committed: dict[str, object] | None = None


def _summary(record: PrivateSyncReceiptSnapshot | None) -> dict[str, object] | None:
    if record is None:
        return None
    return {
        "attempt_id": record.attempt_id, "generation": record.generation,
        "account_scope_id": record.account_scope_id, "state": record.state,
        "symbols": list(record.symbols) if record.symbols is not None else None,
        "started_at": record.started_at, "completed_at": record.completed_at,
        "received_at": record.received_at, "error_code": record.error_code,
        "parts": {
            part.name: {
                "state": part.state, "received_at": part.received_at,
                "coverage_kind": part.coverage_kind, "completeness": part.completeness,
                "symbols": list(part.symbols) if part.symbols is not None else None,
            }
            for part in record.parts
        },
    }


def evaluate_spot_sync_diagnostic(
    *, latest_attempt: PrivateSyncReceiptSnapshot | PrivateSyncReceiptRecord | None,
    last_committed: PrivateSyncReceiptSnapshot | PrivateSyncReceiptRecord | None,
    account_scope_id: str, symbol: str, now_ms: int, max_age_ms: int | None = 30_000,
) -> SpotSyncDiagnostic:
    """Inspect bounded local audit metadata without reading, fetching, or writing."""
    canonical_spot_symbol(symbol)

    def snapshot(record):
        if record is None:
            return None
        if isinstance(record, PrivateSyncReceiptSnapshot):
            if record.account_scope_id != account_scope_id:
                raise ValueError("Private sync receipt account scope mismatch")
            return record
        return snapshot_private_sync_receipt(
            record, account_scope_id=account_scope_id, strict=False,
        )

    latest = snapshot(latest_attempt)
    committed = snapshot(last_committed)
    checks: list[SpotPreflightCheck] = []

    def add(code: str, status: CheckStatus, reason: str):
        checks.append(SpotPreflightCheck(code, status, reason))

    attempt_status: CheckStatus = "unknown"
    if latest is not None:
        if latest.state == "committed":
            attempt_status = "pass"
        elif latest.state in {"partial", "failed", "abandoned"}:
            attempt_status = "fail"
    add("sync_attempt_state", attempt_status,
        "Only the latest attempt is shown; its state does not establish complete synchronization.")

    parts = {part.name: part for part in committed.parts} if committed is not None else {}
    committed_state = committed is not None and committed.state in {"committed", "partial"}
    policy = valid_receipt_time(now_ms) and valid_receipt_time(max_age_ms)

    def fresh(received_at):
        return (policy and valid_receipt_time(received_at)
                and 0 <= now_ms - received_at <= max_age_ms)

    all_fresh = (committed_state and fresh(committed.received_at)
                 and all(name in parts and parts[name].state == "available"
                         and fresh(parts[name].received_at)
                         for name in ("account", "orders", "trades")))
    add("sync_receipt_freshness", "pass" if all_fresh else "unknown",
        "Each required available part needs its own local receipt within the inclusive age bound.")

    scope_status: CheckStatus = "unknown"
    if committed_state:
        scopes = [committed.symbols] + [
            parts[name].symbols if name in parts else None for name in ("orders", "trades")
        ]
        if any(scope is not None and scope and symbol not in scope for scope in scopes):
            scope_status = "fail"
        elif all(scope is not None and symbol in scope for scope in scopes):
            scope_status = "pass"
    add("sync_symbol_scope", scope_status,
        "The committed request and order/trade part scopes must explicitly include the symbol.")
    add("sync_part_coverage", "unknown",
        "Compatibility receipt state and coverage labels cannot prove complete financial coverage.")
    return SpotSyncDiagnostic(tuple(checks), _summary(latest), _summary(committed))
