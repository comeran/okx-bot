# SDD ledger — plan: docs/superpowers/plans/2026-11-11-spot-allocation-ledger-plan.md

Base `7fa0bc19a163f2f822be06ded0c833362e220044`; branch `codex/spot-allocation-ledger`, existing managed worktree reused. User explicitly authorized continuing the incremental roadmap.

Preflight interfaces: Task 1 produces `SpotLedgerStore.get_strategy` exact string snapshot → Task 2 returns it in readiness envelope → Task 3 renders and fences by account epoch/draft revision. Task 2 owns allocation service/API/store list method; Task 3 owns frontend; no shared product-file mutations. Tasks agree on non-executing/read-only boundaries.

Decision: implement exact internal prepared ledger plus diagnostic readiness before allocation activation. Existing persisted avail_bal and provider updated_at do not prove authoritative available cash, received freshness, complete sync, identity or account mode. The preview explicitly retains unknown/blocking checks. Cost: ledger initialization/order manager integration and real activation remain pending; this batch cannot trade.

Decision: keep legacy float paper accounting unchanged and use dedicated Decimal text tables in scoped facts stores. Internal seed never claims external positions/funds and has no REST entry point. Future provider integration needs exact quote cost/fee currency rather than float reinterpretation. Scope identity and allocation IDs remain unchanged.

## Backend implementation and review

Task 1: dedicated `SpotLedgerStore` tables use Decimal TEXT, private precision 80 and native `BEGIN IMMEDIATE` transactions in a validated account file. Exact seed/order/fill/terminal events, base/quote fees, reservations, cost basis/PnL, unknown outcomes and audit rollback are implemented. No order-manager/Repository ownership hook was added.

- RED: missing module, 3 failed / 45 errors; `/tmp/okx-phase2b-ledger-red.log`.
- GREEN: 59 ledger tests; affected storage/allocation/paper/repository suite 352 passed.
- Root review found exact reserve retries after terminal were incorrectly rejected. A regression failed before correction; exact retries now return the current terminal snapshot without changing balances, reservations or audit. Changed economic intent still conflicts. Malformed unhashable sides also fail with a domain error rather than `TypeError`.
- Independent review by the readiness author: PASS, no open findings; current ledger tests independently 59 passed. `/tmp/okx-phase2b-review-ledger.md`.

Task 2: one active-draft snapshot, fixed readiness checks and a scope/revision/symbol-guarded GET expose diagnostic facts. Native quote observations and provider timestamps are explicitly indicative; unknown completeness and closed activation gates remain visible. The GET creates no financial rows and makes no provider call.

- RED: unit 55 failed, API 27 failed / 2 boundary passes before the feature/dependency existed.
- GREEN: 143 focused readiness/allocation/context tests, including the real prepared ledger store.
- Independent review by the ledger author: PASS, no open findings; readiness unit/API independently 84 passed. `/tmp/okx-phase2b-review-readiness-backend.md`.

UI integration exposed that the old draft envelope did not contain an execution account ID. Added `execution_context` to the envelope, derived from the same single captured settings object as its unchanged allocation scope. The client can match the authoritative account context directly without duplicating the server hash algorithm or requiring browser cryptography. Two configured/unconfigured regression cases first failed for the absent field, then passed; independent scoped review passed. Existing plus new allocation API tests: 55 passed.

Backend source stable after this reviewed binding change: full suite **2247 passed**, one existing dependency deprecation warning, 25.03s. Full Ruff check passed. Logs: `/tmp/okx-phase2b-final-backend.log`, `/tmp/okx-phase2b-final-ruff.log`.

## Frontend implementation and review

The preview renders saved budget, same-quote proposals, indicative availability, prepared/uninitialized ledger, balances/reservations/cost/PnL/fees and translated checks. All preview monetary values remain strings. Evaluation time and manual refresh make the observation boundary visible. No activation, funding or strategy-start action was added.

The parent validates the draft envelope against the authoritative account-context store. Parent and preview clear synchronously on account invalidation, saved strategy-symbol/lifecycle changes and request identity changes. Sequence plus epoch guards cover success, failure, finally and unmount; saved revision/scope/symbol fence the response. Unsaved edits do not change the saved preview. No-credentials display and legacy numeric draft-save behavior are preserved.

Ownership split: the execution author implemented the UI/locales while root completed the fixed type/service contract. The ledger author independently reviewed all frontend changes, including the service; no author reviewed their own code as the final reviewer. Initial service tests were prepared by the original frontend worker. This replaces the original Task 3 review pairing, while retaining independent review of each surface.

- RED: missing component; wrong account envelope; parent epoch callbacks; evaluation-time label. Service RED: absent export, 1 failed / 3 passed; then 4 passed.
- Independent UI review found an Important saved-symbol invalidation gap: changing the configured symbol without changing strategy name/status left old results visible. Parent/view and child regressions failed before correction. The saved config symbol now flows into the panel, and the preview watches direct request identity. Focused UI/view tests: 62 passed; service: 4 passed.
- Independent final UI review: PASS, no open findings. `/tmp/okx-phase2b-review-frontend.md`. Root-owned same-settings envelope binding was also independently reviewed and verified.

## Final verification and delivery boundary

- Backend: **2247 passed**, 25.03s; one existing dependency deprecation warning.
- Frontend: **557 passed** across 59 files, 3.80s.
- `npm run build`: type check and production build passed, 14.07s; existing large-bundle warning remains.
- Full Ruff and final diff checks passed.
- Logs: `/tmp/okx-phase2b-final-backend.log`, `/tmp/okx-phase2b-final-frontend.log`, `/tmp/okx-phase2b-final-build.log`, `/tmp/okx-phase2b-final-ruff.log`.

Delivery policy: separate reviewed local commit on `codex/spot-allocation-ledger`; no remote PR/merge, deployment, real exchange calls, strategy starts or actual fund operations in this slice. Temporary frontend dependency symlink is removed after verification; the original dependency directory is preserved.

Delivered: internal exact prepared ledger and saved-draft diagnostic readiness. Still pending: trustworthy exact provider ingestion/sync receipts, verified account identity/mode/instrument metadata, durable complete synchronization and reconciliation, account-wide funded allocation, activation and order-manager integration. Phases 2 and 3 remain incomplete; `can_activate` and `execution_ready` stay false.
