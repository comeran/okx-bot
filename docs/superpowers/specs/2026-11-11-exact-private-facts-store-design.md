# Durable exact private facts — Phase 2c-b

This slice persists the lossless raw SPOT fill observations from the exact page
adapter without promoting them to balances, positions, PnL, or activation
readiness.

## Storage contract

- Each validated account-facts database has one immutable provider UID binding.
  A different UID, direct update, delete, or replace fails closed. Parent-account
  IDs and account mode are not inferred.
- Exact fills are keyed by `(account_scope_id, uid, bill_id)`. Provider strings
  remain unchanged, including fee sign, timestamp text, and large IDs. Optional
  `clOrdId`, `fee`, and `feeCcy` keep separate missing/null/value states.
- Every accepted page stores its deterministic filter identity, raw JSON text,
  canonical payload hash, requested and next cursors, coverage, and local
  receive time. The raw journal has its own bounded size and does not use the
  small sync-receipt JSON budget; credentials are rejected before persistence.
- A traversal cursor is versioned and bound to account, UID, endpoint, SPOT
  instrument filter, and `before` boundary. Page facts, raw journal, UID
  binding, and cursor advance commit in one account-facts transaction.
- Replaying the same page is idempotent, including after later pages advance the
  cursor. A stale version, changed response for the same page identity,
  conflicting bill, malformed payload, or persisted cursor cycle rolls back the
  whole transaction.

## Boundary

The cursor records observed progress, not an all-history watermark. The provider
retention window, missing account bills/transfers, incomplete algorithmic-order
coverage, account-mode evidence, instrument rules, and reconciliation remain
open. The exact store is not read by SpotLedger, legacy private-sync projections,
or activation checks.
