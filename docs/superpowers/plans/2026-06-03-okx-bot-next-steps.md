# OKX Bot Next Steps

## Scope

Stabilize the current web dashboard/settings/market/i18n changes, prepare them for commit, then continue with the remaining UI gaps.

## Locked decisions

- Keep the current work in this isolated worktree until the user asks to commit or merge.
- Do not commit automatically.
- Preserve secret masking behavior for OKX credentials and Telegram bot token.
- Keep machine-readable identifiers untranslated: API field names, strategy IDs, trading symbols, YAML/config keys, and persisted settings keys.

## Step 1: Overall regression verification

Goal: prove the current backend and frontend changes still work together.

Checks:

1. Run backend tests: `uv run pytest`
2. Run Python lint/format checks: `uv run ruff check .` and `uv run ruff format --check .`
3. Run frontend tests: `npm --prefix frontend exec vitest -- --run`
4. Run frontend build: `npm --prefix frontend run build`
5. If the dev servers are running, manually smoke test the browser UI after any fix:
   - Dashboard loads and language switching still works.
   - Strategies page starts/stops a strategy.
   - Market page loads tickers/klines.
   - Settings page loads and preserves masked secrets.

Exit criteria:

- All automated checks pass, or failures are documented with root cause and next action.
- No browser console errors during manual smoke checks.

## Step 2: Working tree cleanup and commit preparation

Goal: make the current diff understandable and safe to commit when requested.

Checks:

1. Inspect `git status --short`.
2. Inspect unstaged diff with `git diff`.
3. Confirm no temporary screenshots, generated build artifacts, credentials, or accidental local files are included.
4. Confirm dependency changes are limited to `vue-i18n` and lockfile updates.
5. Run an independent runtime-correctness review of the current diff.
6. Prepare a concise commit message, but do not commit unless the user explicitly asks.

Exit criteria:

- Working tree contains only intended source/test/lockfile/plan changes.
- Review has no open critical/runtime-correctness findings.
- Commit summary is ready for user approval.

## Step 3: Backtest page implementation

Goal: replace the Backtest placeholder with a usable strategy validation page.

Planned behavior:

- Form fields: strategy, symbol, timeframe, start/end time, initial capital.
- Submit to `/api/backtest/run`.
- Show metrics: total return, Sharpe ratio, max drawdown, win rate, total trades.
- Show recent result history from `/api/backtest/results` if available.
- Keep UI text covered by i18n.

Verification:

- Add tests for service/form behavior where practical.
- Run frontend tests and build.
- Manually run a backtest in the browser.

## Step 4: Trades page implementation

Goal: replace the Trades placeholder with trading state visibility.

Planned behavior:

- Display current positions from `/api/trading/positions`.
- Display current/open orders from `/api/trading/orders`.
- Display account summary from `/api/trading/account` or link to Dashboard state.
- Keep UI text covered by i18n.

Verification:

- Add tests for data loading where practical.
- Run frontend tests and build.
- Manually verify the page in the browser.

## Step 5: Trading loop UI polish

Goal: make Settings, Strategies, Dashboard, Backtest, and Trades feel like one coherent control loop.

Potential work:

- Surface current mode from Settings in Dashboard.
- Show strategy runtime state and recent actions more clearly.
- Add risk/account status summary.
- Add empty/error states consistently across pages.

Verification:

- Full frontend test/build.
- Browser smoke test across all navigation pages.
