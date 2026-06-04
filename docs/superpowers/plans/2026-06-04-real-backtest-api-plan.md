# Real Backtest API Implementation Plan

## Context

The Backtest page is usable, and the backend already contains a `BacktestEngine`, `OrderMatcher`, report generation, cached kline storage, and a `BacktestDataSource`. The missing piece is that `/api/backtest/run` still returns deterministic synthetic results and stores run history in process memory.

This milestone should make the Backtest page truthful: a run should use cached historical candles, execute the selected strategy through the real backtest engine, return metrics derived from the engine report, and persist result history for later display.

## Current State

Backend:

- `src/web/api/backtest.py` exposes `POST /api/backtest/run` and `GET /api/backtest/results`.
- `POST /api/backtest/run` validates strategy existence but fabricates synthetic trades/metrics from request metadata.
- Result history lives in an in-memory `_results` list.
- `src/backtest/engine.py` can run a strategy over `Bar` data and produce a `BacktestReport`.
- `src/backtest/datasource.py` can read/write cached candles through `Repository`.
- `src/data/repository.py` already supports `save_kline()` and `get_klines()`.

Frontend:

- `frontend/src/views/Backtest.vue` already sends strategy, symbol, timeframe, start/end timestamps, and initial capital.
- `frontend/src/services/backtest.ts` already calls `/api/backtest/run` and `/api/backtest/results`.
- Current UI only needs summary metrics/history for the first real-engine milestone.

## Goals

1. Replace synthetic `/api/backtest/run` behavior with real `BacktestEngine` execution.
2. Load backtest bars from the local kline cache for the requested symbol/timeframe/date range.
3. Return clear errors when the requested historical data is unavailable or insufficient.
4. Persist backtest result summaries instead of keeping only process-memory history.
5. Keep the frontend response contract small and compatible with the current Backtest page.

## Non-Goals

- Do not add equity/drawdown charts in this milestone.
- Do not add per-trade detail UI yet.
- Do not implement OKX historical fetch-on-cache-miss in the same slice unless the cache-only milestone is already passing.
- Do not redesign the current backtest engine accounting model here.
- Do not add parameter comparison, optimization, exports, or result detail pages yet.

## Contract

### Request

Keep the existing `POST /api/backtest/run` request shape:

```json
{
  "strategy": "ma_cross",
  "symbol": "BTC-USDT",
  "timeframe": "1m",
  "start_time": 1717200000000,
  "end_time": 1717286400000,
  "initial_capital": 100000
}
```

Validation rules:

- `strategy` must resolve to a known strategy.
- `start_time` and `end_time` must be millisecond timestamps.
- `end_time` must be greater than `start_time`.
- `initial_capital` must be greater than `0`.
- At least two bars must exist for the requested symbol/timeframe/range.

### Response

Keep the current frontend-compatible response shape for this milestone:

```json
{
  "id": "bt_...",
  "strategy": "ma_cross",
  "symbol": "BTC-USDT",
  "timeframe": "1m",
  "start_time": 1717200000000,
  "end_time": 1717286400000,
  "initial_capital": 100000,
  "metrics": {
    "total_return": 0.0123,
    "sharpe_ratio": 1.2,
    "max_drawdown": 0.05,
    "win_rate": 0.54,
    "total_trades": 12
  },
  "created_at": 1717286400000
}
```

For missing or insufficient data, return a 4xx error with a localized-agnostic machine-readable message. The frontend can continue showing `trades.runError` for now.

Recommended first error payload:

```json
{
  "detail": "insufficient historical data for requested backtest range"
}
```

## Persistence Model

Add a persisted summary table for backtest results. Keep detailed trades/equity curves out of scope for this slice.

Suggested model:

```python
class BacktestResultRecord(SQLModel, table=True):
    id: str = Field(primary_key=True)
    strategy: str = Field(index=True)
    symbol: str = Field(index=True)
    timeframe: str = Field(index=True)
    start_time: int = Field(index=True)
    end_time: int = Field(index=True)
    initial_capital: float
    total_return: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    total_trades: int
    created_at: int = Field(index=True)
```

Repository methods:

```python
def save_backtest_result(self, result: BacktestResultRecord) -> BacktestResultRecord: ...
def get_backtest_results(self, limit: int = 50) -> list[BacktestResultRecord]: ...
```

Ordering:

- `get_backtest_results()` should return newest first.
- Keep a default limit to avoid unbounded history responses.

## Implementation Steps

### 1. Add repository persistence with TDD

Modify:

- `src/data/models.py`
- `src/data/repository.py`
- `tests/unit/test_repository.py`

Add tests first:

- Saving a `BacktestResultRecord` persists all summary fields.
- Fetching results returns newest first.
- Fetching results honors the default or explicit limit.

Verification:

```bash
uv run pytest tests/unit/test_repository.py -v
```

### 2. Add a small backtest API service seam

Create or extend a backend service so `src/web/api/backtest.py` does not directly contain all orchestration logic.

Suggested file:

- `src/backtest/service.py`

Responsibilities:

- Resolve strategy name to strategy instance/config.
- Load cached bars through `BacktestDataSource`.
- Reject insufficient bars.
- Run `BacktestEngine` with request fee/slippage/default config values.
- Convert `BacktestReport` into `BacktestResultRecord` and API response shape.

Keep strategy resolution narrow for the first slice: support existing built-in strategies only.

### 3. Replace synthetic API behavior with real engine execution

Modify:

- `src/web/api/backtest.py`
- `tests/integration/test_web_api.py`

Add or update tests first:

- `POST /api/backtest/run` uses cached bars and returns metrics from a real engine run.
- Unknown strategy still returns an error.
- Missing/insufficient cached bars returns a 4xx error and does not persist a result.
- `GET /api/backtest/results` returns persisted result summaries, newest first.

Avoid assertions that depend on fragile exact Sharpe values. Prefer stable checks:

- `total_trades` equals expected count for a deterministic test strategy and bars.
- `created_at` exists.
- response echoes request fields.
- repository contains one persisted result after a successful run.

Verification:

```bash
uv run pytest \
  tests/unit/test_repository.py \
  tests/unit/test_backtest_engine.py \
  tests/unit/test_datasource.py \
  tests/integration/test_web_api.py \
  -v
```

### 4. Keep frontend contract compatible

Modify frontend only if backend response field names change. Prefer not changing the UI in this slice.

If service tests need updates:

- `frontend/src/types/backtest.ts`
- `frontend/src/services/backtest.ts`
- `frontend/src/services/backtest.test.ts`

The Backtest page should continue to show:

- latest metrics
- persisted history rows
- existing validation errors
- generic run failure message for backend 4xx/5xx

Verification:

```bash
npm --prefix frontend exec vitest run src/services/backtest.test.ts src/utils/backtest.test.ts
npm --prefix frontend run build
```

### 5. Browser smoke

Run backend and frontend, then manually/browser-smoke `/backtest`:

- With cached bars present, run a backtest and confirm metrics/history update.
- With missing bars, confirm the UI shows the existing run error rather than synthetic metrics.
- Confirm `/api/backtest/results` is served from persisted result records after refresh.
- Confirm console has no new errors.

## Historical Data Cache-Miss Contract

After the cache-only real-engine slice works, `/api/backtest/run` should try to fill missing local candle data from OKX before returning an insufficient-data error.

### Fetch interface

Extend the public OHLCV adapter contract to support a CCXT-compatible `since` value:

```python
async def fetch_ohlcv(
    self,
    symbol: str,
    timeframe: str,
    limit: int = 100,
    since: int | None = None,
) -> list[Bar]: ...
```

Rules:

- `symbol` and `timeframe` remain machine-readable and are passed through unchanged.
- `since` is a millisecond timestamp and maps directly to CCXT `fetch_ohlcv(..., since=since, limit=limit)`.
- The first implementation uses `OKXSpotAdapter` with blank credentials, matching the existing public market API.
- Default page size is `100`; maximum page size is `300` for backtest cache fills.

### Gap detection

For a request range `[start_time, end_time]`:

1. Read cached bars for exact `symbol` and `timeframe`.
2. Convert `timeframe` to milliseconds for supported values: `1m`, `5m`, `15m`, `1h`, `4h`, `1d`.
3. Build expected timestamps from the first aligned timestamp at or after `start_time` through `end_time`, inclusive.
4. Treat any expected timestamp absent from cache as missing.
5. Fetch missing contiguous ranges only; do not re-fetch fully cached ranges.

If the timeframe is unsupported, return `422` with:

```json
{
  "detail": "unsupported timeframe for historical backtest data"
}
```

### Pagination

For each missing contiguous range:

1. Start with `since = missing_range_start`.
2. Request up to `min(300, expected_missing_count)` bars.
3. Keep only returned bars whose timestamps are within `[missing_range_start, missing_range_end]`.
4. Advance `since` to `last_returned_timestamp + timeframe_ms`.
5. Stop when `since > missing_range_end`, the adapter returns no rows, or the returned timestamp stops advancing.

This avoids infinite loops and avoids repeatedly fetching the entire requested range.

### Deduplication and persistence

- Deduplicate by `timestamp` after combining cached and fetched bars.
- Prefer fetched bars when a fetched timestamp matches a cached timestamp.
- Persist fetched bars through `BacktestDataSource.save_bars_to_cache()`.
- Repository-level duplicate prevention is out of scope for this slice; service-level dedupe must prevent duplicate inserts during one request.
- Return sorted bars by timestamp to the backtest engine.

### Error semantics

- If OKX fetching raises, return `502` with:

```json
{
  "detail": "failed to fetch historical market data"
}
```

- If fetching succeeds but fewer than two usable bars remain, return `422` with the existing detail:

```json
{
  "detail": "insufficient historical data for requested backtest range"
}
```

- If fetching returns partial data but at least two bars exist, run the backtest with available sorted bars. Exact full-range coverage is not required until the engine needs strict candle continuity.

### API behavior

`POST /api/backtest/run` should behave as follows:

1. Validate request fields and strategy.
2. Load cached bars.
3. If fewer than two bars exist or there are missing expected timestamps, try OKX cache-miss fetching.
4. Persist fetched bars.
5. Reload/merge bars and run the real backtest engine.
6. Persist summary results as before.

The frontend can continue using the existing generic success/error messages for this slice.

## Risks and Tradeoffs

- Existing `BacktestEngine` accounting is still simplified. This milestone makes the API truthful to the engine, not fully exchange-realistic.
- Strategy instantiation may need a narrow built-in strategy resolver before general persisted strategy configs exist.
- Historical data availability is the main UX risk. Cache-only behavior is honest but may produce more errors until cache-miss fetching is implemented.
- Persisting only summary fields keeps scope small but delays equity curve and per-trade UI.

## Verification Checklist

Automated:

```bash
uv run pytest tests/unit/test_repository.py tests/unit/test_backtest_engine.py tests/unit/test_datasource.py tests/integration/test_web_api.py -v
uv run ruff check .
npm --prefix frontend exec vitest run
npm --prefix frontend run build
```

Manual:

- `/backtest` can run with known cached bars.
- `/backtest` shows an error for missing bars and does not invent results.
- Result history survives backend process restart.
- Dashboard, Strategies, Market, Trades, and Settings still load after the backend change.

## Suggested Subagent Split

If using parallel subagents:

1. **Repository/persistence subagent**
   - Add `BacktestResultRecord`, repository methods, repository tests.
2. **Backend API/service subagent**
   - Add service orchestration and replace synthetic API behavior with real engine execution.
3. **Frontend verification subagent**
   - Confirm current frontend contract still works, update service/types only if needed, and add browser smoke notes.

Do not let multiple subagents edit the same file concurrently unless their exact ownership is pre-split.
