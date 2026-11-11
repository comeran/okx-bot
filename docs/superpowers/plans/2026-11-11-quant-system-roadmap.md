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
- [ ] Define exclusive initial spot ownership, budget consumption/reservations and strategy-local cash/position/PnL queries.
- [ ] Account-mode, instrument-unit and available-funds preflight. Derivatives require their own account/risk design before activation.
- [ ] Atomically validate/activate a draft from trusted fresh exchange facts; refuse overlapping/excess allocation and unsafe runtime edits.

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

Next slices: instrument-rule freshness policy and reconciliation/recovery, building on delivered scoped instrument/config/balance persistence, single-response acquisition, resumable exact fill-page ingestion and explicitly bounded multi-page catch-up. Funded account-wide allocations and order-manager integration remain pending until these prerequisites have evidence.

Balance spec/evidence: `docs/superpowers/specs/2026-11-12-account-balance-facts-design.md` and `docs/superpowers/plans/2026-11-12-account-balance-facts-plan.md`.

Instrument spec/evidence: `docs/superpowers/specs/2026-11-12-spot-instrument-facts-design.md` and `docs/superpowers/plans/2026-11-12-spot-instrument-facts-plan.md`.

## Phase 3 — reconciliation, unknown outcomes and lifecycle recovery

- [x] Add an isolated exact raw OKX SPOT fills page parser and single-page adapter. It preserves provider decimal strings, signed fee/rebate values, bill/order identities, `fillTime` versus `ts`, and missing/null/empty optional fields; it never infers completeness or updates the compatibility ledger.
- [x] Persist exact SPOT page journals, typed fill facts, immutable provider UID binding, and a transactional versioned cursor. Replays are idempotent, stale/cyclic traversal fails closed, and coverage remains non-complete.
- [x] Add injected single-response account-config ingestion and exact SPOT fill-page orchestration; each explicit call resumes its durable filter-bound tail/version and returns detached evidence. No automatic catch-up or completeness inference.
- [x] Add caller-invoked bounded exact SPOT fill catch-up using durable filter-bound cursors. Each page commits independently; short pages continue, empty pages stay unknown and the page bound returns partial coverage. Stale/cyclic cursors fail closed; there are no retries or completeness/ledger/activation writes.
- [ ] Startup and continuous private-state synchronization, with bounded retries and visible freshness.
- [ ] Resolve ambiguous submission/cancel outcomes by durable client/exchange identity before another action; never blindly resubmit.
- [ ] Persist intended/actual runtime state and checkpoints; reconcile before resume, singleton execution lease and graceful shutdown.

Acceptance: delayed/partial fills, more than one history page, lost responses, network interruption, duplicate messages and process restarts converge to explained orders/fills/positions without duplicate orders.

## Phase 4 — continuous risk and operator controls

- [ ] Monitor stale prices/account facts, position plus pending exposure, daily loss/drawdown, and conditional derivative margin risk.
- [ ] Distinct pause-opening, stop-engine, cancel-order and flatten-position controls with recorded outcomes and manual recovery.
- [ ] End-to-end UI authentication, per-source heartbeat/freshness and configuration saved/effective/restart-required states.
- [ ] Health/readiness, bounded push queues and durable alert retries.

Acceptance: stale/unknown state visibly inhibits new exposure; risk actions remain explainable and reconcilable; UI disconnection does not block execution supervision.

## Phase 5 — reproducible research and complete accounting

- [ ] Immutable run manifest: code/config/cost/model versions, data hash and retained dataset; persist dated equity/drawdown and rejected orders.
- [ ] Closed-candle and missing/duplicate/revised-data policy; reproducible replay.
- [ ] Strategy callbacks/query parity and validated version-to-runtime release binding.
- [ ] Sample separation, walk-forward, parameter stability and benchmark comparisons; record all trials.
- [ ] Currency-aware fees/rebates and ledger for trades, funding, transfers and settlements; reconcile net equity changes.

Acceptance: a run is independently reproducible, online-vs-research differences can be attributed, and account equity movements reconcile to ledger events.

## Phase 6 — deployment and running acceptance

- [ ] Automated backend/frontend checks, reproducible packaging and process supervision.
- [ ] Versioned database migration with backup/restore and rollback rehearsal; retention and audit export.
- [ ] Long-running OKX sandbox acceptance and injected failure drills for Phases 2–5.
- [ ] Separately authorized, explicitly funded small live acceptance after all prerequisites have evidence.

Acceptance: restart, disconnect and restore drills pass and all residual differences are explained. Local tests alone do not constitute exchange or long-running acceptance.

## Later expansion

Multi-symbol/multi-strategy portfolio budgeting and rebalancing follow the initial closed loop. Multi-exchange, order-book/high-frequency models, machine learning and distributed infrastructure are demand-driven extensions.
