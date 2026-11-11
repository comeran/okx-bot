# Exact private sync orchestration

## Delivered contract

- [x] Inject a raw account-config client; fetch exactly one
  `private_get_account_config({})` response and parse the strict `uid` fact.
- [x] Commit the identical raw response and typed configuration through
  `commit_private_account_config`, returning detached stored evidence.
- [x] Read the durable SPOT cursor for the explicit UID, instrument and `before`
  boundary. Request exactly one raw fills-history page with its saved `after`
  tail (or none), explicit `instType=SPOT` and an integer limit of 1–100.
- [x] Commit raw page, exact facts and cursor with the saved version (or zero).
  Repository reopen resumes the same traversal; instrument/boundary changes use
  separate cursors. Conflicts propagate without automatic retries.
- [x] Honor provider/parser exceptions, pending cancellation before commit and
  transactional storage rollback without advancing the cursor.
- [x] Preserve all operational tables, legacy projections, SpotLedger, sync
  receipts, readiness and activation state. No HTTP/frontend integration.

The single-response helpers live in `src/ops/exact_private_sync.py`. Each of
those invocations is bounded to one response and never certifies completion.
An empty page remains unknown;
a subsequent explicit invocation still uses the stored tail, including `None`,
and repository replay/conflict validation remains authoritative.

## Verification

- TDD RED: 23 new cases failed for the missing adapter/helpers; 108 existing
  account-config parser cases passed.
- GREEN: 131 focused account-config/orchestration cases passed; the expanded
  exact fact/parser/store/config suite passed all 384 cases.
- Full backend suite: 2731 passed, with one existing aiohttp deprecation warning.
- Ruff and `git diff --check`: passed.
- All providers are fake raw clients; automated tests forbid real network calls.

## Delivered bounded fill catch-up

- [x] Add frozen `ExactSpotFillCatchUpResult` and cursor snapshots, and a
  caller-invoked `catch_up_exact_spot_fills` helper that composes the existing
  one-page helper sequentially from durable UID/instrument/`before` cursors.
- [x] Validate positive integer `max_pages`, request limit and filters before
  provider calls. Require an integer timestamp or a per-page factory, validating
  every value within `0..2**63 - 1` before that page's request and commit.
- [x] Continue short non-empty pages. Empty observations stop with `unknown`;
  exactly exhausting the caller's page bound on non-empty data returns `partial`.
- [x] Reject non-incrementing cursor versions, repeated tails, and changed/stale
  durable cursor reads before requesting another page. Keep persisted cycle and
  conflict checks authoritative. Idempotent replay without cursor advancement
  fails closed rather than automatically fetching another page.
- [x] Preserve prior independent page transactions on provider/parser/storage
  exceptions or caller cancellation; never retry or infer completeness.
- [x] Keep all operational/ledger/receipt/readiness/activation state untouched.

Catch-up verification: 38 new cases first failed for the missing helper (RED).
The catch-up and existing one-page orchestration suites then passed 58 cases
(GREEN), using fake raw providers and real scoped repositories only.

- Expanded catch-up/orchestration/store/parser suite: 143 passed.
- Full backend suite: 3176 passed, with the existing aiohttp deprecation warning.
- Repository-wide Ruff and `git diff --check`: passed.

## Deferred

Automatic scheduling, retry/freshness policy, balance/bills reconciliation and
activation gates remain future work. These helpers collect durable evidence
without granting trading permission.
