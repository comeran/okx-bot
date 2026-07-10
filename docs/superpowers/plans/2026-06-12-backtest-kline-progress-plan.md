# Backtest History K-line Progress and Plan

> **For agentic workers:** Continue from this file when resuming the Backtest History K-line feature. Do not commit or push unless the user explicitly instructs it.

**Goal:** Add a candlestick/K-line chart to the Backtest History page and mark buy/sell points for the selected backtest result.

**Status:** Implemented. Backend detail endpoint, `BacktestTradeRecord` persistence, engine marker fields, repository accessors, and frontend K-line chart with buy/sell markers are all landed on `main` (merged via PR #9). Backend tests (`test_backtest_engine`, `test_repository`, `test_web_api`) and frontend service tests pass locally.

---

## Current Progress

### Completed decisions

The `/grill-me` decision session for this feature is complete: 18/18 decisions were answered and locked.

Locked decisions:

1. Backtest detail chart data must use local `KlineCache`; opening a history detail must not fetch OKX.
2. Buy/sell markers must be persisted in a dedicated `BacktestTradeRecord` table.
3. Do not reuse live/paper `TradeRecord` or `OrderRecord` for backtest markers.
4. `BacktestEngine` should emit marker-ready fields in `report.trades` at fill time.
5. Repository methods should expose backtest result and backtest trade detail access.
6. Add `GET /api/backtest/results/{result_id}` returning `result`, `klines`, and `markers` in one payload.
7. Missing result detail should return HTTP 404.
8. Reuse and extend `frontend/src/components/charts/Candlestick.vue`.
9. Marker rendering should live inside `Candlestick.vue` via an optional `markers` prop.
10. `Backtest.vue` should not directly mutate ECharts or overlay marker DOM.
11. Add `fetchBacktestResultDetail(id)` to `frontend/src/services/backtest.ts`.
12. Add shared `BacktestMarker` and `BacktestResultDetail` types in `frontend/src/types/backtest.ts`.
13. Clicking a Backtest History table row selects that result and loads its detail chart.
14. Before a row is selected, show a select-history empty state instead of a blank chart.
15. If detail loading fails, show a clear error message in the page.
16. Update backend unit tests for `BacktestEngine` trade fields and repository trade persistence/retrieval.
17. Add integration tests for `GET /api/backtest/results/{result_id}`.
18. Update frontend service tests and rely on build/browser smoke for chart marker rendering; do not add Vue component/view tests now.

### Current source state

No successful source edits have been applied for this K-line marker feature yet.

Confirmed current gaps:

- `src/backtest/engine.py` still records report trades with only `pnl`, `fee`, and `timestamp`.
- `src/data/models.py` still has `KlineCache` and `BacktestResultRecord`, but no `BacktestTradeRecord`.
- `src/data/repository.py` still lacks:
  - `get_backtest_result(result_id)`
  - `save_backtest_trades(trades)`
  - `get_backtest_trades(result_id)`
- `src/web/api/backtest.py` still persists only summary metrics and lacks `GET /api/backtest/results/{result_id}`.
- `frontend/src/views/Backtest.vue` still renders only the run form, latest metrics, and summary history table.
- `frontend/src/components/charts/Candlestick.vue` still has no marker prop/series support.
- `frontend/src/services/backtest.ts` still lacks a detail fetch function.
- `frontend/src/types/backtest.ts` still lacks marker/detail response types.

### Latest implementation attempt

The first edit attempt failed because the edit tool required the files to be read in the current context before editing. After that, these files were read successfully and are ready for precise edits:

- `src/backtest/engine.py`
- `src/data/models.py`
- `src/data/repository.py`
- `src/web/api/backtest.py`
- `frontend/src/views/Backtest.vue`

No tests have been run yet for this feature.

---

## Future Plan

### Phase 1: Backend detail data

Modify `src/backtest/engine.py` so each filled trade includes marker-ready fields:

```python
{
    "symbol": order.symbol,
    "side": order.side.value,
    "amount": order.amount,
    "price": match.fill_price,
    "pnl": pnl,
    "fee": match.fee,
    "timestamp": bar.timestamp,
}
```

Modify `src/data/models.py` to add a dedicated backtest marker table:

```python
class BacktestTradeRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    result_id: str = Field(index=True)
    symbol: str = Field(index=True)
    side: str = Field(index=True)
    timestamp: int = Field(index=True)
    price: float
    amount: float
    fee: float
    pnl: float
```

Modify `src/data/repository.py` to:

- Import `BacktestTradeRecord`.
- Add `get_backtest_result(result_id)`.
- Add `save_backtest_trades(trades)`.
- Add `get_backtest_trades(result_id)`, ordered by timestamp and id.
- Ensure the new table exists for existing SQLite databases.

Modify `src/web/api/backtest.py` to:

- Generate `result_id` before saving the summary.
- Save `BacktestResultRecord` with that `result_id`.
- Save `BacktestTradeRecord` rows from `report.trades`.
- Add `GET /api/backtest/results/{result_id}`.
- Return:
  - `result`: selected `BacktestResultRecord` dump
  - `klines`: cached `KlineCache` rows for the result symbol/timeframe/start/end range
  - `markers`: saved backtest trade rows
- Return HTTP 404 when `result_id` does not exist.
- Do not call OKX or `ensure_historical_bars` in the detail endpoint.

### Phase 2: Frontend detail chart

Modify `frontend/src/types/backtest.ts` to add:

```ts
import type { Kline } from '@/types/market';

export interface BacktestMarker {
  symbol: string;
  side: 'buy' | 'sell';
  timestamp: number;
  price: number;
  amount: number;
  fee: number;
  pnl: number;
}

export interface BacktestResultDetail {
  result: BacktestResult;
  klines: Kline[];
  markers: BacktestMarker[];
}
```

Modify `frontend/src/services/backtest.ts` to add:

```ts
export async function fetchBacktestResultDetail(id: string): Promise<BacktestResultDetail> {
  const response = await axios.get<BacktestResultDetail>(`/api/backtest/results/${id}`);
  return response.data;
}
```

Modify `frontend/src/components/charts/Candlestick.vue` to:

- Import/register `ScatterChart`.
- Add an optional `markers?: BacktestMarker[]` prop.
- Split markers into buy and sell scatter series.
- Match marker timestamps to candle category indexes.
- Render buy markers in green and sell markers in red.
- Keep marker implementation inside the chart component.

Modify `frontend/src/views/Backtest.vue` to:

- Import `Candlestick`.
- Import `fetchBacktestResultDetail`.
- Track selected result id, selected detail, detail loading state, and detail error state.
- Add row-click handling to the history table.
- Render the selected detail K-line chart with markers.
- Show a select-history empty state before selection.
- Show a visible detail load error when the endpoint fails.

Modify locale files:

- `frontend/src/locales/en.ts`
- `frontend/src/locales/zh-CN.ts`

Add labels for:

- History chart title
- Select-history empty state
- Detail load error

### Phase 3: Regression coverage

Backend unit tests:

- Update `tests/unit/test_backtest_engine.py` to assert marker-ready fields in `report.trades`.
- Update `tests/unit/test_repository.py` to cover `BacktestTradeRecord` save/read behavior and `get_backtest_result`.

Backend integration tests:

- Update `tests/integration/test_web_api.py` to:
  - Assert `/api/backtest/run` persists backtest trades.
  - Add `GET /api/backtest/results/{result_id}` success test.
  - Add missing-result 404 test.
  - Assert detail payload includes `result`, `klines`, and `markers`.

Frontend service tests:

- Update `frontend/src/services/backtest.test.ts` to cover `fetchBacktestResultDetail(id)`.

Do not add Vue component/view unit tests for marker rendering unless a later blocker requires it.

### Phase 4: Verification gates

Run targeted tests first:

```bash
uv run pytest tests/unit/test_backtest_engine.py tests/unit/test_repository.py tests/integration/test_web_api.py
npm --prefix frontend exec -- vitest run frontend/src/services/backtest.test.ts
```

Then run full local gates:

```bash
uv run pytest
uv run ruff check .
npm --prefix frontend exec -- vitest run
npm --prefix frontend run build
```

Because this includes UI/frontend changes, start the backend and frontend dev servers and browser-smoke the Backtest page before reporting completion.

Browser smoke should verify:

- Backtest page loads.
- History table is visible.
- Before selecting a row, the page shows the select-history empty state.
- Selecting a history row requests detail data.
- If detail data exists, the K-line chart renders.
- Buy/sell markers are visible when marker data exists.
- Browser console has no current error-level messages.

---

## Constraints

- Do not commit or push unless the user explicitly instructs it.
- Do not place real OKX orders.
- Backtest detail loading must not fetch OKX; it must use local cached `KlineCache` only.
- Do not reuse live/paper trade tables for backtest markers.
- Do not run destructive database reset/delete commands.
- Do not use `--no-verify`.
- Do not force push.
- Do not amend commits unless explicitly asked.
- Do not run `npm audit fix --force` without separate explicit authorization.
