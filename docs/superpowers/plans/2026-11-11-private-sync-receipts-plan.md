# Private sync receipts implementation plan

Base: merged Phase 2b `20cbe46`; branch `codex/private-sync-receipts`.

## Delivery

- [x] Add an account-facts `PrivateSyncReceiptRecord` with bounded attempt state,
  monotonic generation, requested scope, local times, conservative part coverage,
  cursor metadata and sanitized failure reason.
- [x] Add Repository and accounting-transaction methods for start/finalize/fail/read;
  successful projection and committed receipt must share one transaction.
- [x] Integrate `sync_private_state` and the manual sync route without changing its
  provider permissions. Trades-unavailable becomes an explicit partial receipt;
  malformed/auth/provider-capability failures become failed attempts.
- [x] Add read-only account-bound status API and tests for wrong scope, no provider
  calls, failed attempt retaining last committed receipt, partial commit, rollback,
  restart/in-progress visibility and empty-scope unknown coverage.
- [x] Add a regression proving `since=1, limit=200` with a 100-row provider page is
  not marked complete. Do not infer pagination exhaustion from parsed row count.
- [x] Keep `sync_completeness`, reconciliation, identity, account mode and instrument
  metadata closed/unknown; no ledger or activation integration.

## Verification

Run focused RED/GREEN tests first, then the full backend suite and Ruff. A later UI
slice will render the receipt; this slice does not add automatic sync or activation.
