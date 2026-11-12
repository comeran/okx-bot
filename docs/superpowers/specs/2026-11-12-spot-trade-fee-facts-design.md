# Exact SPOT trade-fee evidence

Continue the approved spot-first roadmap by retaining the inputs needed for a later currency-aware fee reserve model. Account config `feeType` alone does not contain maker/taker rates; legacy unified float fee projections cannot provide exact evidence.

## Provider contract

Verified against [OKX Get fee rates](https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-fee-rates) on 2026-11-12. Read-only `GET /api/v5/account/trade-fee` accepts `instType=SPOT` and `instId`. Supplying the symbol matters for market-maker incentive rates. The response contains no account UID and need not echo the requested symbol. Positive maker/taker rates represent rebates and negative rates commissions. `feeGroup` requires instrument group mapping for later interpretation; root USDT and USDⓈ/crypto rates are distinct. The API does not reflect every zero-fee promotion. Account configuration separately defines received-currency versus quote-currency charging.

## Approach and boundary

Add an exact frozen parser/DTO, a single-response injected raw adapter, scoped append-only persistence and an explicit one-call collection helper. Require a native spot instrument ID on every request; no unfiltered/groupId queries. Store that request context alongside the complete raw response. Parsing only or calculating a reserve directly would either leave evidence non-durable or prematurely choose fee groups/currency/rounding. The builder chooses this bounded acquisition slice under the approved incremental plan.

No actual private exchange requests are made during implementation or tests. There is no runtime/API collector, fee reserve computation, balance/ledger/funding write, activation or order submission in this slice. Existing preflight fee and operational blockers remain unknown.

## Exact contract

- `SpotTradeFeeFact(requested_inst_id, inst_type, level, maker, taker, maker_usdc, taker_usdc, ts, rule_type, category, fee_group)` is frozen. Eight optional root strings retain missing/null/empty/value states. `fee_group` is MISSING, None or an immutable tuple of `SpotFeeGroupFact(group_id, maker, taker, rpi_maker)` with unique nonempty raw group IDs and exact optional text states. No numeric coercion, rate normalization, rebate clamping, fee estimation or precision loss. Uninterpretable strings remain observations, never coverage proof.
- Parser `parse_okx_spot_trade_fee(payload, *, inst_id)` requires one success row, exact raw `instType=SPOT`, and validated requested symbol. If a response supplies `instId`, require it to match the request. Other raw fields remain in the complete journal.
- Adapter calls only `private_get_account_trade_fee({'instType': 'SPOT', 'instId': inst_id})` once and returns the parsed fact; transport remains caller-owned.
- Persistence requires an already bound scoped UID and rejects any supplied different UID. Identity `(account_scope_id, uid, requested_inst_id, payload_hash)` keeps identical responses for different requests separate. Validate bounded raw JSON and raw/typed equality before writes. Preserve original JSON and first receipt on replay. Keep root state/value columns and canonical `fee_group_json` with missing/null/value marker. Reject UPDATE, DELETE and REPLACE collisions.
- Getter validates header, UID, request symbol, raw hash, instType, every projection/state and group JSON before returning detached records. Corruption fails closed; no older fallback or money projection.
- `sync_exact_spot_trade_fee(repository, exchange, received_at, *, inst_id, uid=None)` checks UID, native symbol and timestamp before transport; captures one response, honors cancellation before the atomic commit, propagates provider/parse/commit failures without retry, and returns detached stored evidence. It cannot verify credential ownership of an injected transport.

## Acceptance

Network-denied tests use real temporary scoped stores. Cover precision/signs/optional states/groups, invalid envelopes/types/symbols, request context separation, immutable/reopened/detached observations, raw/hash/projection mismatch, replay times, UID/scope/master refusal, cancellation and unchanged operational state. Full backend suite, Ruff, diff check and independent actual-code review precede PR delivery.
