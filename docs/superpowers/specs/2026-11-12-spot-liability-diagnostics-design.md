# Stored SPOT liability diagnostics

Continue the authorized spot-first roadmap by turning verified stored balance observations into explicit debt/interest and cash-shortfall diagnostics. This slice performs no provider requests, financial writes, funding, order submission or strategy activation.

## Provider basis

Verified the public [OKX balance contract](https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-balance) on 2026-11-12. `liab` is currency liability, `crossLiab` cross liability, `isoLiab` isolated liability, `uplLiab` liability due to unrealized losses, and `interest` accrued interest. Applicable account modes vary; inapplicable fields may be empty. Currency rows may be omitted when cashBal and eq are both zero. Consequently, observed zero/empty/missing rows cannot certify a complete liability-free account. Do not add these overlapping fields or aggregate currencies into a fabricated debt total.

## Approach and tradeoffs

Use the existing hash-verified immutable full balance/details journal, adding the three currently unprojected optional asset fields `cross_liab`, `iso_liab`, `upl_liab` at the end of the frozen DTO. The parser/optional mapping preserves missing/null/empty/text exactly. No table migration or historical rewrite is necessary because asset details are already retained as full JSON. Invalid historical values newly fail closed during reparse. A new endpoint/collector would duplicate evidence already retained; blindly certifying zero balances would invent completeness. Those alternatives are excluded.

Add a pure liability diagnostic requiring fresh config and balance receipts, the fixed inclusive 30,000 ms age bound in the stored API, no future times, and matching config/expected/balance UID. No interpretation or counts are published without this binding. Examine every returned currency, including currencies outside the requested pair. Parse only bounded plain decimal strings (at most 128 characters), preserving tiny positive values without floats or ambient decimal-context arithmetic.

Any positive liab/crossLiab/isoLiab/uplLiab/interest is explicit adverse evidence, even if another field is missing, unsupported, negative or contradictory. It sets the internal liability_free attestation to False and the existing preflight gate to fail. Never emit True. Negative liability/interest values, exponents, missing/null/empty and unsupported text stay uninterpretable; do not cancel a positive field against another value. Negative cashBal is a separate shortfall check and does not itself claim a loan. Cash does not offset debt.

Checks: `liability_evidence_freshness`, `liability_evidence_identity`, `reported_liabilities`, `cash_balance_sign`. reported_liabilities fails on any positive observation; otherwise it remains unknown, including complete explicit zeros. cash_balance_sign fails on any negative cash, passes only when all returned nonempty rows have interpretable nonnegative cash, and is otherwise unknown. Missing/mismatched/stale binding makes both interpretation checks unknown.

The strict four-string API request stays unchanged. Response adds liability_diagnostic with coverage=unknown and bounded counts only: observed currencies, currencies with positive liability/interest, currencies with negative cash, uninterpretable liability fields and uninterpretable cash fields. No full balance, currency names, raw amounts or field values are serialized. Count affected currencies once; field uncertainty counts each field. No derived fee reserve or sync/reconciliation attestation. The API remains diagnostic_only and can_submit/can_activate remain false.

## Acceptance

TDD covers each new raw field state and validation, historical journal reconstruction/replay/reopen with no row/schema changes, invalid legacy values failing closed, fresh identity binding and boundaries, any-currency debt, tiny/large exact values and ambient Decimal traps, all-zero/empty/missing data never certifying safety, partial positive evidence overriding uncertainty, separate cash-shortfall behavior, and real API no writes/privacy/strict-input/latest-no-fallback behavior. Full backend, Ruff, whitespace and independent actual-code review precede authorized PR/merge delivery.
