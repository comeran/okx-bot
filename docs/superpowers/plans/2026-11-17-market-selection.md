# Independent Market Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Share one OKX credential set across per-strategy/per-order market choices, including configuration pages.

**Architecture:** Infer durable market identity from instrument symbols. Route individual operations to market adapters and account-wide sync to a multi-market adapter. Keep account identity independent of market.

**Tech Stack:** Python/FastAPI/CCXT/SQLite, Vue/TypeScript/Element Plus/Vitest.

**Spec:** docs/superpowers/specs/2026-11-17-market-selection-design.md

## Global Constraints

- Preserve pre-existing working tree edits; work in this checkout to retain them. Do not commit unrelated changes.
- No live provider mutations or strategy starts during verification.
- Read legacy exchange.market_type but omit it from newly saved settings and public account configuration.
- Account context uses market_type=all with supported market_types; market is not account identity.
- Strategy symbol is authoritative; do not add a redundant persisted strategy market column.

## Review Focus

- Switching the UI market must invalidate late instrument-list responses and incompatible symbols.
- Account-wide refresh must not stale positions merely because they belong to another market.
- Symbol/market mismatch must fail before any order is submitted.
- Legacy settings and saved derivative strategies must continue to load.
- Funding/allocation scopes must follow the selected strategy instrument, not the old global setting.

### Task 1: Market inference and account adapter

**Files:** src/exchange/markets.py, src/exchange/factory.py, src/exchange/okx_account.py, tests/unit/test_market_routing.py.

**Interfaces:** market_type_for_symbol(symbol: object) -> str; create_okx_adapter(exchange, adapter_classes=None, *, market_type: str | None = None), where no explicit market produces the account adapter and explicit markets produce existing adapters. Account adapter canonicalizes in auto mode, dispatches symbol operations, and safely aggregates private snapshots.

- [x] Add and run failing tests for all four native/CCXT market types, malformed symbols and mixed-market account snapshots.
- [x] Implement inference and routing, preserving private-fact provenance and completeness.
- [x] Run focused adapter tests; review aggregate synchronization behavior.

### Task 2: Strategy runtime and allocation scopes

**Files:** src/web/api/strategies.py, src/web/api/allocations.py, src/ops/phase3_recovery.py, related tests.

**Interfaces:** create_order_manager gains optional market_type; allocation_context gains optional market_type, with callers deriving it from strategy symbols. Existing strategy config/YAML schemas remain unchanged.

- [x] Add and run failing tests for spot/swap strategies under the same credential config and instrument-specific allocation scopes.
- [x] Bind manager safeguards, market data, refresh and funding to the strategy market; remove global-market dependencies.
- [x] Run strategy/allocation regression tests and report compatibility implications.

### Task 3: Configuration, account context and manual requests

**Files:** src/web/api/settings.py, src/core/runtime_settings.py, src/core/execution_scope.py, src/web/exchange_auth.py, src/web/api/trading.py, src/web/api/ops.py, related tests.

- [x] Add and run failing tests for market-neutral account access and settings serialization, legacy config, manual market mismatch and independent routing.
- [x] Remove market from active settings, auth and account validation; use account adapter for sync and operation-specific adapters for manual orders.
- [x] Run backend integration tests; check all remaining exchange.market_type dependencies.

### Task 4: Configuration and operation pages

**Files:** frontend/src/views/Settings.vue, frontend/src/components/StrategyForm.vue, frontend/src/views/Trades.vue, frontend/src/services/trading.ts, frontend/src/types/settings.ts, account context/allocation components and locale files, related tests.

- [x] Add and run failing tests for credential-only settings saves and market-filtered strategy/manual instrument selection.
- [x] Remove market from settings; add per-operation selectors with race-safe instrument loading, using existing UI styles and translations.
- [x] Keep account matching independent of selected market; preserve account-switch protections.
- [x] Run frontend tests and production build.

### Task 5: Integration review and completion

- [x] Review the full integrated diff against the spec, preserving unrelated user edits.
- [x] Run complete backend and frontend suites plus frontend build; record any pre-existing failures precisely.
- [x] Inspect the local UI with synthetic/read-only state where feasible.
- [x] Report implemented behavior, verification and any remaining limits. Leave changes for user review without deploying or starting trading.


## Verification results (2026-11-17)

- Backend full suite: 4,279 passed after the runtime OPTION endpoint fix; one existing aiohttp deprecation warning.
- Frontend full suite: 641 passed; production build succeeded.
- Ruff and diff whitespace checks passed.
- Independent review verified conservative cross-market position cleanup and retention of all fetched trade evidence; no remaining evidenced P1/P2 findings.
- Browser inspection confirmed shared-credential settings, independent strategy/manual market selectors, incompatible-symbol clearing, market-filtered choices, and desktop/mobile layouts with no horizontal overflow.
- All verification used local/synthetic state. No live orders, strategy starts, credential changes, deployment, push, or merge were performed.
- Local paper accounting remains spot-only; derivative paper reservation operations return the existing explicit capability error.
- Several changes entered HEAD through concurrent external commits during execution; remaining edits are left in the current checkout for review, preserving unrelated pre-existing files.


## Authorized local service update

The user reported the original page unchanged because localhost:3000 and :8080 still ran a different checkout. After explicit approval to stop MAG and restart, the services were updated and verified on the original ports:

- Frontend source: current project frontend directory.
- Backend source: current project via uvicorn --app-dir; retained the previous runtime working directory to preserve its settings, strategy files, database and account facts.
- Backed up the master database and five account-facts databases with SQLite backup, plus the runtime settings file.
- Migrated the legacy live + exchange.demo=true combination to demo mode, preserving the original sandbox account identity and credentials. Every strategy remains stopped.
- Fixed the actual account-sync regression: OPTION supports ordinary pending orders but is excluded from the algo-pending endpoint's accepted instrument types. Explicit OPTION adapters and account-wide synchronization now skip only that unsupported query; other market queries and errors remain intact.
- Actual page verification: settings has no global market selector, manual orders has an independent selector, automatic private synchronization returned HTTP 200, backend/database health passed, and the original account identity matched.
- Final backend suite: 4,279 passed. Independent OPTION regression review: 410 passed.
