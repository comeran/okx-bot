# Risk and Account Runtime Backend Design

## Goal

Add a backend-only runtime slice that makes paper-mode risk rejections explainable over WebSocket and keeps open paper positions/accounts marked to the latest runtime bar close.

## Scope

This slice includes:

- Broadcasting `risk_event` when the runtime order manager rejects an order at the risk gate.
- Persisting mark-to-market updates for an existing open position when a runtime bar arrives for that strategy and symbol.
- Broadcasting existing `positions` and `account` messages after mark-to-market updates.
- Tests for risk event order/payload and mark-to-market long/short/account behavior.

This slice excludes:

- Frontend UI changes for displaying `risk_event`.
- Persisted risk event history or new risk-event database tables.
- Rolling daily PnL recalculation.
- Circuit breakers, pause-all, manual unlock, notifications, channel subscriptions, or sequence numbers.
- Global price registry or cross-strategy batch mark-to-market.

## Existing Context

`UnifiedOrderManager.submit()` already builds an `Order`, runs `_passes_risk_gate()`, persists rejected orders, and invokes `on_order_update` for runtime broadcasts. `RiskManager.check_order()` returns `RiskCheckResult` with `passed` and a human-readable `reason`, but the order manager currently discards the reason.

`PaperAccountingService.process_filled_order()` persists filled orders into trades, positions, accounts, and cash ledger records. It currently sets account `unrealized_pnl` to `0.0` and computes `equity` as `cash_balance + open_position_cost_basis`. `PositionRecord` already has `mark_price` and `unrealized_pnl` fields.

The strategy API already wires `UnifiedOrderManager` with a repository, latest-price provider, and `on_order_update`, then broadcasts `orders`, `positions`, and `account` updates through the existing WebSocket manager.

## Risk Rejection Event Design

When `UnifiedOrderManager.submit()` rejects an order because the risk gate fails, it must:

1. Create the order with its generated `order_id`.
2. Run the risk check and retain the full result.
3. Mark the order as `rejected`.
4. Persist the rejected order.
5. Invoke a new optional `on_risk_event` callback.
6. Invoke the existing `on_order_update` callback.

The callback payload must be a dictionary with these fields:

```json
{
  "type": "risk_event",
  "strategy": "ma_cross_btc",
  "order_id": "ma_cross_btc-BTC-USDT-...-1",
  "symbol": "BTC-USDT",
  "side": "buy",
  "order_type": "market",
  "amount": 1.0,
  "price": null,
  "reason": "Order exceeds maximum position size",
  "reason_code": "max_position_exceeded",
  "timestamp": 1700000000000
}
```

`reason` should use the current `RiskManager` English reason string. `reason_code` should be a stable machine value derived from the reason:

- `Order exceeds maximum position size` -> `max_position_exceeded`
- `Daily loss exceeds maximum allowed loss` -> `daily_loss_exceeded`
- `Drawdown exceeds maximum allowed drawdown` -> `drawdown_exceeded`
- `Order requires a stop loss` -> `stop_loss_required`
- Any unknown non-empty reason -> `risk_rejected`

The API layer should broadcast this payload as-is. Machine-readable values such as `strategy`, `symbol`, `side`, `order_type`, and `reason_code` must remain untranslated.

## Mark-to-Market Design

Mark-to-market runs in the runtime bar flow before `strategy.on_bar(bar)` sees the bar. That ensures strategy code and risk checks read the latest paper equity for the current bar.

For each bar delivered to a strategy:

1. Look up the current open position for `strategy.name` and `bar.symbol` equivalent. The symbol comes from the strategy instance because `Bar` currently carries OHLCV values but not a symbol.
2. If there is no open position for that strategy/symbol, do nothing: do not create an account and do not broadcast account updates.
3. Use `bar.close` as the paper runtime mark price.
4. Compute unrealized PnL:
   - long: `(mark_price - entry_price) * amount`
   - short: `(entry_price - mark_price) * amount`
5. Persist the updated position with:
   - `mark_price = bar.close`
   - `unrealized_pnl = computed_unrealized_pnl`
   - `timestamp = bar.timestamp`
6. Update the existing account for that strategy with:
   - `unrealized_pnl = sum(unrealized_pnl for open positions in that strategy after the position update)`
   - `equity = cash_balance + open_position_cost_basis + unrealized_pnl`
   - `updated_at = bar.timestamp`
7. Broadcast only existing `positions` and `account` messages after a mark-to-market update.

`open_position_cost_basis` continues the current paper-accounting model: long positions add `abs(amount) * entry_price`, short positions subtract `abs(amount) * entry_price`. This avoids switching account models in this slice.

## Components

### Runtime risk event callback

`UnifiedOrderManager` should accept an optional callback such as:

```python
RiskEventCallback = Callable[[dict[str, object]], Awaitable[None]]
```

The order manager owns event construction because it has the order, risk result, strategy name, and timestamp in one place. The strategy API owns broadcasting because it already knows the WebSocket broadcaster.

### Mark-to-market service

Add a small backend component focused on paper mark-to-market accounting, for example `src/order/mark_to_market.py`. It should expose one public method that accepts repository, strategy name, symbol, mark price, and timestamp, then returns whether an update occurred.

This keeps mark-to-market logic out of `BotEngine` and avoids bloating `PaperAccountingService`, whose current responsibility is processing filled orders.

### BotEngine integration

`BotEngine` should accept an optional callback that runs before `strategy.on_bar(bar)`, for example:

```python
BeforeStrategyBarCallback = Callable[[object, Bar], Awaitable[None]]
```

The strategy API should provide this callback and use the repository associated with the strategy order manager. If no repository or position exists, the callback returns without side effects.

## WebSocket Behavior

Risk rejection broadcasts this sequence:

1. `risk_event`
2. `orders`
3. `positions`
4. `account`

The rejected order is already persisted before `risk_event` is broadcast, so consumers can associate the event with `order_id`.

Mark-to-market broadcasts this sequence only when an open position was updated:

1. `positions`
2. `account`

It does not broadcast `orders`, `trades`, or any new event type.

## Testing Strategy

Backend tests should drive the behavior at the smallest useful boundaries:

- Unit tests for reason-to-code mapping and `UnifiedOrderManager` risk rejection callback payload.
- Unit tests for mark-to-market long and short unrealized PnL formulas.
- Unit tests that account equity becomes `cash_balance + open_position_cost_basis + unrealized_pnl`.
- Integration tests through the strategies API showing:
  - a risk-rejected runtime order broadcasts `risk_event` before the normal trading state updates;
  - a runtime bar with an open position updates persisted position/account and broadcasts `positions` then `account`;
  - a runtime bar with no open position does not create account records or broadcast account updates.

Verification commands:

```bash
uv run pytest tests/unit/test_order_router.py tests/unit/test_paper_accounting.py tests/unit/test_engine.py tests/integration/test_web_api.py -v
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

## Open Decisions Resolved

- No frontend UI changes in this slice.
- No risk event persistence in this slice.
- No rolling daily PnL in this slice.
- `risk_event` includes both `reason` and `reason_code`.
- Mark-to-market uses latest runtime bar close as the paper mark price.
- Mark-to-market runs before `strategy.on_bar(bar)`.
- Mark-to-market updates only existing open positions for the strategy/symbol receiving the bar.
