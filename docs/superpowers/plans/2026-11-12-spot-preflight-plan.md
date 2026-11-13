# Exact SPOT order preflight implementation

## Delivered

- [x] Frozen immutable check/result DTOs and pure exact-fact evaluator.
- [x] Explicit UID/balance binding, local freshness policy and stale/future blocks.
- [x] Borrow-policy evidence separated from liability-free caller attestation.
- [x] Native live instrument, positive exact rules and tick/lot/minimum checks.
- [x] Side-specific exact availBal, fee reserve and USD-versus-USDT protection.
- [x] Explicit reconciliation/sync completeness gates defaulting to unknown.
- [x] No mutation, network, storage, activation or order-manager integration.
- [x] Update spec and roadmap with delivered scope and deferred runtime gates.

## Verification

- TDD RED before production: 106 test cases failed because the evaluator module
  did not exist (`/tmp/spot-preflight-red.log`). Imports occur inside tests so
  missing implementation is recorded per case rather than stopping collection.
- Focused GREEN: 106 cases passed. Coverage includes empty account, USD equity
  versus USDT cash, missing/null/empty/invalid/negative availBal, enabled and
  unknown borrow flags, acctLv-only safety, instrument states and mismatches,
  decimal increments/minimums and precision independent of caller context,
  side-specific balances including fee reserve, stale/future/invalid receipt
  times, invalid requests, reason codes, immutable results and no external I/O.
- Full backend GREEN: 3282 passed in 43.12s, with one existing aiohttp
  deprecation warning.
- Ruff across `src` and `tests`, and `git diff --check`: passed.

## Deferred

This is caller-invoked read-only diagnosis. Trusted scope/evidence acquisition,
liability and fee coverage proofs, reconciliation/recovery, exclusive funded
allocations, atomic reservations, activation and order-manager enforcement
remain future work. Diagnostic booleans do not authorize runtime changes.
