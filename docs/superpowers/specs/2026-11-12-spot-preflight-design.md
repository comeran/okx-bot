# Exact read-only SPOT order preflight

## Scope

`src/ops/spot_preflight.py` combines immutable exact config, balance and instrument
facts into explainable checks for one native SPOT limit order. Inputs include
local receipt times, requested base amount and quote-per-base price as strings,
and caller-owned account binding/completeness evidence. The evaluator performs
no provider calls, storage access, reservations, allocation changes, activation
or order-manager integration.

The caller must select observations from the same trusted account/environment.
The balance endpoint has no UID, so a matching expected UID, configuration UID
and explicitly supplied balance UID binding are required. These parameters are
caller assertions, not independent credential verification.

## Required checks

Every check has a stable code, `pass|fail|unknown` status and a reason. Overall
`can_submit` and `can_activate` mean only that every check in this diagnostic
passed. They grant no execution authority; funded allocation and runtime gates
remain separate unfinished work.

- `freshness_policy`, `config_freshness`, `balance_freshness`,
  `instrument_freshness`: nonnegative bounded integer milliseconds, inclusive
  `now - receipt <= max_age_ms`, and no future receipts. The default bound is
  30 seconds. Missing/invalid time or `max_age_ms=None` is unknown, never a
  bypass. Stale dependent facts cannot pass their checks.
- `identity`: a fresh configuration and all three equal UID bindings. Missing
  binding is unknown; observed mismatch fails.
- `borrow_policy`: either fresh raw `autoLoan='1'` or
  `enableSpotBorrow='1'` fails. Only both raw flags equal to `'0'` pass this
  policy check. Missing/null/empty/unrecognized flags remain unknown. Account
  level, including `acctLv='1'`, proves neither disabled borrowing nor absence
  of liabilities.
- `liability_free`: a separate explicit boolean caller attestation is required.
  Neither disabled borrow policy nor a balance response alone supplies it.
- `symbol`, `side`, `amount`, `price`: native BASE-QUOTE ID, exact `buy|sell`,
  and bounded positive plain decimal strings. No aliases, float coercion,
  exponent notation, nonfinite values, stripping or quantization.
- `instrument_identity`, `instrument_state`, `instrument_rules`: fresh native
  symbol match, exact `state='live'`, and positive exact tick/lot/minimum values.
  Absent/null/empty state is unknown; any observed other state fails.
- `amount_increment`, `price_increment`, `minimum_size`: exact Decimal remainder
  and comparison enforce lotSz/tickSz multiples and minSz in base units. A fresh
  isolated arithmetic context prevents caller precision from rounding checks.
- `fee_reserve`: exact nonnegative spending-currency amount supplied by the
  caller, covering its fee model. Zero is explicit evidence; absent/invalid
  reserve or unknown fee coverage is unknown. A fee charged in another asset
  requires a future separate currency-aware model; callers must pass None.
- `available_funds`: buys require quote `availBal >= amount * price + reserve`;
  sells require base `availBal >= amount + reserve`. Currency matching is exact.
  Missing/null/empty/invalid per-currency availBal is unknown; negative or
  insufficient values fail. `cashBal`, equity and account `totalEq` (USD) never
  replace the spending currency's exact availBal.
- `reconciliation`, `sync_completeness`: explicit boolean caller attestations
  must both be True. False fails; absent, integers and strings remain unknown.

All user/provider quantities stay as input strings. Decimal is used only
internally, with bounded inputs and enough precision for exact products,
remainders and fee additions. Frozen check/result DTOs expose no mutable state.

## Boundaries

No provider facts in this slice independently prove full history, absence of
liabilities, fee coverage, complete reconciliation or exclusive strategy funds.
The evaluator accepts those separately from a trusted caller and defaults them
to unknown. It does not refresh facts or infer completeness from an empty
account, a short page, a receipt or a simple account level. No trading or
activation path consumes this evaluator in this slice.
