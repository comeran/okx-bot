# Progress — plan: docs/superpowers/plans/2026-11-11-quant-foundation-plan.md

Baseline: `a41ff98`; branch `codex/quant-system-foundation` in managed worktree.

User intent: plan and incrementally implement the audit roadmap. Current slice is configuration drafts and feasible spot research. Exchange operations remain outside this code-change task.

User scope reply: spot first (2026-11-11); recorded in the spec and roadmap.

## Preflight

| Tasks / interface | Check | Result |
| --- | --- | --- |
| 1 → 2 allocation API | Context, nullable draft, revisions and errors are shared contract | Backend publishes exact envelopes before panel implementation |
| 1 / 3 | New allocation modules and existing backtest engine do not share implementation files | Independent |
| 1 / live runtime | Optional ownership hooks would accidentally open startup before correct accounting | Draft store intentionally does not implement live authorization hooks |
| All / data safety | Test collection isolation exists before application import | Full baseline: 1945 passed, one existing aiohttp deprecation warning, 17.72s |

## Execution

- Plan/spec written; no product changes at this entry.
- Task 1: implementation complete; focused 44 passed and related regressions 932 passed. Report `/tmp/okx-foundation-allocation-report.md`.
- Task 2: implementation complete; focused 40 passed, full frontend 502 passed in 56 files, production build/type check passed. Report `/tmp/okx-foundation-frontend-report.md`.
- Task 3: implementation complete; related engine/strategy/API regressions 425 passed. Preserves research request overrides and nameless/symbolless custom strategies. Report `/tmp/okx-foundation-backtest-report.md`.
- Task 4: complete. After the review fix, final integrated backend full suite 2040 passed (20.95s), full-project Ruff passed and diff whitespace passed. Frontend unchanged since its final 502-test/build/type-check pass. Independent cross-review covered every production change outside its author's own implementation.

Verification warnings: existing aiohttp Python 3.12.9 cleanup deprecation; existing Node localstorage warning; Vite reports VueUse PURE annotation handling and a large output chunk. No new test/build failures.

Review package: `/tmp/okx-foundation-review.diff`. First independent reviewer could not start because its provider returned model-disabled 403; two fresh reviewer attempts then produced no progress for an extended period and were interrupted. These are tooling failures, not code verdicts. Recovered with cross-review by the existing working agents: the backtest author reviews allocation/backend/UI changes authored by others, and the allocation author reviews the backtest/UI changes authored by others. The root also reviewed integration boundaries and ran the complete backend checks. No author self-review is being counted as independent review of their own code.

Cross-review finding: Important — a mutable pending Order could change its symbol after enqueue validation and still fill against the original symbol's bars, producing a trade/position mismatch. `/tmp/okx-foundation-cross-review-backtest.md` records a pure-memory reproduction. Fixed by validating the pending symbol immediately before matching. ETH/SWAP mutation tests failed before the fix and passed afterward; same-symbol alias compatibility is covered. Related regressions: 428 passed. Scoped independent re-review: ADDRESSED/PASS, no new Important/Critical finding. Final integrated suite: 2040 passed.

Independent allocation/UI review: `/tmp/okx-foundation-cross-review-allocation-ui.md`, spec/quality pass with no reproducible Critical/Important finding. No deferred minor findings were reported. This is implementation review and isolated local testing, not exchange or long-running acceptance.

Delivery state: all changes remain uncommitted on the attached branch. Temporary frontend dependency symlink removed after checks; original checkout and its unrelated `.workbuddy/` preserved. The next capability is roadmap Phase 2, account isolation and active allocation, followed by Phase 3 reconciliation/recovery. No live authorization or exchange operation was performed.

Delivery authorization (2026-11-11): the user requested committing, opening and merging the PR, then continuing the roadmap. Revalidate the current tree before delivery; do not treat this as permission to start trading or operate exchange accounts.

## Remaining scope

Account-scoped exchange facts, active ownership/budget accounting, recovery/reconciliation and later roadmap phases are not complete merely because a draft exists.

## Integration decisions

- Ruling: require `expected_scope_id` on every draft mutation in addition to revision. A settings/account switch otherwise lets an old first-create form save into the new scope. Cost: stale pages must reload before saving; no automatic retry across accounts. Added to spec, API/UI contract and regression coverage before implementation completes.
- Ruling: also bind mutations to `expected_symbol`, and soft-release all scopes' drafts inside Repository's existing strategy-delete transaction. Otherwise a changed strategy symbol could receive an unintended budget, or a deleted strategy's orphaned draft could keep a symbol unavailable. Cost: stale symbol forms must reload; deleting a strategy also removes its inert draft settings while retaining their audit history. No funds or exchange positions are affected.
- Corrected the runtime-data ignore pattern from `data/` to `/data/`: the old unanchored pattern also hid the new `src/data/allocations.py` source file. Verified root runtime data remains ignored and the source is visible to Git.
