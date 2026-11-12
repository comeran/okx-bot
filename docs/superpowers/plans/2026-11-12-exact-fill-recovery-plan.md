# Verified Exact-Fill Recovery Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans, TDD and final independent actual-code review.

**Goal:** Resume only from a verified exact page/fill/cursor state, with no automatic repair or completeness claims.

**Architecture:** one account-wide journal verifier inside the existing accounting transaction; reuse canonical raw decoding/parser/projection helpers. Exact read wrappers provide one transaction, append/replay revalidates before mutation, existing orchestration remains the caller.

**Tech Stack:** Python 3.12, SQLModel/SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-11-12-exact-fill-recovery-design.md`.

## Global constraints and review focus

- No schema migration, historical rewrites, live exchange requests, financial/runtime writes, retries or execution authority.
- Hash/raw/context/chain/projection sets must agree, including before idempotent replay.
- Insertion order governs first-seen metadata; receipt time can be non-monotonic.
- Valid overlap and changed later metadata must remain compatible across interleaved traversals.
- Entire account exact state is validated; no subset fallback or automatic repair of corrupt state.
- Recovery reads share one SQLite transaction; sync refuses invalid persisted state before provider call and commit rechecks after response.

## Task 1: Verified journal reconstruction and repository boundaries

Files: `src/data/repository.py`, new `tests/unit/test_exact_fill_recovery.py`.

- [x] Write failing tests for full valid/reopened first-observation state and corrupt hash/raw/page/context/chain/cursor/projections/foreign/orphan records.
- [x] Observe RED; implement `_verified_exact_fill_history` returning verified page/cursor/fact collections in one accounting transaction. Reuse strict decoder/parser and `_exact_record_values` with shared immutable financial fields.
- [x] Guard all three exact getters and append/replay; reject non-root first requests. Use explicit single-transaction read wrapper for file-backed and memory SQLite.
- [x] Run focused GREEN plus existing exact store/parser/catch-up tests.

## Task 2: Restart/call-boundary regressions and delivery

Files: new `tests/unit/test_exact_fill_recovery_sync.py`, roadmap, spec/plan evidence.

- [x] Write failing reopen/corruption sync/catch-up tests proving zero fake-provider calls and unchanged database rows; verify post-response corruption/replay is rejected atomically.
- [x] Cover transaction identity, memory SQLite, first-seen metadata/overlap, empty tail/replay and non-monotonic receipt order.
- [x] Run full backend/Ruff/diff and independent actual-code review, fix material findings and record decisions/evidence.

Authorized delivery procedure: commit/push/create and attach PR, merge the verified head and read back remote state. The attached PR and final task response retain delivery evidence.

## Execution evidence

- Task 1 TDD: 116 expected integrity-check failures and 7 existing passes. Whole-history validation and read/append/replay guards implemented; recovery plus existing store/sync/catch-up regressions: **215 passed**. Full-project Ruff and diff check passed.
- Cross-traversal first-seen metadata is retained; exact getters preserve existing sort order and detached/frozen output. The superseded row-only fact decoder was removed.
- Task 2: 16 additional restart/call-boundary/transaction and metadata tests passed. Running these against the baseline repository in a separate pytest process reproduced **11 failures and 5 existing passes**; no working-tree source was swapped. Corruption before requests and after responses, plus in-memory actual transactions, now fail closed.
- First full backend run: **3863 passed**, existing aiohttp deprecation warning only, 78.57 seconds. Final run including all supplemental tests: **3879 passed**, same existing warning, 75.12 seconds. Full-project Ruff and diff whitespace checks passed. Collection isolates settings/databases before app import and tests deny network access.
- Independent actual-code review read the complete production diff and both new test files plus parser/model/transaction/orchestration boundaries: **231 focused tests passed**, no Critical/Important/Minor findings or required test gaps. No deferred minors.
- Decisions: verify the entire exact account history and stop on damage rather than automatically repair; cost grows with retained history and corrupt unrelated exact traversals block recovery. Require a root initial request; previously manually seeded non-root traversals now require explicit evidence review. These checks establish local integrity only and do not certify provider coverage, account-equity reconciliation or activation readiness.
