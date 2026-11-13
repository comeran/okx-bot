# Exact account balance observations

This slice retains a single raw OKX account-balance response in the existing
account-scoped facts store. It provides audit evidence without allocating funds,
posting ledger entries, reconciling holdings, or opening trading activation.

## Contract

- An injected raw client makes exactly one `private_get_account_balance({})`
  call. The parser requires success code `"0"`, exactly one object in `data`,
  and a `details` array. An empty details array is a valid empty observation.
- Immutable DTOs retain `totalEq`, `adjEq`, `availEq`, `uTime` and each currency's
  `ccy`, `cashBal`, `eq`, `availBal`, `frozenBal`, `upl`, and `uTime`. The useful
  optional fields `liab`, `interest`, `eqUsd`, and `ordFrozen` also remain exact.
  Optional values distinguish missing keys, JSON null and empty strings. Text
  stays bounded, is never trimmed or numerically coerced, and boolean/numeric
  JSON values are rejected for these fields. Duplicate currencies fail even
  when their balances agree. Complete raw rows retain additional provider fields.
- `totalEq` is USD account equity, never USDT cash. Only a currency's details
  row describes its cash observation. Missing available balances do not imply
  zero or available funds; this response never proves unleveraged safety.
- The balance endpoint supplies no UID. A prior immutable scoped UID binding
  is mandatory. Balance ingestion never invents or changes it. An optional
  caller UID must match it. The injected transport must belong to that same
  account; this single response cannot verify credential ownership.
- Each record stores account scope, bound UID, canonical payload hash, first
  receive time, original bounded JSON, exact top-level states and details JSON.
  Semantically identical payloads replay idempotently with their original
  receive time; changed payloads remain separate observations. Database triggers
  reject update, delete and replacement of an existing observation.
- Raw JSON passes the existing credential, nesting and size checks. Raw/typed
  equality and UID/scope validation happen in the same transaction as insertion.
  Provider/parser exceptions, pending cancellation before insertion and storage
  rollback leave all financial, exact, operational and activation state intact.

## Boundary

`sync_exact_account_balance` requires a pre-existing UID before fetching and
returns detached stored evidence. It never calls unified `fetch_balance`, retries,
schedules work, creates sync receipts or advances exact fill cursors. No HTTP,
frontend, allocation, legacy AccountRecord/assets or order-manager integration
is included. Freshness, completeness, funds availability and reconciliation
remain future policy work.
