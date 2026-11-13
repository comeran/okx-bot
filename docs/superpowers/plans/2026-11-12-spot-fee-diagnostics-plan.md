# Group-bound Spot Fee Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox syntax for tracking.

**Goal:** Explain group-selected signed fee scenarios and currency from verified stored evidence, without certifying coverage.

**Architecture:** Raw-only instrument group projection plus a same-transaction fee observation feed a pure Decimal diagnostic. Existing strict API gains bounded provenance/checks/scenarios; no fee reserve or execution permission.

**Tech Stack:** Python 3.12, SQLModel/SQLite, FastAPI, Decimal, pytest.

**Spec:** `docs/superpowers/specs/2026-11-12-spot-fee-diagnostics-design.md`.

## Global Constraints

- No live private requests or financial/runtime writes; API body and fixed 30,000 ms receipt-age policy unchanged.
- No historical row/schema rewrite; group_id lives in verified raw journal and parsed DTO.
- Exact group and normal rule types, no rate/category fallback, no precision loss or rebate credit.
- Unrounded scenarios do not prove coverage; rounding and all operational blockers remain unknown.

## Review Focus

- Legacy raw groupId must survive reopen/replay without changing immutable facts.
- Fee selection must be latest for requested symbol and use same account/transaction.
- Another group's valid rates and top-level hints must never replace missing matching rates.
- Fee types, sides, rebates, long decimals and ambient Decimal traps must not change arithmetic.
- Additive diagnostic output must remain bounded and cannot accept client proof/rounding overrides.

### Task 1: Raw group projection and pure fee model

**Files:** `src/exchange/okx_instrument_facts.py`, `src/ops/spot_fee_diagnostic.py`, `tests/unit/test_spot_fee_diagnostic.py`.

**Interfaces:** append `SpotInstrumentFact.group_id`; produce frozen `SpotFeeDiagnostic` with checks and selected group/currency/rate/amount fields; `evaluate_spot_fee_diagnostic` accepts config/instrument/fee, symbol/side/amount/price, receipt times/current time/max age and expected/fee UID.

- [x] Write failing tests for raw group states/historical exact store roundtrip and pure model group/rule/identity/freshness/currency/Decimal contracts.
- [x] Observe missing-feature failures; implement raw-only group and pure unrounded scenarios.
- [x] Run focused tests; verify no schema/row changes and unknown rounding.

### Task 2: Persisted fee selection and API diagnostics

**Files:** `src/data/spot_preflight_evidence.py`, `src/data/repository.py`, `src/ops/spot_preflight_evidence.py`, `tests/unit/test_spot_preflight_evidence.py`, `tests/integration/test_spot_fee_diagnostic_api.py`, existing API tests, roadmap.

**Interfaces:** append defaulted `SpotPreflightEvidence.trade_fee`; same-transaction latest per-symbol validated observation. Existing API adds evidence.trade_fee, fee_diagnostic and fee checks; body remains four strict strings.

- [x] Write failing tests for latest same-symbol and corrupted fee selection plus actual API scenarios/freshness/replay/body rejection/privacy/no writes.
- [x] Reuse full fee record validation and pure evaluator; never pass a derived reserve to order preflight.
- [x] Run focused/full backend tests, Ruff and diff check; independent actual-code review, fix material findings and update roadmap/evidence.

Authorized delivery procedure: commit/push, open and attach the PR, merge its verified head and read back the remote result. The PR and final task response retain integration evidence.

## Execution evidence

- Task 1 TDD: observed 64 missing-feature failures, then 64 passed. Raw-only group metadata preserves optional states and exact historical journal values without ORM/schema migration.
- Task 2 TDD: observed missing snapshot/API behavior (16 failures, 29 existing passes); integrated verified same-transaction fee selection and pure diagnostics. Focused model/repository/API suite: 136 passed; historical malformed raw group regression: 6 passed.
- Final backend suite: **3597 passed**, one existing aiohttp Python 3.12.9 deprecation warning. Full-project Ruff and `git diff --check` passed. Tests use collection-time isolated databases/settings and deny network requests.
- Independent actual-code review covered production changes, tests, transaction/API boundaries and parser/model contracts; 142 focused tests passed independently. No Critical/Important or deferred minor finding. This verifies local diagnostic behavior, not exchange or long-running operation.
- Decision: reconstruct group_id from the immutable validated raw journal rather than add/rewrite historical projection columns. ORM records expose it through raw_json; parsed immutable facts expose group_id. New invalid legacy groupId fails closed.
- Decision: publish signed unrounded scenarios only. Fee rounding/promotion coverage, fee reserves, liability freedom and complete synchronization/reconciliation remain unknown; submission/activation stay false.
- Delivery uses the reviewed tree and verified head under existing user authorization; remote integration evidence belongs to the attached PR and final task response.
