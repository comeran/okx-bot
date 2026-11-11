# Exact account configuration facts implementation plan

## Delivered

- [x] Add strict raw OKX account-config parser with mandatory `uid` and no
  `mainUid` fallback.
- [x] Preserve optional raw field states and conservative borrow/mode status.
- [x] Persist account-scoped config observations with raw JSON, canonical hash,
  detached reads and idempotent replay.
- [x] Reuse immutable UID binding and account-facts transaction rollback.
- [x] Keep legacy projections, SpotLedger, sync receipts and activation unchanged.

## Verification

- Focused exact/config compatibility tests: 361 passed.
- Full backend suite: 2708 passed with one existing aiohttp deprecation warning.
- Independent review passed; Ruff and `git diff --check` passed.

## Deferred

Raw account-config provider adapter, account-mode policy, balance/borrow/
bills reconciliation, instrument rules and activation preflight remain future
work.
