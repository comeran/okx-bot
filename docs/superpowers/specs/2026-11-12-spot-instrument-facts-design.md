# Exact SPOT instrument rule observations

This slice acquires exact OKX public instrument-rule evidence through an injected
raw client. Its durable extension retains these observations in the validated
account/environment facts store. An observation proves neither rule freshness
nor account eligibility and grants no trading permission.

## Contract

- `OKXSpotInstrumentFactsAdapter.fetch(inst_id=None)` makes exactly one
  `public_get_public_instruments` call with `{"instType": "SPOT"}` and, when
  supplied, the unchanged native `instId`. No market loading, unified projection,
  retry, private endpoint or order submission is involved.
- The parser requires success code `"0"`, a `data` list and object rows whose
  `instType` is exactly `"SPOT"`. An empty list remains an empty observation.
  Native uppercase `BASE-QUOTE` identity must agree exactly with the non-empty
  `baseCcy` and `quoteCcy` fields. A filtered response may contain only the
  requested instrument. Malformed requests fail before transport.
- Immutable `SpotInstrumentFact` values retain `tickSz`, `lotSz` and `minSz`
  as positive plain decimal strings of at most 128 characters, preserving every
  digit, leading zero and trailing zero. Decimal conversion is internal to
  positivity validation; nothing returns floats or quantized values. Boolean or
  numeric JSON, whitespace, exponent syntax, non-finite values and unbounded
  strings fail closed.
- Optional `state`, `ruleType`, `tradeQuoteCcy`, `maxLmtSz`, `maxMktSz` and
  `lever` remain bounded raw text (at most 256 characters), JSON null, empty
  strings or the existing `MISSING` sentinel. These states are never coerced or
  interpreted as availability, zero, leverage permission or safety.
- Duplicate `instId` rows fail even when identical. Conflicting duplicate rule
  observations also fail; the parser cannot select a preferred/newer observation.
  Provider and parser failures propagate without retries or partial results.

## Boundary

The adapter and parser remain independent of repositories. The opt-in
`sync_exact_spot_instruments(repository, exchange, received_at, *, inst_id=None)`
captures precisely the response parsed by the existing adapter, yields for pending
cancellation and commits atomically. It never calls `load_markets`, retries or
submits orders. Provider, parser and storage failures propagate without partial
writes. It requires no private UID; public rules can be the first account-scoped
observation.

## Durable observation contract

- `commit_private_spot_instruments` revalidates the immutable store header,
  integer receive time, raw JSON and requested instrument. The raw response must
  parse to exactly the supplied typed tuple, including order and optional states.
- `private_spot_instrument_response` stores bounded credential-free raw JSON once
  per scope and canonical SHA-256 payload hash. Original JSON text is retained
  when supplied; mapping input is serialized deterministically. The existing
  exact-response guard rejects duplicate JSON keys, non-JSON values, non-finite
  numbers, excessive nesting, credential keys and responses larger than 4 MiB.
- `private_spot_instrument` stores exact per-instrument values keyed by scope,
  response hash and native instrument ID. Each optional value has an explicit
  `missing`, `null` or `value` state, including empty strings. Changed payloads
  create distinct historical observations. Identical canonical response replay
  returns detached records with the original raw text and first receive time.
- Reads return detached records, optionally filtered by native instrument ID;
  `raw_json` is attached from the single response row without duplicating it in
  the per-instrument SQL table. Empty responses retain their response journal
  entry and produce no instrument records.
- SQLite triggers reject UPDATE, DELETE and INSERT OR REPLACE collisions in both
  tables. Instrument inserts require an existing response with the same scope,
  payload hash and receive time. Constraints preserve optional state consistency,
  bounded field lengths and native currency identity.

This extension has no legacy account/assets, ledger, receipt, activation,
allocation, frontend or order-manager effects. Freshness policy, automatic refresh,
runtime rule changes, order rounding/minimum checks and funded activation require
later slices. Account and balance facts remain separate inputs.
