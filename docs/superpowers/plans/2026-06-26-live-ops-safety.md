# Live Ops Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an operator kill switch, OKX private order/trade reconciliation, and Telegram risk notifications so live/demo operations can be stopped, audited, and alerted safely.

**Architecture:** The kill switch is persisted in SQLite and enforced at both strategy-start and order-submit boundaries. Reconciliation extends the OKX adapter with private order/trade reads, maps those snapshots into existing repository records, and emits persisted risk/divergence events. Telegram notification remains a side effect of the existing WebSocket risk-event path so trading logic never blocks on alert delivery.

**Tech Stack:** Python 3, FastAPI, SQLModel, SQLite, ccxt async OKX adapter, pytest, httpx, existing WebSocket runtime broadcaster.

---

## Non-negotiable safety rules

- Do not print OKX API keys, secrets, passphrases, Telegram tokens, `.env` values, or `data/settings.local.yaml` contents.
- Keep `exchange.demo` defaulting to `true` whenever adding or touching config defaults.
- Do not perform real live-money verification in this plan. Manual live-money verification only happens after demo-mode smoke tests pass and the user explicitly asks for real live mode.
- Do not commit or push unless the user explicitly asks. This plan intentionally uses local verification checkpoints instead of commit steps.

## Locked OKX private sync decisions

These decisions were confirmed in `/grill-me` on 2026-06-26 and supersede older reconciliation task text below if there is a conflict.

- The first OKX private sync endpoint is demo-mode-only and must refuse to run when `exchange.demo` is `false`.
- The first sync path writes idempotently immediately; do not add a separate read-only diff preview step.
- Exchange adapters return lightweight snapshots; the ops/sync layer converts snapshots to SQLModel records, performs upserts, and emits divergence events.
- Strategy attribution first matches local `OrderRecord` rows by `client_order_id` or `exchange_order_id`; unmatched exchange records use the reserved strategy namespace `"__exchange__"`.
- High-risk divergence automatically engages the kill switch; low-risk divergence only writes a persisted `risk_event`.
- For high-risk divergence, engage the kill switch first, save the risk event second, then do idempotent account/order/trade upserts.
- Account-level `AccountRecord.strategy` is always `"__exchange__"` because OKX account equity is account-level state, not strategy-level PnL.
- Trade/fill sync pulls the most recent N trades with `limit=100` by default and optional `since`; `exchange_trade_id` provides idempotency.
- Use one endpoint, `POST /api/ops/sync/private`, to sync account, open orders, and trades; the response returns per-section counts and a divergence summary.
- The private sync endpoint only requires `exchange.demo == true`; it does not require the kill switch to already be engaged.
- `exchange.demo` is both an API safety gate and an OKX adapter setting that enables ccxt OKX demo/sandbox behavior.
- Demo behavior is encapsulated in `OKXBaseAdapter(demo: bool = True)` and passed through by the spot/swap/future/option adapter subclasses.
- For unmatched exchange orders, generate a stable local `OrderRecord.order_id` such as `okx:{exchange_order_id}` and preserve the raw OKX id in `exchange_order_id`.
- Map OKX/ccxt order statuses into the project's existing `OrderStatus` values before writing `OrderRecord.status`.
- Trade fees use ccxt standard `fee.cost`; missing or non-standard fee payloads write `0.0` until a multi-currency fee model exists.
- The sync request supports optional `symbols: list[str] = []`; an empty list syncs account-level data, and a non-empty list syncs each symbol.
- Dedupe divergence events within a single sync request only; do not add cross-run dedupe schema in the first version.
- High-risk divergence notifications use the existing Telegram risk-notification path; low-risk divergence is persisted only.
- Demo smoke requires automated tests first, then a manual OKX demo private API smoke with `exchange.demo=true`; do not perform real live-money verification in this plan.
- First-version settings/UI work exposes only `exchange.demo` and `exchange.market_type` needed for demo/private sync. Real live enablement is a separate later task after demo smoke passes.

## Current-state facts this plan depends on

- `src/order/manager.py` currently has no live-specific constructor arguments. Add kill-switch support with new optional callbacks only; do not assume `live_safeguards`, `live_state_refresher`, `allow_live_open_orders`, or `live_max_order_notional` exist.
- `src/web/api/trading.py` exposes persisted trading state. Keep private sync under `src/web/api/ops.py` at `POST /api/ops/sync/private`, not under the trading read endpoints.
- `src/web/app.py` currently includes `strategies`, `backtest`, `trading`, `market`, and `settings`. Add a new `ops` router.
- `src/exchange/live_sync.py` is not currently present as a source file in this checkout. Create it with account/position snapshot persistence first, then extend it for order/trade reconciliation.
- `src/notify/telegram.py` already has `TelegramNotifier.send()` and `format_risk_alert()`; wire it in rather than replacing it.
- `src/core/config.py` is the source of config dataclass defaults. Implementation must first align `exchange.demo`, `exchange.market_type`, `risk.allow_live_open_orders`, and `risk.live_max_order_notional` there before kill-switch or live reconciliation tasks.
- `src/exchange/live_sync.py` imports `AccountSnapshot` and `PositionSnapshot`; implementation must first define those dataclasses in `src/core/types.py` before extending live sync.

## File structure

### Modify

- `src/core/config.py`
  - Add live/demo exchange defaults and live-risk defaults used by runtime settings and settings API.

- `src/core/types.py`
  - Add `AccountSnapshot` and `PositionSnapshot` dataclasses consumed by live private-state sync.

- `src/data/models.py`
  - Add `KillSwitchRecord` for persistent emergency-stop state.
  - Add `RiskEventRecord` for risk/divergence/audit events.
  - Add exchange identity fields to order/trade records for reconciliation.

- `src/data/repository.py`
  - Add kill-switch get/set methods.
  - Add risk-event persistence methods.
  - Add idempotent `upsert_order()` and `upsert_trade()` methods.
  - Add order/trade lookup helpers used by reconciliation.
  - Extend SQLite migrations for new columns on existing tables.

- `src/order/manager.py`
  - Add optional kill-switch callback to `UnifiedOrderManager`.
  - Reject new orders before the normal risk gate when the kill switch is engaged.
  - Emit `reason_code="kill_switch_engaged"` through the existing risk-event callback.

- `src/web/api/strategies.py`
  - Block `POST /api/strategies/{name}/start` when the persisted kill switch is engaged.
  - Pass the kill-switch checker into `create_order_manager()`.
  - Persist risk events in `broadcast_risk_event()`.
  - Build and call `TelegramNotifier` from the existing `broadcast_risk_event()` path.

- `src/web/app.py`
  - Include the new `ops` router under `/api/ops`.

- `src/exchange/base.py`
  - Add `demo: bool = True` OKX adapter configuration and keep demo behavior encapsulated in the adapter.
  - Add private OKX read methods for account snapshots, open orders, and recent trade history.
  - Map ccxt private payloads into lightweight snapshot dataclasses, not SQLModel records.

- `src/web/api/trading.py`
  - Continue exposing persisted account/order/trade state only; private sync writes should be triggered from the ops router.

- `src/web/api/settings.py`
  - Add `POST /api/settings/notify/test` endpoint.
  - Reuse `_merge_secret()` and `_mask_secret()`; never return Telegram token plaintext.

### Create

- `src/web/api/ops.py`
  - New router for `GET /api/ops/kill-switch` and `PUT /api/ops/kill-switch`.
  - Add demo-only `POST /api/ops/sync/private` for account, open-order, and recent-trade sync.
  - Broadcast kill-switch state changes to WebSocket clients.
  - Persist a risk event for activation/deactivation and high-risk private-sync divergences.

- `src/exchange/live_sync.py`
  - Add live account/position snapshot persistence service.
  - Add order/trade reconciliation service methods.
  - Persist remote orders/trades idempotently.
  - Detect and report divergence records.

### Tests

- `tests/unit/test_config.py`
  - Runtime config defaults keep `exchange.demo` enabled and accept live exchange/risk keys.

- `tests/unit/test_core_types.py`
  - Account and position snapshot dataclasses expose fields used by live sync.

- `tests/unit/test_repository.py`
  - Kill-switch persistence.
  - Risk-event persistence.
  - Idempotent order/trade upserts.

- `tests/unit/test_order_manager_kill_switch.py`
  - Kill switch rejects before router submission.
  - Risk event uses `kill_switch_engaged`.

- `tests/integration/test_web_api.py`
  - Kill-switch API get/put.
  - Strategy start blocked while kill switch is engaged.
  - Demo-only private sync endpoint behavior at `POST /api/ops/sync/private`.
  - Telegram test endpoint masks secrets and calls notifier without leaking token.

- `tests/integration/test_exchange_adapter.py`
  - OKX private fetch methods map ccxt order/trade payloads correctly.

- `tests/unit/test_live_sync_reconciliation.py`
  - Reconciliation persists remote open orders.
  - Reconciliation persists remote trades idempotently.
  - Reconciliation returns divergence events for stale local open orders.

---

## Task 0: Align live config and snapshot types

**Files:**
- Modify: `src/core/config.py`
- Modify: `src/core/types.py`
- Test: `tests/unit/test_config.py`
- Test: `tests/unit/test_core_types.py`

- [ ] **Step 1: Write failing config tests**

Create `tests/unit/test_config.py` with these tests. Use `tmp_path` so no real local settings file or secret-bearing config is read.

```python
from src.core.config import AppConfig, load_config


def test_app_config_defaults_keep_demo_enabled():
    config = AppConfig()

    assert config.exchange.demo is True
    assert config.exchange.market_type == "spot"
    assert config.risk.allow_live_open_orders is False
    assert config.risk.live_max_order_notional == 0.0


def test_load_config_accepts_live_exchange_and_risk_keys(tmp_path):
    config_path = tmp_path / "settings.yaml"
    config_path.write_text(
        """
mode: live
exchange:
  api_key: ${OKX_API_KEY}
  secret: ${OKX_SECRET}
  passphrase: ${OKX_PASSPHRASE}
  market_type: swap
  demo: true
risk:
  max_daily_loss_pct: 0.03
  max_drawdown_pct: 0.12
  max_total_position_pct: 0.7
  allow_live_open_orders: false
  live_max_order_notional: 250.0
""".strip(),
        encoding="utf-8",
    )

    config = load_config(str(config_path))

    assert config.mode == "live"
    assert config.exchange.market_type == "swap"
    assert config.exchange.demo is True
    assert config.risk.allow_live_open_orders is False
    assert config.risk.live_max_order_notional == 250.0
```

- [ ] **Step 2: Write failing snapshot type tests**

Create `tests/unit/test_core_types.py` with these tests.

```python
from src.core.types import AccountSnapshot, PositionSide, PositionSnapshot


def test_account_snapshot_exposes_live_sync_fields():
    snapshot = AccountSnapshot(
        initial_equity=1000.0,
        cash_balance=900.0,
        equity=1010.0,
        realized_pnl=10.0,
        unrealized_pnl=0.0,
        daily_pnl=10.0,
        fees_paid=1.5,
        timestamp=1700000000000,
    )

    assert snapshot.cash_balance == 900.0
    assert snapshot.equity == 1010.0
    assert snapshot.timestamp == 1700000000000


def test_position_snapshot_exposes_live_sync_fields():
    snapshot = PositionSnapshot(
        symbol="BTC/USDT:USDT",
        side=PositionSide.LONG,
        amount=0.5,
        entry_price=50000.0,
        mark_price=50100.0,
        realized_pnl=5.0,
        unrealized_pnl=50.0,
        leverage=2,
        timestamp=1700000000000,
    )

    assert snapshot.symbol == "BTC/USDT:USDT"
    assert snapshot.side == PositionSide.LONG
    assert snapshot.unrealized_pnl == 50.0
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run:

```bash
pytest tests/unit/test_config.py tests/unit/test_core_types.py -v
```

Expected: FAIL because `ExchangeConfig.demo`, `ExchangeConfig.market_type`, `RiskConfig.allow_live_open_orders`, `RiskConfig.live_max_order_notional`, `AccountSnapshot`, and `PositionSnapshot` do not exist yet.

- [ ] **Step 4: Add live config defaults**

In `src/core/config.py`, update the dataclasses to include the runtime/settings fields. Keep `demo` defaulting to `True`.

```python
@dataclass
class ExchangeConfig:
    api_key: str = ""
    secret: str = ""
    passphrase: str = ""
    market_type: str = "spot"
    demo: bool = True
```

```python
@dataclass
class RiskConfig:
    max_daily_loss_pct: float = 0.05
    max_drawdown_pct: float = 0.15
    max_total_position_pct: float = 0.8
    allow_live_open_orders: bool = False
    live_max_order_notional: float = 0.0
```

- [ ] **Step 5: Add live snapshot dataclasses**

In `src/core/types.py`, add these dataclasses after `Position`.

```python
@dataclass
class AccountSnapshot:
    initial_equity: float
    cash_balance: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    daily_pnl: float
    fees_paid: float
    timestamp: int


@dataclass
class PositionSnapshot:
    symbol: str
    side: PositionSide
    amount: float
    entry_price: float
    mark_price: float
    realized_pnl: float
    unrealized_pnl: float
    leverage: int
    timestamp: int
```

- [ ] **Step 6: Run the prerequisite tests to verify they pass**

Run:

```bash
pytest tests/unit/test_config.py tests/unit/test_core_types.py -v
```

Expected: PASS.

- [ ] **Step 7: Run the existing settings API tests if present**

Run:

```bash
pytest tests/integration/test_web_api.py -v
```

Expected: PASS. If this file has unrelated failures, capture the failing test names and fix only failures caused by the config/type alignment change before moving to Task 1.

---

## Task 1: Persist kill-switch and risk-event audit data

**Files:**
- Modify: `src/data/models.py`
- Modify: `src/data/repository.py`
- Test: `tests/unit/test_repository.py`

- [ ] **Step 1: Write failing repository tests**

Append these tests to `tests/unit/test_repository.py`. If the file already has imports for `Repository`, `create_engine`, or `SQLModel`, reuse them instead of duplicating.

```python
from sqlmodel import SQLModel, create_engine

from src.data.models import OrderRecord, TradeRecord
from src.data.repository import Repository


def create_memory_repository() -> Repository:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Repository(engine=engine)


def test_kill_switch_defaults_to_disengaged():
    repository = create_memory_repository()

    state = repository.get_kill_switch()

    assert state.engaged is False
    assert state.reason == ""
    assert state.updated_at == 0


def test_set_kill_switch_persists_latest_state():
    repository = create_memory_repository()

    repository.set_kill_switch(True, "operator emergency stop", 1700000000000)
    repository.set_kill_switch(False, "demo reset", 1700000005000)

    state = repository.get_kill_switch()
    assert state.engaged is False
    assert state.reason == "demo reset"
    assert state.updated_at == 1700000005000


def test_save_risk_event_persists_payload_fields():
    repository = create_memory_repository()

    saved = repository.save_risk_event(
        {
            "type": "risk_event",
            "strategy": "ma_cross",
            "reason_code": "kill_switch_engaged",
            "reason": "Kill switch engaged",
            "symbol": "BTC/USDT",
            "timestamp": 1700000000000,
        }
    )

    events = repository.get_risk_events()
    assert len(events) == 1
    assert events[0].id == saved.id
    assert events[0].event_type == "risk_event"
    assert events[0].strategy == "ma_cross"
    assert events[0].reason_code == "kill_switch_engaged"
    assert events[0].payload["symbol"] == "BTC/USDT"


def test_upsert_order_updates_existing_exchange_order():
    repository = create_memory_repository()

    repository.upsert_order(
        OrderRecord(
            order_id="local-1",
            exchange_order_id="okx-1",
            client_order_id="client-1",
            strategy="ma_cross",
            symbol="BTC/USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=50000.0,
            status="pending",
            fill_price=0.0,
            timestamp=1700000000000,
            updated_at=1700000000000,
        )
    )
    repository.upsert_order(
        OrderRecord(
            order_id="local-2",
            exchange_order_id="okx-1",
            client_order_id="client-1",
            strategy="ma_cross",
            symbol="BTC/USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=50000.0,
            status="filled",
            fill_price=50100.0,
            timestamp=1700000000000,
            updated_at=1700000005000,
        )
    )

    orders = repository.get_orders()
    assert len(orders) == 1
    assert orders[0].order_id == "local-2"
    assert orders[0].exchange_order_id == "okx-1"
    assert orders[0].status == "filled"
    assert orders[0].fill_price == 50100.0


def test_upsert_trade_deduplicates_exchange_trade_id():
    repository = create_memory_repository()

    trade = TradeRecord(
        exchange_trade_id="trade-1",
        order_id="okx-1",
        strategy="ma_cross",
        symbol="BTC/USDT",
        side="buy",
        amount=1.0,
        price=50000.0,
        fee=1.2,
        timestamp=1700000000000,
    )
    repository.upsert_trade(trade)
    repository.upsert_trade(trade)

    trades = repository.get_trades("ma_cross")
    assert len(trades) == 1
    assert trades[0].exchange_trade_id == "trade-1"
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
pytest tests/unit/test_repository.py -q
```

Expected: FAIL because `KillSwitchRecord`, `RiskEventRecord`, `get_kill_switch()`, `set_kill_switch()`, `save_risk_event()`, `get_risk_events()`, `upsert_order()`, `upsert_trade()`, and new model fields do not exist yet.

- [ ] **Step 3: Add model fields and records**

In `src/data/models.py`, update imports and model definitions as follows.

```python
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel
```

Add these new fields to `TradeRecord`:

```python
class TradeRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    exchange_trade_id: str = Field(default="", index=True)
    order_id: str = Field(default="", index=True)
    strategy: str
    symbol: str
    side: str
    amount: float
    price: float
    fee: float
    timestamp: int
```

Add these new fields to `OrderRecord`:

```python
class OrderRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    order_id: str = Field(index=True)
    exchange_order_id: str = Field(default="", index=True)
    client_order_id: str = Field(default="", index=True)
    strategy: str
    symbol: str
    side: str
    type: str
    amount: float
    price: float
    status: str
    fill_price: float
    timestamp: int
    updated_at: int = Field(default=0, index=True)
```

Add these models after `PositionRecord`:

```python
class KillSwitchRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    engaged: bool = False
    reason: str = ""
    updated_at: int = Field(index=True)


class RiskEventRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    event_type: str = Field(index=True)
    strategy: str = Field(default="", index=True)
    reason_code: str = Field(default="", index=True)
    reason: str = ""
    timestamp: int = Field(index=True)
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
```

- [ ] **Step 4: Add repository imports and methods**

In `src/data/repository.py`, include the new models in the existing import block:

```python
from src.data.models import (
    AccountRecord,
    BacktestResultRecord,
    CashLedgerRecord,
    KillSwitchRecord,
    KlineCache,
    OrderRecord,
    PositionRecord,
    RiskEventRecord,
    StrategyConfigRecord,
    TradeRecord,
)
```

Add these methods inside `Repository`, near the existing order/trade methods:

```python
    def get_kill_switch(self) -> KillSwitchRecord:
        with Session(self.engine) as session:
            state = session.exec(select(KillSwitchRecord).order_by(KillSwitchRecord.id)).first()
            if state is not None:
                return state
            return KillSwitchRecord(engaged=False, reason="", updated_at=0)

    def set_kill_switch(
        self,
        engaged: bool,
        reason: str,
        updated_at: int,
    ) -> KillSwitchRecord:
        with Session(self.engine) as session:
            state = session.exec(select(KillSwitchRecord).order_by(KillSwitchRecord.id)).first()
            if state is None:
                state = KillSwitchRecord(
                    engaged=engaged,
                    reason=reason,
                    updated_at=updated_at,
                )
            else:
                state.engaged = engaged
                state.reason = reason
                state.updated_at = updated_at
            session.add(state)
            session.commit()
            session.refresh(state)
            return state

    def save_risk_event(self, payload: dict[str, object]) -> RiskEventRecord:
        event = RiskEventRecord(
            event_type=str(payload.get("type", "risk_event")),
            strategy=str(payload.get("strategy", "")),
            reason_code=str(payload.get("reason_code", "")),
            reason=str(payload.get("reason", "")),
            timestamp=int(payload.get("timestamp", 0) or 0),
            payload=dict(payload),
        )
        with Session(self.engine) as session:
            session.add(event)
            session.commit()
            session.refresh(event)
            return event

    def get_risk_events(self, strategy: str | None = None) -> list[RiskEventRecord]:
        statement = select(RiskEventRecord)
        if strategy is not None:
            statement = statement.where(RiskEventRecord.strategy == strategy)
        statement = statement.order_by(RiskEventRecord.timestamp.desc())
        with Session(self.engine) as session:
            return list(session.exec(statement).all())

    def upsert_order(self, order: OrderRecord) -> OrderRecord:
        with Session(self.engine) as session:
            existing = None
            if order.exchange_order_id:
                existing = session.exec(
                    select(OrderRecord).where(
                        OrderRecord.exchange_order_id == order.exchange_order_id
                    )
                ).first()
            if existing is None:
                existing = session.exec(
                    select(OrderRecord).where(OrderRecord.order_id == order.order_id)
                ).first()
            if existing is None:
                session.add(order)
                session.commit()
                session.refresh(order)
                return order

            existing.order_id = order.order_id
            existing.exchange_order_id = order.exchange_order_id
            existing.client_order_id = order.client_order_id
            existing.strategy = order.strategy
            existing.symbol = order.symbol
            existing.side = order.side
            existing.type = order.type
            existing.amount = order.amount
            existing.price = order.price
            existing.status = order.status
            existing.fill_price = order.fill_price
            existing.timestamp = order.timestamp
            existing.updated_at = order.updated_at
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing

    def get_open_orders(self, strategy: str | None = None) -> list[OrderRecord]:
        statement = select(OrderRecord).where(OrderRecord.status == "pending")
        if strategy is not None:
            statement = statement.where(OrderRecord.strategy == strategy)
        with Session(self.engine) as session:
            return list(session.exec(statement).all())

    def upsert_trade(self, trade: TradeRecord) -> TradeRecord:
        with Session(self.engine) as session:
            existing = None
            if trade.exchange_trade_id:
                existing = session.exec(
                    select(TradeRecord).where(
                        TradeRecord.exchange_trade_id == trade.exchange_trade_id
                    )
                ).first()
            if existing is None:
                session.add(trade)
                session.commit()
                session.refresh(trade)
                return trade

            existing.order_id = trade.order_id
            existing.strategy = trade.strategy
            existing.symbol = trade.symbol
            existing.side = trade.side
            existing.amount = trade.amount
            existing.price = trade.price
            existing.fee = trade.fee
            existing.timestamp = trade.timestamp
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing
```

Keep the existing `save_order()` and `save_trade()` methods for compatibility. Later code should use `upsert_order()` and `upsert_trade()` when data came from OKX reconciliation.

- [ ] **Step 5: Add SQLite migrations for new columns**

Extend `_migrate_sqlite_schema()` in `src/data/repository.py` so it separately migrates `positionrecord`, `orderrecord`, and `traderecord`.

Replace the current body inside `with engine.begin() as connection:` with:

```python
            position_columns = {
                row[1]
                for row in connection.execute(text("PRAGMA table_info(positionrecord)"))
            }
            position_migrations = {
                "mark_price": "ALTER TABLE positionrecord ADD COLUMN mark_price FLOAT",
                "realized_pnl": "ALTER TABLE positionrecord ADD COLUMN realized_pnl FLOAT DEFAULT 0.0",
                "unrealized_pnl": "ALTER TABLE positionrecord ADD COLUMN unrealized_pnl FLOAT DEFAULT 0.0",
            }
            for column, statement in position_migrations.items():
                if position_columns and column not in position_columns:
                    connection.execute(text(statement))

            order_columns = {
                row[1]
                for row in connection.execute(text("PRAGMA table_info(orderrecord)"))
            }
            order_migrations = {
                "exchange_order_id": "ALTER TABLE orderrecord ADD COLUMN exchange_order_id VARCHAR DEFAULT ''",
                "client_order_id": "ALTER TABLE orderrecord ADD COLUMN client_order_id VARCHAR DEFAULT ''",
                "updated_at": "ALTER TABLE orderrecord ADD COLUMN updated_at INTEGER DEFAULT 0",
            }
            for column, statement in order_migrations.items():
                if order_columns and column not in order_columns:
                    connection.execute(text(statement))

            trade_columns = {
                row[1]
                for row in connection.execute(text("PRAGMA table_info(traderecord)"))
            }
            trade_migrations = {
                "exchange_trade_id": "ALTER TABLE traderecord ADD COLUMN exchange_trade_id VARCHAR DEFAULT ''",
                "order_id": "ALTER TABLE traderecord ADD COLUMN order_id VARCHAR DEFAULT ''",
            }
            for column, statement in trade_migrations.items():
                if trade_columns and column not in trade_columns:
                    connection.execute(text(statement))
```

`SQLModel.metadata.create_all(engine)` will create the new `killswitchrecord` and `riskeventrecord` tables on startup; no manual `CREATE TABLE` migration is needed for those new tables.

- [ ] **Step 6: Run repository tests**

Run:

```bash
pytest tests/unit/test_repository.py -q
```

Expected: PASS.

---

## Task 2: Enforce kill switch in `UnifiedOrderManager.submit()`

**Files:**
- Modify: `src/order/manager.py`
- Create: `tests/unit/test_order_manager_kill_switch.py`

- [ ] **Step 1: Write failing kill-switch order-manager tests**

Create `tests/unit/test_order_manager_kill_switch.py`:

```python
from unittest.mock import AsyncMock

import pytest

from src.core.types import Order, OrderSide, OrderStatus, OrderType
from src.order.manager import UnifiedOrderManager


class RecordingRepository:
    def __init__(self):
        self.orders = []

    def save_order(self, order):
        self.orders.append(order)
        return order


@pytest.mark.asyncio
async def test_kill_switch_rejects_order_before_router_submit():
    router = AsyncMock()
    repository = RecordingRepository()
    risk_events = []

    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        on_risk_event=lambda payload: risk_events.append(payload),
        kill_switch_checker=lambda: True,
    )

    order = await manager.submit(
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=1.0,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.REJECTED
    router.submit.assert_not_awaited()
    assert repository.orders[0].status == "rejected"
    assert risk_events[0]["reason_code"] == "kill_switch_engaged"
    assert risk_events[0]["reason"] == "Kill switch engaged"


@pytest.mark.asyncio
async def test_disengaged_kill_switch_allows_router_submit():
    submitted = Order(
        id="submitted-1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=1.0,
        status=OrderStatus.FILLED,
        fill_price=50000.0,
        fill_time=1700000000000,
    )
    router = AsyncMock()
    router.submit.return_value = submitted
    repository = RecordingRepository()

    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        kill_switch_checker=lambda: False,
    )

    order = await manager.submit(
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=1.0,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.FILLED
    router.submit.assert_awaited_once()
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
pytest tests/unit/test_order_manager_kill_switch.py -q
```

Expected: FAIL because `UnifiedOrderManager.__init__()` does not accept `kill_switch_checker`.

- [ ] **Step 3: Add kill-switch constructor support**

In `src/order/manager.py`, update the constructor signature:

```python
        risk_manager: Any | None = None,
        price_provider: Callable[[str], float | None] | None = None,
        kill_switch_checker: Callable[[], bool] | None = None,
    ) -> None:
```

Add this assignment in the constructor body:

```python
        self.kill_switch_checker = kill_switch_checker
```

- [ ] **Step 4: Add risk reason mapping**

Update `risk_reason_code()` in `src/order/manager.py`:

```python
def risk_reason_code(reason: str) -> str:
    return {
        "Order exceeds maximum position size": "max_position_exceeded",
        "Daily loss exceeds maximum allowed loss": "daily_loss_exceeded",
        "Drawdown exceeds maximum allowed drawdown": "drawdown_exceeded",
        "Order requires a stop loss": "stop_loss_required",
        "Kill switch engaged": "kill_switch_engaged",
    }.get(reason, "risk_rejected")
```

- [ ] **Step 5: Reject orders before normal risk checks**

In `UnifiedOrderManager.submit()`, immediately after the `Order(...)` object is created and before `_check_risk_gate()`, add:

```python
        if self.kill_switch_checker is not None and self.kill_switch_checker():
            order.status = OrderStatus.REJECTED
            timestamp = self.timestamp_ms()
            risk_result = RiskGateResult(
                passed=False,
                reason="Kill switch engaged",
                order_value=0.0,
                effective_price=price,
            )
            self._persist_order(order, strategy_name, timestamp=timestamp)
            try:
                if self.on_risk_event is not None:
                    await self.on_risk_event(
                        self._risk_event_payload(order, strategy_name, risk_result, timestamp)
                    )
            finally:
                if self.on_order_update is not None:
                    await self.on_order_update(strategy_name)
            return order
```

- [ ] **Step 6: Run kill-switch order tests**

Run:

```bash
pytest tests/unit/test_order_manager_kill_switch.py -q
```

Expected: PASS.

- [ ] **Step 7: Run existing order tests**

Run:

```bash
pytest tests/unit/test_order_router.py tests/unit/test_paper_accounting.py -q
```

Expected: PASS.

---

## Task 3: Add kill-switch API and block strategy starts

**Files:**
- Create: `src/web/api/ops.py`
- Modify: `src/web/app.py`
- Modify: `src/web/api/strategies.py`
- Test: `tests/integration/test_web_api.py`

- [ ] **Step 1: Write failing API tests**

Append these tests to `tests/integration/test_web_api.py`. Adapt the local `client` fixture name if the file already defines one.

```python
from fastapi.testclient import TestClient

from src.web.app import create_app


def test_kill_switch_api_defaults_to_disengaged():
    client = TestClient(create_app())

    response = client.get("/api/ops/kill-switch")

    assert response.status_code == 200
    assert response.json()["engaged"] is False
    assert response.json()["reason"] == ""


def test_kill_switch_api_persists_engaged_state():
    client = TestClient(create_app())

    response = client.put(
        "/api/ops/kill-switch",
        json={"engaged": True, "reason": "operator emergency stop"},
    )

    assert response.status_code == 200
    assert response.json()["engaged"] is True
    assert response.json()["reason"] == "operator emergency stop"
    assert client.get("/api/ops/kill-switch").json()["engaged"] is True


def test_strategy_start_blocked_when_kill_switch_engaged():
    client = TestClient(create_app())
    client.put(
        "/api/ops/kill-switch",
        json={"engaged": True, "reason": "operator emergency stop"},
    )

    response = client.post("/api/strategies/ma_cross/start")

    assert response.status_code == 423
    assert response.json()["detail"] == "Kill switch engaged"
```

If existing integration tests use a patched temporary database, keep that pattern. These tests must not write to the user's real `data/bot.db`.

- [ ] **Step 2: Run the failing API tests**

Run:

```bash
pytest tests/integration/test_web_api.py -q
```

Expected: FAIL because `/api/ops/kill-switch` does not exist and strategy start does not check kill switch.

- [ ] **Step 3: Create `ops` router**

Create `src/web/api/ops.py`:

```python
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import APIRouter
from pydantic import BaseModel

from src.data.models import KillSwitchRecord
from src.data.repository import Repository

RuntimeBroadcaster = Callable[[dict[str, object]], Awaitable[None]]


class KillSwitchUpdate(BaseModel):
    engaged: bool
    reason: str = ""


def current_timestamp_ms() -> int:
    return int(time.time() * 1000)


def serialize_kill_switch(state: KillSwitchRecord) -> dict[str, object]:
    return {
        "engaged": state.engaged,
        "reason": state.reason,
        "updated_at": state.updated_at,
    }


def create_router(broadcast: RuntimeBroadcaster | None = None) -> APIRouter:
    router = APIRouter()

    @router.get("/kill-switch")
    async def get_kill_switch() -> dict[str, object]:
        return serialize_kill_switch(Repository().get_kill_switch())

    @router.put("/kill-switch")
    async def update_kill_switch(update: KillSwitchUpdate) -> dict[str, object]:
        repository = Repository()
        timestamp = current_timestamp_ms()
        state = repository.set_kill_switch(update.engaged, update.reason, timestamp)
        event = {
            "type": "risk_event",
            "strategy": "",
            "reason_code": "kill_switch_engaged" if update.engaged else "kill_switch_disengaged",
            "reason": "Kill switch engaged" if update.engaged else "Kill switch disengaged",
            "detail": update.reason,
            "timestamp": timestamp,
        }
        repository.save_risk_event(event)
        message = {
            "type": "kill_switch",
            **serialize_kill_switch(state),
        }
        if broadcast is not None:
            await broadcast(message)
            await broadcast(event)
        return serialize_kill_switch(state)

    return router
```

- [ ] **Step 4: Register ops router in app**

In `src/web/app.py`, update the import line:

```python
from src.web.api import backtest, market, ops, settings, strategies, trading
```

Add this include before settings:

```python
    app.include_router(ops.create_router(ws_manager.broadcast), prefix="/api/ops", tags=["ops"])
```

- [ ] **Step 5: Block strategy start and pass kill switch into manager**

In `src/web/api/strategies.py`, add this helper inside `create_router()` near `strategy_exists()`:

```python
    def kill_switch_engaged() -> bool:
        return Repository().get_kill_switch().engaged
```

In `start_strategy()`, after validating `strategy_exists(name)` and before acquiring/inside the lifecycle lock, add:

```python
        if kill_switch_engaged():
            raise HTTPException(status_code=423, detail="Kill switch engaged")
```

In the `create_order_manager(...)` call inside `start_strategy()`, add:

```python
                                kill_switch_checker=kill_switch_engaged,
```

Update the `create_order_manager()` function signature to accept and pass the checker:

```python
    kill_switch_checker: Callable[[], bool] | None = None,
) -> UnifiedOrderManager:
```

Add the argument to `UnifiedOrderManager(...)`:

```python
        kill_switch_checker=kill_switch_checker,
```

- [ ] **Step 6: Run API tests**

Run:

```bash
pytest tests/integration/test_web_api.py -q
```

Expected: PASS.

---

## Task 4: Add Telegram notification wiring for risk events and test sends

**Files:**
- Modify: `src/web/api/strategies.py`
- Modify: `src/web/api/settings.py`
- Test: `tests/integration/test_web_api.py`
- Existing helper: `src/notify/telegram.py`

- [ ] **Step 1: Write failing Telegram API tests**

Append to `tests/integration/test_web_api.py`:

```python
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from src.web.app import create_app


def test_notify_test_endpoint_requires_configured_telegram_settings():
    client = TestClient(create_app())

    response = client.post("/api/settings/notify/test")

    assert response.status_code == 400
    assert response.json()["detail"] == "Telegram settings are not configured"


def test_notify_test_endpoint_sends_without_returning_token():
    client = TestClient(create_app())
    client.put(
        "/api/settings",
        json={
            "mode": "backtest",
            "exchange": {
                "api_key": "",
                "secret": "",
                "passphrase": "",
                "market_type": "spot",
                "demo": True,
            },
            "backtest": {
                "initial_capital": 100000,
                "fee_rate": 0.0005,
                "slippage": 0.001,
                "data_cache_dir": "./data",
            },
            "risk": {
                "max_daily_loss_pct": 0.05,
                "max_drawdown_pct": 0.15,
                "max_total_position_pct": 0.8,
                "allow_live_open_orders": False,
                "live_max_order_notional": 0.0,
            },
            "notify": {
                "telegram_bot_token": "123456:telegram-token",
                "telegram_chat_id": "chat-1",
            },
            "web": {"host": "0.0.0.0", "port": 8080},
        },
    )

    with patch("src.web.api.settings.TelegramNotifier") as notifier_cls:
        notifier = notifier_cls.return_value
        notifier.send = AsyncMock()
        response = client.post("/api/settings/notify/test")

    assert response.status_code == 200
    assert response.json() == {"status": "sent"}
    notifier.send.assert_awaited_once()
    assert "telegram-token" not in response.text
```

- [ ] **Step 2: Run failing Telegram tests**

Run:

```bash
pytest tests/integration/test_web_api.py -q
```

Expected: FAIL because `/api/settings/notify/test` does not exist.

- [ ] **Step 3: Add notifier factory in settings router**

In `src/web/api/settings.py`, update imports:

```python
from fastapi import APIRouter, HTTPException
from src.notify.telegram import TelegramNotifier
```

Inside `create_router()`, add this endpoint after `update_settings()`:

```python
    @router.post("/notify/test")
    async def send_test_notification() -> dict[str, str]:
        if not settings.notify.telegram_bot_token or not settings.notify.telegram_chat_id:
            raise HTTPException(
                status_code=400,
                detail="Telegram settings are not configured",
            )
        notifier = TelegramNotifier(
            settings.notify.telegram_bot_token,
            settings.notify.telegram_chat_id,
        )
        await notifier.send("OKX Bot test notification")
        return {"status": "sent"}
```

This endpoint must never return token text.

- [ ] **Step 4: Wire Telegram into risk-event broadcast**

In `src/web/api/strategies.py`, add import:

```python
from src.notify.telegram import TelegramNotifier
```

Add this helper near `create_risk_manager()`:

```python
def create_telegram_notifier() -> TelegramNotifier | None:
    notify = load_runtime_settings().notify
    if not notify.telegram_bot_token or not notify.telegram_chat_id:
        return None
    return TelegramNotifier(notify.telegram_bot_token, notify.telegram_chat_id)
```

Replace `broadcast_risk_event()` with:

```python
    async def broadcast_risk_event(payload: dict[str, object]) -> None:
        repository = Repository()
        repository.save_risk_event(payload)
        if broadcast is not None:
            await broadcast(payload)
        notifier = create_telegram_notifier()
        if notifier is None:
            return
        reason_code = str(payload.get("reason_code", "risk_event"))
        detail = str(payload.get("reason", ""))
        symbol = payload.get("symbol")
        strategy = payload.get("strategy")
        if strategy:
            detail = f"Strategy: {strategy}\n{detail}"
        if symbol:
            detail = f"Symbol: {symbol}\n{detail}"
        try:
            await notifier.send(notifier.format_risk_alert(reason_code, detail))
        except Exception:
            return
```

Rationale: Telegram failure must not prevent WebSocket broadcast, order rejection, or strategy lifecycle updates.

- [ ] **Step 5: Avoid double-persisting ops events**

`src/web/api/ops.py` already saves its own risk event because it does not call `strategies.broadcast_risk_event()`. Keep that separate. Do not import `strategies.create_router()` into `ops.py`.

- [ ] **Step 6: Run Telegram and web tests**

Run:

```bash
pytest tests/integration/test_web_api.py tests/unit/test_telegram.py -q
```

Expected: PASS.

---

## Task 5: Add OKX private order/trade fetch methods

> Locked decision override: include account snapshot reads, recent trade reads with default `limit=100`, and `OKXBaseAdapter(demo: bool = True)`. Adapter methods return lightweight snapshots only; do not return SQLModel records.

**Files:**
- Modify: `src/core/types.py`
- Modify: `src/exchange/base.py`
- Test: `tests/integration/test_exchange_adapter.py`

- [ ] **Step 1: Write failing adapter tests**

Append to `tests/integration/test_exchange_adapter.py`:

```python
@pytest.mark.asyncio
async def test_fetch_open_orders_maps_ccxt_orders():
    with patch("src.exchange.base.ccxt") as ccxt:
        exchange = AsyncMock()
        exchange.fetch_open_orders.return_value = [
            {
                "id": "okx-order-1",
                "clientOrderId": "client-1",
                "symbol": "BTC/USDT",
                "side": "buy",
                "type": "limit",
                "amount": 1.0,
                "price": 50000.0,
                "status": "open",
                "average": None,
                "timestamp": 1700000000000,
                "lastTradeTimestamp": None,
            }
        ]
        ccxt.okx.return_value = exchange
        adapter = OKXSpotAdapter("api-key", "secret", "passphrase")

        orders = await adapter.fetch_open_orders(["BTC/USDT"])

    assert len(orders) == 1
    assert orders[0].exchange_order_id == "okx-order-1"
    assert orders[0].client_order_id == "client-1"
    assert orders[0].status == OrderStatus.PENDING
    exchange.fetch_open_orders.assert_awaited_once_with("BTC/USDT")


@pytest.mark.asyncio
async def test_fetch_order_history_maps_closed_orders():
    with patch("src.exchange.base.ccxt") as ccxt:
        exchange = AsyncMock()
        exchange.fetch_closed_orders.return_value = [
            {
                "id": "okx-order-2",
                "clientOrderId": "client-2",
                "symbol": "BTC/USDT",
                "side": "sell",
                "type": "market",
                "amount": 0.5,
                "price": None,
                "status": "closed",
                "average": 51000.0,
                "timestamp": 1700000001000,
                "lastTradeTimestamp": 1700000002000,
            }
        ]
        ccxt.okx.return_value = exchange
        adapter = OKXSpotAdapter("api-key", "secret", "passphrase")

        orders = await adapter.fetch_order_history(["BTC/USDT"], since=1700000000000)

    assert orders[0].exchange_order_id == "okx-order-2"
    assert orders[0].status == OrderStatus.FILLED
    assert orders[0].fill_price == 51000.0
    assert orders[0].updated_at == 1700000002000
    exchange.fetch_closed_orders.assert_awaited_once_with("BTC/USDT", since=1700000000000)


@pytest.mark.asyncio
async def test_fetch_trade_history_maps_my_trades():
    with patch("src.exchange.base.ccxt") as ccxt:
        exchange = AsyncMock()
        exchange.fetch_my_trades.return_value = [
            {
                "id": "trade-1",
                "order": "okx-order-2",
                "symbol": "BTC/USDT",
                "side": "sell",
                "amount": 0.5,
                "price": 51000.0,
                "fee": {"cost": 0.8},
                "timestamp": 1700000002000,
            }
        ]
        ccxt.okx.return_value = exchange
        adapter = OKXSpotAdapter("api-key", "secret", "passphrase")

        trades = await adapter.fetch_trade_history(["BTC/USDT"], since=1700000000000)

    assert trades[0].exchange_trade_id == "trade-1"
    assert trades[0].order_id == "okx-order-2"
    assert trades[0].fee == 0.8
    exchange.fetch_my_trades.assert_awaited_once_with("BTC/USDT", since=1700000000000)
```

- [ ] **Step 2: Run failing adapter tests**

Run:

```bash
pytest tests/integration/test_exchange_adapter.py -q
```

Expected: FAIL because the snapshot dataclasses and fetch methods do not exist.

- [ ] **Step 3: Add private snapshot dataclasses**

In `src/core/types.py`, add after `Order`:

```python
@dataclass
class ExchangeOrderSnapshot:
    exchange_order_id: str
    client_order_id: str
    symbol: str
    side: OrderSide
    type: OrderType
    amount: float
    price: float
    status: OrderStatus
    fill_price: float
    timestamp: int
    updated_at: int


@dataclass
class ExchangeTradeSnapshot:
    exchange_trade_id: str
    order_id: str
    symbol: str
    side: OrderSide
    amount: float
    price: float
    fee: float
    timestamp: int
```

- [ ] **Step 4: Import dataclasses in adapter**

In `src/exchange/base.py`, update the import:

```python
from src.core.types import (
    Bar,
    ExchangeOrderSnapshot,
    ExchangeTradeSnapshot,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
)
```

- [ ] **Step 5: Add private fetch methods to `OKXBaseAdapter`**

Add these methods inside `OKXBaseAdapter` after `fetch_tickers()`:

```python
    async def fetch_open_orders(
        self,
        symbols: list[str] | None = None,
    ) -> list[ExchangeOrderSnapshot]:
        return await self._fetch_orders("fetch_open_orders", symbols, since=None)

    async def fetch_order_history(
        self,
        symbols: list[str] | None = None,
        since: int | None = None,
    ) -> list[ExchangeOrderSnapshot]:
        return await self._fetch_orders("fetch_closed_orders", symbols, since=since)

    async def fetch_trade_history(
        self,
        symbols: list[str] | None = None,
        since: int | None = None,
    ) -> list[ExchangeTradeSnapshot]:
        target_symbols = symbols or [None]
        trades: list[ExchangeTradeSnapshot] = []
        for symbol in target_symbols:
            if symbol is None:
                rows = await self._exchange.fetch_my_trades(since=since)
            else:
                rows = await self._exchange.fetch_my_trades(symbol, since=since)
            trades.extend(self._map_trade(row) for row in rows)
        return trades

    async def _fetch_orders(
        self,
        method_name: str,
        symbols: list[str] | None,
        since: int | None,
    ) -> list[ExchangeOrderSnapshot]:
        method = getattr(self._exchange, method_name)
        target_symbols = symbols or [None]
        orders: list[ExchangeOrderSnapshot] = []
        for symbol in target_symbols:
            if symbol is None:
                rows = await method(since=since)
            elif since is None:
                rows = await method(symbol)
            else:
                rows = await method(symbol, since=since)
            orders.extend(self._map_order(row) for row in rows)
        return orders
```

- [ ] **Step 6: Add mapping helpers**

Add these helpers inside `OKXBaseAdapter` before `_map_status()`:

```python
    def _map_order(self, row: dict) -> ExchangeOrderSnapshot:
        timestamp = int(row.get("timestamp") or 0)
        updated_at = int(row.get("lastTradeTimestamp") or timestamp)
        return ExchangeOrderSnapshot(
            exchange_order_id=str(row.get("id") or ""),
            client_order_id=str(row.get("clientOrderId") or row.get("clientOid") or ""),
            symbol=str(row.get("symbol") or ""),
            side=OrderSide(str(row.get("side") or "buy")),
            type=OrderType(str(row.get("type") or "limit")),
            amount=float(row.get("amount") or 0.0),
            price=float(row.get("price") or 0.0),
            status=self._map_status(row.get("status")),
            fill_price=float(row.get("average") or 0.0),
            timestamp=timestamp,
            updated_at=updated_at,
        )

    def _map_trade(self, row: dict) -> ExchangeTradeSnapshot:
        fee = row.get("fee") or {}
        return ExchangeTradeSnapshot(
            exchange_trade_id=str(row.get("id") or ""),
            order_id=str(row.get("order") or row.get("orderId") or ""),
            symbol=str(row.get("symbol") or ""),
            side=OrderSide(str(row.get("side") or "buy")),
            amount=float(row.get("amount") or 0.0),
            price=float(row.get("price") or 0.0),
            fee=float(fee.get("cost") or 0.0) if isinstance(fee, dict) else 0.0,
            timestamp=int(row.get("timestamp") or 0),
        )
```

Update `_map_status()` so OKX/ccxt open orders map to local pending:

```python
    def _map_status(self, status: str | None) -> OrderStatus:
        return {
            "open": OrderStatus.PENDING,
            "closed": OrderStatus.FILLED,
            "canceled": OrderStatus.CANCELLED,
            "cancelled": OrderStatus.CANCELLED,
            "rejected": OrderStatus.REJECTED,
        }.get(status or "", OrderStatus.PENDING)
```

- [ ] **Step 7: Run adapter tests**

Run:

```bash
pytest tests/integration/test_exchange_adapter.py -q
```

Expected: PASS.

---

## Task 6: Reconcile OKX private orders/trades into local repository

> Locked decision override: strategy attribution must match existing local orders first and use `"__exchange__"` for unmatched exchange/account records. High-risk divergence engages the kill switch before saving risk events or upserting records. Unmatched exchange order ids become stable local ids such as `okx:{exchange_order_id}`.

**Files:**
- Modify: `src/exchange/live_sync.py`
- Modify: `src/order/manager.py`
- Test: `tests/unit/test_live_sync_reconciliation.py`

- [ ] **Step 1: Write failing reconciliation tests**

Create `tests/unit/test_live_sync_reconciliation.py`:

```python
from sqlmodel import SQLModel, create_engine

from src.core.types import ExchangeOrderSnapshot, ExchangeTradeSnapshot, OrderSide, OrderStatus, OrderType
from src.data.models import OrderRecord
from src.data.repository import Repository
from src.exchange.live_sync import LiveStateSyncService


class FakeAdapter:
    def __init__(self, orders=None, trades=None):
        self.orders = orders or []
        self.trades = trades or []

    async def fetch_open_orders(self, symbols=None):
        return self.orders

    async def fetch_order_history(self, symbols=None, since=None):
        return []

    async def fetch_trade_history(self, symbols=None, since=None):
        return self.trades


def create_memory_repository() -> Repository:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Repository(engine=engine)


async def noop_account_position_methods(adapter):
    async def fetch_account_snapshot():
        raise AssertionError("refresh() should not run in reconciliation tests")

    async def fetch_position_snapshots(symbols=None):
        raise AssertionError("refresh() should not run in reconciliation tests")

    adapter.fetch_account_snapshot = fetch_account_snapshot
    adapter.fetch_position_snapshots = fetch_position_snapshots


import pytest


@pytest.mark.asyncio
async def test_reconcile_persists_remote_open_orders():
    repository = create_memory_repository()
    adapter = FakeAdapter(
        orders=[
            ExchangeOrderSnapshot(
                exchange_order_id="okx-1",
                client_order_id="client-1",
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                type=OrderType.LIMIT,
                amount=1.0,
                price=50000.0,
                status=OrderStatus.PENDING,
                fill_price=0.0,
                timestamp=1700000000000,
                updated_at=1700000000000,
            )
        ]
    )
    await noop_account_position_methods(adapter)

    result = await LiveStateSyncService(adapter, repository, lambda: 1700000005000).reconcile_orders_and_trades(
        "ma_cross",
        ["BTC/USDT"],
    )

    assert result.orders_seen == 1
    assert repository.get_orders()[0].exchange_order_id == "okx-1"
    assert repository.get_orders()[0].status == "pending"


@pytest.mark.asyncio
async def test_reconcile_persists_remote_trades_idempotently():
    repository = create_memory_repository()
    trade = ExchangeTradeSnapshot(
        exchange_trade_id="trade-1",
        order_id="okx-1",
        symbol="BTC/USDT",
        side=OrderSide.SELL,
        amount=0.5,
        price=51000.0,
        fee=0.8,
        timestamp=1700000002000,
    )
    adapter = FakeAdapter(trades=[trade])
    await noop_account_position_methods(adapter)
    service = LiveStateSyncService(adapter, repository, lambda: 1700000005000)

    await service.reconcile_orders_and_trades("ma_cross", ["BTC/USDT"])
    await service.reconcile_orders_and_trades("ma_cross", ["BTC/USDT"])

    trades = repository.get_trades("ma_cross")
    assert len(trades) == 1
    assert trades[0].exchange_trade_id == "trade-1"


@pytest.mark.asyncio
async def test_reconcile_reports_stale_local_open_order():
    repository = create_memory_repository()
    repository.upsert_order(
        OrderRecord(
            order_id="local-1",
            exchange_order_id="okx-missing",
            client_order_id="",
            strategy="ma_cross",
            symbol="BTC/USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=50000.0,
            status="pending",
            fill_price=0.0,
            timestamp=1700000000000,
            updated_at=1700000000000,
        )
    )
    adapter = FakeAdapter(orders=[])
    await noop_account_position_methods(adapter)

    result = await LiveStateSyncService(adapter, repository, lambda: 1700000005000).reconcile_orders_and_trades(
        "ma_cross",
        ["BTC/USDT"],
    )

    assert result.divergences == [
        {
            "type": "risk_event",
            "strategy": "ma_cross",
            "reason_code": "reconciliation_divergence",
            "reason": "Local open order missing from OKX open orders",
            "order_id": "local-1",
            "exchange_order_id": "okx-missing",
            "symbol": "BTC/USDT",
            "timestamp": 1700000005000,
        }
    ]
```

- [ ] **Step 2: Run failing reconciliation tests**

Run:

```bash
pytest tests/unit/test_live_sync_reconciliation.py -q
```

Expected: FAIL because `reconcile_orders_and_trades()` and `ReconciliationResult` do not exist.

- [ ] **Step 3: Add reconciliation result dataclass**

In `src/exchange/live_sync.py`, update imports:

```python
from src.core.types import AccountSnapshot, ExchangeOrderSnapshot, ExchangeTradeSnapshot, PositionSnapshot
from src.data.models import AccountRecord, OrderRecord, PositionRecord, TradeRecord
```

If `AccountSnapshot` and `PositionSnapshot` are not currently defined in `src/core/types.py`, keep the import aligned with the current project branch before implementing. Do not invent alternate account/position models in this task.

Add below `LiveStateSyncResult`:

```python
@dataclass(frozen=True)
class ReconciliationResult:
    orders_seen: int
    trades_seen: int
    orders_persisted: int
    trades_persisted: int
    divergences: list[dict[str, object]]
```

- [ ] **Step 4: Add reconciliation method**

Add this method inside `LiveStateSyncService`:

```python
    async def reconcile_orders_and_trades(
        self,
        strategy: str,
        symbols: list[str] | None = None,
        since: int | None = None,
    ) -> ReconciliationResult:
        open_orders = await self.adapter.fetch_open_orders(symbols)
        historical_orders = await self.adapter.fetch_order_history(symbols, since=since)
        trades = await self.adapter.fetch_trade_history(symbols, since=since)
        orders = [*open_orders, *historical_orders]

        persisted_orders = [
            self.repository.upsert_order(self._order_record(strategy, snapshot))
            for snapshot in orders
        ]
        persisted_trades = [
            self.repository.upsert_trade(self._trade_record(strategy, snapshot))
            for snapshot in trades
        ]
        divergences = self._find_order_divergences(strategy, symbols, open_orders)
        for divergence in divergences:
            self.repository.save_risk_event(divergence)
        return ReconciliationResult(
            orders_seen=len(orders),
            trades_seen=len(trades),
            orders_persisted=len(persisted_orders),
            trades_persisted=len(persisted_trades),
            divergences=divergences,
        )
```

- [ ] **Step 5: Add mapping and divergence helpers**

Add these methods inside `LiveStateSyncService`:

```python
    def _order_record(
        self,
        strategy: str,
        snapshot: ExchangeOrderSnapshot,
    ) -> OrderRecord:
        return OrderRecord(
            order_id=snapshot.exchange_order_id,
            exchange_order_id=snapshot.exchange_order_id,
            client_order_id=snapshot.client_order_id,
            strategy=strategy,
            symbol=snapshot.symbol,
            side=snapshot.side.value,
            type=snapshot.type.value,
            amount=snapshot.amount,
            price=snapshot.price,
            status=snapshot.status.value,
            fill_price=snapshot.fill_price,
            timestamp=snapshot.timestamp,
            updated_at=snapshot.updated_at,
        )

    def _trade_record(
        self,
        strategy: str,
        snapshot: ExchangeTradeSnapshot,
    ) -> TradeRecord:
        return TradeRecord(
            exchange_trade_id=snapshot.exchange_trade_id,
            order_id=snapshot.order_id,
            strategy=strategy,
            symbol=snapshot.symbol,
            side=snapshot.side.value,
            amount=snapshot.amount,
            price=snapshot.price,
            fee=snapshot.fee,
            timestamp=snapshot.timestamp,
        )

    def _find_order_divergences(
        self,
        strategy: str,
        symbols: list[str] | None,
        open_orders: list[ExchangeOrderSnapshot],
    ) -> list[dict[str, object]]:
        remote_ids = {order.exchange_order_id for order in open_orders if order.exchange_order_id}
        requested_symbols = set(symbols) if symbols is not None else None
        divergences: list[dict[str, object]] = []
        for order in self.repository.get_open_orders(strategy):
            if requested_symbols is not None and order.symbol not in requested_symbols:
                continue
            if order.exchange_order_id and order.exchange_order_id not in remote_ids:
                divergences.append(
                    {
                        "type": "risk_event",
                        "strategy": strategy,
                        "reason_code": "reconciliation_divergence",
                        "reason": "Local open order missing from OKX open orders",
                        "order_id": order.order_id,
                        "exchange_order_id": order.exchange_order_id,
                        "symbol": order.symbol,
                        "timestamp": self.timestamp_ms(),
                    }
                )
        return divergences
```

- [ ] **Step 6: Add top-level helper**

Add to bottom of `src/exchange/live_sync.py`, after `refresh_okx_live_state()`:

```python
async def reconcile_okx_orders_and_trades(
    exchange,
    repository,
    strategy: str,
    symbols: list[str] | None,
    timestamp_ms: Callable[[], int],
    since: int | None = None,
) -> ReconciliationResult:
    adapter = create_okx_adapter(exchange)
    try:
        return await LiveStateSyncService(adapter, repository, timestamp_ms).reconcile_orders_and_trades(
            strategy,
            symbols,
            since=since,
        )
    finally:
        await adapter.close()
```

- [ ] **Step 7: Run reconciliation tests**

Run:

```bash
pytest tests/unit/test_live_sync_reconciliation.py -q
```

Expected: PASS.

---

## Task 7: Add private sync API endpoint

> Locked decision override: implement this as demo-only `POST /api/ops/sync/private` in `src/web/api/ops.py`, not as `/api/trading/reconcile`. The request accepts optional `symbols` and `since`; the endpoint syncs account, open orders, and recent trades, then returns per-section counts and divergence summary.

**Files:**
- Modify: `src/web/api/ops.py`
- Test: `tests/integration/test_web_api.py`

- [ ] **Step 1: Write failing endpoint tests**

Append to `tests/integration/test_web_api.py`:

```python
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from src.web.app import create_app


def test_reconcile_endpoint_rejects_backtest_mode():
    client = TestClient(create_app())

    response = client.post(
        "/api/trading/reconcile",
        json={"strategy": "ma_cross", "symbols": ["BTC/USDT"]},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Reconciliation requires demo or live mode"


def test_reconcile_endpoint_returns_result_in_demo_mode():
    client = TestClient(create_app())
    settings = client.get("/api/settings").json()
    settings["mode"] = "demo"
    client.put("/api/settings", json=settings)

    with patch("src.web.api.trading.reconcile_okx_orders_and_trades", new_callable=AsyncMock) as reconcile:
        reconcile.return_value.orders_seen = 1
        reconcile.return_value.trades_seen = 2
        reconcile.return_value.orders_persisted = 1
        reconcile.return_value.trades_persisted = 2
        reconcile.return_value.divergences = []

        response = client.post(
            "/api/trading/reconcile",
            json={"strategy": "ma_cross", "symbols": ["BTC/USDT"], "since": 1700000000000},
        )

    assert response.status_code == 200
    assert response.json() == {
        "orders_seen": 1,
        "trades_seen": 2,
        "orders_persisted": 1,
        "trades_persisted": 2,
        "divergences": [],
    }
```

- [ ] **Step 2: Run failing endpoint tests**

Run:

```bash
pytest tests/integration/test_web_api.py -q
```

Expected: FAIL because `/api/trading/reconcile` does not exist.

- [ ] **Step 3: Add request model and imports**

In `src/web/api/trading.py`, update imports:

```python
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.core.runtime_settings import load_runtime_settings
from src.exchange.live_sync import reconcile_okx_orders_and_trades
```

Add near module constants:

```python
class ReconcileRequest(BaseModel):
    strategy: str
    symbols: list[str] | None = None
    since: int | None = None


def current_timestamp_ms() -> int:
    return int(time.time() * 1000)
```

- [ ] **Step 4: Add endpoint**

Add after `get_trades()` and before `get_account()`:

```python
@router.post("/reconcile")
async def reconcile_private_state(request: ReconcileRequest) -> dict[str, Any]:
    settings = load_runtime_settings()
    if settings.mode not in {"demo", "live"}:
        raise HTTPException(
            status_code=400,
            detail="Reconciliation requires demo or live mode",
        )
    repository = Repository()
    result = await reconcile_okx_orders_and_trades(
        settings.exchange,
        repository,
        request.strategy,
        request.symbols,
        current_timestamp_ms,
        since=request.since,
    )
    return {
        "orders_seen": result.orders_seen,
        "trades_seen": result.trades_seen,
        "orders_persisted": result.orders_persisted,
        "trades_persisted": result.trades_persisted,
        "divergences": result.divergences,
    }
```

- [ ] **Step 5: Run endpoint tests**

Run:

```bash
pytest tests/integration/test_web_api.py -q
```

Expected: PASS.

---

## Task 8: Broadcast private-sync divergences and refresh WebSocket data

> Locked decision override: broadcast from the ops private-sync endpoint. High-risk divergence also goes through the existing Telegram risk-notification path; low-risk divergence is persisted only.

**Files:**
- Modify: `src/web/api/ops.py`
- Modify: `src/web/app.py`
- Test: `tests/integration/test_web_api.py`

- [ ] **Step 1: Add broadcast support to trading router**

`src/web/api/trading.py` currently has a module-level `router = APIRouter()`. Replace it with a factory while keeping a default router for import compatibility:

```python
from collections.abc import Awaitable, Callable

RuntimeBroadcaster = Callable[[dict[str, object]], Awaitable[None]]
```

Wrap the existing route definitions in:

```python
def create_router(broadcast: RuntimeBroadcaster | None = None) -> APIRouter:
    router = APIRouter()

    # move existing @router.get and @router.post endpoint functions here

    return router


router = create_router()
```

Keep endpoint function bodies unchanged except for `reconcile_private_state()` below.

- [ ] **Step 2: Broadcast divergences and updated orders/trades**

Inside `reconcile_private_state()`, after `result` is returned and before the response dict:

```python
    if broadcast is not None:
        for divergence in result.divergences:
            await broadcast(divergence)
        await broadcast({"type": "orders", "orders": serialize_records(repository.get_orders())})
        await broadcast({"type": "trades", "trades": serialize_records(repository.get_trades(request.strategy))})
```

- [ ] **Step 3: Register factory router in app**

In `src/web/app.py`, replace:

```python
    app.include_router(trading.router, prefix="/api/trading", tags=["trading"])
```

with:

```python
    app.include_router(
        trading.create_router(ws_manager.broadcast),
        prefix="/api/trading",
        tags=["trading"],
    )
```

- [ ] **Step 4: Run web tests**

Run:

```bash
pytest tests/integration/test_web_api.py -q
```

Expected: PASS.

---

## Task 9: End-to-end verification suite

**Files:**
- No new production files unless earlier tasks exposed missing imports.

- [ ] **Step 1: Run focused Python tests**

Run:

```bash
pytest \
  tests/unit/test_repository.py \
  tests/unit/test_order_manager_kill_switch.py \
  tests/unit/test_live_sync_reconciliation.py \
  tests/unit/test_telegram.py \
  tests/integration/test_exchange_adapter.py \
  tests/integration/test_web_api.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run full Python suite**

Run:

```bash
pytest -q
```

Expected: PASS.

- [ ] **Step 3: Run app import smoke test**

Run:

```bash
python - <<'PY'
from src.web.app import create_app
app = create_app()
print(app.title)
PY
```

Expected output contains:

```text
OKX Bot API
```

- [ ] **Step 4: Demo-mode manual smoke checklist**

Run the backend and frontend exactly as this repository currently documents. Then verify:

1. `GET /api/ops/kill-switch` returns `engaged: false` on a clean database.
2. `PUT /api/ops/kill-switch` with `engaged: true` returns the same state.
3. Starting a strategy while engaged returns HTTP 423 and does not create a running engine.
4. Submitting an order through a strategy while engaged persists a rejected order and emits `reason_code: kill_switch_engaged`.
5. `PUT /api/ops/kill-switch` with `engaged: false` allows strategy start again.
6. With `mode=demo`, `POST /api/trading/reconcile` uses OKX demo credentials and returns counts without printing secrets.
7. With Telegram token/chat configured, `POST /api/settings/notify/test` sends one test message and returns only `{"status":"sent"}`.
8. Risk events continue broadcasting over WebSocket when Telegram is misconfigured or Telegram API fails.

Do not switch to real live-money mode during this checklist.

---

## Self-review checklist

### Spec coverage

- Kill switch: covered by Tasks 1, 2, 3, 4, and 9.
- OKX private state reconciliation: covered by Tasks 5, 6, 7, 8, and 9.
- Telegram risk notifications: covered by Tasks 3, 4, and 9.
- Persistence/auditability: covered by Task 1 and reused by Tasks 3, 4, and 6.
- Demo-first verification: covered by safety rules and Task 9.

### Placeholder scan

This plan avoids `TBD`, unexpanded TODOs, and unspecified "write tests" instructions. Each code-changing task includes concrete file paths, code snippets, commands, and expected outcomes.

### Type consistency

- Kill switch state uses `KillSwitchRecord.engaged`, `reason`, and `updated_at` consistently across repository, ops API, and strategy start checks.
- Risk events use `type`, `strategy`, `reason_code`, `reason`, and `timestamp` consistently across order manager, ops API, repository, WebSocket, and Telegram.
- Reconciliation snapshots use `ExchangeOrderSnapshot` and `ExchangeTradeSnapshot`; persisted rows use `OrderRecord` and `TradeRecord` with matching exchange identity fields.
- `OrderStatus.PENDING` remains the local representation for OKX/ccxt `open` orders.

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-26-live-ops-safety.md`.

Two execution options:

1. **Subagent-Driven (recommended)** - dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** - execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
