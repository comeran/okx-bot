# Persisted Spot Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Explain an exact spot order using account-scoped persisted evidence through a read-only API.

**Architecture:** Repository selects and verifies immutable source facts in one transaction. An ops helper invokes the pure evaluator with unknown operational attestations; the API owns time and scope binding.

**Tech Stack:** Python, SQLModel/SQLite, FastAPI/Pydantic, pytest.

**Spec:** `docs/superpowers/specs/2026-11-12-persisted-spot-preflight-design.md`.

## Global Constraints

- No provider requests, financial writes, funding, reservations, activation or order submission.
- Exact strings and raw optional states remain authoritative; 30,000 ms server receipt-age bound.
- No client fee/liability/reconciliation/sync attestations; missing proofs remain unknown.
- Preserve first receipt on replay; never fall back from the latest unsafe/absent observation.

## Review Focus

- Mixed scopes or UID records must fail, including malformed store headers.
- New empty or other-symbol responses must invalidate old instrument selection.
- Same-time rows and replay must not resurrect older policy or refresh its age.
- Corrupt raw/hash/typed states and instrument row sets must fail without leaking data.
- Concurrent commits must not mix evidence generations during one repository read.

### Task 1: Consistent verified evidence snapshot

**Files:** `src/data/spot_preflight_evidence.py`, `src/data/repository.py`, `tests/unit/test_spot_preflight_evidence.py`.

**Interfaces:** Produce `Repository.get_spot_preflight_evidence(symbol, *, expected_account_scope_id) -> SpotPreflightEvidence`. Frozen source wrappers contain fact, payload_hash and received_at; snapshot contains scope, UID, config, balance and latest instrument response (including missing symbol).

- [x] Write tests against real stores for reconstruction, selection, scope refusal, corruption, read consistency and unchanged database contents.
- [x] Run focused tests; expect failure for missing repository interface.
- [x] Implement bounded raw/typed consistency validation and one-transaction selection.
- [x] Run focused tests; expect all pass.
- [x] Commit verified data bridge with plan/spec.

### Task 2: Read-only diagnostic API and delivery

**Files:** `src/ops/spot_preflight_evidence.py`, `src/web/api/ops.py`, `tests/integration/test_spot_preflight_api.py`, roadmap.

**Interfaces:** Consume Task 1 snapshot. Produce `build_stored_spot_preflight(evidence, *, symbol, side, amount, price, now_ms) -> dict` and `POST /api/ops/spot/preflight` accepting four strict bounded strings and the existing scope query handshake.

- [x] Write tests for 200 diagnostic output, 409 scope mismatch, 422 extra/coerced inputs, sanitized 503 corruption, stale/future evidence and no database/provider side effects.
- [x] Run focused tests; expect missing-route failures.
- [x] Implement helper/route; supply no fee or operational attestations.
- [x] Run focused tests then full `uv run --no-sync pytest -q`, Ruff and diff check; expect pass (existing aiohttp warning permitted).
- [ ] Independent review, fix material findings, update roadmap/evidence, commit/push/open PR/attach/merge and read back status under existing user authorization.

## Execution evidence

- Task 1 RED: 24 failures for missing repository interface. GREEN: 24 passed.
- Task 2 RED: 27 failures for missing route (404). GREEN: 51 combined new tests passed.
- Full backend: 3333 passed, one existing aiohttp deprecation warning (50.56 s).
- `uv run --no-sync ruff check --no-cache .` and `git diff --check`: passed.
- Test commands use the existing shared project venv via `UV_PROJECT_ENVIRONMENT`; pytest isolates all databases/settings before collection and denies network access.
- Ruling: latest instrument response wins even if it contains another symbol; absent persisted request scope prevents certifying an older rule. This deliberately reduces diagnostic availability until a later matching observation.
- Independent review and remote delivery pending.
