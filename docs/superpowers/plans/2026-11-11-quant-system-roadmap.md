# Quant system implementation roadmap

Baseline: main `a41ff98`; audit 2026-11-11. User authorized a plan and incremental modifications. Execution evidence belongs in each slice's plan/progress; checkboxes indicate delivered capabilities, not aspirational availability.

Confirmed scope: the user selected spot first. Phases 2–6 target unleveraged spot; derivative account, funding and liquidation models require a separate implementation slice.

## Phase 1 — configuration and feasible spot research (delivered)

- [x] Persistent account-scoped allocation drafts, audit/revision control and stopped-only API.
- [x] Strategy-page draft configuration with correct local paper / OKX sandbox / live semantics.
- [x] Spot backtest cash/holdings constraints and account query contract.
- [x] Regression checks and independent cross-review (2040 backend, 502 frontend; build/type check/Ruff passed).

Detailed spec: `docs/superpowers/specs/2026-11-11-quant-foundation-design.md`.
Detailed plan: `docs/superpowers/plans/2026-11-11-quant-foundation-plan.md`.

Acceptance: explicit draft configuration persists without granting trading permission; infeasible spot trades cannot inflate research results. No exchange activation.

## Phase 2 — account isolation and active strategy allocation

- [x] Persist environment/account identity on live account, order, fill, position and reconciliation state through immutable scoped facts stores; keep legacy ambiguous rows audit-only.
- [x] Deliver exact internal spot accounting and a read-only saved-draft readiness preview (Phase 2b); this prepares accounting without funding or activation.
- [x] Add durable private-sync attempts/receipts and conservative coverage status (Phase 2c-a); this records partial/failed evidence without certifying completeness.
- [x] Retain exact OKX account-configuration observations and bind the provider `uid` immutably; borrow and mode flags remain evidence only and do not open activation.
- [x] Retain exact raw account-balance observations under the existing scoped UID binding, preserving USD equity, per-currency cash strings and missing/null/empty states. Identical payload replay is idempotent; this evidence does not fund allocations or prove available funds.
- [x] Acquire and atomically retain exact raw public SPOT instrument rules through an injected single-response adapter. Account/environment-scoped append-only response and instrument tables preserve exact strings and optional states, canonical replay identity and observation history without requiring private UID. Rules remain evidence only; freshness certification and trading authorization are deferred.
- [x] Define exclusive initial spot ownership, budget consumption/reservations and strategy-local cash/position/PnL queries.
- [x] Pure read-only exact SPOT order preflight with UID/balance binding, receipt freshness, borrow policy, native live instrument rules, decimal tick/lot/minimum checks and side-specific availBal including caller fee reserve. Unknown liability/fee/reconciliation/sync evidence keeps diagnostics closed; account level alone cannot prove unleveraged safety.
- [x] Bind persisted exact config/balance/instrument evidence to a read-only SPOT preflight API. One transaction validates store/UID binding, raw hashes and typed projections; latest missing or unsafe evidence never falls back. Responses expose bounded source provenance and checks. Fee/liability/reconciliation/sync proofs remain unknown and execution stays closed.
- [x] Retain exact account SPOT trade-fee rates and nested fee-group observations through a single-response injected raw adapter and explicit collector. UID/account/request-symbol-bound append-only storage preserves signed strings, raw/hash/state integrity and original receipt time on replay. Rates alone do not prove fee coverage; no preflight blocker is removed.
- [x] Explain group-bound ordinary SPOT maker/taker fees in the stored preflight API. Verified raw instrument groupId and latest request-symbol fee evidence determine signed, exact unrounded scenarios and charging currency under fresh UID-bound configuration. No root/category fallback; rounding/promotion coverage remains unknown and execution stays closed.
- [x] Surface explicit balance liabilities/accrued interest across every returned currency and separate negative-cash shortfalls. Fresh UID-bound positive debt makes the existing liability gate fail; zero/empty/missing fields never prove account-wide liability freedom. Extended asset fields reconstruct from immutable full journals without a migration or historical rewrite; API exposes bounded counts only.
- [x] Integrate trusted preflight evidence with atomic funded allocations and runtime order enforcement. Derivatives require their own account/risk design before activation.
- [x] Atomically validate/activate a draft from trusted fresh exchange facts; refuse overlapping/excess allocation and unsafe runtime edits.

Acceptance: empty-account first trade and subsequent closes use correct strategy funds; two strategies cannot consume the same position/cash; switching accounts/environments cannot reuse authorization or historical state. Live gate opens only after this and Phase 3 prerequisites pass.

Phase 2a implements account isolation for cached snapshots and explicit private synchronization. REST/WS bind to the captured credential/environment; clients invalidate financial data, history and pending requests on account changes. It does not grant active allocation or live strategy permission. Credential binding remains unverified exchange identity; different API keys retain separate history.

Spec: `docs/superpowers/specs/2026-11-11-account-isolation-design.md`.
Plan/evidence: `docs/superpowers/plans/2026-11-11-account-isolation-plan.md` and `2026-11-11-account-isolation-progress.md`.

Phase 2b adds a separate Decimal ledger in scoped facts stores and a diagnostic preview that includes global/account kill state. Internal prepared ledgers do not prove funding or adopt exchange holdings. Available quote observations and provider timestamps remain indicative; missing identity, account mode, metadata, full synchronization and reconciliation keep activation closed.

Spec: `docs/superpowers/specs/2026-11-11-spot-allocation-ledger-design.md`.
Plan/evidence: `docs/superpowers/plans/2026-11-11-spot-allocation-ledger-plan.md` and `2026-11-11-spot-allocation-ledger-progress.md`.

Phase 2c-a adds account-bound attempt/receipt status, same-transaction projection finalization, bounded audit samples and conservative trade-page completeness. Empty results and single-page provider data remain unknown; receipt state does not certify exact holdings, identity, account mode or complete synchronization.

Spec: `docs/superpowers/specs/2026-11-11-private-sync-receipts-design.md`.
Plan/evidence: `docs/superpowers/plans/2026-11-11-private-sync-receipts-plan.md`.

Next slices: trusted evidence acquisition and reconciliation/recovery, building on delivered pure receipt-freshness preflight, scoped instrument/config/balance persistence, single-response acquisition, resumable exact fill-page ingestion and explicitly bounded multi-page catch-up. Funded spot allocations now require an all-pass stored preflight in one transaction and are enforced on the order path. Phase 3 recovery is in place. Live opening stays closed until the Phase 6 small-size acceptance; a real stored preflight still fails closed.

Balance spec/evidence: `docs/superpowers/specs/2026-11-12-account-balance-facts-design.md` and `docs/superpowers/plans/2026-11-12-account-balance-facts-plan.md`.

Instrument spec/evidence: `docs/superpowers/specs/2026-11-12-spot-instrument-facts-design.md` and `docs/superpowers/plans/2026-11-12-spot-instrument-facts-plan.md`.

Read-only preflight spec/evidence: `docs/superpowers/specs/2026-11-12-spot-preflight-design.md` and `docs/superpowers/plans/2026-11-12-spot-preflight-plan.md`. Diagnostic readiness does not activate strategies or submit orders.

Persisted diagnostic bridge: `docs/superpowers/specs/2026-11-12-persisted-spot-preflight-design.md` and `docs/superpowers/plans/2026-11-12-persisted-spot-preflight-plan.md`. `POST /api/ops/spot/preflight` consumes only server-stored exact evidence and requires the current account-scope handshake; client readiness attestations are rejected.

Fee acquisition evidence: `docs/superpowers/specs/2026-11-12-spot-trade-fee-facts-design.md` and `docs/superpowers/plans/2026-11-12-spot-trade-fee-facts-plan.md`. Raw rates alone do not prove coverage. The diagnostic slice below adds group/currency/freshness interpretation; reserves additionally require trusted rounding and promotion handling.

Fee diagnostic evidence: `docs/superpowers/specs/2026-11-12-spot-fee-diagnostics-design.md` and `docs/superpowers/plans/2026-11-12-spot-fee-diagnostics-plan.md`. Group/currency/freshness interpretation is delivered as unrounded scenarios. Unknown fee rounding, promotion coverage, liability evidence or synchronization/reconciliation still makes the stored preflight fail, so activation refuses.

Liability diagnostic evidence: `docs/superpowers/specs/2026-11-12-spot-liability-diagnostics-design.md` and `docs/superpowers/plans/2026-11-12-spot-liability-diagnostics-plan.md`. Observed positive liabilities/interest now explicitly reject readiness; balance coverage and mode applicability are not certified, so absence remains unknown. Full synchronization, reconciliation and trusted fee coverage still keep a real preflight from passing, so the funded path refuses and live opening stays closed.

## Phase 3 — reconciliation, unknown outcomes and lifecycle recovery

- [x] Add an isolated exact raw OKX SPOT fills page parser and single-page adapter. It preserves provider decimal strings, signed fee/rebate values, bill/order identities, `fillTime` versus `ts`, and missing/null/empty optional fields; it never infers completeness or updates the compatibility ledger.
- [x] Persist exact SPOT page journals, typed fill facts, immutable provider UID binding, and a transactional versioned cursor. Replays are idempotent, stale/cyclic traversal fails closed, and coverage remains non-complete.
- [x] Add injected single-response account-config ingestion and exact SPOT fill-page orchestration; each explicit call resumes its durable filter-bound tail/version and returns detached evidence. No automatic catch-up or completeness inference.
- [x] Add caller-invoked bounded exact SPOT fill catch-up using durable filter-bound cursors. Each page commits independently; short pages continue, empty pages stay unknown and the page bound returns partial coverage. Stale/cyclic cursors fail closed; there are no retries or completeness/ledger/activation writes.
- [x] Verify exact local fill recovery before reads and append/replay: raw/hash/request identities, insertion-ordered traversal chains, complete cursor/page sets and first-observed full fill projections must agree within one transaction. Corrupt/orphan state blocks resume without automatic repair; valid overlap, advanced replay and empty-tail semantics remain conservative. This establishes local journal integrity, not provider-history completeness.
- [x] Startup and continuous private-state synchronization, with bounded retries and visible freshness.
- [x] Resolve ambiguous submission/cancel outcomes by durable client/exchange identity before another action; never blindly resubmit.
- [x] Persist intended/actual runtime state and checkpoints; reconcile before resume, singleton execution lease and graceful shutdown.

Acceptance: delayed/partial fills, more than one history page, lost responses, network interruption, duplicate messages and process restarts converge to explained orders/fills/positions without duplicate orders.

The running loop publishes freshness and retries an injected private fetch at most three times. It does not mark coverage complete and the default process does not open an exchange socket. A lost submit or cancel is adopted by the existing client or exchange id, or it stays blocking. A dead runner is reconciled before resume; shutdown releases only this process's lease. Fills update the existing order and are not turned into a second order or a guessed position. Live opening stays closed.

Local exact recovery spec/evidence: `docs/superpowers/specs/2026-11-12-exact-fill-recovery-design.md` and `docs/superpowers/plans/2026-11-12-exact-fill-recovery-plan.md`. Exact getters and page commits validate the account's complete retained journal before returning a resumable cursor. History-dependent verification cost and explicit operator recovery remain; account-equity reconciliation and provider completeness are not certified.

## Phase 4 — continuous risk and operator controls

- [x] Monitor stale prices/account facts, position plus pending exposure, daily loss/drawdown, and conditional derivative margin risk.
- [x] Distinct pause-opening, stop-engine, cancel-order and flatten-position controls with recorded outcomes and manual recovery.
- [x] End-to-end UI authentication, per-source heartbeat/freshness and configuration saved/effective/restart-required states.
- [x] Health/readiness, bounded push queues and durable alert retries.

Acceptance: stale/unknown state visibly inhibits new exposure; risk actions remain explainable and reconcilable; UI disconnection does not block execution supervision.

The supervisor records unknown or stale prices, account facts, combined position and pending exposure, daily loss, and drawdown. Derivative margin is checked only for swap, futures, and options; spot does not use it and derivative trading stays closed. A blocking reason pauses new exposure. Pause, stop-engine, cancel, and flatten stay separate recorded actions. Recovery is manual and is refused while a blocking reason remains. Login remains required when an API token is configured. Source heartbeats and saved, effective, or restart-required config state are visible. Health stays up when readiness is not ready. The supervision push queue is bounded, and failed alerts remain in a durable outbox for retry. The default loop does not call the exchange. Live opening stays closed.

## Phase 5 — reproducible research and complete accounting

- [x] Immutable run manifest: code/config/cost/model versions, data hash and retained dataset; persist dated equity/drawdown and rejected orders.
- [x] Closed-candle and missing/duplicate/revised-data policy; reproducible replay.
- [x] Strategy callbacks/query parity and validated version-to-runtime release binding.
- [x] Sample separation, walk-forward, parameter stability and benchmark comparisons; record all trials.
- [x] Currency-aware fees/rebates and ledger for trades, funding, transfers and settlements; reconcile net equity changes.

Acceptance: a run is independently reproducible, online-vs-research differences can be attributed, and account equity movements reconcile to ledger events.

A research run freezes code, config and cost-model versions with the retained closed bars, dated equity and drawdown, and rejected orders. The same inputs reload to the same manifest id. Unclosed bars are excluded. Missing, duplicate and revised bars are recorded, and the first retained bar is not replaced. Replay of that dataset keeps the same hash. Research and runtime strategies must expose the same callbacks and queries. A strategy version runs only when it is bound to an allowed runtime version. Price, fee and quantity differences between research and online fills are listed. Train, validation and test samples stay separate. Walk-forward windows, parameter stability and the benchmark excess are recorded for every trial, including rejected ones. Fees, rebates, funding, transfers and settlements reconcile to the equity change in each currency. Live opening stays closed.

## Phase 6 — deployment and running acceptance

- [ ] Automated backend/frontend checks, reproducible packaging and process supervision.
- [ ] Versioned database migration with backup/restore and rollback rehearsal; retention and audit export.
- [ ] Long-running OKX sandbox acceptance and injected failure drills for Phases 2–5.
- [ ] Separately authorized, explicitly funded small live acceptance after all prerequisites have evidence.

Acceptance: restart, disconnect and restore drills pass and all residual differences are explained. Local tests alone do not constitute exchange or long-running acceptance.

## Later expansion

Multi-symbol/multi-strategy portfolio budgeting and rebalancing follow the initial closed loop. Multi-exchange, order-book/high-frequency models, machine learning and distributed infrastructure are demand-driven extensions.
