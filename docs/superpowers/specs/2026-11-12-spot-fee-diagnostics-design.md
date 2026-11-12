# Group-bound spot fee diagnostics

Continue the approved spot-first roadmap by interpreting the exact fee evidence delivered in PR #31 within the existing read-only preflight API. No provider request, funding, ledger mutation, order submission or activation.

## Provider basis and scope

Verified public [instrument documentation](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-instruments), [fee rates](https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-fee-rates) and account config on 2026-11-12. Join fees by exact SPOT `groupId`; never guess using a root rate or quote-currency category. Config `feeType=0` charges the received currency (base on buys, quote on sells); `feeType=1` charges quote on both sides. Positive rates are rebates and negative rates commissions. This slice handles ordinary native SPOT limit-order diagnostics, selecting ordinary maker/taker rather than RPI rates.

The checked V5 contract does not provide a universal spot trade-fee rounding rule. Price tick, quantity lot size and withdrawal precision are not evidence of such a rule. Publish exact **unrounded scenarios**, not a guaranteed fee or spendable-funds reserve. Rounding, promotion coverage, complete synchronization, liabilities and reconciliation remain unknown; fee_reserve and execution flags stay closed.

## Approach

Extend `SpotInstrumentFact` with a final optional `group_id` field. It is parsed and validated from the existing immutable full raw journal, including historical observations; it is intentionally not an ORM column. `INSTRUMENT_OPTIONAL_FIELDS` remains the mapping of persisted optional columns, documented as such. Reparse/hash validation already covers raw-only fields, and raw/typed equality covers ingestion. This avoids an additive-column migration that would need to rewrite append-only projections where old raw JSON already contains groupId. Ordinary ORM rows continue exposing it through raw_json only. Invalid historical groupId newly fails closed.

Capture the latest fee observation **for the requested symbol** inside the same repository transaction as config/balance/instrument. Validate scope/UID, canonical raw hash, all projections and group JSON. Preserve first receipt on replay; do not fall back from a newer bad/stale/future fee or use another symbol's observation.

Add a pure Decimal fee diagnostic with frozen result/checks. It requires fresh config, instrument and fee receipts (inclusive 30,000 ms, future rejected), matching UID/native symbol and exact `normal` rule types. Select exactly the instrument's nonempty raw group ID in fee_group. Missing group evidence is unknown; an explicit nonempty list with no matching ID fails. No top-level-rate fallback. Both selected rates must be bounded plain decimal strings with absolute value <1; uninterpretable/unsupported observations remain unknown.

For positive base amount q and quote-per-base price p, the fee basis is q for feeType=0 buys, otherwise q*p. Multiply each signed provider rate by that basis in an isolated Decimal context. Preserve original rate spelling, render computed amounts without exponent/float, and return max(0, -maker_amount, -taker_amount) as an unrounded commission scenario. No rebate is counted as available cash. Invalid order inputs produce no fee amounts; existing preflight checks explain them.

The existing strict API body stays unchanged. Response adds trade_fee source provenance, fee checks and a bounded fee_diagnostic with selected group, currency, rates and signed unrounded amounts. No raw response, full fee group list, full balance or private config is serialized. Source identity/freshness/mapping/value/currency/scenario checks may pass; fee_rounding stays unknown and no reserve is supplied to the underlying preflight evaluator.

## Acceptance

Tests cover historical raw group projection without schema/row changes, all optional states, invalid metadata, exact group matching/no fallback, both fee types/sides, rebates, decimal context isolation and boundaries, latest same-symbol fee selection, UID/scope/hash/group corruption, freshness and strict API/unchanged database rows. Full backend suite, Ruff, diff check and independent actual-code review precede PR delivery. No actual private exchange or trading acceptance.
