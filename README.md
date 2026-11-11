# OKX Bot

OKX Bot is a Python and Vue quantitative trading bot workspace. It includes a FastAPI backend for strategy/runtime APIs, SQLModel persistence, OKX exchange adapters, a real backtest engine, and a Vue 3 dashboard.

## Tech stack

- Backend: Python 3.12, FastAPI, SQLModel, ccxt, pytest, ruff, uv
- Frontend: Vue 3, TypeScript, Vite, Axios, Element Plus, Vitest
- Storage: SQLite-backed SQLModel repository

## Repository layout

- `src/backtest/` — backtest engine, order matcher, historical data cache/fetch orchestration
- `src/exchange/` — OKX ccxt adapters
- `src/data/` — SQLModel models and repository methods
- `src/strategy/` — strategy base classes, registry, built-in strategies, YAML strategies
- `src/web/` — FastAPI app, REST APIs, WebSocket runtime snapshot
- `frontend/` — Vue dashboard
- `tests/` — backend unit and integration tests
- `config/settings.yaml` — local runtime and backtest defaults

## Setup

Install backend dependencies:

```bash
uv sync
```

Install frontend dependencies:

```bash
npm --prefix frontend install
```

Optional exchange and notification credentials are read from environment variables referenced by `config/settings.yaml`:

```bash
OKX_API_KEY=
OKX_SECRET=
OKX_PASSPHRASE=
TG_BOT_TOKEN=
TG_CHAT_ID=
```

Saving settings keeps environment-provided credentials as `${VARIABLE}` references,
so their values are not copied into the local settings file. Empty credential fields
in older local settings files also fall back to the corresponding environment variables.

Backtests can run with public OKX historical market data; private trading/account features require valid OKX credentials.

Set `OKX_BOT_DB_PATH` to override the default SQLite path (`data/bot.db`); the test suite uses this to keep tests off the real database.

Current private spot facts are stored separately for each API-key fingerprint and
OKX environment under `account-facts/<master-database-filename>/` next to the master
database. With the default path this is `data/account-facts/bot.db/`. The master
database retains strategy configuration, allocation drafts, paper/backtest data and
legacy live audit records. Legacy live records are not imported into the current
account view. Changing the API key starts a separate history, even when both keys
belong to the same exchange account; the key binding is not verified account identity.

Back up the master database and the entire matching facts directory together.
Stop the backend before copying SQLite files so pending WAL writes are included.
A facts file with missing or mismatching identity metadata is rejected; it is never
silently reused as another account's file.

The current-account client first reads `GET /api/trading/context`, then supplies
its `account_scope_id` as `expected_account_scope_id` for snapshot reads, exchange
views, private refresh/sync and the exchange WebSocket. Context and cached snapshot
reads do not contact OKX. Missing snapshots remain unavailable until an explicit
private refresh/sync succeeds. Local `paper`/`demo` execution is distinct from the
OKX sandbox environment selected by `exchange.demo`.

Allocation drafts do not activate strategy funds or grant live trading permission.
Live strategy performance/aggregate views remain empty until strategy ownership,
accounting and reconciliation are implemented.

Saved allocation drafts have a read-only readiness preview in the strategy page.
It compares proposals in their native quote currency and shows missing or blocking
evidence. Cached asset availability is indicative: a recent provider timestamp or
an empty stored order list does not prove a complete current exchange snapshot.
The preview never allocates funds, initializes a ledger, or enables activation.

The internal spot accounting kernel stores decimal strings in account-scoped
tables. It supports cash-only prepared balances, buy/sell reservations, idempotent
incremental fills with base/quote fees, cost basis and reconciled terminal release.
Prepared balances model local accounting; they do not prove exchange funding or
adopt existing holdings. This kernel is not connected to live order submission.
Exact provider quote costs, other fee currencies/rebates, trusted synchronization
receipts, account-wide funded allocation and reconciliation remain prerequisites
for that integration. Unknown order outcomes retain reservations.

## Run locally

Start the backend API on port 8080:

```bash
uv run uvicorn src.web.app:app --host 127.0.0.1 --port 8080 --reload
```

Start the frontend dashboard on port 3000:

```bash
npm --prefix frontend run dev
```

The Vite dev server proxies `/api` and `/ws` to the backend at `127.0.0.1:8080`.

Set `OKX_BOT_API_TOKEN` before starting the backend to require an `Authorization: Bearer <token>` (or `X-API-Token`) header on `/api` and a `token` query parameter on `/ws`. Without it, the API relies on the localhost bind and the local-origin CORS/WebSocket checks; do not expose the port beyond the local machine.

## Verification

Run the backend test suite:

```bash
uv run pytest
```

Run backend linting:

```bash
uv run ruff check .
```

Run frontend tests:

```bash
npm --prefix frontend exec -- vitest run
```

Run the frontend type check and production build:

```bash
npm --prefix frontend run build
```

## Backtest API

### Run a backtest

`POST /api/backtest/run`

Request body:

```json
{
  "strategy": "ma_cross",
  "symbol": "BTC-USDT",
  "timeframe": "1h",
  "start_time": 1700002800000,
  "end_time": 1700089200000,
  "initial_capital": 100000
}
```

Successful responses keep the frontend-compatible flat metrics contract:

```json
{
  "total_return": 0.0123,
  "sharpe_ratio": 1.4,
  "max_drawdown": 0.03,
  "win_rate": 0.55,
  "total_trades": 12
}
```

Each successful run is persisted as a backtest summary record.

### List persisted results

`GET /api/backtest/results`

Returns recent persisted backtest summaries with request fields, flat metrics, `id`, and `created_at`.

## Historical data cache behavior

Backtests use `ensure_historical_bars()` to build a complete candle series for the requested `symbol`, `timeframe`, `start_time`, and `end_time`.

Supported timeframes:

- `1m`
- `5m`
- `15m`
- `1h`
- `4h`
- `1d`

For cache misses, the backend automatically fetches missing OKX OHLCV candles through the OKX spot adapter. Missing timestamps are grouped into contiguous ranges and fetched with paginated `since` and `limit` inputs. The first expected candle is the first timeframe-aligned timestamp at or after `start_time`, and coverage continues through `end_time` inclusively.

Fetched candles are saved to the local cache only after the merged cached-and-fetched series covers every expected timestamp. Partial provider responses fail the request without persisting partial klines or a backtest result.

Relevant error responses:

- `422` — `unsupported timeframe for historical backtest data`
- `422` — `insufficient historical data for requested backtest range`
- `502` — `failed to fetch historical market data`

The frontend displays FastAPI `detail` messages from backtest failures when available, and falls back to the generic localized run error otherwise.
