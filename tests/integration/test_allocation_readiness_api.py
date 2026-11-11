import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlmodel import Session, select

from src.core.config import AppConfig, ExchangeConfig
from src.core.execution_scope import build_execution_context
from src.core.runtime_settings import save_runtime_settings
from src.data.allocations import AllocationStore
from src.data.exchange_store import open_exchange_repository
from src.data.models import AccountRecord, OrderRecord, StrategyConfigRecord
from src.data.repository import Repository
from src.web import app as web_app
from src.web.api import allocations, ops, strategies, trading

NOW = 1_000_000


def account(available=150, **values):
    return AccountRecord(
        **{
            "strategy": "__exchange__",
            "source": "live_exchange",
            "runtime_mode": "live",
            "initial_equity": 999_999,
            "cash_balance": 999_999,
            "available_balance": 999_999,
            "equity": 999_999,
            "realized_pnl": 0,
            "unrealized_pnl": 0,
            "daily_pnl": 0,
            "fees_paid": 0,
            "updated_at": NOW,
            "assets": [{"ccy": "USDT", "avail_bal": available}],
            **values,
        }
    )


@pytest.fixture
def configured(monkeypatch):
    config = AppConfig(
        mode="live",
        exchange=ExchangeConfig(
            api_key="test-key",
            secret="test-secret",
            passphrase="test-passphrase",
            demo=True,
        ),
    )
    save_runtime_settings(config)
    runtime = strategies.StrategyRuntimeState()
    monkeypatch.setattr(strategies, "StrategyRuntimeState", lambda: runtime)
    monkeypatch.setattr(allocations.time, "time", lambda: NOW / 1000)

    def forbidden(*args, **kwargs):
        raise AssertionError("readiness must not construct a private provider")

    monkeypatch.setattr(ops, "_create_adapter", forbidden)
    monkeypatch.setattr(trading, "refresh_okx_live_state", forbidden)
    with TestClient(web_app.create_app()) as client:
        before = client.get("/api/allocations/ma_cross").json()
        result = client.put(
            "/api/allocations/ma_cross",
            json={
                "budget": 100.1,
                "expected_revision": 0,
                "expected_scope_id": before["context"]["scope_id"],
                "expected_symbol": "BTC-USDT",
            },
        )
        assert result.status_code == 200
        draft = result.json()["draft"]
        execution_context, execution_scope = build_execution_context(config.exchange)
        params = {
            "expected_account_scope_id": execution_context["account_scope_id"],
            "expected_scope_id": draft["scope_id"],
            "expected_revision": draft["revision"],
            "expected_symbol": draft["symbol"],
        }
        facts = open_exchange_repository(execution_scope, master_engine=Repository().engine)
        yield SimpleNamespace(
            client=client,
            runtime=runtime,
            config=config,
            facts=facts,
            execution_scope=execution_scope,
            params=params,
            draft=draft,
        )


def readiness(state, **params):
    return state.client.get(
        "/api/allocations/ma_cross/readiness", params={**state.params, **params}
    )


def checks(payload):
    return {item["code"]: item["status"] for item in payload["checks"]}


def test_readiness_contract_never_infers_funds_or_initializes_ledger(configured):
    state = configured
    state.facts.upsert_account(account())
    response = readiness(state)
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "execution_context",
        "allocation_scope_id",
        "draft_revision",
        "strategy",
        "symbol",
        "currency",
        "budget",
        "evaluated_at",
        "capacity",
        "ledger",
        "checks",
        "can_activate",
        "execution_ready",
    }
    assert (
        payload["execution_context"]["account_scope_id"]
        == state.params["expected_account_scope_id"]
    )
    assert payload["allocation_scope_id"] == state.params["expected_scope_id"]
    assert payload["allocation_scope_id"] != payload["execution_context"]["account_scope_id"]
    assert payload["budget"] == "100.1"
    assert payload["draft_revision"] == 1
    assert payload["capacity"] == {
        "observed_quote_available": "150",
        "proposed_total": "100.1",
        "comparison": "within_observed",
        "trusted": False,
    }
    assert payload["ledger"] is None
    assert checks(payload)["quote_capacity"] == "unknown"
    assert checks(payload)["observed_open_orders"] == "unknown"
    assert checks(payload)["sync_completeness"] == "unknown"
    assert payload["can_activate"] is payload["execution_ready"] is False
    assert "test-key" not in response.text


@pytest.mark.parametrize(
    "missing",
    ["expected_account_scope_id", "expected_scope_id", "expected_revision", "expected_symbol"],
)
def test_every_query_precondition_is_mandatory(configured, missing):
    params = dict(configured.params)
    params.pop(missing)
    assert (
        configured.client.get("/api/allocations/ma_cross/readiness", params=params).status_code
        == 422
    )


@pytest.mark.parametrize(
    "changed,code",
    [
        ({"expected_account_scope_id": "other"}, "account_scope_changed"),
        ({"expected_scope_id": "other"}, "allocation_scope_changed"),
        ({"expected_revision": 2}, "revision_conflict"),
        ({"expected_symbol": "ETH-USDT"}, "allocation_symbol_changed"),
    ],
)
def test_stale_query_preconditions_fail_closed(configured, changed, code):
    response = readiness(configured, **changed)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == code


def test_unknown_strategy_stays_404(configured):
    response = configured.client.get("/api/allocations/missing/readiness", params=configured.params)
    assert response.status_code == 404


def test_deleted_or_recreated_draft_invalidates_saved_revision(configured):
    state = configured
    state.client.delete(
        "/api/allocations/ma_cross",
        params={
            "revision": 1,
            "expected_scope_id": state.params["expected_scope_id"],
            "expected_symbol": "BTC-USDT",
        },
    )
    assert readiness(state).status_code == 409
    state.client.put(
        "/api/allocations/ma_cross",
        json={
            "budget": 100.1,
            "expected_revision": 0,
            "expected_scope_id": state.params["expected_scope_id"],
            "expected_symbol": "BTC-USDT",
        },
    )
    assert readiness(state).status_code == 409
    assert readiness(state, expected_revision=3).status_code == 200


def test_only_selected_account_facts_not_legacy_or_paper_are_read(configured):
    state = configured
    master = Repository()
    master.upsert_account(account(999))
    state.facts.upsert_account(account(777, strategy="paper", source="paper", runtime_mode="paper"))
    missing = readiness(state).json()
    assert missing["capacity"]["observed_quote_available"] is None
    assert checks(missing)["account_snapshot"] == "unknown"
    state.facts.upsert_account(account(12))
    assert readiness(state).json()["capacity"]["observed_quote_available"] == "12"
    state.config.exchange.api_key = "test-other-account"
    save_runtime_settings(state.config)
    assert readiness(state).status_code == 409
    context, _ = build_execution_context(state.config.exchange)
    new_draft = state.client.get("/api/allocations/ma_cross").json()
    state.client.put(
        "/api/allocations/ma_cross",
        json={
            "budget": 100.1,
            "expected_revision": 0,
            "expected_scope_id": new_draft["context"]["scope_id"],
            "expected_symbol": "BTC-USDT",
        },
    )
    response = readiness(
        state,
        expected_account_scope_id=context["account_scope_id"],
        expected_scope_id=new_draft["context"]["scope_id"],
    )
    assert response.status_code == 200
    assert response.json()["capacity"]["observed_quote_available"] is None
    assert response.json()["ledger"] is None


@pytest.mark.parametrize(
    "global_kill,account_kill", [(False, False), (True, False), (False, True), (True, True)]
)
def test_readiness_reports_both_effective_kill_inputs(configured, global_kill, account_kill):
    Repository().set_kill_switch(global_kill, "global", 1)
    configured.facts.set_kill_switch(account_kill, "account", 2)
    response = readiness(configured)
    assert response.status_code == 200
    statuses = checks(response.json())
    assert statuses["global_kill"] == ("blocked" if global_kill else "passed")
    assert statuses["account_kill"] == ("blocked" if account_kill else "passed")
    assert response.json()["can_activate"] is False


def test_all_drafts_are_read_once_and_deleted_other_quote_excluded(configured, monkeypatch):
    state = configured
    scope = allocations.allocation_context()[1]
    store = AllocationStore(Repository().engine)
    for name, symbol, budget in [
        ("beta", "ETH-USDT", 0.2),
        ("gone", "SOL-USDT", 999),
        ("other-quote", "ETH-USDC", 99_999),
    ]:
        store.save_draft(
            scope, strategy=name, symbol=symbol, budget=budget, expected_revision=0, timestamp=1
        )
    store.delete_draft(scope, "gone", expected_revision=1, timestamp=2)
    original = AllocationStore.list_drafts
    reads = []

    def capture(self, current_scope):
        reads.append(current_scope.scope_id)
        return original(self, current_scope)

    monkeypatch.setattr(AllocationStore, "list_drafts", capture)
    response = readiness(state)
    assert response.status_code == 200
    assert response.json()["capacity"]["proposed_total"] == "100.3"
    assert reads == [scope.scope_id]


def row_counts(repository):
    with repository.engine.connect() as connection:
        names = (
            connection.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            )
            .scalars()
            .all()
        )
        return {
            name: connection.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar_one()
            for name in names
        }


def test_repeated_get_has_no_financial_writes_or_private_calls(configured, deny_network_requests):
    state = configured
    master_before = row_counts(Repository())
    facts_before = row_counts(state.facts)
    assert readiness(state).status_code == 200
    assert readiness(state).status_code == 200
    assert row_counts(Repository()) == master_before
    after = row_counts(state.facts)
    assert all(after[name] == count for name, count in facts_before.items())
    assert all(count == 0 for name, count in after.items() if name not in facts_before)
    assert deny_network_requests == []
    from src.data.spot_ledger import SpotLedgerStore

    assert (
        SpotLedgerStore(state.facts.engine, state.execution_scope).get_strategy("ma_cross") is None
    )
    assert (
        strategies.has_live_strategy_ownership_mapping(Repository(), "ma_cross", "BTC-USDT")
        is False
    )
    assert state.client.post("/api/strategies/ma_cross/start").status_code == 409


def test_prepared_ledger_is_returned_and_draft_edit_marks_seed_mismatch(configured):
    from src.data.spot_ledger import SpotLedgerStore

    state = configured
    ledger = SpotLedgerStore(state.facts.engine, state.execution_scope)
    ledger.initialize_strategy(
        strategy="ma_cross",
        symbol="BTC-USDT",
        initial_quote="100.1",
        allocation_scope_id=state.params["expected_scope_id"],
        draft_revision=1,
        timestamp=1,
    )
    first = readiness(state)
    assert first.status_code == 200
    assert first.json()["ledger"] == ledger.get_strategy("ma_cross")
    assert checks(first.json())["ledger_state"] == "passed"
    state.client.put(
        "/api/allocations/ma_cross",
        json={
            "budget": 200,
            "expected_revision": 1,
            "expected_scope_id": state.params["expected_scope_id"],
            "expected_symbol": "BTC-USDT",
        },
    )
    assert readiness(state).status_code == 409
    second = readiness(state, expected_revision=2)
    assert second.status_code == 200
    assert checks(second.json())["ledger_state"] == "blocked"
    assert second.json()["can_activate"] is False


def test_malformed_scope_header_fails_without_legacy_fallback(configured):
    state = configured
    Repository().upsert_account(account(999))
    path = Path(state.facts.engine.url.database)
    state.facts.engine.dispose()
    path.write_bytes(b"invalid scoped facts")
    before = path.read_bytes()
    result = readiness(state)
    assert result.status_code == 503
    assert result.json()["detail"]["code"] == "account_store_unavailable"
    assert path.read_bytes() == before


@pytest.mark.parametrize("change", ["key", "environment", "secret", "symbol", "draft"])
def test_gather_rechecks_configuration_and_draft_before_return(configured, monkeypatch, change):
    from src.data.spot_ledger import SpotLedgerStore

    state = configured
    original = SpotLedgerStore.get_strategy

    def change_during_read(self, name):
        if change in {"key", "environment", "secret"}:
            if change == "key":
                state.config.exchange.api_key = "changed-key"
            elif change == "environment":
                state.config.exchange.demo = False
            else:
                state.config.exchange.secret = "changed-secret"
            save_runtime_settings(state.config)
        elif change == "symbol":
            Repository().upsert_strategy_config(
                StrategyConfigRecord(
                    name="ma_cross",
                    strategy_type="ma_cross",
                    symbol="ETH-USDT",
                    timeframe="1h",
                    params={},
                    created_at=1,
                    updated_at=2,
                )
            )
        else:
            scope = allocations.allocation_context()[1]
            AllocationStore(Repository().engine).save_draft(
                scope,
                strategy="ma_cross",
                symbol="BTC-USDT",
                budget=200,
                expected_revision=1,
                timestamp=2,
            )
        return original(self, name)

    monkeypatch.setattr(SpotLedgerStore, "get_strategy", change_during_read)
    result = readiness(state)
    assert result.status_code == 409


@pytest.mark.asyncio
async def test_readiness_joins_shared_lifecycle_lock_and_checks_latest_state(
    configured, monkeypatch
):
    state = configured
    acquired = asyncio.Event()
    original_lock = state.runtime.lifecycle_lock
    lock = original_lock("ma_cross")

    def observed_lock(name):
        acquired.set()
        return original_lock(name)

    monkeypatch.setattr(state.runtime, "lifecycle_lock", observed_lock)
    async with AsyncClient(
        transport=ASGITransport(app=state.client.app), base_url="http://test"
    ) as api:
        async with lock:
            pending = asyncio.create_task(
                api.get("/api/allocations/ma_cross/readiness", params=state.params)
            )
            await asyncio.wait_for(acquired.wait(), 2)
            assert not pending.done()
            state.runtime.strategy_status["ma_cross"] = "running"
        response = await pending
    assert response.status_code == 200
    assert checks(response.json())["strategy_stopped"] == "blocked"


def test_unavailable_context_readiness_fails_closed_but_existing_panel_still_works(configured):
    state = configured
    save_runtime_settings(AppConfig(exchange=ExchangeConfig()))
    response = readiness(state)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "account_context_unavailable"
    panel = state.client.get("/api/allocations/ma_cross")
    assert panel.status_code == 200
    assert panel.json()["draft"] is None


def test_unknown_outcome_on_unrelated_symbol_blocks_preview(configured):
    configured.facts.upsert_order(
        OrderRecord(
            order_id="external",
            strategy="__exchange__",
            source="live_exchange",
            runtime_mode="live",
            symbol="ETH-USDC",
            side="buy",
            type="limit",
            amount=1,
            price=1,
            status="unknown",
            fill_price=0,
            timestamp=1,
        )
    )
    response = readiness(configured)
    assert response.status_code == 200
    assert checks(response.json())["observed_open_orders"] == "blocked"


def test_local_paper_api_is_unchanged(configured):
    Repository().upsert_account(
        account(
            42,
            strategy="paper-one",
            source="paper",
            runtime_mode="paper",
            equity=42,
            cash_balance=42,
        )
    )
    response = configured.client.get("/api/trading/account", params={"runtime_mode": "paper"})
    assert response.status_code == 200
    assert response.json()["equity"] == 42
    with Session(Repository().engine) as session:
        assert len(session.exec(select(AccountRecord)).all()) == 1
