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

Backtests can run with public OKX historical market data; private trading/account features require valid OKX credentials.

## Run locally

Start the backend API on port 8080:

```bash
uv run uvicorn src.web.app:app --host 0.0.0.0 --port 8080 --reload
```

Start the frontend dashboard on port 3000:

```bash
npm --prefix frontend run dev
```

The Vite dev server proxies `/api` and `/ws` to the backend at `127.0.0.1:8080`.

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
