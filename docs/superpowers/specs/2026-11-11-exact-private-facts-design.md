# Exact raw private facts — Phase 2c-b (first slice)

This slice keeps a lossless observation path beside the existing float-based
compatibility projection. It does not establish account identity, persist a
history watermark, reconcile balances, update the SpotLedger, or authorize a
strategy.

## Delivered boundary

- `SpotFillFact` stores OKX SPOT fill identifiers and numeric values as bounded
  provider strings. No float conversion, quantization, or numeric reserialization
  occurs.
- `fillTime` is retained as `fill_time`; `ts` is retained as `record_time`.
  Signed `fee` values and `feeCcy` are retained so a positive rebate cannot be
  mistaken for a charge.
- Missing keys, JSON `null`, and empty strings remain distinct for optional
  client-order and fee fields.
- Native two-asset `BASE-QUOTE` instruments are the only accepted facts. The
  canonical symbol follows the existing project identity (`BTC-USDT`).
- `OKXSpotFillFactsAdapter` issues exactly one raw
  `private_get_trade_fills_history` request with `instType=SPOT` and a limit no
  greater than 100. It never calls unified `fetch_my_trades`.
- Page parsing preserves an opaque tail `billId` cursor and reports
  `partial`/`unknown` coverage. Short or empty pages and provider flags do not
  become a completeness certificate.
- Duplicate observations are idempotent only when their financial payload is
  byte-for-byte consistent. Conflicting bill IDs, repeated pages, request-boundary
  echoes, and non-advancing cursors fail closed.

## Deferred work

The next slice must add account UID/mode evidence, a scoped raw journal, exact
facts persistence, and a transactional durable cursor. It must bind the cursor
to the account scope, endpoint, SPOT filter, symbol, range, and parser version;
resume after restarts; and commit page facts and cursor together. It must still
report the provider's limited retention window and avoid claiming an atomic
account snapshot. Exact facts remain separate from the prepared ledger until
currency-aware fees, bills, balances, and reconciliation have passed their own
invariants.

## Verification boundary

The new unit tests cover precision beyond IEEE-754, large opaque IDs, signed
fees, distinct optional states, `fillTime != ts`, malformed values, SPOT-only
scope, request limits, overlap/idempotency, conflicting duplicates, repeated
pages, and conservative coverage. The full backend suite remains the required
regression gate before delivery.
