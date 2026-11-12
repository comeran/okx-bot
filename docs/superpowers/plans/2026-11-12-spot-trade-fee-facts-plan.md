# Exact SPOT Trade Fee Facts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax for tracking.

**Goal:** Retain exact, request-bound account SPOT fee observations without granting fee coverage or execution permission.

**Architecture:** Frozen raw parser/adapter feeds an append-only account-scoped table and explicit single-call sync helper. Signed rate text and group observations remain evidence; preflight policy is unchanged.

**Tech Stack:** Python 3.12, SQLModel/SQLite, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-11-12-spot-trade-fee-facts-design.md`.

## Global Constraints

- No live private provider requests or financial/runtime writes.
- Require native requested instId and already-bound UID; preserve original JSON, exact signs and missing/null/empty states.
- Never infer a fee reserve, safe account or sync completeness from rate observations.
- Identical response replay preserves first receipt; different requested symbols remain separate.

## Review Focus

- Identity-free fee responses must never bind UID or substitute for another request.
- Nested fee groups and optional root values must retain exact states without coercion.
- Corrupt raw/hash/projection and request contexts fail closed on reads.
- Cancellation or failed commits must leave operational state and prior observations intact.
- A repeated response cannot refresh fee evidence or open preflight readiness.

### Task 1: Raw fee fact parser and adapter

**Files:** `src/exchange/okx_trade_fee_facts.py`, `tests/unit/test_okx_trade_fee_facts.py`.

**Interfaces:** `SPOT_TRADE_FEE_FIELDS` dict of root snake_case/provider names; frozen `SpotFeeGroupFact`, `SpotTradeFeeFact`; `parse_okx_spot_trade_fee(payload, *, inst_id)`; `RawOKXTradeFeeExchange`; `OKXSpotTradeFeeFactsAdapter.fetch(inst_id)`.

- [x] Write tests for signed exact strings, root/group states, frozen DTOs, malformed envelopes/types/groups and native symbol/echo matching, one exact raw call and error propagation.
- [x] Run focused tests, observe missing-feature failures.
- [x] Implement parser/adapter with no numeric interpretation or persistence.
- [x] Run focused tests; actual-code review requested for the complete slice.

### Task 2: Scoped persistence and explicit collection

**Files:** `src/data/models.py`, `src/data/repository.py`, `src/ops/exact_private_sync.py`, `tests/unit/test_spot_trade_fee_store.py`, `tests/unit/test_exact_trade_fee_sync.py`, roadmap.

**Interfaces:** `PrivateSpotTradeFeeRecord`; `Repository.commit_private_spot_trade_fee(fact, *, raw_payload, received_at, uid=None)` and `get_private_spot_trade_fees(inst_id=None, *, uid=None)`; `sync_exact_spot_trade_fee(repository, exchange, received_at, *, inst_id, uid=None)`.

- [x] Write failing real-store tests for roundtrip/reopen, request identity/replay, corruption, UID/scope/master refusal and append-only/rollback behavior.
- [x] Add root state columns, group JSON/state, scoped/hash/request identity and append-only triggers; raw/typed validation on commits and reads.
- [x] Write failing collection tests for one request/commit, validation-before-transport, provider/parse failures and cancellation with unchanged operational rows.
- [x] Implement single-call capture/commit helper; no retry or policy integration.
- [x] Run focused and full backend tests, Ruff and diff check; update roadmap and evidence.
- [x] Independent actual-code review; commit/push/create PR/attach under existing authorization. PR #31 is prepared for merge and state readback after this final documentation update.

## Execution evidence

- Store RED: 34 missing-feature failures. Parser RED: 109 missing-module failures. Parser/store GREEN: 143 passed.
- Sync RED: 22 missing-helper failures after parser was present. Combined GREEN: 165 passed; eight additional root-length cases were subsequently included in the full suite.
- Full backend: 3506 passed (173 new tests), one existing aiohttp deprecation warning, 52.91 s. `uv run --no-sync ruff check --no-cache .` and `git diff --check` passed.
- Tests use the shared existing project venv via `UV_PROJECT_ENVIRONMENT`; pytest isolates database/settings before collection and blocks network.
- Execution switched to inline when the delegated parser produced no files; no overlapping writes or agent-generated implementation was retained. An earlier sync collection attempt was blocked by the pending parser and was not counted as TDD evidence.
- Ruling: this slice records exact request-bound fee inputs and leaves coverage unknown. A future reserve model must address fee-group mapping, fee currency, rounding, promotions and freshness before opening that check.
- Independent actual-code review found no Critical or Important issue. It inspected parser/model/repository/sync patches; test commands were verified by the implementer rather than rerun by the reviewer. Empty fee history on an unbound store (even with an explicit UID query) matches existing getter semantics and does not prove identity; collection and persistence still require the binding.
- Remote delivery: `https://github.com/comeran/okx-bot/pull/31`, targeting `main`; source commit `5fd33ec`. Created, pushed and attached; merge/readback follows final documentation push. Remote CI is not configured. No actual exchange request or trading acceptance was performed.
