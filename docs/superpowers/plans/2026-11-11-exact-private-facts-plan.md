# Exact raw private facts implementation plan

## Delivered

- [x] Add immutable exact SPOT fill DTO/parser with bounded raw strings.
- [x] Preserve fee sign/currency, both provider timestamps, identifiers and
  missing/null/empty optional values.
- [x] Add an injected raw OKX fills-history single-page adapter with fixed
  `instType=SPOT`, opaque cursors and a 100-row maximum.
- [x] Reject malformed envelopes, non-SPOT rows, conflicting bill IDs, repeated
  pages and cursor non-advancement; keep coverage `partial`/`unknown`.
- [x] Keep the new path separate from sync receipts, float projections,
  SpotLedger and activation.

## Verification

- Focused exact-facts tests: 201 passed.
- Full backend suite: 2548 passed with one pre-existing aiohttp deprecation
  warning.
- Ruff and `git diff --check`: passed.

## Deferred

Durable raw journal, immutable account UID binding, transactional facts/cursor
storage, account-mode and instrument metadata, complete algorithmic-order
coverage, bills/balance reconciliation, and activation remain future slices.
