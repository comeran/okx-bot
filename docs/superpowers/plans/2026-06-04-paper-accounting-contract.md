# Paper-Mode Accounting Contract

## Scope

This contract defines the next paper-mode accounting milestone for the OKX bot. It turns persisted order/fill records into a consistent local account, cash, current net position, realized PnL, and fill-price model.

The goal is not exchange-perfect accounting. The goal is a truthful local paper-mode source of state that the web console, APIs, runtime, and later WebSocket/risk features can rely on without fabricating values.

## Standing decisions

- Paper-mode state is local Repository state, not OKX demo/live synchronization.
- Machine-readable values remain untranslated: strategy IDs, symbols, side values, API fields, and config keys.
- Never fill an order at `0.0` unless the user explicitly submitted price `0.0`, which should be invalid for trading use.
- Do not expose placeholder account fields as real exchange data.
- Keep this milestone focused on accounting semantics, not a full continuous market-data runtime loop.

## Non-goals for this milestone

- OKX demo/live account reconciliation.
- Exchange margin, liquidation, leverage, and funding accounting.
- Pending limit-order matching against live candles.
- Real-time WebSocket account snapshots.
- Full migration framework such as Alembic.
- Strategy config persistence.
- Risk manager circuit-breaker integration.

## Account model

Paper account state is scoped at least by `strategy`. A future account-level aggregate can sum across strategies, but this milestone should make per-strategy accounting correct first.

Recommended fields:

- `strategy: str`
- `initial_equity: float`
- `cash_balance: float`
- `equity: float`
- `realized_pnl: float`
- `unrealized_pnl: float`
- `daily_pnl: float`
- `fees_paid: float`
- `updated_at: int`

### Initial capital

Use this priority order:

1. configured paper/backtest initial capital if already available through app config;
2. existing default initial capital if config omits it;
3. test-injected value for deterministic unit tests.

Do not add a new Settings UI field in this milestone.

### Cash balance

`cash_balance` is the remaining paper cash after notional movements and fees.

For the synthetic long/short paper model:

- buy notional decreases cash;
- sell notional increases cash;
- fees always decrease cash.

This is intentionally simplified and does not model exchange margin requirements yet.

### Equity

Until mark-to-market exists:

```text
equity = cash_balance + open_position_cost_basis_value
```

Where:

```text
open_position_cost_basis_value = sum(abs(position.amount) * position.entry_price)
```

After mark prices are available:

```text
equity = cash_balance + open_position_mark_value
```

For this milestone, cost-basis equity is acceptable if clearly tested and documented through field semantics.

### Realized PnL

`realized_pnl` accumulates only closed-position profit/loss after fees.

Long close:

```text
realized_pnl += (sell_price - entry_price) * closed_amount - close_fee_allocated
```

Short close:

```text
realized_pnl += (entry_price - buy_price) * closed_amount - close_fee_allocated
```

Opening fees should be included in `fees_paid` and cash, but the first implementation may keep realized PnL as price-difference PnL and expose `fees_paid` separately. Tests must lock whichever convention is chosen.

Recommended convention for this milestone:

```text
realized_pnl = price-difference PnL only
fees_paid = total fees
cash_balance includes fees
```

Reason: current fee is `0.0`, and separating fee effects avoids hiding fee semantics inside realized PnL before fee policy is mature.

### Unrealized PnL

For this milestone, if no mark price exists:

```text
unrealized_pnl = 0.0
```

Do not expose it as exchange truth. It is local paper-mode unrealized PnL.

When mark price exists:

Long:

```text
unrealized_pnl = (mark_price - entry_price) * amount
```

Short:

```text
unrealized_pnl = (entry_price - mark_price) * amount
```

### Daily PnL

For this milestone, `daily_pnl` is accepted as a simplified paper-mode field:

```text
daily_pnl = cumulative realized_pnl for the local paper account
```

It is not a true rolling 24-hour exchange PnL yet. A later ledger-backed implementation should replace this with:

```text
daily_pnl = sum(realized_pnl_delta for closes whose fill timestamp is within the last 24 hours)
```

That later step requires explicit realized-PnL ledger events and timestamp-window queries.

## Position model

A current position is scoped by `(strategy, symbol)`.

There should be at most one current net position per `(strategy, symbol)`.

Recommended fields:

- `strategy: str`
- `symbol: str`
- `side: "long" | "short"`
- `amount: float`
- `entry_price: float`
- `mark_price: float | None`
- `realized_pnl: float`
- `unrealized_pnl: float`
- `leverage: int`
- `timestamp: int`

Flat positions should not be returned by `/api/trading/positions` by default.

Implementation options:

1. delete flat current position rows;
2. keep amount `0.0` rows and filter them out of API responses.

Recommendation: delete or hide flat rows in Repository-level `get_open_positions()` and keep historical facts in trades/orders/ledger.

## Fill model

A fill is the accounting event produced by a filled order.

Minimum fields needed by accounting:

- `order_id`
- `strategy`
- `symbol`
- `side: buy | sell`
- `amount`
- `price`
- `fee`
- `timestamp`

`TradeRecord` remains historical executed trade history. Account and current position APIs should not recompute their whole state from `TradeRecord` on every request once account/position snapshots exist.

## Netting rules

### Buy when flat

Input:

```text
position = none
buy amount = A
price = P
fee = F
```

Result:

```text
position.side = long
position.amount = A
position.entry_price = P
cash_balance -= A * P + F
fees_paid += F
```

### Buy when long

Input:

```text
existing long amount = A1
existing entry = E1
buy amount = A2
price = P
```

Result:

```text
new_amount = A1 + A2
new_entry = ((A1 * E1) + (A2 * P)) / new_amount
cash_balance -= A2 * P + fee
```

### Sell when long, partial close

Input:

```text
long amount = A1
entry = E
sell amount = A2 where A2 < A1
price = P
```

Result:

```text
closed_amount = A2
remaining_amount = A1 - A2
realized_pnl_delta = (P - E) * closed_amount
position remains long with same entry E
cash_balance += A2 * P - fee
realized_pnl += realized_pnl_delta
```

### Sell when long, full close

Input:

```text
sell amount = long amount
```

Result:

```text
realized_pnl_delta = (P - E) * amount
position becomes flat and is not returned by positions API
cash_balance += amount * P - fee
```

### Sell when long, flip to short

Input:

```text
long amount = A1
sell amount = A2 where A2 > A1
price = P
```

Result:

```text
closed_amount = A1
short_open_amount = A2 - A1
realized_pnl_delta = (P - E) * closed_amount
position.side = short
position.amount = short_open_amount
position.entry_price = P
cash_balance += A2 * P - fee
```

### Sell when flat

Result:

```text
position.side = short
position.amount = A
position.entry_price = P
cash_balance += A * P - fee
```

### Sell when short

Weighted average entry updates like adding to long:

```text
new_amount = A1 + A2
new_entry = ((A1 * E1) + (A2 * P)) / new_amount
cash_balance += A2 * P - fee
```

### Buy when short, partial close

```text
realized_pnl_delta = (entry_price - buy_price) * closed_amount
position remains short with same entry price
cash_balance -= buy_amount * buy_price + fee
```

### Buy when short, full close

Position becomes flat and is not returned by positions API.

### Buy when short, flip to long

```text
closed_amount = existing_short_amount
long_open_amount = buy_amount - existing_short_amount
realized_pnl_delta = (entry_price - buy_price) * closed_amount
position.side = long
position.amount = long_open_amount
position.entry_price = buy_price
cash_balance -= buy_amount * buy_price + fee
```

## Fee policy

Initial policy:

```text
fee = notional * fee_rate
```

Fee rate priority:

1. configured backtest/paper fee rate if already available;
2. default `0.0`.

Persist actual fee on `TradeRecord`.

`fees_paid` accumulates all fees.

## Fill-price policy

Paper-mode orders must never silently fill at zero.

Price resolution priority:

1. explicit order price;
2. injected latest-price provider;
3. latest cached market close/ticker if a provider exists;
4. reject the order if no price is available.

Recommended first implementation:

- limit order with price: fill immediately at limit price;
- market order with explicit price: fill at explicit price;
- market order with latest provider price: fill at provider price;
- market order with no price: `rejected`, no trade, no position/account mutation.

This is deliberately simpler than a real matching engine.

## Repository contract

Recommended additions:

- `save_account(account)` or `upsert_account(account)`
- `get_account(strategy: str | None = None)`
- `save_cash_ledger_entry(entry)`
- `get_cash_ledger(strategy: str | None = None)`
- `get_position(strategy: str, symbol: str)`
- `upsert_position(position)`
- `delete_position(strategy: str, symbol: str)` or equivalent flat handling
- `get_open_positions(strategy: str | None = None)`

If the existing `PositionRecord` is retained, document whether it is a current snapshot table or historical table. Recommendation for this milestone: treat it as current net position state and use orders/trades/ledger for history.

## API contract

### `GET /api/trading/account`

Return local paper-mode account summary.

Minimum response fields:

```json
{
  "cash_balance": 100000.0,
  "equity": 100000.0,
  "realized_pnl": 0.0,
  "unrealized_pnl": 0.0,
  "daily_pnl": 0.0,
  "fees_paid": 0.0
}
```

Keep `equity` and `daily_pnl` for current frontend compatibility.

Do not add `available_balance` until margin/locked-order semantics exist.

### `GET /api/trading/positions`

Return current open net positions, newest or most recently updated first.

Each row should include at least:

```json
{
  "id": 1,
  "strategy": "ma_cross",
  "symbol": "BTC-USDT",
  "side": "long",
  "amount": 0.1,
  "entry_price": 50000.0,
  "mark_price": null,
  "realized_pnl": 0.0,
  "unrealized_pnl": 0.0,
  "leverage": 1,
  "timestamp": 1700000000000
}
```

Flat positions should not appear by default.

### `GET /api/trading/orders`

Continue to return historical order records.

Rejected market orders with no price should appear here with status `rejected`.

### `GET /api/trading/trades`

Continue to return historical executed trades only.

Rejected or pending orders should not create trades.

## Order manager contract

`UnifiedOrderManager` responsibilities:

1. build an order with a unique local order id;
2. submit through `OrderRouter`;
3. persist the resulting `OrderRecord`;
4. if status is `filled`, delegate fill accounting to a paper accounting service;
5. if status is `pending`, `rejected`, or `cancelled`, do not mutate account/position/trade state;
6. preserve `cancel(order_id, symbol)` behavior.

`UnifiedOrderManager` should not contain netting math after this milestone.

## Paper order handler contract

`LocalPaperOrderHandler` responsibilities:

1. determine whether an order can fill immediately;
2. set order status;
3. set `fill_price` and `fill_time` only for filled orders;
4. reject market orders with no usable price;
5. preserve immediate-fill limit behavior for this milestone.

## Test contract

### Accounting unit tests

Required cases:

- buy opens long;
- buy adds to long and updates weighted average entry;
- sell partially closes long and realizes PnL;
- sell fully closes long and removes/hides position;
- sell flips long to short;
- sell opens short from flat;
- sell adds to short and updates weighted average entry;
- buy partially closes short and realizes PnL;
- buy fully closes short;
- buy flips short to long;
- fees reduce cash and increase `fees_paid`;
- rejected/pending order does not mutate account or position.

### Repository tests

Required cases:

- account upsert/get;
- open position upsert/get;
- flat position handling;
- cash ledger ordering and strategy filtering;
- existing order/trade tests remain green.

### Order manager tests

Required cases:

- filled order persists order and delegates accounting;
- rejected market order persists rejected order but creates no trade/position mutation;
- pending order creates no accounting mutation;
- generated order IDs remain unique;
- cancel still forwards symbol.

### API integration tests

Required cases:

- account returns cash/equity/realized/unrealized/daily/fees fields;
- positions returns current net positions;
- flat positions are omitted;
- orders include rejected zero-price market order;
- trades exclude rejected orders;
- strategy filter behavior still works.

## Subagent implementation boundaries

After this contract is accepted, implementation can be split as follows.

### Subagent A: Repository and models

Files:

- `src/data/models.py`
- `src/data/repository.py`
- `tests/unit/test_repository.py`

Deliverables:

- account model;
- cash ledger model;
- current position repository methods;
- repository tests.

### Subagent B: Accounting service

Files:

- `src/order/accounting.py` or `src/paper/accounting.py`
- `tests/unit/test_paper_accounting.py`

Deliverables:

- netting implementation;
- cash/equity/realized PnL updates;
- fee accumulation;
- tests for all required accounting cases.

### Subagent C: Fill pricing and paper order handler

Files:

- `src/web/api/strategies.py`
- possibly a small price provider module
- `tests/integration/test_web_api.py`
- `tests/unit/test_order_router.py`

Deliverables:

- no more `order.price or 0.0` fills;
- market order without price is rejected;
- limit behavior stays deterministic;
- tests for filled/rejected behavior.

### Subagent D: Trading API

Files:

- `src/web/api/trading.py`
- `tests/integration/test_web_api.py`

Deliverables:

- account endpoint reads account state;
- positions endpoint reads current net positions;
- orders/trades semantics remain historical;
- integration tests.

### Subagent E: Frontend compatibility

Files:

- `frontend/src/stores/dashboard.ts`
- `frontend/src/views/Dashboard.vue`
- `frontend/src/locales/en.ts`
- `frontend/src/locales/zh-CN.ts`

Deliverables:

- display new account fields;
- display new position fields if present;
- graceful fallback to `—` for missing optional fields;
- frontend tests/build remain green.

## Sequential integration step

The lead agent should integrate after subagents finish:

1. align repository method names with accounting service;
2. wire accounting service into `UnifiedOrderManager`;
3. wire price provider into strategy startup paper order manager;
4. update old tests that expected one position row per filled order;
5. run full backend and frontend verification;
6. browser-check Dashboard, Strategies, and Trades.

## Verification commands

Backend:

```bash
uv run pytest -v
uv run ruff check .
```

Frontend:

```bash
npm --prefix frontend exec vitest run
npm --prefix frontend run build
```

Manual browser checks:

- `/` Dashboard shows account and positions without fabricated fields.
- `/strategies` start action does not create zero-price market trades.
- `/trades` shows only executed trades.

## Locked decisions before implementation

1. `initial_equity` uses existing `backtest.initial_capital` for this milestone.
   - Defer a separate `paper.initial_capital` config key until paper, backtest, and live semantics need to diverge.
2. Flat positions are not returned by the trading positions API.
   - Implementation may keep `amount=0` rows initially, but Repository `get_open_positions()` should filter them out. Preserve history through orders, trades, and ledger until a dedicated position history exists.
3. `realized_pnl` remains price-difference PnL.
   - Track fees separately in `fees_paid`, persist fee per trade, and deduct fees from `cash_balance`. Add `net_realized_pnl` later if needed.
4. Paper market orders with no usable price are `rejected`.
   - Persist the rejected order, but create no trade, account mutation, or position mutation.
5. Synthetic shorts are allowed in paper mode for this milestone, including spot-like symbols such as `BTC-USDT`.
   - Document this as a simplified paper net-position model, not a claim that OKX spot supports real shorting. Specialize by instrument type later.
