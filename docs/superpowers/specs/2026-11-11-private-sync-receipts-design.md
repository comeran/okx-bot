# Private sync receipts and bounded coverage — Phase 2c-a

This slice makes private-state synchronization observable and restart-safe without
granting allocation ownership or activation authority. It records a durable attempt
and a committed receipt in the validated per-account facts database. Existing float
account/order/trade rows remain compatibility projections and are never upgraded into
exact financial facts by the receipt.

## Scope

- Personal unleveraged OKX spot only.
- Sandbox/manual private sync remains the only provider entry point.
- No live strategy start, order submission, ledger mutation, transfer or funding.
- No claim of exchange-atomic state: account, open orders and trades are fetched by
  separate provider calls.

## Attempt and receipt contract

Each sync starts an `in_progress` attempt with a monotonic per-facts-store sequence,
opaque attempt ID, captured account scope, requested symbols, `since`, limit and local
start time. A failed provider call finalizes that attempt as `failed` with a bounded
reason code. A process that dies before finalization remains observable as
`in_progress`; readers may label it abandoned after a local timeout without changing
the last committed receipt.

The old account/order/trade projection and its receipt are committed in one facts DB
transaction when the sync reaches persistence. A trades-network failure may commit
account and open-order projections, but its attempt is `partial`; the trades part
records unavailable coverage. Authentication, malformed data and unsupported provider
responses commit no projection and finalize `failed`.

`last_committed` is independent from `latest_attempt`: a failed attempt never clears
or refreshes the last committed evidence. A partial commit is visible as partial and
does not become a full-sync certificate.

Each part records local `received_at`, provider timestamp when available, requested
scope and a conservative coverage object:

- account: `account_snapshot`, completeness `unknown` because the compatibility
  projection normalizes fields and loses provider presence/fee provenance;
- orders: `open_orders`, completeness `unknown` because the current ordinary/trigger
  fetch is one page and does not cover every OKX algorithmic order family;
- trades: `recent_trades`, completeness `partial` or `unknown`; the current adapter
  has no durable cursor or bounded all-history proof. A short parsed page is never
  considered exhausted merely because `len(rows) < limit`.

Receipt `parts` and `cursor` are JSON audit data, not an activation grant. Empty
arrays carry `unknown` coverage unless an explicit provider-complete marker exists.
The receipt includes a generation/attempt ID so delayed A→B→A responses cannot be
attached to another account facts store. It does not update the prepared SpotLedger.

## Read API boundary

Expose a read-only account-bound sync status query for the dashboard/readiness UI.
It returns `latest_attempt`, `last_committed`, and explicit part state/coverage. A
wrong or missing account scope fails closed. The query makes no provider call and
does not turn HTTP success or provider `updated_at` into freshness proof; the caller
uses local `received_at` and receipt coverage.

## Deferred work

Exact raw Decimal provider payloads, exchange UID/account-mode evidence, instrument
metadata, paginated OKX bill cursors, complete algorithmic-order coverage and full
reconciliation remain subsequent slices. Until those exist, readiness keeps
`sync_completeness` and `reconciliation` unknown and activation closed.

## Verification boundary for this slice

The compatibility projection and receipt use separate semantics: a successful HTTP
response can return the complete legacy operation result while the durable receipt
stores only bounded counters and samples. Receipt generation ordering rejects an old
attempt that would overwrite a newer committed projection. The read API is account
bound and read-only; it does not refresh, retry, or infer freshness from a provider
timestamp or a non-empty list.
