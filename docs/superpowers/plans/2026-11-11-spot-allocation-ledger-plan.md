# Strategy spot ledger and readiness implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: subagent-driven-development or executing-plans; record RED/GREEN and independent reviews in the progress ledger.

**Goal:** Provide exact internal spot accounting/reservations and an honest, read-only saved-allocation readiness view.
**Architecture:** Separate Decimal TEXT ledger tables inside validated scoped facts DBs, with explicit internal seed/intent/fill/terminal transactions. Master drafts and scoped cached facts feed a diagnostic API; client epoch/revision fencing prevents stale previews. No activation or manager integration.
**Tech Stack:** Python/Decimal/SQLAlchemy/SQLite/FastAPI; Vue/TypeScript/Vitest.
**Spec:** `docs/superpowers/specs/2026-11-11-spot-allocation-ledger-design.md`.

## Global constraints

- Spot only; no external calls, real database writes, strategy starts, live ownership hooks, or activation endpoints.
- Use existing managed worktree/new branch `codex/spot-allocation-ledger` from `7fa0bc1`; preserve previous commits and unrelated files.
- Decimal text/string arithmetic; reject float inputs and preserve prepared/non-executing semantics.
- Quote observations and provider timestamps are indicative; no new durable completeness/identity receipt is invented.
- Tests isolate paths before app imports and block network; reuse existing dependency runtime.

## Review focus

- Cross-strategy/account identity conflicts and replays that differ only after partial/terminal mutation.
- Base versus quote fees, unspent buffers and recurring cost-basis residue on final liquidation.
- Unknown outcomes and cancel acknowledgements prematurely freeing reservations.
- Unsynchronized cached assets/timestamps falsely reported as activation-ready.
- Account/draft A→B→A and delete/recreate responses leaking into the readiness UI.

### Task 1: Internal exact spot ledger

Files: create `src/data/spot_ledger.py`; optionally split arithmetic/validation into `src/order/spot_ledger.py`; unit tests `tests/unit/test_spot_ledger.py` (split concurrency tests if useful). Do not change legacy paper accounting/models/manager.

Interfaces: `SpotLedgerStore(engine, scope)`; `SpotLedgerError(code, message)`; `get_strategy(strategy)->dict|None`; `get_order(order_id)->dict|None`; `get_events(strategy)->list[dict]`; mutation methods below accept keyword-only fields and return snapshots. All externally serialized numerics are strings.

```python
ledger.initialize_strategy(strategy="alpha", symbol="BTC-USDT", initial_quote="100",
    allocation_scope_id="saved-scope", draft_revision=1, timestamp=1)
ledger.reserve_order(strategy="alpha", order_id="o1", side="buy", amount="1",
    price_cap="90", fee_buffer="1", timestamp=2)
ledger.apply_fill(strategy="alpha", order_id="o1", trade_id="t1", amount="0.5",
    price="80", fee="0.5", fee_currency="USDT", timestamp=3)
ledger.mark_unknown(strategy="alpha", order_id="o1", timestamp=4)
ledger.confirm_terminal(strategy="alpha", order_id="o1", status="canceled",
    confirmed_filled_quantity="0.5", timestamp=5)
```

- [x] Write failing behavioral tests for exact seed/reserve/partial-fill/base+quote fees/PnL/replay, zero base naked-sell, cross-symbol/strategy/account conflicts, invalid decimals, overfill/cap/buffer, unknown/terminal proof, reopen/audit rollback and concurrency.
- [x] Run `UV_PROJECT_ENVIRONMENT=/Users/zane/Documents/Self/Project/Self/okx-bot/.venv uv run --no-sync pytest -q tests/unit/test_spot_ledger.py`; expected missing-feature failures before production code.
- [x] Implement dedicated metadata/tables with atomic transactions using the scoped engine's write-lock semantics. Validate header first; no legacy-import/fallback. Preserve exact replay identity and no financial mutation on failure. Add meaningful rollback failure injection in tests.
- [x] Run new tests plus existing storage/allocations/paper-accounting suites; report commands/results and any deviations. No task-local commit until root combines reviewed changes.
- [x] Independently review readiness backend from Task 2 once ready; own ledger reviewed by Task 2 author.

### Task 2: Readiness service and bound API

Files: create `src/ops/allocation_readiness.py`; modify `src/web/api/allocations.py`, `src/data/allocations.py` for `list_drafts(scope)->list[AllocationDraft]`; tests `tests/unit/test_allocation_readiness.py`, `tests/integration/test_allocation_readiness_api.py`.

Consumes Task 1 `SpotLedgerStore.get_strategy`. Add mandatory query contract exactly as spec. Keep allocation IDs unchanged. Scope config/draft/lifecycle preconditions derive from same captured settings; map execution scope with existing binding helper. Never initialize/seed on GET. Return checks from the fixed code list in the spec; encode unknown evidence honestly. Monetary budget/proposal/observation fields are strings, comparison has trusted:false.

- [x] Write failing tests: same-currency draft sum and deleted exclusion, stale/future/absent provider timestamps, missing/duplicate assets unknown, indicative-within never passed quote_capacity, empty order list never passed completeness, unknown/pending orders, four kill combinations, ledger null/mismatch and closed ownership gate.
- [x] Write failing endpoint tests for missing/wrong query prerequisites, draft edit/delete/recreate/symbol/config change, cross-account legacy exclusion, no financial-row creation/no private calls, prepared ledger read, unchanged paper API.
- [x] Run focused tests; expected feature absent/404 or missing API before implementation.
- [x] Implement consistent draft reads and scoped diagnostic gathering. Provider errors/storage failures must not fall back to legacy facts or show a fabricated success. Recheck scope/draft preconditions immediately before response if implementation introduces awaits.
- [x] Run readiness + existing allocation/context tests; report RED/GREEN and independent ledger review. Root owns one final full suite.

### Task 3: Read-only allocation readiness panel

Files: new `frontend/src/components/strategy/StrategyAllocationReadiness.vue` and tests; modify allocation types/services/panel and their tests/locales. Use existing accountContext store rather than a new authority.

Consumes fixed Task 2 response. Use saved allocation scope/revision/symbol and authoritative execution context. No draft means save-first message with no readiness request. No activate button or financial POST. Display strings directly; show state/check labels via translations rather than raw internal codes.

- [x] Write failing tests for service query mapping, saved-budget display, unknown/blocker checks, prepared/null ledger, errors, no mutations, account epoch A→B→A, saved revision/symbol changes, stale failures/finally and unmount cancellation.
- [x] Run focused Vitest tests; verify absent feature failures before product implementation.
- [x] Implement component and epoch-aware allocation-envelope refresh. Do not let an old envelope re-enable edit after context invalidation; do not accept a readiness context as authoritative identity. Keep draft creation/deletion and paper lifecycle tests.
- [x] Run affected frontend suites and type check; report commands/results. Root runs final full frontend/build after stable source.
- [x] Independently review all UI/service changes with the ledger author; backend readiness and ledger remain cross-reviewed by their respective authors. Root completes type/service integration without reviewing its own work as the final reviewer.

### Task 4: Integrated verification and local delivery

- [x] Cross-review all changed code; fix Important/Critical findings with reproduced regressions and scoped re-review. Record limitations and decisions.
- [x] Run final backend suite, frontend suite/build, Ruff and diff checks on stable source; no repeated unchanged green checks.
- [x] Update roadmap/progress with exact delivered boundaries, remaining activation/reconciliation work and final verification evidence.

Delivery: create a separate reviewed local commit; no automatic exchange operations or remote merge.
