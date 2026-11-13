# SDD ledger — plan: docs/superpowers/plans/2026-11-11-account-isolation-plan.md

Base: merged `origin/main` = `268280c6d35d1e2de0be9fe80677bf8d3c7d3e2b`; branch `codex/account-isolation`.

Delivery: Phase 1 PR https://github.com/comeran/okx-bot/pull/18 merged; merge tree equals tested `aae8e1d`. Original main fast-forwarded, unrelated `.workbuddy/` preserved.

Pre-flight: Task 1 scope/header → Task 2 REST/sync binding → Task 3 context envelope and account epoch. Interfaces agreed in design/plan; client never opens files or supplies authoritative identity. Task 2 owns backend integration files; Task 3 owns frontend; Task 1 owns scope/storage.

Ruling: use per-scope SQLite facts files for personal single-node deployment instead of changing all legacy financial uniqueness/migration paths — keeps existing matching transactions account-local — cost: credential rotation has separate history, backups include facts directory, execution must later combine master and account kill checks. No cross-file accounting mutation or execution activation in this slice.

Execution: reuse three already-working agents; they cross-review others' code. Earlier fresh reviewer dispatches failed/stalled; recovery is recorded in ERR-20261111-001. No redundant design/permission checkpoint: user explicitly authorized continuing the roadmap.

Task 2 compatibility evidence: old API suite initially reported 48 failed / 217 passed after closing unbound live reads. Root updated 11 generic serialization/filter/order tests to paper provenance, retained mixed paper/live input and added explicit live-empty assertions. Focused verification: 11 passed / 254 deselected in 1.50s; `/tmp/okx-phase2-generic-tests.log`. Provider, exchange and WS contracts remain assigned to the backend implementer.

Storage implementation refinement: tests exposed a macOS conflict between locking the database file and SQLite's own locks. Bootstrap now uses a sidecar lock. Immutable SQLite reads also ignore WAL/checkpoint locking, so header checks use standard read-only transactions. SQLite-managed sidecars are permitted; invalid financial files are never migrated, imported, or overwritten. Relevant failure/recovery logged in ERR-20261111-002.

Task 1: implemented. RED: 36 missing-feature failures; concurrent initialization/checkpoint regression also reproduced before the read-lock correction. Affected scope/storage/repository/allocation/paper suites: 316 passed. Independent backend-author review: `/tmp/okx-phase2-review-storage.md`, PASS, no remaining Critical/Important findings.

Task 2: implemented. Focused REST/WS plus existing API suite: 294 passed. Cross-review found queued websocket sends surviving context retirement. Both halves were fixed: retire all old exchange bindings before any await; check binding object identity before every queued send and failure cleanup. Tests `test_context_change_retires_all_bindings_before_slow_control_send` and `test_inflight_financial_broadcast_skips_retired_connections` verified RED→GREEN and independently rerun 2/2. `/tmp/okx-phase2-review-backend.md`: PASS, no remaining Critical/Important findings.

Integration corrections before final verification: paper financial broadcasts carry explicit source/runtime provenance and still refresh local strategy analytics; unbound financial history remains rejected. Socket closure invalidates the account immediately and delays reconnect instead of spinning on persistent handshake rejection.

First full backend verification before the final handshake fix: `UV_PROJECT_ENVIRONMENT=/Users/zane/Documents/Self/Project/Self/okx-bot/.venv uv run --no-sync pytest -q` → **2099 passed**, 1 existing aiohttp/Python deprecation warning, 20.22s. Final-source verification supersedes this count below.

Final review correction: a config change while `ws.accept()` waited could miss the unregistered connection, which would then register and send an obsolete snapshot. A manager generation now invalidates pending exchange AND strategy handshakes; a rejected handshake notifies reload/closes/returns false, and the app exits before reading/sending a snapshot. Independent pre-fix reproduction: 2 failed; post-fix scoped checks: 32 passed, including an endpoint guard. `/tmp/okx-phase2-review-backend.md` updated to PASS.

Task 3: implemented and independently reviewed. Root early feedback restored provenanced paper analytics refresh and replaced immediate reconnect loops with delayed, cancelled-on-epoch retries. Final review also covered trade/trades event/snapshot classification before history insertion and suppression of missing-snapshot notices during errors/loading. `/tmp/okx-phase2-review-frontend.md`: PASS, no open Critical/Important/Minor findings.

Final frontend verification on the final source: `npm test -- --run` → **528 passed / 58 files**, 4.72s; `npm run build` (vue-tsc + Vite) → exit 0, 15.49s. Existing Node localstorage, VueUse annotation and bundle-size warnings remain. Logs `/tmp/okx-phase2-final-frontend.log` and `/tmp/okx-phase2-final-build.log`.

Final backend verification on the final source, including handshake generation guards: `UV_PROJECT_ENVIRONMENT=/Users/zane/Documents/Self/Project/Self/okx-bot/.venv uv run --no-sync pytest -q` → **2102 passed**, 1 existing aiohttp/Python deprecation warning, 20.27s. Full Ruff and `git diff --check` passed. Log `/tmp/okx-phase2-final-backend.log`. The second full run was required by the newly reproduced handshake defect; unchanged checks were otherwise not repeated.

No outstanding reviewed correctness findings. No real exchange calls, private-account acceptance, strategy start, deployment, or fund operations were performed. Tests isolate database/settings paths before imports and deny network sockets.

Whole-slice integration review: `/tmp/okx-phase2-review-integration.md` PASS. Reviewed capture→facts store→REST/private sync→WS contracts, the final admission guard, absence of legacy live callers, and the still-closed ownership gate. All tasks complete for Phase 2a; source preserved as a separate local commit on `codex/account-isolation` rather than folded into the already-merged Phase 1 PR.

Next: strategy-local spot cash/holdings and reservations, explicit activation preflight, then reconciliation/recovery prerequisites. This slice deliberately leaves all allocation/ownership activation gates closed. New scoped Repository instances do not expose ownership-authorization hooks. Preserve key-rotation history separation and compose global OR account-local kill state before any future live manager integration.
