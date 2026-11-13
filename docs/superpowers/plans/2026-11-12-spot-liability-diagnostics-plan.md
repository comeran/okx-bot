# Stored SPOT Liability Diagnostics Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans; follow TDD and final independent actual-code review.

**Goal:** Surface explicit debt/interest and cash shortfalls from verified stored evidence without certifying account-wide absence of liabilities.

**Spec:** `docs/superpowers/specs/2026-11-12-spot-liability-diagnostics-design.md`.

**Architecture:** append optional fields to the immutable asset DTO; reconstruct from existing raw/details JSON; a pure diagnostic feeds only False/None to the existing liability gate and adds bounded checks/counts to stored preflight. No schema migration or exchange calls.

**Tech Stack:** Python 3.12, frozen dataclasses, Decimal, FastAPI, SQLModel/SQLite, pytest.

## Global constraints and review focus

- No live provider request, funds/ledger/runtime mutation, fee reserve or execution authority.
- The API remains four strict strings and uses a fixed inclusive 30,000 ms age bound; future receipts and missing identity cannot be interpreted.
- Positive evidence in any currency takes precedence over partial/contradictory fields; overlapping fields and currencies are never summed or offset.
- Historical raw fields are revalidated without migrations or append-only row rewrites; latest bad evidence never falls back.
- Response contains nullable counts and checks only; missing/zero/empty evidence never becomes account-wide proof.

## Task 1: Exact raw asset liability fields and pure interpretation

Files: `src/exchange/okx_balance_facts.py`, new `src/ops/spot_liability_diagnostic.py`, new `tests/unit/test_spot_liability_diagnostic.py`.

- [x] Write failing tests for crossLiab/isoLiab/uplLiab raw states, direct validation, historical/replay persistence and malformed history.
- [x] Write failing pure tests for all liability fields, freshness/UID binding, zero/unknown handling, other-currency debt, non-offsetting evidence, negative cash and Decimal precision/context isolation.
- [x] Observe RED, implement frozen `SpotLiabilityDiagnostic` with checks, False/None liability_free and counts; run focused GREEN.

Interface: `evaluate_spot_liability_diagnostic(*, config, balance, now_ms, config_received_at=None, balance_received_at=None, max_age_ms=30_000, expected_uid=None, balance_uid=None)`. Result counts are nullable unless the context is fresh and identity-bound. Positive observations take precedence over unsupported siblings. No arithmetic aggregation.

## Task 2: Stored API bridge and delivery

Files: `src/ops/spot_preflight_evidence.py`, new `tests/integration/test_spot_liability_diagnostic_api.py`, roadmap, this plan.

- [x] Write failing HTTP tests for adverse facts, no liability-free proof from zeros, unknown/future/latest/replay handling, strict input, sanitized corrupt history, privacy and no writes.
- [x] Invoke liability model from the already verified snapshot; pass only False/None to preflight, merge checks, expose counts/unknown coverage without raw balances.
- [x] Run focused and full backend suite, Ruff, diff check and independent actual-code review. Update roadmap/evidence and fix material findings.

Authorized delivery procedure: commit/push/create and attach PR, merge the verified head and read back the remote tree/clean workspace. The attached PR and final task response retain integration evidence.

## Execution evidence

- Raw/history tests: observed 73 missing-feature failures, then 73 passed. Direct/parser states, original/reopened snapshot, replay, unchanged schema/rows and latest invalid historical raw all covered.
- Pure/API TDD: observed 65 missing-feature failures and 5 existing rejection/error passes, then all passed. Integrated focused total: 143 passed.
- Final backend suite: **3740 passed**, one existing aiohttp Python 3.12.9 deprecation warning, 64.52 seconds. Full-project Ruff and diff whitespace checks passed. Test collection isolates settings/databases before app import and tests deny network access.
- Independent actual-code review inspected the full implementation and transaction/API/parser boundaries; its focused suite passed 201 tests. No material findings or deferred minors; this is local diagnostic validation, not exchange or long-running acceptance.
- Decision: debt/interest fields only disprove liability freedom; zeros/absent/inapplicable fields cannot prove coverage. Negative cash is a separate shortfall rather than a loan assertion. No debt totals or cross-currency offsets are fabricated.
