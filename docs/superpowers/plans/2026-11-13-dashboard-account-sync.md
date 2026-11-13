# Dashboard account synchronization repair

## Cause and scope

The authentication probe reads OKX account configuration only. Dashboard loading,
Refresh and WebSocket snapshots read the current account's local store. The current
sandbox account has no private-sync attempt or committed snapshot, leaving its
account null. The UI exposed no way to invoke the existing manual sync operation.

Restore that missing entry point with an explicit **Sync OKX account** action.
Reuse the existing demo-only operation rather than introduce a parallel account
model or make page loads silently run reconciliation. Keep existing auth edits
and worktree state. No real sync, notifications, strategy starts, orders, settings
saves, commits or pushes are part of automated acceptance.

## Contract and behavior

- Only a currently authenticated, available sandbox account can request sync.
- POST `/api/ops/sync/private` with body `{}` and captured
  `expected_account_scope_id`; no symbols restriction or historical backfill.
- Deduplicate clicks and bind results, errors and loading cleanup to the current
  account epoch/request. Reject mismatched or malformed response contexts.
- On success, reload the existing snapshot through its current WebSocket and
  request-version protections. A GET failure is separate from a failed sync and
  retains the previous snapshot.
- Show safe translated errors, result counts, partial trade retrieval and risk
  protection outcomes. Never render raw provider text or divergence details.
- Explain that sync saves a local snapshot and account discrepancies can trigger
  risk protection and configured notifications. Authentication alone does not
  load balances or authorize trading.

## Work

- [x] Confirm missing local snapshot and missing sync receipts through read-only GETs.
- [x] Add failing UI regressions for the manual action, gating and safe outcomes.
- [x] Implement scoped sync service/store with RED/GREEN coverage.
- [x] Complete UI action, feedback and both locales. Authentication success now starts one scoped automatic sync; the retry action appears only after a sync or snapshot refresh failure.
- [x] Run full frontend tests, type check/build and whitespace checks.
- [x] Independent review and read-only browser acceptance; keep services running.

## Verification

- Frontend: 61 files / 611 tests passed; production build passed; `git diff --check` passed.
- Automatic trigger regression: App shell starts one sync when authentication transitions to verified; failed-auth and unrelated account scopes do not trigger it.
- Store regressions retain prior data, reject stale scope/epoch responses, sanitize provider errors, and clear stale success summaries before a later attempt.
- No real private sync was invoked during implementation or browser acceptance.
