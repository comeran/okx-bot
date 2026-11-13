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

## Delivered bounded catch-up

Account configuration/UID evidence, scoped raw journals, exact facts persistence,
and transactional durable cursors were delivered in subsequent slices.
`catch_up_exact_spot_fills` now composes the existing one-page helper sequentially,
resuming the durable UID/instrument/`before` traversal for every request.

- The caller supplies a positive integer `max_pages` (default 10), integer
  `limit` from 1 to 100, and a required integer timestamp or zero-argument
  timestamp factory. Invalid arguments fail before provider access. Each factory
  result must be an integer from 0 to `2**63 - 1` before that page is requested
  or committed.
- A short non-empty page continues. An empty page stops with `unknown` coverage;
  reaching exactly `max_pages` on a non-empty page stops with `partial` coverage.
  Neither result certifies provider history completeness or advances a watermark.
- The frozen result contains `pages_committed`, a frozen `last_cursor` snapshot,
  `coverage`, and `stop_reason` (`empty_page` or `max_pages`).
- Cursor version must increase by exactly one per committed page and a non-empty
  tail must be new within the invocation. A changed/stale durable cursor read
  fails before another request. Repository history checks remain authoritative
  for cycles across invocations. An idempotent replay that does not advance the
  version also fails closed instead of requesting another page.
- Each page has its own atomic commit. Provider/parser/storage failures and
  caller cancellation propagate without retries; earlier committed pages remain
  available after reopening the repository.
- This helper performs no legacy synchronization, SpotLedger, receipt,
  readiness, activation, or other operational writes.

## Deferred work

Scheduling, retry/freshness policy, reconciliation and lifecycle recovery remain
separate slices. Provider retention limits still prevent an all-time or atomic
account-snapshot claim. Exact facts remain separate from the prepared ledger
until currency-aware fees, bills, balances, and reconciliation have passed their
own invariants.

## Verification boundary

The new unit tests cover precision beyond IEEE-754, large opaque IDs, signed
fees, distinct optional states, `fillTime != ts`, malformed values, SPOT-only
scope, request limits, overlap/idempotency, conflicting duplicates, repeated
pages, and conservative coverage. The full backend suite remains the required
regression gate before delivery.
