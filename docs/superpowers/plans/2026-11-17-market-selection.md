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

- [ ] Add and run failing tests for all four native/CCXT market types, malformed symbols and mixed-market account snapshots.
- [ ] Implement inference and routing, preserving private-fact provenance and completeness.
- [ ] Run focused adapter tests; review aggregate synchronization behavior.

### Task 2: Strategy runtime and allocation scopes

**Files:** src/web/api/strategies.py, src/web/api/allocations.py, src/ops/phase3_recovery.py, related tests.

**Interfaces:** create_order_manager gains optional market_type; allocation_context gains optional market_type, with callers deriving it from strategy symbols. Existing strategy config/YAML schemas remain unchanged.

- [ ] Add and run failing tests for spot/swap strategies under the same credential config and instrument-specific allocation scopes.
- [ ] Bind manager safeguards, market data, refresh and funding to the strategy market; remove global-market dependencies.
- [ ] Run strategy/allocation regression tests and report compatibility implications.

### Task 3: Configuration, account context and manual requests

**Files:** src/web/api/settings.py, src/core/runtime_settings.py, src/core/execution_scope.py, src/web/exchange_auth.py, src/web/api/trading.py, src/web/api/ops.py, related tests.

- [ ] Add and run failing tests for market-neutral account access and settings serialization, legacy config, manual market mismatch and independent routing.
- [ ] Remove market from active settings, auth and account validation; use account adapter for sync and operation-specific adapters for manual orders.
- [ ] Run backend integration tests; check all remaining exchange.market_type dependencies.

### Task 4: Configuration and operation pages

**Files:** frontend/src/views/Settings.vue, frontend/src/components/StrategyForm.vue, frontend/src/views/Trades.vue, frontend/src/services/trading.ts, frontend/src/types/settings.ts, account context/allocation components and locale files, related tests.

- [ ] Add and run failing tests for credential-only settings saves and market-filtered strategy/manual instrument selection.
- [ ] Remove market from settings; add per-operation selectors with race-safe instrument loading, using existing UI styles and translations.
- [ ] Keep account matching independent of selected market; preserve account-switch protections.
- [ ] Run frontend tests and production build.

### Task 5: Integration review and completion

- [ ] Review the full integrated diff against the spec, preserving unrelated user edits.
- [ ] Run complete backend and frontend suites plus frontend build; record any pre-existing failures precisely.
- [ ] Inspect the local UI with synthetic/read-only state where feasible.
- [ ] Report implemented behavior, verification and any remaining limits. Leave changes for user review without deploying or starting trading.
