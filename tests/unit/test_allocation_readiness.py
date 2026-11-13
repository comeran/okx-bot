from dataclasses import replace
from decimal import Decimal, localcontext
from importlib import import_module
from types import SimpleNamespace

import pytest

from src.data.allocations import AllocationScope, AllocationStore
from src.data.repository import Repository

NOW = 1_000_000
CHECK_CODES = {
    "strategy_stopped",
    "account_snapshot",
    "provider_timestamp",
    "global_kill",
    "account_kill",
    "observed_open_orders",
    "quote_capacity",
    "ledger_state",
    "identity_verified",
    "account_mode",
    "instrument_metadata",
    "sync_completeness",
    "reconciliation",
    "activation_implemented",
}


def scope(**values):
    return AllocationScope(
        account_fingerprint="a" * 64, environment=values.get("environment", "sandbox")
    )


def make_draft(**values):
    from src.data.allocations import AllocationDraft

    return AllocationDraft(
        **{
            "strategy": "alpha",
            "symbol": "BTC-USDT",
            "currency": "USDT",
            "budget": 100.1,
            "revision": 1,
            "scope_id": scope().scope_id,
            "environment": "sandbox",
            "account_fingerprint": "a" * 64,
            "market_type": "spot",
            "created_at": 1,
            "updated_at": 1,
            **values,
        }
    )


def observed_account(**values):
    return SimpleNamespace(
        **{
            "strategy": "__exchange__",
            "source": "live_exchange",
            "runtime_mode": "live",
            "updated_at": NOW,
            "active": True,
            "assets": [{"ccy": "USDT", "avail_bal": 150}],
            # These are deliberately unrelated to quote capacity.
            "cash_balance": 999_999,
            "equity": 999_999,
            "available_balance": 999_999,
            **values,
        }
    )


def preview(**values):
    draft = values.pop("draft", make_draft())
    return import_module("src.ops.allocation_readiness").build_allocation_readiness(
        **{
            "draft": draft,
            "drafts": [draft],
            "execution_context": {"account_scope_id": "execution-scope", "execution_ready": False},
            "lifecycle_status": "stopped",
            "account": observed_account(),
            "orders": [],
            "global_kill": False,
            "account_kill": False,
            "ledger": None,
            "evaluated_at": NOW,
            **values,
        }
    )


def statuses(result):
    assert {check["code"] for check in result["checks"]} == CHECK_CODES
    assert all(set(check) == {"code", "status"} for check in result["checks"])
    return {check["code"]: check["status"] for check in result["checks"]}


def test_saved_drafts_are_only_indicative_same_scope_quote_proposals():
    draft = make_draft()
    others = [
        make_draft(strategy="beta", symbol="ETH-USDT", budget=0.2),
        make_draft(strategy="gamma", symbol="ETH-USDC", currency="USDC", budget=50_000),
        make_draft(strategy="other-account", symbol="SOL-USDT", scope_id="other", budget=50_000),
    ]
    result = preview(draft=draft, drafts=[draft, *others])
    assert Decimal(result["budget"]) == Decimal("100.1")
    assert Decimal(result["capacity"]["proposed_total"]) == Decimal("100.3")
    assert result["capacity"] == {
        "observed_quote_available": "150",
        "proposed_total": "100.3",
        "comparison": "within_observed",
        "trusted": False,
    }
    checks = statuses(result)
    assert checks["quote_capacity"] == "unknown"
    assert checks["observed_open_orders"] == checks["sync_completeness"] == "unknown"
    assert checks["identity_verified"] == checks["account_mode"] == "unknown"
    assert checks["instrument_metadata"] == checks["reconciliation"] == "unknown"
    assert checks["activation_implemented"] == "blocked"
    assert result["ledger"] is None
    assert result["can_activate"] is result["execution_ready"] is False


def test_proposal_sum_does_not_inherit_process_decimal_precision():
    draft = make_draft(budget=123456789.1)
    other = make_draft(strategy="beta", symbol="ETH-USDT", budget=0.2)
    with localcontext() as context:
        context.prec = 3
        result = preview(draft=draft, drafts=[draft, other])
    assert result["capacity"]["proposed_total"] == "123456789.3"


def test_observed_shortfall_is_comparison_without_authoritative_capacity_pass():
    result = preview(account=observed_account(assets=[{"ccy": "USDT", "avail_bal": "0.1"}]))
    assert result["capacity"]["comparison"] == "exceeds_observed"
    assert statuses(result)["quote_capacity"] == "unknown"


@pytest.mark.parametrize(
    "assets",
    [
        [],
        [{"ccy": "USDC", "avail_bal": 999}],
        [{"ccy": "USDT"}],
        [{"ccy": "USDT", "avail_bal": 10}, {"ccy": "usdt", "avail_bal": 20}],
        [{"ccy": "USDT", "avail_bal": True}],
        [{"ccy": "USDT", "avail_bal": -1}],
        [{"ccy": "USDT", "avail_bal": "NaN"}],
        [{"ccy": "USDT", "avail_bal": "Infinity"}],
        [{"ccy": "USDT", "avail_bal": "bad"}],
        [{"ccy": "USDT", "avail_bal": None}],
    ],
)
def test_missing_ambiguous_or_malformed_quote_is_unknown_never_zero(assets):
    result = preview(account=observed_account(assets=assets))
    assert result["capacity"]["observed_quote_available"] is None
    assert result["capacity"]["comparison"] == "unavailable"
    assert statuses(result)["quote_capacity"] == "unknown"


def test_zero_quote_is_a_real_indicative_observation_and_other_quote_is_native():
    draft = make_draft(symbol="ETH-BTC", currency="BTC", budget=0.01)
    result = preview(
        draft=draft,
        account=observed_account(
            assets=[
                {"ccy": "USDT", "avail_bal": 10_000},
                {"ccy": "BTC", "avail_bal": 0},
            ]
        ),
    )
    assert result["capacity"]["observed_quote_available"] == "0"
    assert result["capacity"]["comparison"] == "exceeds_observed"


@pytest.mark.parametrize(
    "timestamp,expected",
    [
        (NOW, "passed"),
        (NOW - 30_000, "passed"),
        (NOW + 5_000, "passed"),
        (NOW - 30_001, "blocked"),
        (NOW + 5_001, "blocked"),
        (0, "unknown"),
        (None, "unknown"),
        (True, "unknown"),
        (-1, "unknown"),
    ],
)
def test_provider_timestamp_is_only_bounded_provider_update_evidence(timestamp, expected):
    result = preview(account=observed_account(updated_at=timestamp))
    checks = statuses(result)
    assert checks["provider_timestamp"] == expected
    assert checks["sync_completeness"] == "unknown"
    if expected != "passed":
        assert result["capacity"]["observed_quote_available"] is None
        assert result["capacity"]["comparison"] == "unavailable"


@pytest.mark.parametrize(
    "account",
    [
        None,
        observed_account(source="paper"),
        observed_account(runtime_mode="paper"),
        observed_account(strategy="alpha"),
        observed_account(active=False),
    ],
)
def test_missing_or_untrusted_account_cannot_supply_capacity(account):
    result = preview(account=account)
    assert statuses(result)["account_snapshot"] == "unknown"
    assert result["capacity"]["observed_quote_available"] is None


@pytest.mark.parametrize("status", ["pending", "submitted", "partially_filled", "unknown", ""])
def test_account_wide_pending_and_unknown_order_outcomes_block(status):
    result = preview(
        orders=[SimpleNamespace(status=status, symbol="ETH-USDC", strategy="external")]
    )
    assert statuses(result)["observed_open_orders"] == "blocked"


@pytest.mark.parametrize("status", ["filled", "canceled", "cancelled", "rejected"])
def test_terminal_rows_do_not_claim_an_empty_complete_order_snapshot(status):
    result = preview(orders=[SimpleNamespace(status=status)])
    assert statuses(result)["observed_open_orders"] == "unknown"


@pytest.mark.parametrize(
    "global_kill,account_kill", [(False, False), (True, False), (False, True), (True, True)]
)
def test_independent_kills_always_keep_activation_closed(global_kill, account_kill):
    result = preview(global_kill=global_kill, account_kill=account_kill)
    checks = statuses(result)
    assert checks["global_kill"] == ("blocked" if global_kill else "passed")
    assert checks["account_kill"] == ("blocked" if account_kill else "passed")
    assert result["can_activate"] is result["execution_ready"] is False


@pytest.mark.parametrize("lifecycle", ["running", "starting", "unknown", "error"])
def test_only_stopped_strategy_passes_lifecycle_check(lifecycle):
    assert statuses(preview(lifecycle_status=lifecycle))["strategy_stopped"] == "blocked"


def prepared_ledger():
    draft = make_draft()
    return {
        "strategy": draft.strategy,
        "symbol": draft.symbol,
        "currency": draft.currency,
        "allocation_scope_id": draft.scope_id,
        "draft_revision": draft.revision,
        "initial_quote": "100.1",
        "status": "prepared",
        "execution_ready": False,
    }


def test_matching_prepared_ledger_is_read_only_and_still_cannot_activate():
    ledger = prepared_ledger()
    result = preview(ledger=ledger)
    assert result["ledger"] == ledger
    assert statuses(result)["ledger_state"] == "passed"
    assert result["can_activate"] is result["execution_ready"] is False


@pytest.mark.parametrize(
    "changed",
    [
        {"strategy": "beta"},
        {"symbol": "ETH-USDT"},
        {"currency": "USDC"},
        {"allocation_scope_id": "other"},
        {"draft_revision": 2},
        {"initial_quote": "100"},
        {"status": "active"},
        {"execution_ready": True},
    ],
)
def test_mismatching_ledger_seed_or_execution_state_blocks(changed):
    assert statuses(preview(ledger={**prepared_ledger(), **changed}))["ledger_state"] == "blocked"


def test_list_drafts_is_scoped_active_snapshot_excluding_tombstones(tmp_path):
    store = AllocationStore(Repository(db_path=str(tmp_path / "master.db")).engine)
    current_scope = scope()
    for strategy, symbol in [("alpha", "BTC-USDT"), ("beta", "ETH-USDT")]:
        store.save_draft(
            current_scope,
            strategy=strategy,
            symbol=symbol,
            budget=100,
            expected_revision=0,
            timestamp=1,
        )
    store.save_draft(
        scope(environment="live"),
        strategy="other",
        symbol="BTC-USDT",
        budget=999,
        expected_revision=0,
        timestamp=1,
    )
    snapshot = store.list_drafts(current_scope)
    assert {draft.strategy for draft in snapshot} == {"alpha", "beta"}
    store.delete_draft(current_scope, "beta", expected_revision=1, timestamp=2)
    assert [draft.strategy for draft in store.list_drafts(current_scope)] == ["alpha"]
    # Already returned values are detached snapshots, not mutable ORM rows.
    assert {draft.strategy for draft in snapshot} == {"alpha", "beta"}
    assert replace(snapshot[0], budget=1).budget == 1
