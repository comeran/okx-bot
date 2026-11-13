"""Diagnostic saved-allocation checks; cached facts never grant execution authority."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Context, Decimal, InvalidOperation, localcontext
from typing import Any

from src.data.allocations import AllocationDraft

PROVIDER_MAX_AGE_MS = 30_000
PROVIDER_MAX_FUTURE_MS = 5_000
_TERMINAL_ORDER_STATUSES = {"filled", "canceled", "cancelled", "rejected"}


def _value(record: object, field: str, default: object = None) -> Any:
    return (
        record.get(field, default)
        if isinstance(record, Mapping)
        else getattr(record, field, default)
    )


def _observed_decimal(value: object) -> Decimal | None:
    # Legacy observations and drafts are float-derived, never authoritative ledger inputs.
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and number >= 0 else None


def _decimal_string(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if value == 0 else rendered


def _trusted_account(account: object) -> bool:
    return (
        account is not None
        and _value(account, "strategy") == "__exchange__"
        and _value(account, "source") == "live_exchange"
        and _value(account, "runtime_mode") == "live"
        and _value(account, "active", True) is not False
    )


def _provider_timestamp_status(account: object, evaluated_at: int) -> str:
    timestamp = _value(account, "updated_at")
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp <= 0:
        return "unknown"
    if (
        evaluated_at - timestamp > PROVIDER_MAX_AGE_MS
        or timestamp - evaluated_at > PROVIDER_MAX_FUTURE_MS
    ):
        return "blocked"
    return "passed"


def _quote_observation(account: object, currency: str) -> Decimal | None:
    assets = _value(account, "assets")
    if not isinstance(assets, (list, tuple)):
        return None
    matching = []
    for asset in assets:
        ccy = _value(asset, "ccy")
        if isinstance(ccy, str) and ccy.strip().upper() == currency:
            matching.append(asset)
    if len(matching) != 1:
        return None
    return _observed_decimal(_value(matching[0], "avail_bal"))


def _ledger_status(ledger: Mapping[str, object] | None, draft: AllocationDraft) -> str:
    if ledger is None:
        return "unknown"
    expected = {
        "strategy": draft.strategy,
        "symbol": draft.symbol,
        "currency": draft.currency,
        "allocation_scope_id": draft.scope_id,
        "draft_revision": draft.revision,
        "status": "prepared",
        "execution_ready": False,
    }
    if any(ledger.get(key) != value for key, value in expected.items()):
        return "blocked"
    if ledger.get("execution_ready") is not False:
        return "blocked"
    seed = _observed_decimal(ledger.get("initial_quote"))
    return "passed" if seed is not None and seed == Decimal(str(draft.budget)) else "blocked"


def build_allocation_readiness(
    *,
    draft: AllocationDraft,
    drafts: Sequence[AllocationDraft],
    execution_context: Mapping[str, object],
    lifecycle_status: str,
    account: object | None,
    orders: Sequence[object],
    global_kill: bool,
    account_kill: bool,
    ledger: Mapping[str, object] | None,
    evaluated_at: int,
) -> dict[str, object]:
    """Explain recorded evidence without reserving, seeding, syncing or activating."""
    with localcontext(Context(prec=80)):
        proposed_total = sum(
            (
                Decimal(str(item.budget))
                for item in drafts
                if item.scope_id == draft.scope_id and item.currency == draft.currency
            ),
            Decimal(0),
        )
    trusted_account = _trusted_account(account)
    timestamp_status = (
        _provider_timestamp_status(account, evaluated_at) if trusted_account else "unknown"
    )
    observed_quote = (
        _quote_observation(account, draft.currency)
        if trusted_account and timestamp_status == "passed"
        else None
    )
    comparison = "unavailable"
    if observed_quote is not None:
        comparison = "within_observed" if proposed_total <= observed_quote else "exceeds_observed"
    open_orders = any(
        str(_value(order, "status", "")).strip().lower() not in _TERMINAL_ORDER_STATUSES
        for order in orders
    )
    checks = {
        "strategy_stopped": "passed" if lifecycle_status == "stopped" else "blocked",
        "account_snapshot": "passed" if trusted_account else "unknown",
        # Provider update time is not a local receipt or a complete snapshot generation.
        "provider_timestamp": timestamp_status,
        "global_kill": "blocked" if global_kill else "passed",
        "account_kill": "blocked" if account_kill else "passed",
        "observed_open_orders": "blocked" if open_orders else "unknown",
        "quote_capacity": "unknown",
        "ledger_state": _ledger_status(ledger, draft),
        "identity_verified": "unknown",
        "account_mode": "unknown",
        "instrument_metadata": "unknown",
        "sync_completeness": "unknown",
        "reconciliation": "unknown",
        "activation_implemented": "blocked",
    }
    return {
        "execution_context": dict(execution_context),
        "allocation_scope_id": draft.scope_id,
        "draft_revision": draft.revision,
        "strategy": draft.strategy,
        "symbol": draft.symbol,
        "currency": draft.currency,
        "budget": _decimal_string(Decimal(str(draft.budget))),
        "evaluated_at": evaluated_at,
        "capacity": {
            "observed_quote_available": (
                _decimal_string(observed_quote) if observed_quote is not None else None
            ),
            "proposed_total": _decimal_string(proposed_total),
            "comparison": comparison,
            "trusted": False,
        },
        "ledger": dict(ledger) if ledger is not None else None,
        "checks": [{"code": code, "status": status} for code, status in checks.items()],
        "can_activate": False,
        "execution_ready": False,
    }
