# Quant system foundation design

## Purpose and scope

Implement the first independently reviewable slice of the 2026-11-11 audit: explicit strategy allocation configuration and a feasible single-symbol spot backtest. Target a personal, single-OKX, low/mid-frequency system. The user authorized planning and incremental code changes; this does not authorize starting strategies or exchange operations.

The user explicitly selected **spot first** in the follow-up scope question. Use unleveraged spot for the next execution/account/risk milestones; derivatives remain a separate future capability.

## Design decisions

1. Save allocations as **draft configuration**, not as balances, reservations, or permission to trade. The current live risk gate, strategy account queries, and exchange account state are not allocation-aware. Keep live startup fail-closed until those components and recovery are integrated.
2. Draft identity includes OKX environment (sandbox/live), a non-secret account fingerprint, market type, strategy, symbol and quote currency. Derive identity from configured credentials on the server; never accept arbitrary account identity from a request or return credentials. Do not assign old exchange snapshots to a current account.
3. For this slice, support spot allocation drafts only. One strategy owns one complete symbol allocation per account scope; two drafts cannot claim the same symbol in that scope. The same strategy/symbol may be configured independently in sandbox and live accounts.
4. A budget is a positive finite quote-currency number. Saving it performs no exchange I/O and does not assert that the account has that amount. Show that balance/position verification and activation are pending. Existing positions are not imported or claimed in this slice.
5. Updates and deletion require a stopped strategy, no starting engine, and optimistic revision checks. Every mutation also carries the previously loaded `expected_scope_id` and `expected_symbol`; reject a changed current account/environment or strategy symbol with 409 before writing, including first creation. Validate on the backend. Mutations and their before/after audit records are atomic. A conflicting symbol claim or stale revision returns 409. Invalid input returns 422. Missing configured credentials produces an unavailable context, never a fabricated account. Deleting a strategy config atomically soft-releases its drafts across all scopes with audit records, so orphaned drafts cannot block a later strategy indefinitely.
6. Add an independent allocation panel to the existing strategy screen. Keep algorithm params/YAML unchanged. Show server-returned account context, currency, budget, revision and draft status. Saving must not start a strategy. Cloning does not copy allocation authorization.
7. The backtest's current spot-data execution path becomes explicitly unleveraged spot: no negative cash, no naked or excess selling. Check affordability using the actual candidate fill price plus fees, at fill time and in submission order. Reject the entire infeasible order and notify `on_order` once. Rejected orders create no trade or accounting changes.
8. Pending orders do not reserve funds in this model. `get_balance` means cash balance. A later order rechecks funds at its own execution time. Slippage and favorable gap fills retain their existing semantics.
9. Complete backtest `get_balance`, `get_position` and `get_positions` using detached snapshots of the same portfolio. `on_init` sees initial cash; fill callbacks see post-fill state without a future close; `on_bar` sees close valuation. Restrict queries to the correct strategy/symbol and never return mutable internal state.
10. Keep `run(strategy, bars)` usable for existing simple custom strategies: use the strategy symbol when present and otherwise bind to the first submitted symbol. Explicitly reject unsupported derivative/multiple-symbol execution rather than silently applying spot accounting. The HTTP API supplies explicit spot/symbol context.

## Boundaries and compatibility

- Preserve previously fixed settings references, test collection isolation, dashboard versioning, backtest cancellation, and gap pricing.
- Local paper/demo execution and actual OKX sandbox are distinct. `mode=demo` currently means local paper; exchange sandbox is selected by `exchange.demo` on the live route.
- Do not read user credential files, call exchange APIs, start services against user state, activate allocations, or migrate historical ownership.
- Do not remove or weaken existing live safety checks.
- Keep new backend allocation code in focused modules; do not restructure the large existing repository or strategy router.
- SQLite remains supported. Use database transactions and uniqueness constraints for conflicting draft writes.
- All automated tests use isolated databases/settings and deny external network calls.
- Work on `codex/quant-system-foundation` in the attached managed worktree. Preserve the original checkout and its `.workbuddy/`.
- Leave code reviewable in the working tree; commit/push/merge are separate delivery actions.

## Acceptance

- Drafts survive Repository reconstruction; sandbox/live and account keys are isolated; a second strategy cannot claim the same scope/symbol; stale edits/deletes cannot overwrite a newer revision; invalid and concurrent writes leave no partial audit/data changes.
- API rejects changes while a strategy is starting/running and never performs exchange I/O. Saving a draft still does not make the existing live startup gate pass.
- UI reports draft/pending verification accurately and can read/save/remove drafts with visible validation/conflict errors. It never presents saved budget as verified available funds.
- Spot backtests reject insufficient cash (including gap/slippage/fees) and naked sells, allow exact affordable fills, apply sequential orders against updated cash/holdings, and preserve cancel behavior.
- Cash/position queries during initialization, fills and bars agree with hand-calculated values, and returned snapshots cannot mutate portfolio state.
- Backend/frontend regressions, Ruff, frontend type check/build, and an independent review pass for changed code.

## Following slices

The roadmap defines account-scoped live facts, active allocation accounting, continuous reconciliation/unknown-order recovery, lifecycle recovery, ongoing risk, reproducible research, accounting and deployment acceptance. This foundation does not declare those capabilities complete.
