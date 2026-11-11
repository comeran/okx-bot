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

The helpers live in `src/ops/exact_private_sync.py`. Each invocation is bounded
to one response and never certifies completion. An empty page remains unknown;
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

## Deferred

Automatic paging/retry policy, scheduling, freshness UI, instrument evidence,
balance/bills reconciliation and activation gates remain future work. These
helpers collect durable evidence without granting trading permission.
