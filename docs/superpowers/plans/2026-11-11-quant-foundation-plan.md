# Quant Foundation Implementation Plan

> **For agentic workers:** Use subagent-driven-development or executing-plans to implement each task with failing tests, implementation, regression checks and review. Steps use checkbox syntax for tracking.

**Goal:** Deliver persistent allocation drafts and economically feasible single-symbol spot backtests without enabling unverified live ownership.

**Architecture:** A dedicated allocation store owns draft identity, transactions and audits; a small API shares strategy lifecycle state with the existing app. A focused frontend panel consumes that API. The backtest's portfolio supplies cash/position queries and validates candidate fills before accounting.

**Tech Stack:** Python 3.12, FastAPI, SQLModel/SQLite, pytest, Vue 3/TypeScript, Vitest.

**Spec:** `docs/superpowers/specs/2026-11-11-quant-foundation-design.md`.

## Global Constraints

- Drafts confer no trading permission; preserve fail-closed live startup and order gates.
- No external exchange calls, user credential-file reads, strategy starts or production data writes during development.
- All tests use isolated DB/settings and block network requests.
- Actual OKX sandbox and local paper are distinct; derive account/environment context server-side.
- Additions remain backward-compatible except rejecting infeasible spot fills and unsupported derivative backtests explicitly.
- Preserve unrelated changes; leave this slice uncommitted until delivery is requested.

## Review Focus

1. Scope changes (credentials, sandbox/live, market type) must not reuse another account's allocation or authorize trading.
2. Concurrent edits and duplicate symbol claims must be atomic and auditable.
3. A starting/running/unknown strategy state must not allow configuration mutations.
4. Fill callbacks and queries must not expose a future close or a mutable accounting object.
5. Price gaps, fees and same-bar orders must never produce negative spot cash/holdings.

## Task 1: Allocation store and configuration-only API

**Files:** Create `src/data/allocations.py`, `src/web/api/allocations.py`, `tests/unit/test_allocations.py`, `tests/integration/test_allocation_api.py`; modify `src/web/app.py` only for router registration and shared lifecycle state, and `src/data/repository.py` only to release drafts atomically during strategy deletion.

**Interfaces:** `AllocationStore(engine)` uses `Repository().engine`; context is derived from runtime exchange settings and exposes only a non-secret fingerprint. Routes: `GET /api/allocations/context`, `GET /api/allocations/{strategy}`, `PUT /api/allocations/{strategy}`, `DELETE /api/allocations/{strategy}?revision=N&expected_scope_id=S&expected_symbol=Y`. PUT body: `{budget: number, expected_revision: integer, expected_scope_id: string, expected_symbol: string}` where creation expects revision 0; scope and symbol come from the loaded envelope. Return draft with `strategy`, `symbol`, `currency`, `budget`, `revision`, `status='draft'`, scope/environment and timestamps. Missing allocation returns a nullable draft envelope; errors follow 404/409/422 semantics. UI receives a clear unavailable context when configured account credentials are absent.

Exact JSON contract (agreed between backend and frontend before implementation):

```ts
type Context = {
  available: boolean; reason: null | 'credentials_unavailable' | 'unsupported_market';
  scope_id: string | null; exchange: 'okx'; environment: 'sandbox' | 'live';
  account_fingerprint: string | null; identity_verified: false; market_type: string;
  execution_mode: string; draft_only: true; execution_ready: false;
};
type Draft = {
  strategy: string; symbol: string; currency: string; budget: number; revision: number;
  status: 'draft'; scope_id: string; environment: 'sandbox' | 'live';
  account_fingerprint: string; market_type: string; created_at: number; updated_at: number;
};
type Envelope = {
  context: Context; draft: Draft | null; strategy: string; symbol: string;
  currency: string; lifecycle_status: string; can_edit: boolean;
};
```

`GET context` returns Context directly; draft GET/PUT/DELETE return Envelope, including `draft:null` after deletion. Domain errors use `{detail:{code,message}}`; request-validation 422 may use the standard detail array. Missing strategy is 404; unavailable mutation context, lifecycle and revision/symbol conflicts are 409. A changed mutation scope returns 409 `allocation_scope_changed`; a changed strategy symbol returns `allocation_symbol_changed`, both without writing. The client must reload, never automatically retry into the new context. Deleted drafts retain a monotonic revision tombstone; recreation with expected revision 0 cannot make a stale pre-deletion revision valid again. `release_strategy_drafts(session, strategy, timestamp)` runs inside Repository's existing config-delete transaction; its models register before Repository schema initialization.

- [x] Write persistence/isolation, finite-positive budget, conflicting symbol, stale revision, atomic audit and stopped-only API tests. Representative behavior:

```python
first = store.save_draft(scope, strategy="alpha", symbol="BTC-USDT", budget=100,
                         expected_revision=0, timestamp=1000)
with pytest.raises(AllocationConflict):
    store.save_draft(scope, strategy="beta", symbol="BTC-USDT", budget=100,
                     expected_revision=0, timestamp=1001)
assert store.get_draft(scope, "alpha").revision == 1
```

- [x] Run the new focused tests; expect missing allocation implementation/routes to fail.
- [x] Implement validated immutable scope, transaction-backed draft/audit storage and isolated API routes. Do not implement the optional `has_strategy_allocation` / `is_strategy_ownership_configured` live hooks.
- [x] Validate implicit and persisted strategy existence/symbol using the existing registry; reject mutation while starting/running/uncertain using the shared lifecycle lock.
- [x] Run focused tests plus existing repository, order and live-start regressions; save commands/results to the progress file.

## Task 2: Strategy allocation panel

**Files:** Create `frontend/src/types/strategyAllocation.ts`, `frontend/src/services/strategyAllocations.ts`, `frontend/src/components/strategy/StrategyAllocationPanel.vue` and focused tests; modify `frontend/src/views/Strategy.vue` and locale messages as required.

**Interfaces:** Consume Task 1's routes/envelopes exactly; panel accepts selected strategy name and refreshes on change. Display server context and draft status. Budget save/delete use expected revision and the loaded account scope; do not call strategy start or use algorithm params for budget.

- [x] Add failing component/service tests for loaded draft, save/update/delete revision, unavailable context, numeric validation, 409 errors, switching selected strategy and no implicit start. Example request assertion:

```ts
expect(request.body).toEqual({ budget: 500, expected_revision: 1,
  expected_scope_id: 'loaded-scope', expected_symbol: 'BTC-USDT' })
expect(screen.getByText(/draft|草稿/i)).toBeVisible()
```

- [x] Run focused Vitest tests and observe behavior failures.
- [x] Implement the panel and strategy-screen integration using existing service/error/i18n conventions; distinguish allocation budget from verified available balance.
- [x] Run focused and existing strategy view tests, then type check/build. Record results.

## Task 3: Feasible spot backtest and account queries

**Files:** Modify `src/backtest/engine.py`, `src/web/api/backtest.py`, `tests/unit/test_backtest_engine.py`; add `tests/unit/test_backtest_spot_account.py` and API coverage in `tests/integration/test_backtest_flow.py` where suitable.

**Interfaces:** Keep `BacktestEngine(initial_capital, matcher)` and `run(strategy, bars)` compatibility. Add explicit spot/symbol context without forcing custom strategies to add attributes. BacktestOrderManager supplies the BaseStrategy account-query signatures. API rejects derivative symbols/models before data fetching. Filled/rejected callbacks observe updated/frozen portfolio state.

- [x] Write failing tests for insufficient and exact cash including fee, no holdings/oversell, sequential competing orders, sell-then-buy, gap affordability, one rejection callback and no trade effects.
- [x] Write query tests for on_init/post-fill/on_bar values, side/strategy/symbol isolation and detached snapshots. Hand-calculated fixture: cash 100, buy 2 at 10 with fee 1 => cash 79, amount 2; at mark 12 => unrealized PnL 4.
- [x] Run focused tests and observe real failures.
- [x] Validate candidate fills before portfolio mutation, emit terminal REJECTED callbacks, bind one spot symbol and implement account queries. Keep pending-order cancellation semantics intact and update the old naked-sell marker test to an actual funded buy/sell sequence.
- [x] Run engine/matcher/builtin smoke/API tests and record results.

## Task 4: Integration, independent review and delivery evidence

**Files:** Update this plan's checkboxes, the roadmap and `docs/superpowers/plans/2026-11-11-quant-foundation-progress.md`; change product code only for review findings with regression coverage.

- [x] Run complete backend and frontend suites, Ruff, frontend type check/build and diff whitespace check. Expected: all pass; report dependency/build warnings explicitly.
- [x] Package the complete working diff, including added files, for an independent reviewer with spec/plan/test evidence.
- [x] Resolve important findings with covering tests, run the relevant checks and record any remaining limits.
- [x] Report Phase 1 capability/limits and next Phase 2 work without claiming allocation activation or exchange acceptance.
