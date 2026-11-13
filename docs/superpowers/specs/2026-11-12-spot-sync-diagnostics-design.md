# Spot synchronization diagnostics design

## Intent

Expose the latest account-bound private-sync attempt and the last committed receipt in the existing read-only spot preflight response. The UI needs bounded state, local receipt freshness, symbol scope, and per-part availability while preserving the existing safety boundary: a receipt is audit metadata, not proof of complete account, order, or trade coverage.

## Goals

- Read `latest_attempt` and `last_committed` from the validated account-facts store in the same read transaction as persisted preflight evidence.
- Project only bounded receipt fields: attempt identity/generation, state, scope, symbols, local times, error code, and account/orders/trades part state/receipt/coverage labels.
- Emit four conservative checks: latest attempt state, local part freshness, requested-symbol scope, and part coverage.
- Keep `sync_completeness`, `reconciliation`, `can_submit`, and `can_activate` closed or unknown.
- Reject foreign/corrupt receipt scope through the existing sanitized 503 boundary, without provider calls or writes.

## Non-goals and anti-goals

No provider sync, retry, trade, funding, ledger, activation, reconciliation proof, or completeness certificate. Do not expose `parts`, `cursor`, `result`, raw provider payloads, or arbitrary JSON through the API. Do not infer freshness from provider timestamps or receipt-level timestamps when a required part is stale/missing.

## Approach

A pure diagnostic function receives detached `PrivateSyncReceiptRecord` values and returns immutable checks plus bounded snapshots. Repository adds a read-only transaction method that validates the account scope, selects the two records, and returns detached records. The existing preflight route reads both evidence and receipt status through one repository read transaction and passes the snapshot to the existing stored-preflight builder. This keeps persistence and policy code separate and preserves the current response `evidence` shape.

A separate endpoint or raw-model passthrough was rejected because it would duplicate account binding, expose unbounded private JSON, and leave the preflight UI unable to explain the same snapshot used for its checks.

## Rules

- `sync_attempt_state`: committed passes; partial/failed/abandoned fail; in-progress or absent is unknown.
- `sync_receipt_freshness`: only a committed/partial receipt whose account, orders, and trades parts are present, available, locally timestamped, non-boolean, non-future, and within the inclusive age bound passes; otherwise unknown.
- `sync_symbol_scope`: explicit `symbols` and order/trade part coverage must include the requested symbol; an explicit mismatch fails, while missing/empty/unknown scope is unknown.
- `sync_part_coverage`: always unknown for this slice, even when legacy JSON claims complete/durable coverage.
- Account-scope mismatch or malformed stored data raises the existing sanitized preflight-unavailable error.

## Verification

Focused unit and integration tests cover absent, failed, in-progress, partial, stale, malformed, sparse, mismatched-symbol, foreign-scope, privacy, read-only, and claimed-complete receipts. Then run the full backend suite, Ruff, and `git diff --check`.
