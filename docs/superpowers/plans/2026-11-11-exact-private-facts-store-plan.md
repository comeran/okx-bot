# Durable exact private facts implementation plan

## Delivered

- [x] Add immutable account UID binding in the validated account-facts store.
- [x] Persist exact SPOT fill rows with raw optional-state distinctions.
- [x] Persist bounded raw page JSON, canonical payload hashes, filter identity,
  coverage and local receipt time.
- [x] Commit UID binding, facts, page journal and versioned traversal cursor in
  one transaction with stale-version protection.
- [x] Make exact page replay idempotent while rejecting changed page content,
  conflicting bill IDs, persisted traversal cycles and forged completeness.
- [x] Keep legacy projections, SpotLedger and activation state unchanged.

## Verification

- Focused exact parser/store tests: 235 passed after the cycle and coverage fix.
- Full backend suite: 2582 passed with one existing aiohttp deprecation warning.
- Ruff and `git diff --check`: passed.

## Deferred

Traversal orchestration and restart/resume, account configuration UID/mode
acquisition, instrument metadata, bills and balance reconciliation, complete
algorithmic-order coverage, and activation gates remain future slices.
