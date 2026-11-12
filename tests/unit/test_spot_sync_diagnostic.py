"""Audit receipt state and timestamps never prove complete financial coverage."""

from copy import deepcopy

import pytest

from src.data.models import PrivateSyncReceiptRecord

SCOPE = "a" * 64


def receipt(*, parts=None, **overrides):
    return PrivateSyncReceiptRecord(**{
        "attempt_id": "attempt-one", "generation": 1, "account_scope_id": SCOPE,
        "state": "committed", "symbols": ["BTC-USDT"], "since": 10, "limit": 100,
        "started_at": 1000, "completed_at": 1100, "received_at": 1090,
        "parts": {
            name: {
                "state": "available", "received_at": 1090,
                "provider_timestamp": 1090,
                "coverage": {
                    "kind": kind, "completeness": "unknown",
                    "symbols": None if name == "account" else ["BTC-USDT"],
                },
            }
            for name, kind in (("account", "account_snapshot"), ("orders", "open_orders"),
                               ("trades", "recent_trades"))
        } if parts is None else parts,
        "cursor": {"durable": False, "complete": False}, "result": {}, **overrides,
    })


def evaluate(*, latest=None, committed=None, **overrides):
    from src.ops.spot_sync_diagnostic import evaluate_spot_sync_diagnostic

    return evaluate_spot_sync_diagnostic(**{
        "latest_attempt": latest, "last_committed": committed,
        "account_scope_id": SCOPE, "symbol": "BTC-USDT", "now_ms": 1200,
        "max_age_ms": 30_000, **overrides,
    })


def statuses(result):
    return {check.code: check.status for check in result.checks}


def test_absent_receipts_leave_every_audit_check_unknown():
    result = evaluate()
    assert statuses(result) == {
        "sync_attempt_state": "unknown", "sync_receipt_freshness": "unknown",
        "sync_symbol_scope": "unknown", "sync_part_coverage": "unknown",
    }
    assert result.latest_attempt is result.last_committed is None


def test_committed_audit_checks_can_pass_without_proving_complete_coverage():
    saved = receipt()
    before = deepcopy(saved.model_dump())
    result = evaluate(latest=saved, committed=saved)
    assert statuses(result) == {
        "sync_attempt_state": "pass", "sync_receipt_freshness": "pass",
        "sync_symbol_scope": "pass", "sync_part_coverage": "unknown",
    }
    assert saved.model_dump() == before


@pytest.mark.parametrize("state,want", [("partial", "fail"), ("failed", "fail"),
                                        ("abandoned", "fail"), ("in_progress", "unknown")])
def test_latest_attempt_state_is_not_hidden_by_an_older_committed_receipt(state, want):
    old = receipt()
    latest = receipt(
        attempt_id="attempt-two", generation=2, state=state, started_at=1150,
        completed_at=None if state == "in_progress" else 1200,
        received_at=1190 if state == "partial" else None,
        parts={} if state != "partial" else None,
        error_code="provider_unavailable" if state in {"failed", "abandoned"} else None,
    )
    if state == "partial":
        for part in latest.parts.values():
            part["received_at"] = part["provider_timestamp"] = 1190
    result = evaluate(latest=latest, committed=latest if state == "partial" else old)
    assert statuses(result)["sync_attempt_state"] == want
    if state != "partial":
        assert result.last_committed["attempt_id"] == "attempt-one"
        assert result.last_committed["received_at"] == 1090


@pytest.mark.parametrize("now,want", [(31_090, "pass"), (31_091, "unknown"),
                                      (1089, "unknown"), (True, "unknown")])
def test_receipt_freshness_uses_inclusive_local_age_and_rejects_future_or_boolean_clock(now, want):
    saved = receipt()
    result = evaluate(latest=saved, committed=saved, now_ms=now)
    assert statuses(result)["sync_receipt_freshness"] == want
    assert statuses(result)["sync_part_coverage"] == "unknown"


def test_fresh_top_level_maximum_cannot_hide_a_stale_account_part():
    saved = receipt()
    saved.parts["account"]["received_at"] = 1010
    result = evaluate(latest=saved, committed=saved, now_ms=31_011)
    assert statuses(result)["sync_receipt_freshness"] == "unknown"


@pytest.mark.parametrize("name", ["account", "orders", "trades"])
@pytest.mark.parametrize("value", [None, True, "1090", 1201, -1])
def test_each_required_part_needs_a_valid_local_receipt_time(name, value):
    saved = receipt()
    saved.parts[name]["received_at"] = value
    result = evaluate(latest=saved, committed=saved)
    assert statuses(result)["sync_receipt_freshness"] != "pass"
    assert statuses(result)["sync_part_coverage"] == "unknown"


@pytest.mark.parametrize("name", ["account", "orders", "trades"])
def test_missing_part_never_inherits_another_parts_freshness(name):
    saved = receipt()
    del saved.parts[name]
    result = evaluate(latest=saved, committed=saved)
    assert statuses(result)["sync_receipt_freshness"] != "pass"
    assert statuses(result)["sync_part_coverage"] == "unknown"


@pytest.mark.parametrize("state", ["pending", "skipped", "unavailable", "failed"])
def test_nonavailable_parts_never_claim_fresh_complete_coverage(state):
    saved = receipt()
    saved.parts["trades"]["state"] = state
    result = evaluate(latest=saved, committed=saved)
    assert statuses(result)["sync_receipt_freshness"] != "pass"
    assert statuses(result)["sync_part_coverage"] == "unknown"


@pytest.mark.parametrize("symbols", [None, [], ["ETH-USDT"]])
def test_unobserved_requested_symbol_cannot_pass_receipt_symbol_scope(symbols):
    saved = receipt(symbols=symbols)
    result = evaluate(latest=saved, committed=saved)
    assert statuses(result)["sync_symbol_scope"] != "pass"
    assert statuses(result)["sync_part_coverage"] == "unknown"


@pytest.mark.parametrize("name", ["orders", "trades"])
def test_part_symbol_scope_cannot_be_inferred_from_the_attempt_request(name):
    saved = receipt()
    saved.parts[name]["coverage"]["symbols"] = ["ETH-USDT"]
    result = evaluate(latest=saved, committed=saved)
    assert statuses(result)["sync_symbol_scope"] != "pass"


@pytest.mark.parametrize("completeness", ["unknown", "partial", "complete"])
def test_any_compatibility_coverage_claim_keeps_sync_completeness_unknown(completeness):
    saved = receipt(cursor={"complete": True, "durable": True})
    for part in saved.parts.values():
        part["coverage"]["completeness"] = completeness
    assert statuses(evaluate(latest=saved, committed=saved))["sync_part_coverage"] == "unknown"


def test_sparse_legacy_receipts_are_unknown_without_inventing_part_coverage():
    saved = receipt(parts={"account": {"received_at": 1090, "completeness": "unknown"}})
    result = evaluate(latest=saved, committed=saved)
    assert statuses(result)["sync_receipt_freshness"] != "pass"
    assert statuses(result)["sync_part_coverage"] == "unknown"


@pytest.mark.parametrize("max_age_ms", [None, True, -1, "30000"])
def test_invalid_local_freshness_policy_never_accepts_receipts(max_age_ms):
    saved = receipt()
    result = evaluate(latest=saved, committed=saved, max_age_ms=max_age_ms)
    assert statuses(result)["sync_receipt_freshness"] == "unknown"


def test_summary_is_a_detached_projection_without_private_provider_payloads():
    saved = receipt(result={"raw": "private-provider-result"},
                    cursor={"token": "private-provider-cursor"})
    saved.parts["account"]["raw"] = "private-provider-account"
    before = deepcopy(saved.model_dump())
    result = evaluate(latest=saved, committed=saved)
    rendered = repr(result)
    for secret in ("private-provider-result", "private-provider-cursor",
                   "private-provider-account"):
        assert secret not in rendered
    assert "result" not in result.last_committed and "cursor" not in result.last_committed
    assert saved.model_dump() == before


@pytest.mark.parametrize("target", ["receipt", "orders", "trades"])
@pytest.mark.parametrize("surrogate", [0xD800, 0xDFFF])
def test_non_utf8_symbol_observations_are_unknown_in_pure_diagnostics(target, surrogate):
    saved = receipt()
    if target == "receipt":
        saved.symbols = [chr(surrogate)]
    else:
        saved.parts[target]["coverage"]["symbols"] = [chr(surrogate)]
    result = evaluate(latest=saved, committed=saved)
    assert statuses(result)["sync_symbol_scope"] == "unknown"
    repr(result).encode("utf-8")
