# Exact SPOT instrument rule observations

This slice acquires exact OKX public instrument-rule evidence through an injected
raw client. It supplies immutable facts without storing them or granting trading
permission. An observation proves neither rule freshness nor account eligibility.

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

The adapter and parser have no repository, legacy projection, ledger, activation,
allocation, frontend or order-manager integration. Persistence, provenance and
freshness policy, runtime rule changes, order rounding/minimum checks and funded
activation require later slices. Account and balance facts remain separate inputs.
