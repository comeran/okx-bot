# Strategy Performance Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a strategy-level performance view to the dashboard so operators can compare each strategy's current equity, PnL, exposure, activity, and win rate without losing the existing account-level overview.

**Architecture:** Build one backend aggregation service over the existing account, position, order, and trade records, expose it through `GET /api/trading/strategy-performance`, and keep the dashboard's existing account/position/order APIs unchanged. The frontend loads the aggregate alongside current dashboard data, refreshes it after debounced account/position/order WebSocket updates, and merges it with the strategy runtime store so strategies with no fills still appear with zero/unknown metrics.

**Tech Stack:** Python 3.12, FastAPI, SQLModel/SQLite, pytest; Vue 3, Pinia, TypeScript, Vitest, Element Plus, existing i18n utilities.

---

## Scope and metric contract

The first version uses the following definitions:

- `strategy`: persisted strategy name or runtime strategy name; exclude the internal `__exchange__` account.
- `initial_equity`, `equity`, `realized_pnl`, `unrealized_pnl`, `fees_paid`: values from that strategy's `AccountRecord`; use zero values when no account row exists.
- `return_pct`: `(equity - initial_equity) / initial_equity`; return `null` when there is no account row or the initial equity is zero, and render it as `—`.
- `open_positions`: count of `PositionRecord` rows whose amount is non-zero.
- `position_notional`: sum of `abs(amount) * (mark_price or entry_price)` for open positions.
- `order_count`: all persisted orders for the strategy.
- `filled_order_count`: orders whose status is `filled`.
- `trade_count`: persisted exchange/paper fills in `TradeRecord`.
- `last_order_at`: latest order timestamp, or `null` when there are no orders.
- `closed_trade_count`, `winning_trade_count`, `losing_trade_count`, `win_rate`: calculated from completed FIFO position matches in `TradeRecord`; open positions do not count toward win rate, and no completed trade returns `null` rather than `0%`.

The account's realized PnL remains authoritative for aggregate PnL. FIFO matching is only used for trade-count and win-rate attribution, so a partially closed position cannot be incorrectly reported as a losing or winning full trade.

## Locked product decisions

- Preserve the existing `Repository.get_account()` aggregate/exchange-account behavior; add `get_accounts()` for per-strategy aggregation.
- Show the union of runtime strategies, saved configurations, and strategies with historical records; exclude only `__exchange__` and empty strategy names.
- The dashboard covers current paper/live runtime records only. Backtest results remain on the separate backtest page and are not mixed into this table.
- Add a full-width strategy-performance card immediately after Account Overview. Use a comparison table by default; clicking a row expands order and position details rather than introducing a separate page in v1.
- Keep the first-level columns to strategy/status, equity, return, realized PnL, unrealized PnL, exposure, trade count, and win rate. Put fees, order counts, latest order time, per-symbol positions, and recent orders in the expanded row.
- Refresh performance after account, position, or order WebSocket updates with one 100 ms debounced HTTP request; do not add a new WebSocket message type.

## File map

- Create `src/analytics/strategy_performance.py` — typed performance row and aggregation/FIFO attribution logic.
- Modify `src/data/repository.py` — add `get_accounts()` so the aggregator can read per-strategy accounts without collapsing them into one global account.
- Modify `src/web/api/trading.py` — expose the strategy-performance endpoint and serialize the aggregate rows.
- Test `tests/unit/test_strategy_performance.py` — pure aggregation, exposure, return, FIFO win-rate, and empty-data behavior.
- Test `tests/integration/test_web_api.py` — endpoint response shape, strategy union, and exclusion of `__exchange__`.
- Create `frontend/src/types/strategyPerformance.ts` — API and merged-row types.
- Create `frontend/src/services/trading.ts` — typed client for `/api/trading/strategy-performance`.
- Create `frontend/src/services/trading.test.ts` — request path and response behavior.
- Modify `frontend/src/stores/dashboard.ts` — load/store performance rows and debounce refreshes after live dashboard updates.
- Do not modify `frontend/src/types/dashboard.ts` — performance refreshes use the dedicated API type and existing account/position/order WebSocket messages.
- Create `frontend/src/utils/strategyPerformance.ts` — merge runtime status rows with metric rows and format nullable win rates consistently.
- Create `frontend/src/utils/strategyPerformance.test.ts` — merge behavior for active, inactive, data-less, and historical-only strategies.
- Modify `frontend/src/views/Dashboard.vue` — render the strategy-performance table and preserve the current recent-orders table.
- Modify `frontend/src/locales/en.ts` and `frontend/src/locales/zh-CN.ts` — add section, column, and empty/error labels.

## Implementation tasks

### Task 1: Define and test the backend performance aggregation

**Files:**
- Create: `src/analytics/strategy_performance.py`
- Test: `tests/unit/test_strategy_performance.py`

- [ ] **Step 1: Write failing tests for the row contract.**

Create fixtures with two strategies and one internal exchange account. Cover:

```python
def test_build_strategy_performance_aggregates_account_activity_and_exposure():
    rows = build_strategy_performance(
        accounts=[
            AccountRecord(
                strategy="trend",
                initial_equity=100_000,
                cash_balance=101_000,
                available_balance=100_000,
                equity=101_500,
                realized_pnl=1_200,
                unrealized_pnl=400,
                daily_pnl=200,
                fees_paid=100,
                updated_at=1700000000000,
            ),
            AccountRecord(
                strategy="__exchange__",
                initial_equity=500_000,
                cash_balance=500_000,
                available_balance=500_000,
                equity=500_000,
                realized_pnl=0,
                unrealized_pnl=0,
                daily_pnl=0,
                fees_paid=0,
                updated_at=1700000000000,
            ),
        ],
        positions=[
            PositionRecord(
                strategy="trend", symbol="BTC-USDT", side="long", amount=2,
                entry_price=40_000, mark_price=41_000, leverage=1,
                timestamp=1700000000000,
            ),
            PositionRecord(
                strategy="trend", symbol="ETH-USDT", side="short", amount=3,
                entry_price=2_000, mark_price=None, leverage=1,
                timestamp=1700000000000,
            ),
        ],
        orders=[
            order_record("trend", "filled"),
            order_record("trend", "rejected"),
            order_record("quiet", "pending"),
        ],
        trades=[],
    )

    assert rows == [
        StrategyPerformance(
            strategy="quiet",
            initial_equity=0.0,
            equity=0.0,
            return_pct=None,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            fees_paid=0.0,
            position_notional=0.0,
            open_positions=0,
            order_count=1,
            filled_order_count=0,
            trade_count=0,
            closed_trade_count=0,
            winning_trade_count=0,
            losing_trade_count=0,
            win_rate=None,
            last_order_at=1700000000000,
        ),
        StrategyPerformance(
            strategy="trend",
            initial_equity=100_000,
            equity=101_500,
            return_pct=0.015,
            realized_pnl=1_200,
            unrealized_pnl=400,
            fees_paid=100,
            position_notional=88_000,
            open_positions=2,
            order_count=2,
            filled_order_count=1,
            trade_count=0,
            closed_trade_count=0,
            winning_trade_count=0,
            losing_trade_count=0,
            win_rate=None,
            last_order_at=1700000000000,
        ),
    ]
```

Also add a FIFO test with a buy at 100, a buy at 110, a sell that closes one unit at 120, and a sell that closes the remaining unit at 90; assert one winning and one losing closed trade and a `0.5` win rate. Add a test that an open-only trade sequence returns `win_rate is None`.

- [ ] **Step 2: Run the focused tests and verify the expected failure.**

Run:

```bash
uv run pytest tests/unit/test_strategy_performance.py -q
```

Expected: collection or assertion failure because `src/analytics/strategy_performance.py` and `StrategyPerformance` do not exist yet.

- [ ] **Step 3: Implement the typed row and aggregation.**

Define a frozen dataclass with the exact fields in the contract. Implement:

```python
@dataclass(frozen=True)
class StrategyPerformance:
    strategy: str
    initial_equity: float
    equity: float
    return_pct: float | None
    realized_pnl: float
    unrealized_pnl: float
    fees_paid: float
    position_notional: float
    open_positions: int
    order_count: int
    filled_order_count: int
    trade_count: int
    closed_trade_count: int
    winning_trade_count: int
    losing_trade_count: int
    win_rate: float | None
    last_order_at: int | None
```

Implement `build_strategy_performance(accounts, positions, orders, trades)` by first building a union of strategy names from all records, removing `__exchange__` and empty names, then aggregating each record into one row. Use `mark_price` when it is a positive number; otherwise use `entry_price` for notional.

Implement FIFO matching per `(strategy, symbol)`. Store open lots as `(side, amount, price, fee)`. When an opposite-side trade closes a lot, allocate entry and exit fees proportionally to the matched quantities, add the resulting net PnL to the current closing trade's PnL, and count that closing trade once as a win or loss. If the closing trade reverses the position, add its residual quantity as a new lot.

- [ ] **Step 4: Run the focused tests and verify they pass.**

Run:

```bash
uv run pytest tests/unit/test_strategy_performance.py -q
```

Expected: all aggregation and FIFO tests pass.

### Task 2: Make per-strategy accounts available and expose the API

**Files:**
- Modify: `src/data/repository.py:101-130`
- Modify: `src/web/api/trading.py:117-143`
- Test: `tests/integration/test_web_api.py`

- [ ] **Step 1: Add a repository test for non-collapsed account retrieval.**

Extend the repository/API test fixture with two `AccountRecord` rows and assert `Repository.get_accounts()` returns both strategy rows, including `__exchange__` for the aggregator to filter. The test must verify that this is not the existing `get_account()` behavior, which collapses multiple accounts.

- [ ] **Step 2: Run the new repository test and verify it fails.**

Run:

```bash
uv run pytest tests/integration/test_web_api.py -k 'get_accounts or strategy_performance' -q
```

Expected: failure because `Repository.get_accounts()` is not defined and the endpoint is not registered.

- [ ] **Step 3: Implement `Repository.get_accounts()`.**

Add a method beside `get_account()`:

```python
def get_accounts(self) -> list[AccountRecord]:
    with Session(self.engine) as session:
        return list(session.exec(select(AccountRecord)).all())
```

Do not change `get_account()` semantics because the dashboard account cards depend on its existing aggregate/exchange-account behavior.

- [ ] **Step 4: Add the endpoint test before implementing the route.**

Add an integration case using a fake repository with `get_accounts`, `get_positions`, `get_orders`, and `get_trades`. Call:

```http
GET /api/trading/strategy-performance
```

Assert HTTP 200, the exact JSON field names from `StrategyPerformance`, sorted rows, and no row whose strategy is `__exchange__`. Include a strategy that has orders but no account to verify zero-valued metrics are returned.

- [ ] **Step 5: Implement `GET /api/trading/strategy-performance`.**

In `src/web/api/trading.py`, import `asdict` and `build_strategy_performance`, then add:

```python
@router.get("/strategy-performance")
async def get_strategy_performance() -> list[dict[str, Any]]:
    repository = Repository()
    rows = build_strategy_performance(
        accounts=repository.get_accounts(),
        positions=repository.get_positions(),
        orders=repository.get_orders(),
        trades=repository.get_trades(),
    )
    return [asdict(row) for row in rows]
```

Keep this endpoint read-only and do not alter `/api/trading/orders`, which remains available for the full order history.

- [ ] **Step 6: Run backend regression tests.**

Run:

```bash
uv run pytest tests/unit/test_strategy_performance.py tests/integration/test_web_api.py -q
uv run ruff check src/analytics/strategy_performance.py src/data/repository.py src/web/api/trading.py tests/unit/test_strategy_performance.py tests/integration/test_web_api.py
```

Expected: all selected tests pass and Ruff reports no violations.

### Task 3: Add typed frontend loading and real-time refresh behavior

**Files:**
- Create: `frontend/src/types/strategyPerformance.ts`
- Create: `frontend/src/services/trading.ts`
- Create: `frontend/src/services/trading.test.ts`
- Modify: `frontend/src/stores/dashboard.ts`
- Modify: `frontend/src/types/dashboard.ts` only if the snapshot type is extended
- Test: `frontend/src/stores/dashboard.test.ts`

- [ ] **Step 1: Define the frontend API type.**

Create an interface matching the backend contract exactly:

```ts
export interface StrategyPerformance {
  strategy: string;
  initial_equity: number;
  equity: number;
  return_pct: number | null;
  realized_pnl: number;
  unrealized_pnl: number;
  fees_paid: number;
  position_notional: number;
  open_positions: number;
  order_count: number;
  filled_order_count: number;
  trade_count: number;
  closed_trade_count: number;
  winning_trade_count: number;
  losing_trade_count: number;
  win_rate: number | null;
  last_order_at: number | null;
}
```

- [ ] **Step 2: Write the service test before the service implementation.**

In `frontend/src/services/trading.test.ts`, mock Axios and assert:

```ts
it('fetches strategy performance from the trading API', async () => {
  mockedAxios.get.mockResolvedValueOnce({ data: [{ strategy: 'trend' }] });

  await expect(fetchStrategyPerformance()).resolves.toEqual([{ strategy: 'trend' }]);
  expect(mockedAxios.get).toHaveBeenCalledWith('/api/trading/strategy-performance');
});
```

Run:

```bash
npm --prefix frontend exec -- vitest run src/services/trading.test.ts
```

Expected: failure because the service file/function does not exist.

- [ ] **Step 3: Implement the typed service.**

Create `fetchStrategyPerformance(): Promise<StrategyPerformance[]>` using the same Axios pattern as the existing service modules.

- [ ] **Step 4: Extend the dashboard store state and initial load.**

Add:

```ts
strategyPerformance: StrategyPerformance[];
strategyPerformanceError: string | null;
```

Initialize the array/error in the state. Load `/api/trading/strategy-performance` with the account, positions, and orders requests. A performance-request failure must set `strategyPerformanceError` but must not discard the account, positions, orders, or ticker data already loaded.

- [ ] **Step 5: Add debounced refresh after runtime data changes.**

Add a store action that schedules one performance fetch 100 ms after any `account`, `positions`, or `orders` WebSocket message. Cancel the previous timer before scheduling a new one so a single order update does not create three HTTP requests. Clear the timer when the store is reset or the page is unmounted through the existing store lifecycle path.

Add tests that:

- load the performance response during initial data loading;
- preserve core dashboard data when the performance request fails;
- schedule one refresh when account/position/order messages arrive close together;
- update `strategyPerformance` with the refreshed response.

- [ ] **Step 6: Run frontend store/service tests.**

Run:

```bash
npm --prefix frontend exec -- vitest run src/services/trading.test.ts src/stores/dashboard.test.ts
```

Expected: all selected tests pass.

### Task 4: Build the dashboard strategy-performance panel

**Files:**
- Create: `frontend/src/utils/strategyPerformance.ts`
- Create: `frontend/src/utils/strategyPerformance.test.ts`
- Modify: `frontend/src/views/Dashboard.vue`
- Modify: `frontend/src/locales/en.ts`
- Modify: `frontend/src/locales/zh-CN.ts`

- [ ] **Step 1: Write tests for merging runtime and metric rows.**

Create a pure helper that accepts runtime summaries and performance rows and returns display rows. Test these cases:

```ts
it('keeps data-less configured strategies visible with empty metrics', () => {
  const rows = mergeStrategyPerformanceRows(
    [{ name: 'quiet', status: 'stopped' }],
    [],
  );

  expect(rows).toEqual([
    expect.objectContaining({
      strategy: 'quiet',
      status: 'stopped',
      equity: 0,
      win_rate: null,
    }),
  ]);
});

it('keeps historical performance rows whose strategy is no longer running', () => {
  const rows = mergeStrategyPerformanceRows(
    [],
    [performanceRow('retired')],
  );

  expect(rows[0]).toMatchObject({ strategy: 'retired', status: 'unknown' });
});
```

- [ ] **Step 2: Run the helper tests and verify they fail.**

Run:

```bash
npm --prefix frontend exec -- vitest run src/utils/strategyPerformance.test.ts
```

Expected: failure because the helper and display-row type do not exist.

- [ ] **Step 3: Implement the merge helper and formatting rules.**

Use the strategy store's runtime summary as the status authority. Merge metric values by strategy name, append metric-only historical rows, and sort runtime rows first followed by metric-only rows alphabetically. Keep `win_rate: null` as null so the UI can render `—`, not `0%`.

- [ ] **Step 4: Add the dashboard panel.**

In `Dashboard.vue`, place a full-width `Strategy Performance` card immediately after Account Overview and before market tickers/orders. Compute merged rows and render these first-level columns:

- Strategy and runtime status
- Equity and return percentage
- Realized PnL and unrealized PnL
- Position notional/exposure
- Closed trade count and win rate

Add an expandable row containing fees, total/filled order counts, latest order time, and nested per-symbol positions/recent orders for that strategy. Use the existing `formatRuntimeCurrency`, `formatRuntimeNumber`, `formatRuntimeTime`, and status-tag helpers. Render `—` for nullable return/win-rate/last-order values. Show a warning alert when `strategyPerformanceError` is set, while keeping the table visible with the last successful data.

Keep the recent-orders cap at 20 in `Dashboard.vue`; the new panel must not reintroduce full order rendering.

- [ ] **Step 5: Add English and Chinese labels.**

Add translations for the panel title, all metric headers, empty state, unavailable win rate, and performance-load warning in the existing `dashboard` locale sections. Do not hardcode user-facing English text in the component.

- [ ] **Step 6: Run frontend tests and build.**

Run:

```bash
npm --prefix frontend exec -- vitest run src/services/trading.test.ts src/stores/dashboard.test.ts src/utils/strategyPerformance.test.ts
npm --prefix frontend run build
```

Expected: all selected tests pass and the production build completes.

### Task 5: Runtime verification and regression review

**Files:**
- No new production files; review all files from Tasks 1–4.

- [ ] **Step 1: Start the backend and frontend using the repository's documented commands.**

Run the backend on `127.0.0.1:8080` and the frontend on `127.0.0.1:3000`. Confirm:

```bash
curl -sS http://127.0.0.1:8080/api/health
curl -sS http://127.0.0.1:8080/api/trading/strategy-performance
curl -sS http://127.0.0.1:3000/
```

Expected: health returns `{"status":"ok"}`, performance returns a JSON array, and Vite returns the application HTML.

- [ ] **Step 2: Drive the dashboard through the browser.**

Open `http://127.0.0.1:3000`, confirm the Strategy Performance panel renders the existing configured strategies (`11`, `tt`, `test` in the current database), and verify the rows show their separate PnL/activity values rather than only the aggregate account totals.

- [ ] **Step 3: Verify a data-less strategy and a live update.**

Create or select a strategy with no fills, confirm it appears with zero metrics and `—` win rate, then trigger one paper order and confirm the affected row's order/fill count and PnL refresh without a manual page reload.

- [ ] **Step 4: Verify the existing dashboard behavior.**

Confirm account totals, positions, recent-orders cap of 20, WebSocket connection state, and strategy status/error display still work. Confirm the full `/api/trading/orders` response remains unchanged for consumers that need history.

- [ ] **Step 5: Leave changes uncommitted.**

Do not commit or push; report the files changed, test commands/results, runtime evidence, and any metric limitations to the user.

## Self-review checklist

- The plan covers backend aggregation, repository access, API serialization, frontend types/service/store, UI rendering, i18n, tests, and browser verification.
- No metric is silently fabricated: missing account data uses zero values, while missing completed trades use `null` win rate and render as `—`.
- The endpoint is additive; existing account, position, order, trade, and WebSocket contracts remain compatible.
- The plan preserves the recent-orders limit and keeps full order history available through the existing API.
