# OKX Bot Unfinished Work

## Scope

This document tracks unfinished work across the OKX quantitative trading bot project, from broad product directions down to concrete follow-up items. It is a working backlog, not an implementation plan for a single sprint.

## Standing decisions

- Do not commit automatically; commit only when explicitly requested.
- Keep machine-readable identifiers untranslated: API field names, strategy IDs, trading symbols, YAML/config keys, side values, and persisted settings keys.
- Preserve secret masking behavior for OKX credentials and Telegram bot token.
- Treat the current web console as an incremental control surface: avoid presenting placeholder data as live trading truth.

## Current near-term focus

### 1. Trading loop UI polish

Goal: make Dashboard and Strategies clearer as runtime-control pages without expanding into backend runtime architecture.

Locked decisions:

- Keep this round frontend UI polish focused; do not implement true WebSocket runtime snapshots, OKX position/order sync, or YAML persistence in this round.
- Dashboard refresh should reload the full first-screen snapshot: account, positions, orders, strategies, and tickers.
- Split Dashboard errors into main runtime errors and `tickerError`, so ticker failures are not silently shown as empty data.
- Render Positions and Orders as structured tables using existing frontend types; show `—` for missing fields and do not fabricate data.
- Improve WebSocket message log readability with message type, received time, and short payload preview; do not add polling or fake real-time snapshots.
- Use per-strategy-row loading for start/stop actions and disable invalid actions based on current `running` / `stopped` status.
- Keep the Strategies YAML area as draft/generated YAML only; do not add save buttons or backend config reads/writes in this round.
- Verify with frontend Vitest, frontend production build, and browser checks for `/` and `/strategies`; backend tests are not required unless backend contracts change.

Detailed items:

- `frontend/src/stores/dashboard.ts`
  - Add or refine `lastUpdatedAt`.
  - Add `tickerError`.
  - Keep main API failures separate from ticker-only failures.
  - Store richer WebSocket message log entries: type, received timestamp, and payload preview.
- `frontend/src/views/Dashboard.vue`
  - Add a manual refresh button.
  - Show last update time.
  - Keep connected/disconnected status visible.
  - Show ticker failure separately from no ticker data.
  - Replace generic Positions and Orders key-value rendering with clear columns.
  - Improve WebSocket messages panel readability.
- `frontend/src/views/Strategy.vue`
  - Add row-level action loading for start/stop.
  - Disable start for running strategies and stop for stopped strategies.
  - Render strategy status with clearer tags.
  - Clarify YAML form/editor copy as draft/generated YAML.
- `frontend/src/locales/en.ts` and `frontend/src/locales/zh-CN.ts`
  - Add or update localized copy for refresh state, last update, ticker errors, table empty states, WebSocket log labels, strategy status, and YAML draft wording.

## Web console backlog

### Dashboard

Current state:

- Dashboard loads account, positions, orders, strategies, and tickers through the frontend store.
- Backend account/positions/orders/trades read local Repository-backed paper-mode state.
- Account summary omits fields that cannot be truthfully derived yet, such as available balance and unrealized PnL.
- WebSocket store handlers exist for richer messages, but backend does not emit them yet.

Unfinished work:

- Show real account state once backend account data is wired.
- Show real positions and open orders once backend trading state is wired.
- Add risk/account status summary.
- Surface current runtime mode from Settings.
- Add equity curve or account history when data exists.
- Add alert/notification area for risk events and strategy failures.
- Make empty/error/loading states consistent across all cards.

### Strategies

Current state:

- Strategy list supports basic start/stop against the API.
- YAML form/editor generates local YAML only.
- Backend exposes a minimal in-memory `ma_cross` strategy with `running` / `stopped` state.

Unfinished work:

- Persist user-created or edited strategy configs.
- Load saved strategies from configuration or database.
- Support strategy create/update/delete flows.
- Show per-strategy runtime details: last signal, last order, PnL, errors, uptime, and recent actions.
- Validate YAML against the strategy DSL before launch.
- Support richer DSL operators such as `crosses_above` and `crosses_below`.
- Connect generated indicator definitions to actual computed indicator values.
- Add strategy isolation and failure handling in the runtime engine.

### Backtest

Current state:

- The Backtest page has a usable form, metrics display, and result history table.
- Frontend service and validation tests exist.
- Backend `/api/backtest/run` currently returns synthetic results instead of running a real historical backtest.
- Backend result history is in memory.
- Detailed implementation plan: [Real Backtest API Implementation Plan](2026-06-04-real-backtest-api-plan.md).

Unfinished work:

- Wire `/api/backtest/run` to the real backtest engine and cached historical candle data.
- Persist backtest results instead of keeping only in-memory history.
- Fetch OKX historical data on cache miss and persist it after the cache-only real-engine slice works.
- Show equity curve, drawdown curve, and per-trade list.
- Add result detail pages or expandable rows.
- Support comparing multiple runs or parameter sets.
- Make fee, slippage, initial capital, and date ranges traceable in saved results.

### Market

Current state:

- Market page can load historical candles and render a basic candlestick chart.
- Ticker symbols are currently limited to a small hard-coded set.

Unfinished work:

- Replace hard-coded ticker symbols with configurable or discoverable markets.
- Add real-time price updates.
- Add order book and recent trades views.
- Add technical indicator overlays such as MA, RSI, MACD, and Bollinger Bands.
- Support smoother timeframe switching and cached reloads.
- Improve market data error states and retry behavior.

### Trades

Current state:

- Trade history page displays persisted trade records from `/api/trading/trades`.
- Optional strategy filtering exists at the API/service layer.
- Current positions, open orders, and account summary are not yet part of the Trades page.

Unfinished work:

- Add filters for strategy, symbol, side, and time range.
- Add pagination or virtualized loading for large histories.
- Add order history beyond executed trade records.
- Add current positions and open orders if the page should become a broader trading-state page.
- Add account summary or link to Dashboard account state.
- Add export/download only after the data model is stable.

### Settings

Current state:

- Settings page can load and save local config values.
- Secrets are masked and blank submissions preserve existing values.

Unfinished work:

- Apply setting changes to running services where safe.
- Clarify which settings require restart.
- Validate runtime mode transitions before applying them.
- Surface current mode in Dashboard and strategy controls.
- Add connection-test actions for OKX and Telegram credentials.
- Add safer handling for live-mode enablement.

## Backend/runtime backlog

Detailed contract for the next paper-accounting milestone: [Paper-Mode Accounting Contract](2026-06-04-paper-accounting-contract.md).

### Trading account, positions, orders, and trades

Current state:

- `/api/trading/account` returns local paper-mode cash, equity, realized PnL, unrealized PnL, daily PnL, and fees from persisted account records.
- `/api/trading/positions`, `/api/trading/orders`, and `/api/trading/trades` read persisted Repository records.
- Runtime strategy startup injects a Repository-backed `UnifiedOrderManager`, so strategy order submissions can persist orders, fills, trades, account records, cash ledger entries, and net positions.
- Paper-mode accounting supports fill rejection for market orders without a usable price, net long/short position updates, realized PnL, and separate fee tracking.

Unfinished work:

- Add true rolling daily PnL using timestamped realized-PnL ledger events.
- Add mark-to-market unrealized PnL using a shared market data price provider.
- Add explicit paper account reset behavior.
- Add reconciliation between exchange state and local repository for live/demo modes.
- Add API tests as account and position contracts become richer.

### Engine and trading loop

Current state:

- `BotEngine` has minimal lifecycle state and strategy registration.
- Strategy start/stop is coarse and in-memory.
- Order manager and risk manager are not fully wired into a continuous trading loop.

Unfinished work:

- Implement a market data driven strategy loop.
- Isolate strategies so one failure does not stop unrelated strategies.
- Wire signal generation to order routing and persistence.
- Add graceful startup/shutdown state restoration.
- Track strategy runtime errors, heartbeat, and uptime.
- Add structured runtime events for UI and logs.

### WebSocket and real-time updates

Current state:

- Backend WebSocket sends a `snapshot` message on connect with local account, positions, orders, and strategy status.
- Strategy start/stop updates broadcast `strategy_status` events through `WebSocketManager.broadcast()`.
- Snapshot strategy status shares the same in-memory runtime state as the strategy API.
- Frontend store can consume snapshot messages and per-domain update messages.

Unfinished work:

- Broadcast repository-backed `orders`, `positions`, and `account` updates after paper-mode strategy fills.
- Decide whether executed trade records should broadcast as `trades` in this slice or remain page/API-only for now.
- Broadcast risk events once risk checks are wired into order submission paths.
- Add subscription or channel semantics if needed.
- Add reconnect/resubscribe behavior for OKX market/private channels.
- Add sequence or stale-data handling where required.

### Risk management

Current state:

- Risk checks exist in code, but are not fully visible in the trading loop or UI.

Unfinished work:

- Wire risk checks into order submission paths.
- Add circuit-breaker state.
- Add pause-all and manual unlock flows.
- Monitor max daily loss, max drawdown, max total position, margin ratio, and liquidation risk.
- Emit risk alerts to UI and notifications.
- Add tests for risk blocking and state transitions.

### Exchange adapters and order routing

Current state:

- OKX spot/futures/swap/options adapters exist at a basic level.
- Derivatives-specific behavior is thin.
- Stop orders and OKX stop-loss/take-profit are not supported.

Unfinished work:

- Add derivatives-specific leverage, margin mode, and position mode handling.
- Support stop-loss and take-profit order semantics.
- Add safer live-mode order validation.
- Add retry/backoff and exchange error normalization.
- Add order lifecycle reconciliation.
- Add integration tests or simulator coverage for order routing.

### Market data service

Current state:

- Market service has basic polling/watch abstraction.
- Web API creates market adapters directly for some requests.

Unfinished work:

- Add shared market data service/cache wiring.
- Add cache-miss historical data fetches.
- Add WebSocket streaming with reconnect and resubscribe.
- Add missed-message recovery.
- Add configurable market universe and instrument type handling.

### Configuration and persistence

Current state:

- App config covers runtime, OKX, backtest, risk, notifications, and web server settings.
- Strategy config list is not yet part of `AppConfig`.
- Settings writes do not automatically reconfigure all running services.

Unfinished work:

- Add strategy configuration persistence.
- Decide which settings are hot-reloadable and which require restart.
- Persist runtime state needed for recovery.
- Add migrations or schema management if repository tables evolve.
- Keep config editing safe around secrets and live trading mode.

## Notifications and observability

Current state:

- Telegram settings exist, but notification behavior is not fully wired into runtime events.

Unfinished work:

- Send alerts for strategy failures, risk circuit breakers, order failures, and live-mode warnings.
- Add structured logs for trading loop events.
- Add UI-visible event history.
- Add health/status endpoints for runtime services.

## Deployment and operations

Unfinished work:

- Add Docker or deployment packaging when the runtime is stable enough.
- Document local development startup for backend and frontend.
- Document production/live-mode safety checklist.
- Add CI checks for backend tests, frontend tests, build, and lint/format.
- Add backup/restore guidance for local data and configs.

## Verification backlog

Automated checks to keep using:

- Backend tests: `uv run pytest`
- Python lint: `uv run ruff check .`
- Python format check: `uv run ruff format --check .`
- Frontend tests: `npm --prefix frontend exec vitest run`
- Frontend build: `npm --prefix frontend run build`

Manual browser smoke checks:

- `/` Dashboard loads, refreshes, and shows empty/error states correctly.
- `/strategies` lists strategies and handles start/stop interactions correctly.
- `/backtest` can run a basic backtest flow and display metrics/history.
- `/market` loads chart data for supported symbols/timeframes.
- `/trades` displays trade history and empty state correctly.
- `/settings` preserves masked secrets and saves non-secret settings correctly.

## Suggested implementation order

1. Wire backtest API to the real engine, cached historical data, and persistent result summaries.
2. Add historical candle cache-miss fetching from OKX once the cache-only backtest path works.
3. Extend backend runtime WebSocket events to broadcast paper order, position, and account updates after fills.
4. Add persisted strategy config management.
5. Wire risk manager and order manager into the continuous market-data-driven engine loop.
6. Expand market data streaming, indicators, and chart overlays.
7. Add notifications, observability, CI, and deployment packaging.
