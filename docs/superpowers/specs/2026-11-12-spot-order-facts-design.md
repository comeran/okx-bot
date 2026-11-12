# Exact SPOT order facts design

## Intent

Add a read-only, request-bound parser and injected raw adapter for one OKX SPOT order detail response. The order manager already records durable client identities when a submission is accepted but uncertain; an explicit order lookup is the missing evidence needed before a later recovery slice can decide whether a submission or cancellation reached a terminal state.

## Goals

- Accept exactly one native SPOT instrument and exactly one lookup identity: `ordId` or `clOrdId`.
- Issue one raw OKX order-detail request with `instType=SPOT`, `instId`, and the selected identity.
- Preserve bounded provider strings exactly for IDs, state, size, price, average fill, fee, fee currency, timestamps, and optional fields; never convert to float or infer terminality.
- Require a successful one-row response whose `instType`, `instId`, and requested identity agree with the request. Missing/null/empty optional values remain distinct.
- Represent not-found/empty data and provider errors as caller-visible unknown/exception boundaries without retries or order side effects.

## Non-goals

No persistence, UID/account binding, raw journal, legacy order projection, reconciliation, ledger mutation, reserve release, cancellation, resubmission, strategy activation, or algorithmic-order family support.

## Approach

Create `src/exchange/okx_order_facts.py` with a frozen `SpotOrderFact`, strict field validation using existing raw-text helpers, a parser for the native success envelope, and an injected `OKXSpotOrderFactsAdapter`. The adapter calls one protocol method and delegates all semantic validation to the parser. This matches existing account, fee, instrument, and fill fact modules and keeps transport independent from storage.

A storage-first implementation was rejected for this slice because it would require choosing journal identity/UID semantics before the parser contract is exercised. A unified CCXT order path was rejected because it loses raw Decimal text, response presence states, and explicit request identity.

## Safety rules

- `ordId` and `clOrdId` are mutually exclusive; each is bounded raw text and cannot be an empty string.
- `inst_id` must be a canonical two-asset spot symbol; no native symbol aliases or margin/swap rows are accepted.
- Required provider fields are validated as bounded raw text; optional fields use `MISSING`/`None`/empty text distinctions.
- `state` is retained as provider text from an allowlisted order-state set when present; it is never mapped to a local terminal decision.
- A response with zero or multiple rows, mismatched IDs/instrument/type, malformed numbers/timestamps, or extra conflicting identity fails closed with `MalformedProviderData`.

## Verification

Unit tests cover request validation before provider access, exact request parameters, one-call behavior, SPOT/type and identity mismatches, empty/not-found responses, malformed required/optional values, large opaque IDs, Decimal precision, missing/null/empty distinctions, and no storage/ledger side effects.
