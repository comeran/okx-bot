# Real Backtest API Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the existing Real Backtest API so it reliably runs the real engine on complete cached-or-fetched OKX historical bars, reports clear API/UI errors, and preserves persisted backtest summaries.

**Architecture:** This is not a from-scratch rewrite: `POST /api/backtest/run` already calls `BacktestEngine`, persists `BacktestResultRecord`, and uses `ensure_historical_bars()` for OKX cache-miss fetching. The work below keeps the current frontend-compatible API contract (`POST /run` returns flat metrics; `GET /results` returns flat persisted summaries), strengthens historical coverage guarantees, and adds focused tests at the historical-data, API, OKX adapter, and frontend error-message boundaries.

**Tech Stack:** Python 3.12, FastAPI, SQLModel, ccxt async OKX adapter, pytest/pytest-asyncio/httpx ASGITransport, Vue 3, TypeScript, Axios, Element Plus, Vitest, Vite.

---

## Current State and Scope

The older plan at `docs/superpowers/plans/2026-06-04-real-backtest-api-plan.md` is partially stale. Current code already includes:

- Real API execution in `src/web/api/backtest.py:30-110` via `BacktestEngine` and `OrderMatcher`.
- Summary persistence in `src/data.models.BacktestResultRecord` and `Repository.save_backtest_result()` / `Repository.get_backtest_results()`.
- Historical cache-miss fetching in `src/backtest/historical_data.py` using `BacktestDataSource`, `OKXSpotAdapter`, timestamp gap detection, pagination, merge, and cache persistence.
- Frontend run/history services in `frontend/src/services/backtest.ts` with a metrics-only run response and flat historical results.

This plan focuses on the remaining correctness gaps:

1. OKX historical data cache-miss auto-fill must refuse to run if expected timestamps remain missing after fetching.
2. Unsupported timeframe, incomplete data, and provider failures must map to stable API errors.
3. OKX adapter contract must be tested to ensure `since` and `limit` reach ccxt.
4. Frontend should surface clear backend error `detail` strings for backtest run failures.
5. Full backend/frontend checks and a browser smoke test must verify the golden path.

Out of scope for this slice:

- Persisting detailed trade lists or equity curves.
- Changing `POST /api/backtest/run` to return a nested `{ metrics: ... }` result object.
- Adding a database uniqueness constraint for `(symbol, timeframe, timestamp)`.
- Rewriting backtest accounting semantics beyond the current engine/report behavior.

---

## File Structure Map

### Backend historical-data lane

- Modify: `src/backtest/historical_data.py`
  - Owns supported timeframe conversion.
  - Owns cache lookup, expected timestamp generation, missing-range grouping, OKX fetch pagination, merge, cache save, and final completeness validation.
  - Add explicit exception classes:
    - `UnsupportedTimeframeError`
    - `InsufficientHistoricalDataError`
- Modify: `tests/unit/test_historical_data.py`
  - Unit tests for alignment, disjoint gaps, page-limit clamp, adapter closing, and incomplete post-fetch coverage.

### Backend API lane

- Modify: `src/web/api/backtest.py`
  - Keep endpoint response contract unchanged.
  - Map `UnsupportedTimeframeError` to HTTP 422 with `unsupported timeframe for historical backtest data`.
  - Map `InsufficientHistoricalDataError` to HTTP 422 with `insufficient historical data for requested backtest range`.
  - Keep provider or malformed adapter errors as HTTP 502 with `failed to fetch historical market data`.
- Modify: `tests/integration/test_web_api.py`
  - Add an integration test proving partially fetched historical data does not run the engine and does not persist a result.

### OKX adapter lane

- Create: `tests/unit/test_exchange_base.py`
  - Unit test for `OKXBaseAdapter.fetch_ohlcv()` passing `symbol`, `timeframe`, `since`, and `limit` to ccxt and mapping ccxt OHLCV rows into `Bar`.

### Frontend lane

- Modify: `frontend/src/utils/backtest.ts`
  - Add an Axios-aware helper that extracts FastAPI `detail` strings from API errors.
- Modify: `frontend/src/utils/backtest.test.ts`
  - Add Vitest coverage for detail extraction and fallback cases.
- Modify: `frontend/src/views/Backtest.vue`
  - Use the helper in `handleRun()` so users see clear errors from the backend when historical data is incomplete or OKX fetching fails.
- Verify: `frontend/src/services/backtest.test.ts`
  - Existing tests already assert the API contract: `runBacktest()` returns metrics and `fetchBacktestResults()` returns flat summaries.

---

## Parallel Subagent Execution Guide

Use `superpowers:subagent-driven-development` for implementation. Dispatch these lanes in parallel after opening isolated worktrees:

| Subagent | Tasks | Can start immediately | Merge order | Notes |
| --- | --- | --- | --- | --- |
| Historical-data agent | Task 1, Task 2 | Yes | 1st | Defines exception names used by API task. Use the names exactly as written in this plan. |
| API agent | Task 3 | Yes | 2nd | Can implement against the planned exception names while historical-data agent works. Rebase if imports need adjustment. |
| OKX adapter agent | Task 4 | Yes | Any time | Independent test-only lane. |
| Frontend agent | Task 5 | Yes | Any time | Independent UI error-message lane. |
| Reviewer/main agent | Task 6 | After merges | Last | Runs full validation and browser smoke. |

Subagent constraints:

- Each subagent works on only the files listed in its task.
- Each subagent writes tests first, runs the targeted failure, implements the minimal change, reruns targeted tests, then commits its own lane.
- If two subagents edit the same file, only Tasks 1 and 2 may touch `src/backtest/historical_data.py` and `tests/unit/test_historical_data.py`; keep them in the same historical-data lane.
- Do not change API response shapes unless a task explicitly says to do so. This plan intentionally preserves the current contract.

---

## Task 1: Add Historical Cache-Miss Coverage Tests

**Files:**

- Modify: `tests/unit/test_historical_data.py`

- [x] **Step 1: Add imports for the new exception class**

Change the import at the top of `tests/unit/test_historical_data.py` from:

```python
from src.backtest.historical_data import ensure_historical_bars, timeframe_to_ms
```

to:

```python
from src.backtest.historical_data import (
    InsufficientHistoricalDataError,
    ensure_historical_bars,
    timeframe_to_ms,
)
```

- [x] **Step 2: Add disjoint missing-range test**

Append this test after `test_ensure_historical_bars_fetches_missing_range_and_persists`:

```python
@pytest.mark.asyncio
async def test_ensure_historical_bars_fetches_disjoint_missing_ranges(repo: Repository):
    save_cached(repo, 0)
    save_cached(repo, 120_000)
    save_cached(repo, 240_000)
    adapter = FakeAdapter([bar(60_000), bar(180_000)])

    bars = await ensure_historical_bars(
        repo=repo,
        symbol="BTC-USDT",
        timeframe="1m",
        start=0,
        end=240_000,
        adapter_factory=lambda: adapter,
    )

    assert [item.timestamp for item in bars] == [0, 60_000, 120_000, 180_000, 240_000]
    assert adapter.calls == [
        {"symbol": "BTC-USDT", "timeframe": "1m", "limit": 1, "since": 60_000},
        {"symbol": "BTC-USDT", "timeframe": "1m", "limit": 1, "since": 180_000},
    ]
    assert [kline.timestamp for kline in repo.get_klines("BTC-USDT", "1m", 0, 240_000)] == [
        0,
        60_000,
        120_000,
        180_000,
        240_000,
    ]
```

- [x] **Step 3: Add unaligned start-time test**

Append this test after the disjoint missing-range test:

```python
@pytest.mark.asyncio
async def test_ensure_historical_bars_aligns_unaligned_start_time(repo: Repository):
    adapter = FakeAdapter([bar(60_000), bar(120_000)])

    bars = await ensure_historical_bars(
        repo=repo,
        symbol="BTC-USDT",
        timeframe="1m",
        start=30_000,
        end=150_000,
        adapter_factory=lambda: adapter,
    )

    assert [item.timestamp for item in bars] == [60_000, 120_000]
    assert adapter.calls == [
        {"symbol": "BTC-USDT", "timeframe": "1m", "limit": 2, "since": 60_000}
    ]
```

- [x] **Step 4: Add page-limit clamp test**

Append this test after `test_ensure_historical_bars_paginates_missing_ranges`:

```python
@pytest.mark.asyncio
async def test_ensure_historical_bars_clamps_page_limit_to_max(repo: Repository):
    adapter = FakeAdapter([bar(timestamp) for timestamp in range(0, 24_000_000, 60_000)])

    bars = await ensure_historical_bars(
        repo=repo,
        symbol="BTC-USDT",
        timeframe="1m",
        start=0,
        end=23_940_000,
        adapter_factory=lambda: adapter,
        page_limit=999,
    )

    assert len(bars) == 400
    assert adapter.calls[0] == {"symbol": "BTC-USDT", "timeframe": "1m", "limit": 300, "since": 0}
    assert adapter.calls[1] == {
        "symbol": "BTC-USDT",
        "timeframe": "1m",
        "limit": 100,
        "since": 18_000_000,
    }
```

- [x] **Step 5: Add adapter-close-on-error test**

Append this test after `test_ensure_historical_bars_does_not_fetch_when_cache_is_complete`:

```python
@pytest.mark.asyncio
async def test_ensure_historical_bars_closes_adapter_when_fetch_fails(repo: Repository):
    class FailingAdapter(FakeAdapter):
        async def fetch_ohlcv(self, symbol, timeframe, limit=100, since=None):
            self.calls.append(
                {"symbol": symbol, "timeframe": timeframe, "limit": limit, "since": since}
            )
            raise RuntimeError("provider unavailable")

    adapter = FailingAdapter([])

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await ensure_historical_bars(
            repo=repo,
            symbol="BTC-USDT",
            timeframe="1m",
            start=0,
            end=60_000,
            adapter_factory=lambda: adapter,
        )

    assert adapter.calls == [{"symbol": "BTC-USDT", "timeframe": "1m", "limit": 2, "since": 0}]
    assert adapter.closed is True
```

- [x] **Step 6: Add incomplete-fetch rejection test**

Append this test after the adapter-close-on-error test:

```python
@pytest.mark.asyncio
async def test_ensure_historical_bars_raises_when_missing_rows_remain_after_fetch(repo: Repository):
    adapter = FakeAdapter([bar(0), bar(120_000)])

    with pytest.raises(InsufficientHistoricalDataError, match="insufficient historical data"):
        await ensure_historical_bars(
            repo=repo,
            symbol="BTC-USDT",
            timeframe="1m",
            start=0,
            end=180_000,
            adapter_factory=lambda: adapter,
        )

    assert adapter.closed is True
    assert repo.get_klines("BTC-USDT", "1m", 0, 180_000) == []
```

- [x] **Step 7: Run the historical-data tests and confirm the intended failure**

Run:

```bash
uv run pytest tests/unit/test_historical_data.py -v
```

Expected before implementation:

- Existing tests pass.
- `test_ensure_historical_bars_raises_when_missing_rows_remain_after_fetch` fails because `InsufficientHistoricalDataError` does not exist or because incomplete data is returned instead of rejected.

- [x] **Step 8: Commit only if this is an isolated TDD checkpoint**

If the subagent is committing tests separately, run:

```bash
git add tests/unit/test_historical_data.py
git commit -m "test: cover historical backtest cache miss gaps"
```

If the implementation will be committed in the same lane, continue to Task 2 and make one combined commit at the end of Task 2.

---

## Task 2: Enforce Historical Data Completeness After OKX Cache-Miss Fetching

**Files:**

- Modify: `src/backtest/historical_data.py`
- Test: `tests/unit/test_historical_data.py`

- [x] **Step 1: Add explicit exception classes**

In `src/backtest/historical_data.py`, insert these classes after `MAX_PAGE_LIMIT = 300`:

```python
class UnsupportedTimeframeError(ValueError):
    pass


class InsufficientHistoricalDataError(ValueError):
    pass
```

- [x] **Step 2: Raise the explicit unsupported-timeframe exception**

Change `timeframe_to_ms()` from:

```python
def timeframe_to_ms(timeframe: str) -> int:
    if timeframe not in TIMEFRAME_MS:
        raise ValueError("unsupported timeframe")
    return TIMEFRAME_MS[timeframe]
```

to:

```python
def timeframe_to_ms(timeframe: str) -> int:
    if timeframe not in TIMEFRAME_MS:
        raise UnsupportedTimeframeError("unsupported timeframe")
    return TIMEFRAME_MS[timeframe]
```

- [x] **Step 3: Validate merged coverage before saving fetched rows**

In `ensure_historical_bars()`, replace the block after the `finally` clause:

```python
    new_bars = [
        bar
        for timestamp, bar in fetched_by_timestamp.items()
        if timestamp not in cached_by_timestamp and start <= timestamp <= end
    ]
    datasource.save_bars_to_cache(new_bars)
    return _sorted_bars(cached_by_timestamp | fetched_by_timestamp)
```

with:

```python
    merged_by_timestamp = cached_by_timestamp | fetched_by_timestamp
    remaining_missing = [timestamp for timestamp in expected if timestamp not in merged_by_timestamp]
    if remaining_missing:
        raise InsufficientHistoricalDataError("insufficient historical data")

    new_bars = [
        bar
        for timestamp, bar in fetched_by_timestamp.items()
        if timestamp not in cached_by_timestamp and start <= timestamp <= end
    ]
    datasource.save_bars_to_cache(new_bars)
    return _sorted_bars(merged_by_timestamp)
```

This preserves the existing OKX cache-miss algorithm:

1. Read cached bars for exact `symbol` and `timeframe`.
2. Convert timeframe to milliseconds using supported values `1m`, `5m`, `15m`, `1h`, `4h`, `1d`.
3. Build expected timestamps from the first aligned timestamp at or after `start` through `end`, inclusive.
4. Detect missing expected timestamps.
5. Fetch only missing contiguous ranges.
6. Paginate with `since = missing_range_start`, `limit = min(page_limit, expected_missing_count)`, and `page_limit <= 300`.
7. Advance with `last_returned_timestamp + timeframe_ms`.
8. Stop each range on range end, empty rows, or no timestamp advancement.
9. Save fetched bars only when the merged cached/fetched set covers every expected timestamp.
10. Return merged bars sorted by timestamp.

- [x] **Step 4: Run the historical-data tests**

Run:

```bash
uv run pytest tests/unit/test_historical_data.py -v
```

Expected after implementation:

- All tests in `tests/unit/test_historical_data.py` pass.
- The incomplete-fetch test proves partial OKX responses are rejected and not cached.

- [x] **Step 5: Run ruff on the changed files**

Run:

```bash
uv run ruff check src/backtest/historical_data.py tests/unit/test_historical_data.py
```

Expected:

- Ruff exits with code 0.

- [x] **Step 6: Commit the historical-data lane**

Run:

```bash
git add src/backtest/historical_data.py tests/unit/test_historical_data.py
git commit -m "fix: reject incomplete historical backtest data"
```

---

## Task 3: Map Historical Data Errors in the Backtest API

**Files:**

- Modify: `src/web/api/backtest.py`
- Modify: `tests/integration/test_web_api.py`

- [x] **Step 1: Add an integration test for partial historical fetches**

Append this test in `tests/integration/test_web_api.py` after `test_run_backtest_fetches_missing_historical_bars_before_running`:

```python
@pytest.mark.asyncio
async def test_run_backtest_rejects_partial_historical_fetch_without_persisting(monkeypatch):
    class FakeRepository:
        results = []
        klines = []

        def get_klines(self, symbol, timeframe, start, end):
            return [
                kline
                for kline in self.klines
                if kline.symbol == symbol
                and kline.timeframe == timeframe
                and start <= kline.timestamp <= end
            ]

        def save_kline(self, kline):
            self.klines.append(kline)
            return kline

        def save_backtest_result(self, result):
            self.results.append(result)
            return result

    class BuyOnceStrategy(BaseStrategy):
        name = "partial_fetch_buy_once"

        async def on_bar(self, bar):
            return await self.buy("BTC-USDT", 1.0)

    class PartialAdapter:
        closed = False

        async def fetch_ohlcv(self, symbol, timeframe, limit=100, since=None):
            return [
                Bar(timestamp=1700002800000, open=100, high=101, low=99, close=100, volume=1),
                Bar(timestamp=1700010000000, open=120, high=121, low=119, close=120, volume=1),
            ]

        async def close(self):
            self.closed = True

    FakeRepository.results = []
    FakeRepository.klines = []
    PartialAdapter.closed = False
    registry = StrategyRegistry()
    registry.register("partial_fetch_buy_once", BuyOnceStrategy)
    monkeypatch.setattr(backtest_api, "Repository", FakeRepository, raising=False)
    monkeypatch.setattr(
        backtest_api, "OKXSpotAdapter", lambda **kwargs: PartialAdapter(), raising=False
    )
    monkeypatch.setattr(backtest_api, "create_strategy_registry", lambda: registry, raising=False)

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/backtest/run",
            json={
                "strategy": "partial_fetch_buy_once",
                "symbol": "BTC-USDT",
                "timeframe": "1h",
                "start_time": 1700002800000,
                "end_time": 1700010000000,
                "initial_capital": 100000,
            },
        )

    assert resp.status_code == 422
    assert resp.json()["detail"] == "insufficient historical data for requested backtest range"
    assert FakeRepository.klines == []
    assert FakeRepository.results == []
```

Why this test matters:

- The adapter returns two bars, so a simple `len(bars) < 2` check would incorrectly allow the backtest to run.
- The expected `1h` timestamps include the middle candle, so the request must fail as insufficient historical data.
- The test proves partial fetched data is not cached and no summary is persisted.

- [x] **Step 2: Run the new integration test and confirm failure before API mapping is updated**

Run:

```bash
uv run pytest tests/integration/test_web_api.py::test_run_backtest_rejects_partial_historical_fetch_without_persisting -v
```

Expected before API mapping is updated:

- If Task 2 is merged first, the test fails with HTTP 502 because `InsufficientHistoricalDataError` is still caught by the generic `ValueError` branch.
- If Task 2 is not merged yet, the test fails with HTTP 200 because incomplete data is still allowed.

- [x] **Step 3: Import the explicit historical-data exceptions**

In `src/web/api/backtest.py`, change the historical-data import from:

```python
from src.backtest.historical_data import MAX_PAGE_LIMIT, ensure_historical_bars
```

to:

```python
from src.backtest.historical_data import (
    InsufficientHistoricalDataError,
    MAX_PAGE_LIMIT,
    UnsupportedTimeframeError,
    ensure_historical_bars,
)
```

- [x] **Step 4: Replace ValueError string matching with explicit exception mapping**

In `run_backtest()`, replace this `except ValueError` block:

```python
    except ValueError as exc:
        if str(exc) == "unsupported timeframe":
            raise HTTPException(
                status_code=422,
                detail="unsupported timeframe for historical backtest data",
            ) from exc
        raise HTTPException(
            status_code=502,
            detail="failed to fetch historical market data",
        ) from exc
```

with these blocks:

```python
    except UnsupportedTimeframeError as exc:
        raise HTTPException(
            status_code=422,
            detail="unsupported timeframe for historical backtest data",
        ) from exc
    except InsufficientHistoricalDataError as exc:
        raise HTTPException(
            status_code=422,
            detail="insufficient historical data for requested backtest range",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="failed to fetch historical market data",
        ) from exc
```

Keep the existing generic `except Exception` block unchanged so provider failures still return 502.

- [x] **Step 5: Run targeted integration tests**

Run:

```bash
uv run pytest \
  tests/integration/test_web_api.py::test_run_backtest_fetches_missing_historical_bars_before_running \
  tests/integration/test_web_api.py::test_run_backtest_rejects_partial_historical_fetch_without_persisting \
  tests/integration/test_web_api.py::test_run_backtest_returns_502_when_historical_fetch_fails \
  tests/integration/test_web_api.py::test_run_backtest_rejects_unsupported_historical_timeframe \
  tests/integration/test_web_api.py::test_run_backtest_rejects_insufficient_cached_bars \
  -v
```

Expected:

- All selected tests pass.
- Missing data returns 422.
- Unsupported timeframe returns 422.
- Provider failure returns 502.
- Successful cache-miss fetch persists fetched bars and a result summary.

- [x] **Step 6: Run ruff on the changed backend API files**

Run:

```bash
uv run ruff check src/web/api/backtest.py tests/integration/test_web_api.py
```

Expected:

- Ruff exits with code 0.

- [x] **Step 7: Commit the API lane**

Run:

```bash
git add src/web/api/backtest.py tests/integration/test_web_api.py
git commit -m "fix: map incomplete backtest history to 422"
```

---

## Task 4: Test OKX OHLCV Adapter Pagination Inputs

**Files:**

- Create: `tests/unit/test_exchange_base.py`
- Test: `src/exchange/base.py`

- [x] **Step 1: Create the OKX adapter unit test file**

Create `tests/unit/test_exchange_base.py` with this content:

```python
import pytest

from src.exchange import base as exchange_base
from src.exchange.base import OKXBaseAdapter


@pytest.mark.asyncio
async def test_okx_base_adapter_fetch_ohlcv_passes_since_and_limit(monkeypatch):
    class FakeOKX:
        instances = []

        def __init__(self, config):
            self.config = config
            self.fetch_calls = []
            self.closed = False
            self.instances.append(self)

        async def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
            self.fetch_calls.append(
                {"symbol": symbol, "timeframe": timeframe, "since": since, "limit": limit}
            )
            return [[1700000000000, 100, 101, 99, 100.5, 12.5]]

        async def close(self):
            self.closed = True

    monkeypatch.setattr(exchange_base.ccxt, "okx", FakeOKX)

    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "spot")
    bars = await adapter.fetch_ohlcv("BTC-USDT", "1h", limit=300, since=1700000000000)
    await adapter.close()

    fake = FakeOKX.instances[0]
    assert fake.config["apiKey"] == "api-key"
    assert fake.config["secret"] == "secret"
    assert fake.config["password"] == "passphrase"
    assert fake.config["options"] == {"defaultType": "spot"}
    assert fake.fetch_calls == [
        {"symbol": "BTC-USDT", "timeframe": "1h", "since": 1700000000000, "limit": 300}
    ]
    assert bars[0].timestamp == 1700000000000
    assert bars[0].open == 100.0
    assert bars[0].high == 101.0
    assert bars[0].low == 99.0
    assert bars[0].close == 100.5
    assert bars[0].volume == 12.5
    assert fake.closed is True
```

- [x] **Step 2: Run the new adapter test**

Run:

```bash
uv run pytest tests/unit/test_exchange_base.py -v
```

Expected:

- The test passes with the current `OKXBaseAdapter.fetch_ohlcv()` implementation.
- If it fails, adjust only `src/exchange/base.py` so `fetch_ohlcv()` calls `self._exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)` and maps the returned row fields to `Bar` exactly as the assertions expect.

- [x] **Step 3: Run ruff on the new test**

Run:

```bash
uv run ruff check tests/unit/test_exchange_base.py
```

Expected:

- Ruff exits with code 0.

- [x] **Step 4: Commit the OKX adapter lane**

Run:

```bash
git add tests/unit/test_exchange_base.py src/exchange/base.py
git commit -m "test: verify okx ohlcv pagination inputs"
```

If `src/exchange/base.py` did not change, Git will commit only `tests/unit/test_exchange_base.py`.

---

## Task 5: Surface Backtest API Detail Messages in the Frontend

**Files:**

- Modify: `frontend/src/utils/backtest.ts`
- Modify: `frontend/src/utils/backtest.test.ts`
- Modify: `frontend/src/views/Backtest.vue`

- [x] **Step 1: Add frontend tests for FastAPI detail extraction**

In `frontend/src/utils/backtest.test.ts`, append these tests inside the existing `describe('backtest validation', () => { ... })` block after `it('accepts valid backtest inputs', ...)`:

```ts
  it('extracts FastAPI detail messages from Axios errors', () => {
    const error = {
      isAxiosError: true,
      response: {
        data: {
          detail: 'insufficient historical data for requested backtest range',
        },
      },
    };

    expect(getBacktestApiErrorMessage(error)).toBe(
      'insufficient historical data for requested backtest range',
    );
  });

  it('ignores non-Axios errors and non-string detail payloads', () => {
    expect(getBacktestApiErrorMessage(new Error('network failed'))).toBeNull();
    expect(
      getBacktestApiErrorMessage({
        isAxiosError: true,
        response: { data: { detail: { message: 'nested detail' } } },
      }),
    ).toBeNull();
    expect(
      getBacktestApiErrorMessage({
        isAxiosError: true,
        response: { data: { detail: '   ' } },
      }),
    ).toBeNull();
  });
```

Then change the import at the top from:

```ts
import { getBacktestValidationError } from './backtest';
```

to:

```ts
import { getBacktestApiErrorMessage, getBacktestValidationError } from './backtest';
```

- [x] **Step 2: Run the frontend utility test and confirm failure**

Run:

```bash
npm --prefix frontend exec -- vitest run src/utils/backtest.test.ts
```

Expected before implementation:

- The test fails because `getBacktestApiErrorMessage` is not exported.

- [x] **Step 3: Add the Axios-aware helper**

In `frontend/src/utils/backtest.ts`, add this import at the top:

```ts
import axios from 'axios';
```

Then append this function after `getBacktestValidationError()`:

```ts
export function getBacktestApiErrorMessage(error: unknown): string | null {
  if (!axios.isAxiosError(error)) {
    return null;
  }

  const detail = error.response?.data?.detail;
  if (typeof detail !== 'string') {
    return null;
  }

  const message = detail.trim();
  return message.length > 0 ? message : null;
}
```

- [x] **Step 4: Use the helper in the Backtest page**

In `frontend/src/views/Backtest.vue`, change the utility import from:

```ts
import { getBacktestValidationError } from '@/utils/backtest';
```

to:

```ts
import { getBacktestApiErrorMessage, getBacktestValidationError } from '@/utils/backtest';
```

Then change the catch block in `handleRun()` from:

```ts
  } catch {
    ElMessage.error(t('backtest.runError'));
  } finally {
```

to:

```ts
  } catch (error) {
    ElMessage.error(getBacktestApiErrorMessage(error) ?? t('backtest.runError'));
  } finally {
```

- [x] **Step 5: Run the frontend utility test**

Run:

```bash
npm --prefix frontend exec -- vitest run src/utils/backtest.test.ts
```

Expected:

- All tests in `frontend/src/utils/backtest.test.ts` pass.

- [x] **Step 6: Run the backtest service contract tests**

Run:

```bash
npm --prefix frontend exec -- vitest run src/services/backtest.test.ts
```

Expected:

- `runBacktest()` still posts to `/api/backtest/run` and returns metrics.
- `fetchBacktestResults()` still gets `/api/backtest/results` and returns flat persisted summaries.

- [x] **Step 7: Build the frontend**

Run:

```bash
npm --prefix frontend run build
```

Expected:

- `vue-tsc --noEmit` passes.
- Vite production build completes.

- [x] **Step 8: Commit the frontend lane**

Run:

```bash
git add frontend/src/utils/backtest.ts frontend/src/utils/backtest.test.ts frontend/src/views/Backtest.vue
git commit -m "fix: show backtest api error details"
```

---

## Task 6: Merge Lanes and Run Full Verification

**Files:**

- Verify: backend and frontend test suites.
- Verify: browser golden path for the Backtest page.

- [x] **Step 1: Merge subagent lane commits into the integration branch**

Run the merge command that matches the branch names created by the subagents. Example if the lane branches are named as below:

```bash
git merge historical-data-backtest-gap-guard
git merge api-backtest-error-mapping
git merge okx-ohlcv-adapter-test
git merge frontend-backtest-api-errors
```

Expected:

- Git merges cleanly.
- If `src/web/api/backtest.py` import ordering conflicts with Task 3, keep the grouped import shown in Task 3.
- If frontend import ordering conflicts with Task 5, keep the grouped utility import shown in Task 5.

- [x] **Step 2: Run the full backend unit and integration suite**

Run:

```bash
uv run pytest
```

Expected:

- All backend tests pass.

- [x] **Step 3: Run backend lint**

Run:

```bash
uv run ruff check .
```

Expected:

- Ruff exits with code 0.

- [x] **Step 4: Run the full frontend Vitest suite**

Run:

```bash
npm --prefix frontend exec -- vitest run
```

Expected:

- All frontend tests pass.

- [x] **Step 5: Run the frontend production build**

Run:

```bash
npm --prefix frontend run build
```

Expected:

- Type checking passes.
- Vite build completes.

- [x] **Step 6: Browser smoke test the Backtest page**

Start the backend API server in one terminal:

```bash
uv run uvicorn src.web.app:create_app --factory --host 127.0.0.1 --port 8000
```

Start the frontend dev server in another terminal:

```bash
npm --prefix frontend run dev
```

In a browser:

1. Open the Vite dev-server URL printed by the frontend command.
2. Navigate to the Backtest page.
3. Verify the strategy selector contains `ma_cross`.
4. Submit a valid BTC-USDT backtest request on a supported timeframe.
5. Expected golden-path result:
   - The run button shows loading during the request.
   - Latest metrics render after success.
   - The history table refreshes and includes the new run.
6. Submit a request that causes incomplete historical data in a local mocked or disconnected-provider setup.
7. Expected error-path result:
   - The UI displays the backend detail string, for example `insufficient historical data for requested backtest range`, instead of only the generic run error.

If a real OKX network call is unavailable in the local environment, use the automated API tests as the error-path proof and record that the live browser error-path depended on external OKX availability.

- [x] **Step 7: Inspect git status and final diff**

Run:

```bash
git status --short
git diff --stat HEAD~4..HEAD
```

Expected:

- Only the planned backend/frontend files changed.
- No generated build artifacts are staged.
- No local database files are staged.

- [x] **Step 8: Final integration commit if lanes were not already committed**

If subagent lanes were merged as commits, do not create a duplicate commit. If changes were applied inline without lane commits, run:

```bash
git add \
  src/backtest/historical_data.py \
  src/web/api/backtest.py \
  tests/unit/test_historical_data.py \
  tests/integration/test_web_api.py \
  tests/unit/test_exchange_base.py \
  frontend/src/utils/backtest.ts \
  frontend/src/utils/backtest.test.ts \
  frontend/src/views/Backtest.vue
git commit -m "fix: harden real backtest historical data flow"
```

---

## Acceptance Criteria

- `ensure_historical_bars()` fetches cache-miss bars from OKX only for missing aligned timestamps.
- Supported backtest timeframes are exactly `1m`, `5m`, `15m`, `1h`, `4h`, and `1d`.
- Unsupported timeframe returns HTTP 422 with `unsupported timeframe for historical backtest data`.
- OKX/provider fetch failures return HTTP 502 with `failed to fetch historical market data`.
- If expected timestamps remain missing after cache-miss fetching, the API returns HTTP 422 with `insufficient historical data for requested backtest range`.
- Partial fetched rows are not saved when the final requested range is incomplete.
- Successful backtests still run `BacktestEngine`, persist `BacktestResultRecord`, return flat metrics from `POST /api/backtest/run`, and list flat persisted summaries from `GET /api/backtest/results`.
- Frontend validation still blocks missing/invalid times and non-positive initial capital before API calls.
- Frontend backtest run failures display backend `detail` strings when FastAPI provides them.
- Backend tests, ruff, frontend Vitest, frontend build, and browser smoke verification complete as described.

---

## Self-Review Checklist

- Spec coverage:
  - Real Backtest API execution is preserved and covered by existing integration tests.
  - OKX historical data cache-miss automatic fill is explicitly covered in Tasks 1 and 2.
  - Unsupported timeframe, fetch failure, and insufficient data errors are covered in Task 3.
  - OKX adapter `since` / `limit` forwarding is covered in Task 4.
  - Frontend-compatible contract is preserved in Task 5 and verified in Task 6.
- Placeholder scan:
  - Every code-changing step includes exact code snippets.
  - Every test step includes exact commands and expected outcomes.
  - No step asks the implementer to invent missing behavior.
- Type/signature consistency:
  - `InsufficientHistoricalDataError` and `UnsupportedTimeframeError` are defined in `src/backtest/historical_data.py` and imported from `src/web/api/backtest.py`.
  - `ensure_historical_bars()` signature remains unchanged.
  - `OKXBaseAdapter.fetch_ohlcv()` keeps the existing `since: int | None = None` signature.
  - Frontend `runBacktest()` continues to return `Promise<BacktestMetrics>`.
  - Frontend `fetchBacktestResults()` continues to return `Promise<BacktestResult[]>`.
