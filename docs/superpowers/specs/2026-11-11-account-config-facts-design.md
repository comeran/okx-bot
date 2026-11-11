# Exact account configuration facts — Phase 2c-c

This slice records raw OKX account-configuration evidence alongside the
account-scoped exact fill store. It identifies the provider `uid` and retains
account-mode and borrow flags without turning configuration into a trading
permission or a liability-free assertion.

## Contract

- The raw response must be a successful OKX envelope with exactly one data row
  and a non-empty string `uid`. `mainUid` is retained only as an optional raw
  field and is never used as a fallback identity.
- `acctLv`, `acctStpMode`, `posMode`, `autoLoan`, `enableSpotBorrow`, `feeType`,
  `settleCcy`, and `mainUid` preserve missing, JSON `null`, and empty-string
  states. Numeric and boolean coercion is rejected.
- `autoLoan` and `enableSpotBorrow` map to booleans only for raw `0`/`1`;
  unknown provider values remain unknown. `acctLv=1` alone does not prove an
  unleveraged account, and configuration alone never proves `is_unleveraged`.
- Observations are stored with canonical payload hash, original bounded JSON,
  receive time, and scoped UID. Identical payloads replay idempotently; changed
  payloads are retained as separate audit observations.
- UID binding, config observation and all validation happen in the account
  facts transaction. Scope changes, malformed/credential-bearing payloads and
  transaction failures leave no binding or observation behind.

## Boundary

This is read-only evidence. It does not call a provider, infer liabilities,
fetch balances or bills, modify legacy snapshots, update SpotLedger, or change
activation/readiness. A later preflight slice must combine this evidence with
balance, instrument, order and reconciliation facts before any ownership gate
can open.
