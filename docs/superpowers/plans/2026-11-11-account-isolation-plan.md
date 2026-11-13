# Account isolation implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Track RED/GREEN evidence and cross-review in the progress ledger.

**Goal:** Bind current private facts and dashboard activity to a single immutable credential/environment scope.
**Architecture:** Server-derived scope and validated per-scope SQLite facts files; master remains config/paper/legacy audit. REST/WS carry context, clients invalidate all financial work by account epoch.
**Tech Stack:** Python/FastAPI/SQLModel/SQLite, Vue/Pinia/TypeScript/Vitest.
**Spec:** `docs/superpowers/specs/2026-11-11-account-isolation-design.md`.

## Global constraints

- Spot first; credential fingerprint is not exchange UID verification.
- Do not redefine persisted allocation scope IDs; do not grant live ownership hooks.
- No network or private exchange operations in verification; all databases/settings are isolated test fixtures.
- No fallback to master or dropped source/runtime/scope filters.
- Never mutate process DB settings to select a facts file; validate header before Repository initialization.
- Preserve unrelated `.workbuddy/` and dependency symlinks; no deployment or strategy start.

## Review focus

- Scope/file header validation across reopen, competing initialization and corrupt/legacy files.
- Any normal REST or WS route still exposing unscoped master live rows.
- Configuration changes during network waits binding writes and broadcasts to different accounts.
- Stale financial data in visible history or derived performance despite correct main balances.
- Paper behavior and global/account kill distinction while execution remains gated.

### Task 1: Immutable scope and scoped storage

Files: create `src/core/execution_scope.py`, `src/data/exchange_store.py`; tests `tests/unit/test_execution_scope.py`, `tests/unit/test_exchange_store.py`.

Interfaces: `build_execution_context(exchange) -> tuple[dict[str, object], ExecutionScope|None]`; frozen `ExecutionScope` with `account_scope_id`, exchange, environment, account_fingerprint. `open_exchange_repository(scope, *, master_engine) -> Repository`. Optional helpers are internal to storage. No raw credentials in metadata/paths/context.

- [x] Write failing tests: same external IDs in two scopes, sandbox/live split, changed market type does not change account ID, unavailable config, reopen/idempotence, wrong/missing/corrupt header unchanged, concurrent initialization, master legacy untouched.
- [x] Run tests with isolated UV environment; record expected missing-feature failures.
- [x] Implement atomic header initialization and validate-before-Repository opening. For example: `repo = open_exchange_repository(scope, master_engine=master.engine)`; no migration touches master live facts.
- [x] Verify all new storage tests and affected repository suites; report exact command/result.
- [x] Cross-review by backend/client implementer before final delivery.

### Task 2: Bound REST/private sync/websocket

Files: `src/web/api/trading.py`, `ops.py`, `settings.py`, `src/web/app.py`, `src/web/ws.py`; create focused request-binding helper if useful. Test integration REST/WS/private-sync contracts.

Consumes Task 1 signatures. Produces authoritative `/api/trading/context` and snapshot envelope from the spec. All financial requests/connections use query `expected_account_scope_id`. `context_changed` is a control message, not an account payload. Capture settings and use the same exchange copy/repository through awaits; errors cannot trigger master fallback.

- [x] Write failing integration tests for legacy-master isolation, missing/wrong precondition, matching empty snapshot, identical IDs in A/B, config switch during delayed sync, bound broadcasts, invalid-header errors and kill OR combinations.
- [x] Run focused tests and record RED evidence.
- [x] Wire scoped repositories to current snapshots and explicit sync/refresh; preserve sandbox-only private-sync guard. Close unimplemented live strategy/aggregate/performance views with empty shape. Keep paper reads unchanged.
- [x] Bind exchange sockets to context, filter all financial broadcasts, and emit context_changed after settings save. Never re-resolve sync context after network awaits.
- [x] Update existing contract fixtures to explicit binding without weakening paper/lifecycle/provider-error coverage; run affected integration/unit suites and report.
- [x] Cross-review Task 1 and later receive independent review from its author.

### Task 3: Account epochs and client integration

Files: `frontend/src/types/*` for context, `services/trading.ts`, `services/trades.ts`, `stores/dashboard.ts`, `composables/useWebSocket.ts`, `App.vue`, `views/Settings.vue`, `views/Trades.vue`, relevant UI/locales/tests.

Consumes Task 2 JSON/query contract. Financial request authority comes from GET context; no arbitrary WS event may select an account. Keep existing per-field WS versions for same-account races.

- [x] Write failing tests for A→B, A→B→A, stale errors/finally, performance/trades invalidation, old closed-socket normal/malformed events, missing/mismatched financial context before history insertion, settings context reload/reconnect.
- [x] Run focused Vitest suites and record RED evidence.
- [x] Implement context loading and epoch invalidation, scope-precondition services, bound exchange socket URL, clear visible financial state/history before authoritative reload. Fix socket onmessage identity guard. Show absent snapshot/unavailable context distinctly.
- [x] Preserve strategy lifecycle rendering and paper behavior; run frontend tests/type check/build and report.
- [x] Cross-review backend binding and receive independent client review from backend/storage author.

### Task 4: Whole-slice verification and progress

- [x] Inspect the combined diff against acceptance and independent review findings; fix important findings with regression evidence and scoped re-review.
- [x] Run full backend `pytest -q`, frontend tests, frontend build/type check, Ruff and `git diff --check` once on final source. Record warnings and exact counts.
- [x] Update roadmap/progress; distinguish completed account isolation from pending active allocation/reconciliation. Preserve new work on `codex/account-isolation` for review; the user's already-requested PR #18 merge is complete.
