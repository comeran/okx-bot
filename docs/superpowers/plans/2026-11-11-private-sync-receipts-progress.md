# Phase 2c-a progress — private sync receipts

Base: merged Phase 2b `20cbe46`; branch `codex/private-sync-receipts`.

## Delivered

- `PrivateSyncReceiptRecord` lives in the account-facts repository with a monotonic
  generation, opaque attempt ID, captured scope/request, local start/finish/receive
  times, bounded JSON parts/cursor/result and fixed failure codes.
- Attempts are durable before provider awaits. Successful projection and committed or
  partial receipt finalize in the same `BEGIN IMMEDIATE` transaction. Failed attempts
  retain the previous committed receipt. An older generation cannot overwrite a newer
  committed projection; its transaction rolls back and reports `superseded`.
- Private sync records conservative account/orders/trades coverage. Empty scope is
  skipped/unknown, trades provider failure is partial, and malformed/auth/capability or
  account/order failures are failed. No cursor/watermark is inferred.
- Receipt result audit is bounded to counters plus at most five samples; the full
  operation result and risk events remain available through their existing paths.
- `GET /api/ops/sync/private/status` is account-bound, read-only and provider-free.
- `RecentTradePage` no longer infers completeness from parsed row count; real OKX
  single-page fetches are incomplete unless an explicit trusted marker is supplied.

## TDD and review evidence

- Storage RED 25 failures, then focused GREEN 31 passed; latest independent storage
  review 443 passed, with supersession and foreign-scope guards.
- Trade-page RED 18 failures, then 21 passed; exchange/private-sync compatibility 604
  passed; independent trade review passed.
- Orchestrator/API RED and boundary regressions were repaired. Focused orchestrator 27
  passed, API 22 passed, and combined relevant regression 544 passed. Independent
  integration review 248 passed with no open findings.
- Ruff and diff checks passed on owned files. Root will run the full backend suite and
  final frontend regression after this source stabilizes.

## Explicitly pending

Exact raw Decimal provider payloads, durable OKX bill cursors, complete algorithmic
order-family coverage, exchange UID/account mode/instrument rules, full reconciliation,
receipt UI and activation remain future work. This slice does not touch SpotLedger,
start strategies or perform actual fund operations.
