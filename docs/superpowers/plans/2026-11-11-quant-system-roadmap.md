# Quant system implementation roadmap

Baseline: main `a41ff98`; audit 2026-11-11. User authorized a plan and incremental modifications. Execution evidence belongs in each slice's plan/progress; checkboxes indicate delivered capabilities, not aspirational availability.

Confirmed scope: the user selected spot first. Phases 2–6 target unleveraged spot; derivative account, funding and liquidation models require a separate implementation slice.

## Phase 1 — configuration and feasible spot research (current slice)

- [x] Persistent account-scoped allocation drafts, audit/revision control and stopped-only API.
- [x] Strategy-page draft configuration with correct local paper / OKX sandbox / live semantics.
- [x] Spot backtest cash/holdings constraints and account query contract.
- [x] Regression checks and independent cross-review (2040 backend, 502 frontend; build/type check/Ruff passed).

Detailed spec: `docs/superpowers/specs/2026-11-11-quant-foundation-design.md`.
Detailed plan: `docs/superpowers/plans/2026-11-11-quant-foundation-plan.md`.

Acceptance: explicit draft configuration persists without granting trading permission; infeasible spot trades cannot inflate research results. No exchange activation.

## Phase 2 — account isolation and active strategy allocation

- [ ] Persist environment/account identity on live account, order, fill, position and reconciliation state; keep legacy ambiguous rows audit-only.
- [ ] Define exclusive initial spot ownership, budget consumption/reservations and strategy-local cash/position/PnL queries.
- [ ] Account-mode, instrument-unit and available-funds preflight. Derivatives require their own account/risk design before activation.
- [ ] Atomically validate/activate a draft from trusted fresh exchange facts; refuse overlapping/excess allocation and unsafe runtime edits.

Acceptance: empty-account first trade and subsequent closes use correct strategy funds; two strategies cannot consume the same position/cash; switching accounts/environments cannot reuse authorization or historical state. Live gate opens only after this and Phase 3 prerequisites pass.

## Phase 3 — reconciliation, unknown outcomes and lifecycle recovery

- [ ] Paginated fill catch-up with durable cursors and idempotent replay.
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
