# Live Trading Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the OKX live trading path so live mode can safely sync account/position state, route spot/derivative/option orders, expose live settings, and support trigger/SL/TP order parameters.

**Architecture:** Keep the current `OrderRouter` + `UnifiedOrderManager` flow, but add a private OKX state sync layer before live risk checks and after live fills. Preserve fail-closed defaults: live opening orders remain disabled unless explicitly enabled, OKX demo stays enabled by default, and real credentials remain masked in API responses.

**Tech Stack:** Python 3.12, FastAPI, SQLModel, ccxt async OKX adapters, pytest/pytest-asyncio, Vue 3, Element Plus, TypeScript, Vitest, vue-tsc.

---

## Safety and repository rules

- Do not print OKX API keys, secrets, passphrases, Telegram tokens, `.env` values, or `data/settings.local.yaml` contents.
- `GET /api/settings` masks secrets, but runtime settings persist full credentials locally. Do not add settings or credential files to git.
- Keep `exchange.demo` defaulting to `true`. Manual live-money verification only happens after demo-mode smoke tests pass and the user explicitly asks for real live mode.
- This repository has a standing instruction: do not commit or push unless the user explicitly asks. The checkpoint commit steps below are included as review boundaries; execute them only after explicit user approval in that execution turn.

## Current gaps this plan closes

1. Live order safeguards read account and position state from the local repository, but the repository is not synced from OKX private account/position endpoints.
2. `UnifiedOrderManager` currently enforces reduce-only live behavior and blocks opening/increasing exposure.
3. `create_live_order_handler()` supports spot/swap/futures but not options.
4. Backend settings already carry `exchange.market_type` and `exchange.demo`, but the frontend settings types/UI do not expose them.
5. Public market endpoints always create `OKXSpotAdapter` and use spot symbols.
6. `OrderType.STOP`, `stop_loss`, and `take_profit` are rejected by `OKXBaseAdapter.submit()`.
7. Live fills currently pass through paper accounting, which can diverge from exchange truth.

## File structure

- Modify `src/core/types.py`
  - Add exchange account/position snapshot dataclasses.
  - Add `Order.trigger_price` for trigger/stop orders.
- Modify `src/core/config.py`
  - Add safe live-opening controls to `RiskConfig`.
- Modify `src/exchange/base.py`
  - Add OKX private `fetch_balance` / `fetch_positions` normalization.
  - Add trigger/stop-loss/take-profit parameter mapping.
- Create `src/exchange/factory.py`
  - Centralize OKX adapter selection for spot/swap/futures/options.
- Create `src/exchange/live_sync.py`
  - Persist OKX private snapshots into `AccountRecord` and `PositionRecord`.
- Modify `src/order/manager.py`
  - Refresh live state before live risk checks and after live fills.
  - Skip paper accounting for live fills.
  - Replace hard reduce-only checks with configurable live order safety.
- Modify `src/web/api/strategies.py`
  - Use the adapter factory.
  - Pass live state refresher into `UnifiedOrderManager`.
  - Refresh live state when starting a live strategy.
- Modify `src/web/api/trading.py`
  - Add a manual live-state refresh endpoint.
- Modify `src/web/api/settings.py`
  - Include live-opening controls in settings payloads.
- Modify `src/web/api/market.py`
  - Select market adapter by `market_type` and support derivative/option symbols.
- Modify `frontend/src/types/settings.ts`
  - Add `market_type`, `demo`, and live-opening risk fields.
- Modify `frontend/src/views/Settings.vue`
  - Expose market type, OKX demo, and live-opening controls.
- Modify `frontend/src/locales/en.ts` and `frontend/src/locales/zh-CN.ts`
  - Add labels for new settings and market controls.
- Modify `frontend/src/types/market.ts`, `frontend/src/services/market.ts`, and `frontend/src/views/Market.vue`
  - Add market-type-aware kline/ticker queries.
- Tests:
  - `tests/unit/test_exchange_base.py`
  - `tests/unit/test_live_sync.py`
  - `tests/unit/test_order_router.py`
  - `tests/integration/test_web_api.py`
  - `frontend/src/services/settings.test.ts`
  - Add or update frontend market/settings tests if existing component tests are present.

---

## Task 1: Add OKX private account and position snapshots

**Files:**
- Modify: `src/core/types.py:1-63`
- Modify: `src/exchange/base.py:1-222`
- Test: `tests/unit/test_exchange_base.py`

- [ ] **Step 1: Extend the fake OKX test double**

In `tests/unit/test_exchange_base.py`, update imports and `FakeOKX`:

```python
from src.core.types import Order, OrderSide, OrderType, PositionSide
```

Add these fields inside `FakeOKX.__init__` after `self.create_order_calls = []`:

```python
        self.balance_response = {}
        self.positions_response = []
        self.fetch_balance_calls = []
        self.fetch_positions_calls = []
```

Add these async methods after `create_order()`:

```python
    async def fetch_balance(self):
        self.fetch_balance_calls.append({})
        return self.balance_response

    async def fetch_positions(self, symbols=None):
        self.fetch_positions_calls.append(symbols)
        return self.positions_response
```

- [ ] **Step 2: Write failing tests for account and position sync parsing**

Append these tests to `tests/unit/test_exchange_base.py`:

```python
@pytest.mark.asyncio
async def test_okx_base_adapter_fetches_account_snapshot_from_private_balance():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.balance_response = {
        "free": {"USDT": 980.5},
        "total": {"USDT": 1000.25},
        "info": {
            "data": [
                {
                    "totalEq": "1000.25",
                    "availEq": "980.5",
                    "upl": "12.75",
                    "uTime": "1700000000123",
                }
            ]
        },
    }

    snapshot = await adapter.fetch_account_snapshot()

    assert fake.fetch_balance_calls == [{}]
    assert snapshot.currency == "USDT"
    assert snapshot.equity == 1000.25
    assert snapshot.cash_balance == 980.5
    assert snapshot.available_balance == 980.5
    assert snapshot.unrealized_pnl == 12.75
    assert snapshot.realized_pnl == 0.0
    assert snapshot.updated_at == 1700000000123


@pytest.mark.asyncio
async def test_okx_base_adapter_fetches_position_snapshots_from_private_positions():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.positions_response = [
        {
            "symbol": "BTC-USDT-SWAP",
            "side": "long",
            "contracts": 2.0,
            "entryPrice": 50000.0,
            "markPrice": 51000.0,
            "unrealizedPnl": 200.0,
            "leverage": 3,
            "timestamp": 1700000000456,
            "info": {"realizedPnl": "5.5"},
        },
        {
            "symbol": "ETH-USDT-SWAP",
            "side": "short",
            "contracts": "1.5",
            "entryPrice": "3000",
            "markPrice": "2950",
            "unrealizedPnl": "75",
            "leverage": "5",
            "timestamp": 1700000000789,
            "info": {},
        },
    ]

    snapshots = await adapter.fetch_position_snapshots(["BTC-USDT-SWAP", "ETH-USDT-SWAP"])

    assert fake.fetch_positions_calls == [["BTC-USDT-SWAP", "ETH-USDT-SWAP"]]
    assert snapshots[0].symbol == "BTC-USDT-SWAP"
    assert snapshots[0].side == PositionSide.LONG
    assert snapshots[0].amount == 2.0
    assert snapshots[0].entry_price == 50000.0
    assert snapshots[0].mark_price == 51000.0
    assert snapshots[0].unrealized_pnl == 200.0
    assert snapshots[0].realized_pnl == 5.5
    assert snapshots[0].leverage == 3
    assert snapshots[0].updated_at == 1700000000456
    assert snapshots[1].symbol == "ETH-USDT-SWAP"
    assert snapshots[1].side == PositionSide.SHORT
    assert snapshots[1].amount == 1.5
```

- [ ] **Step 3: Run the failing tests**

Run:

```bash
uv run pytest tests/unit/test_exchange_base.py::test_okx_base_adapter_fetches_account_snapshot_from_private_balance tests/unit/test_exchange_base.py::test_okx_base_adapter_fetches_position_snapshots_from_private_positions -v
```

Expected:

```text
FAILED ... AttributeError: 'OKXBaseAdapter' object has no attribute 'fetch_account_snapshot'
FAILED ... AttributeError: 'OKXBaseAdapter' object has no attribute 'fetch_position_snapshots'
```

- [ ] **Step 4: Add shared snapshot dataclasses**

In `src/core/types.py`, add these dataclasses after `class PositionSide` and before `class Bar`:

```python
@dataclass(frozen=True)
class AccountSnapshot:
    currency: str
    equity: float
    cash_balance: float
    available_balance: float
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    updated_at: int = 0


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    side: PositionSide
    amount: float
    entry_price: float
    mark_price: float | None = None
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    leverage: int = 1
    updated_at: int = 0
```

- [ ] **Step 5: Add abstract private-state methods to the exchange interface**

In `src/exchange/base.py`, replace the import from `src.core.types` with:

```python
from src.core.types import AccountSnapshot, Bar, Order, OrderStatus, PositionSide, PositionSnapshot
```

In `ExchangeAdapter`, add these abstract methods between `fetch_tickers()` and `close()`:

```python
    @abstractmethod
    async def fetch_account_snapshot(self) -> AccountSnapshot:
        pass

    @abstractmethod
    async def fetch_position_snapshots(
        self,
        symbols: list[str] | None = None,
    ) -> list[PositionSnapshot]:
        pass
```

- [ ] **Step 6: Implement OKX private-state normalization**

In `OKXBaseAdapter`, add these methods after `fetch_tickers()` and before `submit()`:

```python
    async def fetch_account_snapshot(self) -> AccountSnapshot:
        balance = await self._exchange.fetch_balance()
        info = balance.get("info") or {}
        rows = info.get("data") or []
        account = rows[0] if rows else {}
        currency = "USDT"
        free = balance.get("free") or {}
        total = balance.get("total") or {}
        equity = self._float_or_zero(account.get("totalEq") or total.get(currency))
        available = self._float_or_zero(account.get("availEq") or free.get(currency))
        updated_at = self._int_or_zero(account.get("uTime"))
        return AccountSnapshot(
            currency=currency,
            equity=equity,
            cash_balance=available,
            available_balance=available,
            unrealized_pnl=self._float_or_zero(account.get("upl")),
            realized_pnl=self._float_or_zero(account.get("realizedPnl")),
            updated_at=updated_at,
        )

    async def fetch_position_snapshots(
        self,
        symbols: list[str] | None = None,
    ) -> list[PositionSnapshot]:
        rows = await self._exchange.fetch_positions(symbols)
        snapshots = []
        for row in rows:
            snapshot = self._position_snapshot(row)
            if snapshot.amount > 0:
                snapshots.append(snapshot)
        return snapshots

    def _position_snapshot(self, row: dict) -> PositionSnapshot:
        info = row.get("info") or {}
        symbol = str(row.get("symbol") or info.get("instId") or "")
        raw_amount = self._float_or_zero(row.get("contracts") or info.get("pos"))
        side_value = str(row.get("side") or info.get("posSide") or "").lower()
        if raw_amount < 0:
            side = PositionSide.SHORT
        elif side_value == "short":
            side = PositionSide.SHORT
        else:
            side = PositionSide.LONG
        return PositionSnapshot(
            symbol=symbol,
            side=side,
            amount=abs(raw_amount),
            entry_price=self._float_or_zero(row.get("entryPrice") or info.get("avgPx")),
            mark_price=self._optional_float(row.get("markPrice") or info.get("markPx")),
            unrealized_pnl=self._float_or_zero(row.get("unrealizedPnl") or info.get("upl")),
            realized_pnl=self._float_or_zero(info.get("realizedPnl")),
            leverage=self._int_or_default(row.get("leverage") or info.get("lever"), 1),
            updated_at=self._int_or_zero(row.get("timestamp") or info.get("uTime")),
        )

    def _optional_float(self, value: object) -> float | None:
        if value is None or value == "":
            return None
        return self._float_or_zero(value)

    def _float_or_zero(self, value: object) -> float:
        if value is None or value == "":
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _int_or_zero(self, value: object) -> int:
        return self._int_or_default(value, 0)

    def _int_or_default(self, value: object, default: int) -> int:
        if value is None or value == "":
            return default
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default
```

- [ ] **Step 7: Run the new tests and the existing exchange tests**

Run:

```bash
uv run pytest tests/unit/test_exchange_base.py -v
```

Expected:

```text
... passed
```

- [ ] **Step 8: Checkpoint commit if explicitly approved**

Run only after the user explicitly approves committing:

```bash
git add src/core/types.py src/exchange/base.py tests/unit/test_exchange_base.py
git commit -m "feat: sync OKX private account snapshots"
```

---

## Task 2: Persist live account and position snapshots

**Files:**
- Create: `src/exchange/live_sync.py`
- Test: `tests/unit/test_live_sync.py`

- [ ] **Step 1: Write failing sync-service tests**

Create `tests/unit/test_live_sync.py` with:

```python
import pytest

from src.core.types import AccountSnapshot, PositionSide, PositionSnapshot
from src.data.models import AccountRecord, PositionRecord
from src.exchange.live_sync import LiveStateSyncService


class FakeAdapter:
    def __init__(self):
        self.account = AccountSnapshot(
            currency="USDT",
            equity=1000.0,
            cash_balance=975.0,
            available_balance=975.0,
            unrealized_pnl=25.0,
            realized_pnl=4.0,
            updated_at=1700000000000,
        )
        self.positions = [
            PositionSnapshot(
                symbol="BTC-USDT-SWAP",
                side=PositionSide.LONG,
                amount=2.0,
                entry_price=50000.0,
                mark_price=51000.0,
                unrealized_pnl=200.0,
                realized_pnl=10.0,
                leverage=3,
                updated_at=1700000000001,
            )
        ]
        self.account_calls = 0
        self.position_calls = []

    async def fetch_account_snapshot(self):
        self.account_calls += 1
        return self.account

    async def fetch_position_snapshots(self, symbols=None):
        self.position_calls.append(symbols)
        return self.positions


class FakeRepository:
    def __init__(self):
        self.accounts = {}
        self.positions = {}
        self.deleted = []

    def get_account(self, strategy):
        return self.accounts.get(strategy)

    def upsert_account(self, account):
        self.accounts[account.strategy] = account
        return account

    def upsert_position(self, position):
        self.positions[(position.strategy, position.symbol)] = position
        return position

    def delete_position(self, strategy, symbol):
        self.deleted.append((strategy, symbol))
        self.positions.pop((strategy, symbol), None)

    def get_open_positions(self, strategy=None):
        return [
            position
            for position in self.positions.values()
            if position.amount != 0 and (strategy is None or position.strategy == strategy)
        ]


@pytest.mark.asyncio
async def test_live_state_sync_persists_account_and_positions():
    adapter = FakeAdapter()
    repository = FakeRepository()
    service = LiveStateSyncService(adapter, repository, timestamp_ms=lambda: 1700000009999)

    result = await service.refresh("ma_cross", symbols=["BTC-USDT-SWAP"])

    assert adapter.account_calls == 1
    assert adapter.position_calls == [["BTC-USDT-SWAP"]]
    account = repository.accounts["ma_cross"]
    assert account == result.account
    assert account.initial_equity == 1000.0
    assert account.cash_balance == 975.0
    assert account.equity == 1000.0
    assert account.realized_pnl == 4.0
    assert account.unrealized_pnl == 25.0
    assert account.daily_pnl == 0.0
    assert account.fees_paid == 0.0
    assert account.updated_at == 1700000000000
    position = repository.positions[("ma_cross", "BTC-USDT-SWAP")]
    assert position.side == "long"
    assert position.amount == 2.0
    assert position.entry_price == 50000.0
    assert position.mark_price == 51000.0
    assert position.realized_pnl == 10.0
    assert position.unrealized_pnl == 200.0
    assert position.leverage == 3
    assert position.timestamp == 1700000000001


@pytest.mark.asyncio
async def test_live_state_sync_removes_stale_synced_positions_for_requested_symbols():
    adapter = FakeAdapter()
    adapter.positions = []
    repository = FakeRepository()
    repository.positions[("ma_cross", "BTC-USDT-SWAP")] = PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
    )
    repository.positions[("ma_cross", "ETH-USDT-SWAP")] = PositionRecord(
        strategy="ma_cross",
        symbol="ETH-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=3000.0,
        leverage=1,
        timestamp=1700000000000,
    )
    service = LiveStateSyncService(adapter, repository, timestamp_ms=lambda: 1700000009999)

    result = await service.refresh("ma_cross", symbols=["BTC-USDT-SWAP"])

    assert result.positions == []
    assert repository.deleted == [("ma_cross", "BTC-USDT-SWAP")]
    assert ("ma_cross", "BTC-USDT-SWAP") not in repository.positions
    assert ("ma_cross", "ETH-USDT-SWAP") in repository.positions


@pytest.mark.asyncio
async def test_live_state_sync_preserves_existing_daily_pnl_and_fees():
    adapter = FakeAdapter()
    repository = FakeRepository()
    repository.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        initial_equity=900.0,
        cash_balance=900.0,
        equity=900.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=-12.0,
        fees_paid=3.5,
        updated_at=1699999999999,
    )
    service = LiveStateSyncService(adapter, repository, timestamp_ms=lambda: 1700000009999)

    await service.refresh("ma_cross", symbols=["BTC-USDT-SWAP"])

    account = repository.accounts["ma_cross"]
    assert account.initial_equity == 900.0
    assert account.daily_pnl == -12.0
    assert account.fees_paid == 3.5
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
uv run pytest tests/unit/test_live_sync.py -v
```

Expected:

```text
FAILED ... ModuleNotFoundError: No module named 'src.exchange.live_sync'
```

- [ ] **Step 3: Implement the live sync service**

Create `src/exchange/live_sync.py`:

```python
from collections.abc import Callable
from dataclasses import dataclass

from src.core.types import AccountSnapshot, PositionSnapshot
from src.data.models import AccountRecord, PositionRecord


@dataclass(frozen=True)
class LiveStateSyncResult:
    account: AccountRecord
    positions: list[PositionRecord]


class LiveStateSyncService:
    def __init__(self, adapter, repository, timestamp_ms: Callable[[], int]) -> None:
        self.adapter = adapter
        self.repository = repository
        self.timestamp_ms = timestamp_ms

    async def refresh(
        self,
        strategy: str,
        symbols: list[str] | None = None,
    ) -> LiveStateSyncResult:
        account_snapshot = await self.adapter.fetch_account_snapshot()
        position_snapshots = await self.adapter.fetch_position_snapshots(symbols)
        account = self._persist_account(strategy, account_snapshot)
        positions = [self._persist_position(strategy, snapshot) for snapshot in position_snapshots]
        self._delete_stale_positions(strategy, symbols, {position.symbol for position in positions})
        return LiveStateSyncResult(account=account, positions=positions)

    def _persist_account(self, strategy: str, snapshot: AccountSnapshot) -> AccountRecord:
        existing = self.repository.get_account(strategy)
        account = AccountRecord(
            strategy=strategy,
            initial_equity=existing.initial_equity if existing is not None else snapshot.equity,
            cash_balance=snapshot.cash_balance,
            equity=snapshot.equity,
            realized_pnl=snapshot.realized_pnl,
            unrealized_pnl=snapshot.unrealized_pnl,
            daily_pnl=existing.daily_pnl if existing is not None else 0.0,
            fees_paid=existing.fees_paid if existing is not None else 0.0,
            updated_at=snapshot.updated_at or self.timestamp_ms(),
        )
        return self.repository.upsert_account(account)

    def _persist_position(self, strategy: str, snapshot: PositionSnapshot) -> PositionRecord:
        position = PositionRecord(
            strategy=strategy,
            symbol=snapshot.symbol,
            side=snapshot.side.value,
            amount=snapshot.amount,
            entry_price=snapshot.entry_price,
            leverage=snapshot.leverage,
            timestamp=snapshot.updated_at or self.timestamp_ms(),
            mark_price=snapshot.mark_price,
            realized_pnl=snapshot.realized_pnl,
            unrealized_pnl=snapshot.unrealized_pnl,
        )
        return self.repository.upsert_position(position)

    def _delete_stale_positions(
        self,
        strategy: str,
        symbols: list[str] | None,
        synced_symbols: set[str],
    ) -> None:
        requested_symbols = set(symbols) if symbols is not None else None
        for position in self.repository.get_open_positions(strategy):
            if requested_symbols is not None and position.symbol not in requested_symbols:
                continue
            if position.symbol not in synced_symbols:
                self.repository.delete_position(strategy, position.symbol)
```

- [ ] **Step 4: Run sync-service tests**

Run:

```bash
uv run pytest tests/unit/test_live_sync.py -v
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Checkpoint commit if explicitly approved**

Run only after the user explicitly approves committing:

```bash
git add src/exchange/live_sync.py tests/unit/test_live_sync.py
git commit -m "feat: persist live exchange state"
```

---

## Task 3: Centralize OKX adapter selection and add options support

**Files:**
- Create: `src/exchange/factory.py`
- Modify: `src/web/api/strategies.py:17-129`
- Test: `tests/integration/test_web_api.py`

- [x] **Step 1: Write failing tests for adapter factory behavior**

In `tests/integration/test_web_api.py`, update the existing `test_create_order_manager_live_selects_okx_adapter` parametrization to include options:

```python
@pytest.mark.parametrize(
    ("market_type", "adapter_name", "default_type"),
    [
        ("spot", "OKXSpotAdapter", "spot"),
        ("swap", "OKXSwapAdapter", "swap"),
        ("future", "OKXFuturesAdapter", "future"),
        ("futures", "OKXFuturesAdapter", "future"),
        ("option", "OKXOptionsAdapter", "option"),
        ("options", "OKXOptionsAdapter", "option"),
    ],
)
def test_create_order_manager_live_selects_okx_adapter(monkeypatch, market_type, adapter_name, default_type):
    constructed: list[tuple[str, str, str, str, bool]] = []

    class SentinelAdapter(strategy_api.LocalPaperOrderHandler):
        def __init__(self, api_key, secret, passphrase, demo=True):
            constructed.append((adapter_name, api_key, secret, passphrase, demo))
            super().__init__()

    monkeypatch.setattr(strategy_api, adapter_name, SentinelAdapter)
    monkeypatch.setattr(
        strategy_api,
        "load_runtime_settings",
        lambda: AppConfig(
            mode="live",
            exchange=ExchangeConfig(
                api_key="key",
                secret="secret",
                passphrase="pass",
                market_type=market_type,
                demo=True,
            ),
        ),
    )

    manager = strategy_api.create_order_manager(latest_price=lambda symbol: 1.0)

    assert manager.router.mode == "live"
    assert constructed == [(adapter_name, "key", "secret", "pass", True)]
```

If the test currently monkeypatches `strategy_api.OKXSpotAdapter`, `strategy_api.OKXSwapAdapter`, and `strategy_api.OKXFuturesAdapter` directly, keep that pattern and add `OKXOptionsAdapter` to the same monkeypatch setup.

- [x] **Step 2: Run the failing adapter selection test**

Run:

```bash
uv run pytest tests/integration/test_web_api.py::test_create_order_manager_live_selects_okx_adapter -v
```

Expected:

```text
FAILED ... Unsupported OKX market_type for live trading: option
```

- [x] **Step 3: Create a reusable OKX adapter factory**

Create `src/exchange/factory.py`:

```python
from src.exchange.base import ExchangeAdapter
from src.exchange.okx_futures import OKXFuturesAdapter
from src.exchange.okx_options import OKXOptionsAdapter
from src.exchange.okx_spot import OKXSpotAdapter
from src.exchange.okx_swap import OKXSwapAdapter


def create_okx_adapter(exchange) -> ExchangeAdapter:
    if not exchange.api_key or not exchange.secret or not exchange.passphrase:
        raise ValueError("Live trading requires OKX api_key, secret, and passphrase")
    market_type = exchange.market_type.strip().lower()
    if market_type == "spot":
        adapter_cls = OKXSpotAdapter
    elif market_type == "swap":
        adapter_cls = OKXSwapAdapter
    elif market_type in {"future", "futures"}:
        adapter_cls = OKXFuturesAdapter
    elif market_type in {"option", "options"}:
        adapter_cls = OKXOptionsAdapter
    else:
        raise ValueError(f"Unsupported OKX market_type for live trading: {exchange.market_type}")
    return adapter_cls(exchange.api_key, exchange.secret, exchange.passphrase, demo=exchange.demo)
```

- [x] **Step 4: Use the factory in strategy order handler creation**

In `src/web/api/strategies.py`, replace adapter imports:

```python
from src.exchange.factory import create_okx_adapter
```

Remove direct imports:

```python
from src.exchange.okx_futures import OKXFuturesAdapter
from src.exchange.okx_spot import OKXSpotAdapter
from src.exchange.okx_swap import OKXSwapAdapter
```

Replace `create_live_order_handler()` with:

```python
def create_live_order_handler(settings: object) -> OrderHandler:
    return create_okx_adapter(settings.exchange)
```

If tests monkeypatch adapter classes on `strategy_api`, keep compatibility by importing the adapter classes in `strategies.py` and passing them into a local selector instead of using `create_okx_adapter()` directly. The final behavior must still be centralized in one selector and include `option/options`.

- [x] **Step 5: Run adapter selection tests**

Run:

```bash
uv run pytest tests/integration/test_web_api.py::test_create_order_manager_live_selects_okx_adapter -v
```

Expected:

```text
6 passed
```

- [ ] **Step 6: Checkpoint commit if explicitly approved**

Run only after the user explicitly approves committing:

```bash
git add src/exchange/factory.py src/web/api/strategies.py tests/integration/test_web_api.py
git commit -m "feat: route live orders by OKX market type"
```

---

## Task 4: Refresh live state around live order submission and skip paper accounting in live

**Files:**
- Modify: `src/order/manager.py:11-302`
- Test: `tests/unit/test_order_router.py`

- [x] **Step 1: Write failing tests for live refresher and live accounting isolation**

Append to `tests/unit/test_order_router.py`:

```python
@pytest.mark.asyncio
async def test_live_order_manager_refreshes_state_before_live_risk_check():
    handler = MockHandler()
    repository = FakeRepository()
    refresh_calls = []

    async def refresh(strategy_name, symbol):
        refresh_calls.append((strategy_name, symbol))
        repository.accounts[strategy_name] = AccountRecord(
            strategy=strategy_name,
            initial_equity=100000.0,
            cash_balance=100000.0,
            equity=100000.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            daily_pnl=0.0,
            fees_paid=0.0,
            updated_at=1700000000000,
        )
        repository.positions[(strategy_name, symbol)] = PositionRecord(
            strategy=strategy_name,
            symbol=symbol,
            side="long",
            amount=1.0,
            entry_price=50000.0,
            leverage=1,
            timestamp=1700000000000,
        )

    router = OrderRouter(backtest=None, live=handler, mode="live")
    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        initial_equity=100000.0,
        risk_manager=RiskManager(max_position_pct=0.8, max_daily_loss_pct=0.05),
        price_provider=lambda symbol: 50000.0,
        live_safeguards=True,
        live_market_type="swap",
        live_state_refresher=refresh,
    )

    order = await manager.submit(
        symbol="BTC-USDT-SWAP",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=0.25,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.FILLED
    assert refresh_calls[0] == ("ma_cross", "BTC-USDT-SWAP")
    assert handler.submitted[0].params == {"reduceOnly": True}


@pytest.mark.asyncio
async def test_live_order_manager_does_not_run_paper_accounting_for_live_fills(monkeypatch):
    class ExplodingAccounting:
        def __init__(self, *args, **kwargs):
            pass

        def process_filled_order(self, order, strategy_name, timestamp):
            raise AssertionError("paper accounting must not process live fills")

    monkeypatch.setattr("src.order.manager.PaperAccountingService", ExplodingAccounting)
    handler = MockHandler()
    repository = FakeRepository()
    repository.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        initial_equity=100000.0,
        cash_balance=100000.0,
        equity=100000.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
    )
    repository.positions[("ma_cross", "BTC-USDT-SWAP")] = PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
    )
    router = OrderRouter(backtest=None, live=handler, mode="live")
    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        initial_equity=100000.0,
        risk_manager=RiskManager(max_position_pct=0.8, max_daily_loss_pct=0.05),
        price_provider=lambda symbol: 50000.0,
        live_safeguards=True,
        live_market_type="swap",
    )

    order = await manager.submit(
        symbol="BTC-USDT-SWAP",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=0.25,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.FILLED
    assert repository.trades == []
```

- [x] **Step 2: Run the failing tests**

Run:

```bash
uv run pytest tests/unit/test_order_router.py::test_live_order_manager_refreshes_state_before_live_risk_check tests/unit/test_order_router.py::test_live_order_manager_does_not_run_paper_accounting_for_live_fills -v
```

Expected:

```text
FAILED ... TypeError: UnifiedOrderManager.__init__() got an unexpected keyword argument 'live_state_refresher'
FAILED ... AssertionError: paper accounting must not process live fills
```

- [x] **Step 3: Add a live state refresher callback to the order manager**

In `src/order/manager.py`, add this type alias after `RiskEventCallback`:

```python
LiveStateRefresher = Callable[[str, str], Awaitable[None]]
```

Update `UnifiedOrderManager.__init__` signature:

```python
        live_safeguards: bool = False,
        live_market_type: str = "",
        live_state_refresher: LiveStateRefresher | None = None,
```

Add this assignment after `self.live_market_type = live_market_type.strip().lower()`:

```python
        self.live_state_refresher = live_state_refresher
```

In `submit()`, add the pre-risk refresh immediately after constructing `order` and before `risk_result = self._check_risk_gate(...)`:

```python
        if self.live_safeguards and self.live_state_refresher is not None:
            await self.live_state_refresher(strategy_name, symbol)
```

Add the post-fill refresh after `_persist_order(submitted_order, strategy_name)` and before `on_order_update`:

```python
        if (
            self.live_safeguards
            and self.live_state_refresher is not None
            and submitted_order.status == OrderStatus.FILLED
        ):
            await self.live_state_refresher(strategy_name, symbol)
```

- [x] **Step 4: Skip paper accounting for live fills**

In `src/order/manager.py`, replace the live-fill accounting block at the end of `_persist_order()`:

```python
        if order.status == OrderStatus.FILLED:
            PaperAccountingService(
                repository=self.repository,
                initial_equity=self.initial_equity,
                fee_rate=self.fee_rate,
            ).process_filled_order(order, strategy_name, timestamp)
```

with:

```python
        if order.status == OrderStatus.FILLED and not self.live_safeguards:
            PaperAccountingService(
                repository=self.repository,
                initial_equity=self.initial_equity,
                fee_rate=self.fee_rate,
            ).process_filled_order(order, strategy_name, timestamp)
```

- [x] **Step 5: Run order manager tests**

Run:

```bash
uv run pytest tests/unit/test_order_router.py -v
```

Expected:

```text
... passed
```

- [ ] **Step 6: Checkpoint commit if explicitly approved**

Run only after the user explicitly approves committing:

```bash
git add src/order/manager.py tests/unit/test_order_router.py
git commit -m "fix: refresh live state before order risk checks"
```

---

## Task 5: Replace hard reduce-only live behavior with configurable opening controls

**Files:**
- Modify: `src/core/config.py`
- Modify: `src/web/api/settings.py:25-156`
- Modify: `src/web/api/strategies.py:88-170`
- Modify: `src/order/manager.py:22-232`
- Test: `tests/unit/test_order_router.py`
- Test: `tests/integration/test_web_api.py`

- [x] **Step 1: Write failing tests for safe opening controls**

Append to `tests/unit/test_order_router.py`:

```python
@pytest.mark.asyncio
async def test_live_order_manager_rejects_opening_order_by_default():
    handler = MockHandler()
    repository = FakeRepository()
    repository.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        initial_equity=100000.0,
        cash_balance=100000.0,
        equity=100000.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
    )
    router = OrderRouter(backtest=None, live=handler, mode="live")
    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        initial_equity=100000.0,
        risk_manager=RiskManager(max_position_pct=0.8, max_daily_loss_pct=0.05),
        price_provider=lambda symbol: 50000.0,
        live_safeguards=True,
        live_market_type="swap",
    )

    order = await manager.submit(
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.1,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.REJECTED
    assert repository.orders[-1].status == "rejected"
    assert risk_reason_code("Live opening orders are disabled") == "live_opening_disabled"
    assert handler.submitted == []


@pytest.mark.asyncio
async def test_live_order_manager_allows_opening_order_when_enabled_and_within_limits():
    handler = MockHandler()
    repository = FakeRepository()
    repository.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        initial_equity=100000.0,
        cash_balance=100000.0,
        equity=100000.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
    )
    router = OrderRouter(backtest=None, live=handler, mode="live")
    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        initial_equity=100000.0,
        risk_manager=RiskManager(max_position_pct=0.8, max_daily_loss_pct=0.05),
        price_provider=lambda symbol: 50000.0,
        live_safeguards=True,
        live_market_type="swap",
        allow_live_open_orders=True,
        live_max_order_notional=10000.0,
    )

    order = await manager.submit(
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.1,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.FILLED
    assert handler.submitted[0].params == {}


@pytest.mark.asyncio
async def test_live_order_manager_rejects_opening_order_over_notional_cap():
    handler = MockHandler()
    repository = FakeRepository()
    repository.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        initial_equity=100000.0,
        cash_balance=100000.0,
        equity=100000.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
    )
    router = OrderRouter(backtest=None, live=handler, mode="live")
    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        initial_equity=100000.0,
        risk_manager=RiskManager(max_position_pct=0.8, max_daily_loss_pct=0.05),
        price_provider=lambda symbol: 50000.0,
        live_safeguards=True,
        live_market_type="swap",
        allow_live_open_orders=True,
        live_max_order_notional=1000.0,
    )

    order = await manager.submit(
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.1,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.REJECTED
    assert risk_reason_code("Live order exceeds configured notional cap") == "live_order_notional_exceeded"
    assert handler.submitted == []
```

- [x] **Step 2: Write failing settings API assertions**

In `tests/integration/test_web_api.py`, update settings payloads under `risk` to include:

```python
"allow_live_open_orders": False,
"live_max_order_notional": 0.0,
```

In `test_get_and_update_settings`, set non-default values in the update request:

```python
"risk": {
    "max_daily_loss_pct": 0.03,
    "max_drawdown_pct": 0.12,
    "max_total_position_pct": 0.65,
    "allow_live_open_orders": True,
    "live_max_order_notional": 2500.0,
},
```

Assert the saved response includes:

```python
assert saved["risk"] == {
    "max_daily_loss_pct": 0.03,
    "max_drawdown_pct": 0.12,
    "max_total_position_pct": 0.65,
    "allow_live_open_orders": True,
    "live_max_order_notional": 2500.0,
}
```

- [x] **Step 3: Run failing tests**

Run:

```bash
uv run pytest tests/unit/test_order_router.py::test_live_order_manager_rejects_opening_order_by_default tests/unit/test_order_router.py::test_live_order_manager_allows_opening_order_when_enabled_and_within_limits tests/unit/test_order_router.py::test_live_order_manager_rejects_opening_order_over_notional_cap tests/integration/test_web_api.py::test_get_and_update_settings -v
```

Expected:

```text
FAILED ... TypeError: UnifiedOrderManager.__init__() got an unexpected keyword argument 'allow_live_open_orders'
FAILED ... AssertionError ... risk settings missing allow_live_open_orders
```

- [x] **Step 4: Add config and settings fields**

In `src/core/config.py`, add fields to `RiskConfig`:

```python
    allow_live_open_orders: bool = False
    live_max_order_notional: float = 0.0
```

In `src/web/api/settings.py`, update `RiskSettings`:

```python
class RiskSettings(BaseModel):
    max_daily_loss_pct: float = 0.05
    max_drawdown_pct: float = 0.15
    max_total_position_pct: float = 0.8
    allow_live_open_orders: bool = False
    live_max_order_notional: float = 0.0
```

Update `_settings_from_config()` risk mapping:

```python
        risk=RiskSettings(
            max_daily_loss_pct=config.risk.max_daily_loss_pct,
            max_drawdown_pct=config.risk.max_drawdown_pct,
            max_total_position_pct=config.risk.max_total_position_pct,
            allow_live_open_orders=config.risk.allow_live_open_orders,
            live_max_order_notional=config.risk.live_max_order_notional,
        ),
```

Update `_config_from_settings()` risk mapping:

```python
        risk=RiskConfig(
            max_daily_loss_pct=settings.risk.max_daily_loss_pct,
            max_drawdown_pct=settings.risk.max_drawdown_pct,
            max_total_position_pct=settings.risk.max_total_position_pct,
            allow_live_open_orders=settings.risk.allow_live_open_orders,
            live_max_order_notional=settings.risk.live_max_order_notional,
        ),
```

- [x] **Step 5: Pass live-opening controls into the order manager**

In `src/web/api/strategies.py`, update the `UnifiedOrderManager(...)` call in `create_order_manager()`:

```python
        live_safeguards=resolved_mode == "live",
        live_market_type=settings.exchange.market_type if resolved_mode == "live" else "",
        allow_live_open_orders=settings.risk.allow_live_open_orders if resolved_mode == "live" else False,
        live_max_order_notional=settings.risk.live_max_order_notional if resolved_mode == "live" else 0.0,
```

- [x] **Step 6: Replace reduce-only-only safety with configurable live safety**

In `src/order/manager.py`, update `risk_reason_code()` mapping:

```python
        "Live opening orders are disabled": "live_opening_disabled",
        "Live order exceeds configured notional cap": "live_order_notional_exceeded",
        "Live spot sell requires existing position": "live_spot_position_required",
```

Update `UnifiedOrderManager.__init__` signature:

```python
        live_state_refresher: LiveStateRefresher | None = None,
        allow_live_open_orders: bool = False,
        live_max_order_notional: float = 0.0,
```

Add assignments:

```python
        self.allow_live_open_orders = allow_live_open_orders
        self.live_max_order_notional = live_max_order_notional
```

In `_check_risk_gate()`, replace:

```python
            live_result = self._check_live_reduce_only(order, account, position, order_price)
```

with:

```python
            live_result = self._check_live_order_safety(order, account, position, order_price)
```

Replace `_check_live_reduce_only()` with:

```python
    def _check_live_order_safety(
        self,
        order: Order,
        account: Any,
        position: Any,
        order_price: float | None,
    ) -> RiskGateResult:
        if account is None:
            return RiskGateResult(False, "Live safeguards require account state")
        if order_price is None or order_price <= 0:
            return RiskGateResult(False, "Live safeguards require a current price")

        current_amount = 0.0
        has_position = position is not None and float(getattr(position, "amount", 0.0) or 0.0) > 0
        if has_position:
            current_amount = abs(float(getattr(position, "amount", 0.0) or 0.0))
            if getattr(position, "side", "long") == "short":
                current_amount = -current_amount

        order_amount = order.amount if order.side == OrderSide.BUY else -order.amount
        resulting_amount = current_amount + order_amount
        is_reducing = has_position and abs(resulting_amount) < abs(current_amount)
        if is_reducing:
            if self.live_market_type != "spot":
                order.reduce_only = True
                order.params = {**order.params, "reduceOnly": True}
            return RiskGateResult(True, effective_price=order_price)

        if not self.allow_live_open_orders:
            return RiskGateResult(
                False,
                "Live opening orders are disabled",
                order_value=abs(resulting_amount) * order_price,
                effective_price=order_price,
            )

        if self.live_market_type == "spot" and order.side == OrderSide.SELL and not has_position:
            return RiskGateResult(
                False,
                "Live spot sell requires existing position",
                order_value=order.amount * order_price,
                effective_price=order_price,
            )

        order_value = order.amount * order_price
        if self.live_max_order_notional > 0 and order_value > self.live_max_order_notional:
            return RiskGateResult(
                False,
                "Live order exceeds configured notional cap",
                order_value=order_value,
                effective_price=order_price,
            )

        return RiskGateResult(True, order_value=order_value, effective_price=order_price)
```

- [x] **Step 7: Run targeted and related tests**

Run:

```bash
uv run pytest tests/unit/test_order_router.py tests/integration/test_web_api.py::test_get_and_update_settings -v
```

Expected:

```text
... passed
```

- [ ] **Step 8: Checkpoint commit if explicitly approved**

Run only after the user explicitly approves committing:

```bash
git add src/core/config.py src/web/api/settings.py src/web/api/strategies.py src/order/manager.py tests/unit/test_order_router.py tests/integration/test_web_api.py
git commit -m "feat: add guarded live opening controls"
```

---

## Task 6: Wire private state refresh into strategy start and trading API

**Files:**
- Modify: `src/exchange/live_sync.py`
- Modify: `src/web/api/strategies.py:132-504`
- Modify: `src/web/api/trading.py`
- Test: `tests/integration/test_web_api.py`

- [x] **Step 1: Write failing integration tests for manual live state refresh**

In `tests/integration/test_web_api.py`, add a test that monkeypatches the live sync runner and calls the trading API:

```python
@pytest.mark.asyncio
async def test_refresh_live_state_endpoint_requires_live_mode(monkeypatch, app):
    monkeypatch.setattr(
        "src.web.api.trading.load_runtime_settings",
        lambda: AppConfig(mode="paper"),
    )
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/trading/live-state/refresh", params={"strategy": "ma_cross"})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Live state refresh requires live mode"


@pytest.mark.asyncio
async def test_refresh_live_state_endpoint_persists_and_returns_state(monkeypatch, app):
    account = AccountRecord(
        strategy="ma_cross",
        initial_equity=1000.0,
        cash_balance=950.0,
        equity=1000.0,
        realized_pnl=0.0,
        unrealized_pnl=50.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
    )
    position = PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
        mark_price=51000.0,
        unrealized_pnl=1000.0,
    )
    calls = []

    async def fake_refresh(exchange, repository, strategy, symbols, timestamp_ms):
        calls.append((exchange.market_type, strategy, symbols))
        return LiveStateSyncResult(account=account, positions=[position])

    monkeypatch.setattr(
        "src.web.api.trading.load_runtime_settings",
        lambda: AppConfig(
            mode="live",
            exchange=ExchangeConfig(
                api_key="key",
                secret="secret",
                passphrase="pass",
                market_type="swap",
                demo=True,
            ),
        ),
    )
    monkeypatch.setattr("src.web.api.trading.refresh_okx_live_state", fake_refresh)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/trading/live-state/refresh",
            params={"strategy": "ma_cross", "symbol": "BTC-USDT-SWAP"},
        )

    assert resp.status_code == 200
    assert calls == [("swap", "ma_cross", ["BTC-USDT-SWAP"])]
    payload = resp.json()
    assert payload["account"]["equity"] == 1000.0
    assert payload["positions"][0]["symbol"] == "BTC-USDT-SWAP"
```

Add imports used by the new tests:

```python
from src.exchange.live_sync import LiveStateSyncResult
```

- [x] **Step 2: Run failing endpoint tests**

Run:

```bash
uv run pytest tests/integration/test_web_api.py::test_refresh_live_state_endpoint_requires_live_mode tests/integration/test_web_api.py::test_refresh_live_state_endpoint_persists_and_returns_state -v
```

Expected:

```text
FAILED ... assert 404 == 400
FAILED ... AttributeError: module 'src.web.api.trading' has no attribute 'refresh_okx_live_state'
```

- [x] **Step 3: Add a reusable OKX live refresh runner**

In `src/exchange/live_sync.py`, add this import:

```python
from src.exchange.factory import create_okx_adapter
```

Add this function after `LiveStateSyncService`:

```python
async def refresh_okx_live_state(
    exchange,
    repository,
    strategy: str,
    symbols: list[str] | None,
    timestamp_ms: Callable[[], int],
) -> LiveStateSyncResult:
    adapter = create_okx_adapter(exchange)
    try:
        return await LiveStateSyncService(adapter, repository, timestamp_ms).refresh(strategy, symbols)
    finally:
        await adapter.close()
```

- [x] **Step 4: Pass the refresher into live order managers**

In `src/web/api/strategies.py`, add import:

```python
from src.exchange.live_sync import refresh_okx_live_state
```

In `create_order_manager()`, introduce a repository variable before creating `OrderRouter`:

```python
    order_repository = repository if repository is not None else Repository()
```

Add this helper inside `create_order_manager()` before constructing `UnifiedOrderManager`:

```python
    async def live_state_refresher(strategy_name: str, symbol: str) -> None:
        await refresh_okx_live_state(
            settings.exchange,
            order_repository,
            strategy_name,
            [symbol],
            current_timestamp_ms,
        )
```

In the `UnifiedOrderManager(...)` call, replace the repository argument and pass the refresher:

```python
        repository=order_repository,
        live_state_refresher=live_state_refresher if resolved_mode == "live" else None,
```

- [x] **Step 5: Refresh live state on strategy start**

In `src/web/api/strategies.py`, inside `start_strategy()` after `strategy = create_strategy(name)` and before `set_order_manager = getattr(strategy, "set_order_manager", None)`, add:

```python
                    if order_router_mode == "live":
                        await refresh_okx_live_state(
                            load_runtime_settings().exchange,
                            repository,
                            strategy.name,
                            [strategy.symbol],
                            current_timestamp_ms,
                        )
```

- [x] **Step 6: Add manual trading API endpoint**

In `src/web/api/trading.py`, add imports:

```python
import time

from fastapi import HTTPException

from src.core.runtime_settings import load_runtime_settings
from src.exchange.live_sync import refresh_okx_live_state
```

Add a local timestamp helper near other helpers:

```python
def current_timestamp_ms() -> int:
    return int(time.time() * 1000)
```

Inside the trading router, add:

```python
@router.post("/live-state/refresh")
async def refresh_live_state(strategy: str, symbol: str | None = None) -> dict[str, Any]:
    settings = load_runtime_settings()
    if settings.mode.strip().lower() != "live":
        raise HTTPException(status_code=400, detail="Live state refresh requires live mode")
    repository = Repository()
    result = await refresh_okx_live_state(
        settings.exchange,
        repository,
        strategy,
        [symbol] if symbol else None,
        current_timestamp_ms,
    )
    return {
        "account": serialize_account(result.account),
        "positions": serialize_records(result.positions),
    }
```

- [x] **Step 7: Run endpoint and strategy tests**

Run:

```bash
uv run pytest tests/integration/test_web_api.py::test_refresh_live_state_endpoint_requires_live_mode tests/integration/test_web_api.py::test_refresh_live_state_endpoint_persists_and_returns_state tests/integration/test_web_api.py::test_start_strategy -v
```

Expected:

```text
... passed
```

- [ ] **Step 8: Checkpoint commit if explicitly approved**

Run only after the user explicitly approves committing:

```bash
git add src/exchange/live_sync.py src/web/api/strategies.py src/web/api/trading.py tests/integration/test_web_api.py
git commit -m "feat: refresh OKX live state from API and strategies"
```

---

## Task 7: Expose market type, OKX demo mode, and live-opening controls in settings UI

**Files:**
- Modify: `frontend/src/types/settings.ts`
- Modify: `frontend/src/views/Settings.vue`
- Modify: `frontend/src/locales/en.ts:112-146`
- Modify: `frontend/src/locales/zh-CN.ts:112-146`
- Test: `frontend/src/services/settings.test.ts`

- [x] **Step 1: Write failing frontend settings service test expectations**

In `frontend/src/services/settings.test.ts`, update the `exchange` object in test settings update payloads:

```ts
exchange: {
  api_key: 'okx-api-key',
  secret: 'okx-secret-value',
  passphrase: 'okx-passphrase',
  market_type: 'swap',
  demo: true,
},
```

Update `risk` in the same payloads:

```ts
risk: {
  max_daily_loss_pct: 0.03,
  max_drawdown_pct: 0.12,
  max_total_position_pct: 0.65,
  allow_live_open_orders: false,
  live_max_order_notional: 0,
},
```

If the test defines a mocked settings response, include:

```ts
exchange: {
  api_key: 'ok*******ey',
  api_key_set: true,
  secret: 'ok************ue',
  secret_set: true,
  passphrase: 'ok**********se',
  passphrase_set: true,
  market_type: 'swap',
  demo: true,
},
risk: {
  max_daily_loss_pct: 0.03,
  max_drawdown_pct: 0.12,
  max_total_position_pct: 0.65,
  allow_live_open_orders: false,
  live_max_order_notional: 0,
},
```

- [x] **Step 2: Run failing frontend type check**

Run:

```bash
cd frontend && npm run build
```

Expected:

```text
vue-tsc --noEmit
... Property 'market_type' does not exist on type 'ExchangeSettingsUpdate'
... Property 'allow_live_open_orders' does not exist on type 'RiskSettings'
```

- [x] **Step 3: Update frontend settings types**

In `frontend/src/types/settings.ts`, update `ExchangeSettingsView`:

```ts
export interface ExchangeSettingsView {
  api_key: string;
  api_key_set: boolean;
  secret: string;
  secret_set: boolean;
  passphrase: string;
  passphrase_set: boolean;
  market_type: string;
  demo: boolean;
}
```

Update `ExchangeSettingsUpdate`:

```ts
export interface ExchangeSettingsUpdate {
  api_key: string;
  secret: string;
  passphrase: string;
  market_type: string;
  demo: boolean;
}
```

Update the risk settings interface:

```ts
export interface RiskSettings {
  max_daily_loss_pct: number;
  max_drawdown_pct: number;
  max_total_position_pct: number;
  allow_live_open_orders: boolean;
  live_max_order_notional: number;
}
```

- [x] **Step 4: Update Settings.vue reactive defaults and applySettings**

In `frontend/src/views/Settings.vue`, update the reactive `form.exchange` default:

```ts
exchange: {
  api_key: '',
  secret: '',
  passphrase: '',
  market_type: 'spot',
  demo: true,
},
```

Update the reactive `form.risk` default:

```ts
risk: {
  max_daily_loss_pct: 0.05,
  max_drawdown_pct: 0.15,
  max_total_position_pct: 0.8,
  allow_live_open_orders: false,
  live_max_order_notional: 0,
},
```

In `applySettings(settings)`, after resetting credential strings, add:

```ts
form.exchange.market_type = settings.exchange.market_type;
form.exchange.demo = settings.exchange.demo;
```

Keep this existing risk copy so new risk fields are preserved:

```ts
form.risk = { ...settings.risk };
```

- [x] **Step 5: Add settings UI controls**

In `frontend/src/views/Settings.vue`, add these form items inside the OKX exchange section after the mode selector and before credentials:

```vue
<el-form-item :label="t('settings.marketType')">
  <el-select v-model="form.exchange.market_type">
    <el-option :label="t('settings.marketTypes.spot')" value="spot" />
    <el-option :label="t('settings.marketTypes.swap')" value="swap" />
    <el-option :label="t('settings.marketTypes.future')" value="future" />
    <el-option :label="t('settings.marketTypes.option')" value="option" />
  </el-select>
</el-form-item>

<el-form-item :label="t('settings.okxDemo')">
  <el-switch v-model="form.exchange.demo" />
</el-form-item>
```

Add these risk controls after `maxTotalPositionPct`:

```vue
<el-form-item :label="t('settings.allowLiveOpenOrders')">
  <el-switch v-model="form.risk.allow_live_open_orders" />
</el-form-item>

<el-form-item :label="t('settings.liveMaxOrderNotional')">
  <el-input-number v-model="form.risk.live_max_order_notional" :min="0" :step="100" />
</el-form-item>
```

- [x] **Step 6: Add English locale labels**

In `frontend/src/locales/en.ts`, add these keys under `settings` after `okxExchange`:

```ts
marketType: 'Market Type',
marketTypes: {
  spot: 'Spot',
  swap: 'Swap',
  future: 'Futures',
  option: 'Options',
},
okxDemo: 'OKX Demo Trading',
```

Add these keys under the risk labels:

```ts
allowLiveOpenOrders: 'Allow Live Opening Orders',
liveMaxOrderNotional: 'Live Max Order Notional',
```

- [x] **Step 7: Add Chinese locale labels**

In `frontend/src/locales/zh-CN.ts`, add these keys under `settings` after `okxExchange`:

```ts
marketType: '市场类型',
marketTypes: {
  spot: '现货',
  swap: '永续',
  future: '交割',
  option: '期权',
},
okxDemo: 'OKX 模拟盘',
```

Add these keys under the risk labels:

```ts
allowLiveOpenOrders: '允许实盘开仓',
liveMaxOrderNotional: '实盘单笔最大名义价值',
```

- [x] **Step 8: Run frontend build and settings tests**

Run:

```bash
cd frontend && npm run build
```

Expected:

```text
vue-tsc --noEmit
vite build
✓ built
```

If `frontend/src/services/settings.test.ts` is run separately with Vitest in this repo, run:

```bash
cd frontend && npx vitest run frontend/src/services/settings.test.ts
```

Expected:

```text
PASS frontend/src/services/settings.test.ts
```

- [ ] **Step 9: Checkpoint commit if explicitly approved**

Run only after the user explicitly approves committing:

```bash
git add frontend/src/types/settings.ts frontend/src/views/Settings.vue frontend/src/locales/en.ts frontend/src/locales/zh-CN.ts frontend/src/services/settings.test.ts
git commit -m "feat: expose OKX live settings in UI"
```

---

## Task 8: Make market data APIs and frontend market page market-type-aware

**Files:**
- Modify: `src/web/api/market.py`
- Modify: `tests/integration/test_web_api.py`
- Modify: `frontend/src/types/market.ts`
- Modify: `frontend/src/services/market.ts:1-45`
- Modify: `frontend/src/views/Market.vue`
- Modify: `frontend/src/locales/en.ts:96-110`
- Modify: `frontend/src/locales/zh-CN.ts:96-110`

- [x] **Step 1: Write failing market API tests**

In `tests/integration/test_web_api.py`, add tests that patch market adapters and assert derivative selection:

```python
@pytest.mark.asyncio
async def test_market_klines_uses_requested_swap_adapter(monkeypatch, app):
    constructed = []

    class SentinelSwapAdapter:
        def __init__(self, api_key, secret, passphrase, demo=True):
            constructed.append((api_key, secret, passphrase, demo))

        async def fetch_ohlcv(self, symbol, timeframe, limit=100, since=None):
            return [Bar(timestamp=1700000000000, open=1, high=2, low=0.5, close=1.5, volume=10)]

        async def fetch_tickers(self, symbols):
            return []

        async def submit(self, order):
            return order

        async def cancel(self, order_id, symbol=None):
            return True

        async def close(self):
            pass

    monkeypatch.setattr("src.web.api.market.OKXSwapAdapter", SentinelSwapAdapter)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/market/klines",
            params={"market_type": "swap", "symbol": "BTC-USDT-SWAP", "timeframe": "1h"},
        )

    assert resp.status_code == 200
    assert constructed == [("", "", "", True)]
    assert resp.json()[0]["symbol"] == "BTC-USDT-SWAP"


@pytest.mark.asyncio
async def test_market_tickers_accepts_symbols_for_options(monkeypatch, app):
    calls = []

    class SentinelOptionsAdapter:
        def __init__(self, api_key, secret, passphrase, demo=True):
            pass

        async def fetch_ohlcv(self, symbol, timeframe, limit=100, since=None):
            return []

        async def fetch_tickers(self, symbols):
            calls.append(symbols)
            return [{"symbol": symbols[0], "last": 1.0, "bidPx": 0.9, "askPx": 1.1, "vol24h": 2.0}]

        async def submit(self, order):
            return order

        async def cancel(self, order_id, symbol=None):
            return True

        async def close(self):
            pass

    monkeypatch.setattr("src.web.api.market.OKXOptionsAdapter", SentinelOptionsAdapter)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/market/tickers",
            params=[("market_type", "option"), ("symbols", "BTC-USDT-260626-100000-C")],
        )

    assert resp.status_code == 200
    assert calls == [["BTC-USDT-260626-100000-C"]]
    assert resp.json()[0]["symbol"] == "BTC-USDT-260626-100000-C"
```

Add imports if missing:

```python
from src.core.types import Bar
```

- [x] **Step 2: Run failing market tests**

Run:

```bash
uv run pytest tests/integration/test_web_api.py::test_market_klines_uses_requested_swap_adapter tests/integration/test_web_api.py::test_market_tickers_accepts_symbols_for_options -v
```

Expected:

```text
FAILED ... AttributeError: module 'src.web.api.market' has no attribute 'OKXSwapAdapter'
FAILED ... AttributeError: module 'src.web.api.market' has no attribute 'OKXOptionsAdapter'
```

- [x] **Step 3: Update market API adapter selection**

In `src/web/api/market.py`, replace the spot-only adapter import with:

```python
from src.exchange.okx_futures import OKXFuturesAdapter
from src.exchange.okx_options import OKXOptionsAdapter
from src.exchange.okx_spot import OKXSpotAdapter
from src.exchange.okx_swap import OKXSwapAdapter
```

Replace `_MARKET_SYMBOLS` with:

```python
_MARKET_SYMBOLS_BY_TYPE = {
    "spot": ["BTC-USDT", "ETH-USDT", "OKB-USDT", "SOL-USDT"],
    "swap": ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"],
    "future": ["BTC-USDT-260626", "ETH-USDT-260626"],
    "option": [],
}
```

Add helpers near the top of the file:

```python
def normalize_market_type(market_type: str | None) -> str:
    normalized = (market_type or "spot").strip().lower()
    if normalized == "futures":
        return "future"
    if normalized == "options":
        return "option"
    if normalized not in _MARKET_SYMBOLS_BY_TYPE:
        raise HTTPException(status_code=400, detail=f"Unsupported market_type: {market_type}")
    return normalized


def create_public_market_adapter(market_type: str):
    adapter_cls = {
        "spot": OKXSpotAdapter,
        "swap": OKXSwapAdapter,
        "future": OKXFuturesAdapter,
        "option": OKXOptionsAdapter,
    }[market_type]
    return adapter_cls(api_key="", secret="", passphrase="")
```

Update `get_klines()` signature to accept `market_type`:

```python
    market_type: str | None = None,
```

Inside `get_klines()`, replace adapter creation:

```python
    resolved_market_type = normalize_market_type(market_type)
    adapter = create_public_market_adapter(resolved_market_type)
```

Update `get_tickers()` signature:

```python
async def get_tickers(
    market_type: str | None = None,
    symbols: list[str] | None = Query(default=None),
) -> list[dict[str, float | str]]:
```

Replace ticker adapter creation and symbols:

```python
    resolved_market_type = normalize_market_type(market_type)
    requested_symbols = symbols or _MARKET_SYMBOLS_BY_TYPE[resolved_market_type]
    adapter = create_public_market_adapter(resolved_market_type)
    try:
        try:
            return await adapter.fetch_tickers(requested_symbols)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=_MARKET_FETCH_ERROR_DETAIL) from exc
    finally:
        await adapter.close()
```

- [x] **Step 4: Run market API tests**

Run:

```bash
uv run pytest tests/integration/test_web_api.py::test_market_klines_uses_requested_swap_adapter tests/integration/test_web_api.py::test_market_tickers_accepts_symbols_for_options -v
```

Expected:

```text
2 passed
```

- [x] **Step 5: Update frontend market query types and service**

In `frontend/src/types/market.ts`, add `market_type` to `KlineQuery`:

```ts
export interface KlineQuery {
  symbol: string;
  timeframe: string;
  limit?: number;
  start_time?: number;
  end_time?: number;
  market_type?: string;
}
```

In `frontend/src/services/market.ts`, update `fetchTickers()`:

```ts
export async function fetchTickers(marketType = 'spot', symbols?: string[]): Promise<MarketTicker[]> {
  const { data } = await axios.get<RawMarketTicker[]>('/api/market/tickers', {
    params: {
      market_type: marketType,
      symbols,
    },
  });
  return data.map(normalizeTicker);
}
```

- [x] **Step 6: Update Market.vue controls**

In `frontend/src/views/Market.vue`, add reactive state for market type:

```ts
const marketType = ref('spot');
```

When building kline query payloads, include:

```ts
market_type: marketType.value,
```

Add a market type selector near the symbol selector:

```vue
<el-form-item :label="t('market.marketType')">
  <el-select v-model="marketType">
    <el-option :label="t('settings.marketTypes.spot')" value="spot" />
    <el-option :label="t('settings.marketTypes.swap')" value="swap" />
    <el-option :label="t('settings.marketTypes.future')" value="future" />
    <el-option :label="t('settings.marketTypes.option')" value="option" />
  </el-select>
</el-form-item>
```

- [x] **Step 7: Add market locale labels**

In `frontend/src/locales/en.ts`, add under `market`:

```ts
marketType: 'Market Type',
```

In `frontend/src/locales/zh-CN.ts`, add under `market`:

```ts
marketType: '市场类型',
```

- [x] **Step 8: Run backend market tests and frontend build**

Run:

```bash
uv run pytest tests/integration/test_web_api.py::test_market_klines_uses_requested_swap_adapter tests/integration/test_web_api.py::test_market_tickers_accepts_symbols_for_options -v
```

Expected:

```text
2 passed
```

Run:

```bash
cd frontend && npm run build
```

Expected:

```text
vue-tsc --noEmit
vite build
✓ built
```

- [ ] **Step 9: Checkpoint commit if explicitly approved**

Run only after the user explicitly approves committing:

```bash
git add src/web/api/market.py tests/integration/test_web_api.py frontend/src/types/market.ts frontend/src/services/market.ts frontend/src/views/Market.vue frontend/src/locales/en.ts frontend/src/locales/zh-CN.ts
git commit -m "feat: support market-type-aware data endpoints"
```

---

## Task 9: Support OKX stop, trigger, stop-loss, and take-profit parameters

**Files:**
- Modify: `src/core/types.py:38-52`
- Modify: `src/order/manager.py:72-93`
- Modify: `src/exchange/base.py:139-162`
- Test: `tests/unit/test_exchange_base.py`
- Test: `tests/unit/test_order_router.py`

- [x] **Step 1: Write failing adapter tests for trigger and SL/TP params**

Append to `tests/unit/test_exchange_base.py`:

```python
@pytest.mark.asyncio
async def test_okx_base_adapter_maps_stop_order_to_trigger_params():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC-USDT-SWAP": {
            "limits": {"amount": {"min": 0.001}, "cost": {"min": 1}},
            "precision": {"amount": 3, "price": 1},
        }
    }
    order = Order(
        id="1",
        symbol="BTC-USDT-SWAP",
        side=OrderSide.SELL,
        type=OrderType.STOP,
        amount=0.01,
        price=None,
        trigger_price=49000.0,
        reduce_only=True,
        params={"reduceOnly": True},
    )

    result = await adapter.submit(order)

    assert result.id == "okx-1"
    assert fake.create_order_calls == [
        {
            "symbol": "BTC-USDT-SWAP",
            "type": "market",
            "side": "sell",
            "amount": 0.01,
            "price": None,
            "params": {"reduceOnly": True, "triggerPrice": 49000.0},
        }
    ]


@pytest.mark.asyncio
async def test_okx_base_adapter_passes_stop_loss_take_profit_params():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC-USDT-SWAP": {
            "limits": {"amount": {"min": 0.001}, "cost": {"min": 1}},
            "precision": {"amount": 3, "price": 1},
        }
    }
    order = Order(
        id="1",
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=0.01,
        price=50000.0,
        stop_loss=49000.0,
        take_profit=52000.0,
    )

    await adapter.submit(order)

    assert fake.create_order_calls == [
        {
            "symbol": "BTC-USDT-SWAP",
            "type": "limit",
            "side": "buy",
            "amount": 0.01,
            "price": 50000.0,
            "params": {
                "stopLoss": {"triggerPrice": 49000.0},
                "takeProfit": {"triggerPrice": 52000.0},
            },
        }
    ]


@pytest.mark.asyncio
async def test_okx_base_adapter_rejects_stop_order_without_trigger_price():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC-USDT-SWAP": {
            "limits": {"amount": {"min": 0.001}, "cost": {"min": 1}},
            "precision": {"amount": 3, "price": 1},
        }
    }
    order = Order(
        id="1",
        symbol="BTC-USDT-SWAP",
        side=OrderSide.SELL,
        type=OrderType.STOP,
        amount=0.01,
    )

    with pytest.raises(ValueError, match="Stop orders require trigger_price"):
        await adapter.submit(order)

    assert fake.create_order_calls == []
```

- [x] **Step 2: Write failing order manager test for trigger price propagation**

Append to `tests/unit/test_order_router.py`:

```python
@pytest.mark.asyncio
async def test_order_manager_propagates_trigger_price_to_order():
    handler = MockHandler()
    router = OrderRouter(backtest=handler, mode="backtest")
    manager = UnifiedOrderManager(
        router=router,
        timestamp_ms=lambda: 1700000000000,
        price_provider=lambda symbol: 50000.0,
    )

    order = await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.SELL,
        order_type=OrderType.STOP,
        amount=0.1,
        trigger_price=49000.0,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.FILLED
    assert handler.submitted[0].trigger_price == 49000.0
```

- [x] **Step 3: Run failing trigger tests**

Run:

```bash
uv run pytest tests/unit/test_exchange_base.py::test_okx_base_adapter_maps_stop_order_to_trigger_params tests/unit/test_exchange_base.py::test_okx_base_adapter_passes_stop_loss_take_profit_params tests/unit/test_exchange_base.py::test_okx_base_adapter_rejects_stop_order_without_trigger_price tests/unit/test_order_router.py::test_order_manager_propagates_trigger_price_to_order -v
```

Expected:

```text
FAILED ... TypeError: Order.__init__() got an unexpected keyword argument 'trigger_price'
FAILED ... TypeError: UnifiedOrderManager.submit() got an unexpected keyword argument 'trigger_price'
```

- [x] **Step 4: Add trigger_price to shared Order and manager submit**

In `src/core/types.py`, add field to `Order` after `price`:

```python
    trigger_price: float | None = None
```

In `src/order/manager.py`, update `submit()` signature:

```python
        price: float | None = None,
        trigger_price: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
```

When constructing `Order`, add:

```python
            trigger_price=trigger_price,
```

- [x] **Step 5: Map OKX trigger and attached SL/TP params**

In `src/exchange/base.py`, replace the first lines of `submit()`:

```python
        if order.type.value == "stop":
            raise ValueError("Stop orders require OKX trigger parameters")
        if order.stop_loss is not None or order.take_profit is not None:
            raise ValueError("OKX stop_loss and take_profit are not supported")
        await self._validate_order_against_market(order)
        response = await self._exchange.create_order(
            order.symbol,
            order.type.value,
            order.side.value,
            order.amount,
            order.price,
            dict(order.params),
        )
```

with:

```python
        order_type, params = self._okx_order_type_and_params(order)
        await self._validate_order_against_market(order)
        response = await self._exchange.create_order(
            order.symbol,
            order_type,
            order.side.value,
            order.amount,
            order.price,
            params,
        )
```

Add this helper before `_validate_order_against_market()`:

```python
    def _okx_order_type_and_params(self, order: Order) -> tuple[str, dict[str, object]]:
        params = dict(order.params)
        order_type = order.type.value
        if order.type.value == "stop":
            if order.trigger_price is None:
                raise ValueError("Stop orders require trigger_price")
            order_type = "market" if order.price is None else "limit"
            params["triggerPrice"] = order.trigger_price
        if order.stop_loss is not None:
            params["stopLoss"] = {"triggerPrice": order.stop_loss}
        if order.take_profit is not None:
            params["takeProfit"] = {"triggerPrice": order.take_profit}
        return order_type, params
```

- [x] **Step 6: Run trigger and exchange tests**

Run:

```bash
uv run pytest tests/unit/test_exchange_base.py tests/unit/test_order_router.py::test_order_manager_propagates_trigger_price_to_order -v
```

Expected:

```text
... passed
```

- [ ] **Step 7: Checkpoint commit if explicitly approved**

Run only after the user explicitly approves committing:

```bash
git add src/core/types.py src/order/manager.py src/exchange/base.py tests/unit/test_exchange_base.py tests/unit/test_order_router.py
git commit -m "feat: support OKX trigger order parameters"
```

---

## Task 10: Full verification and demo-mode smoke path

**Files:**
- No source files expected for this task.
- Use this task to validate the completed implementation.

- [x] **Step 1: Run Python formatting/lint checks**

Run:

```bash
uv run ruff check src tests
```

Expected:

```text
All checks passed!
```

- [x] **Step 2: Run backend unit and integration tests**

Run:

```bash
uv run pytest tests/unit/test_exchange_base.py tests/unit/test_live_sync.py tests/unit/test_order_router.py tests/integration/test_web_api.py -v
```

Expected:

```text
... passed
```

- [x] **Step 3: Run the full backend test suite**

Run:

```bash
uv run pytest -v
```

Expected:

```text
... passed
```

- [x] **Step 4: Run frontend build**

Run:

```bash
cd frontend && npm run build
```

Expected:

```text
vue-tsc --noEmit
vite build
✓ built
```

- [x] **Step 5: Manual UI verification in OKX demo mode**

Start the backend and frontend using the repository's normal commands. If the backend command is not already running in the session, use the documented project command for the FastAPI app. Start the frontend with:

```bash
cd frontend && npm run dev -- --host 127.0.0.1
```

Expected:

```text
VITE ... ready
Local: http://127.0.0.1:5173/
```

Manual checks:

1. Open Settings.
2. Set mode to `Live`.
3. Set market type to `Swap`.
4. Keep `OKX Demo Trading` enabled.
5. Leave `Allow Live Opening Orders` disabled.
6. Save settings.
7. Confirm existing credentials display only masked values.
8. Open Market and select `Swap`; load `BTC-USDT-SWAP` candles.
9. Start a strategy in demo live mode.
10. Confirm live state refresh does not print secrets and risk events reject opening orders when the allow-open switch is off.

Verified on 2026-06-17 with live + swap + OKX demo mode: settings used masked/presence-only credential output, the swap Market page rendered a `BTC-USDT-SWAP` chart, `ma_cross` started and stopped, live state synced from OKX demo, and a guarded opening attempt was rejected locally with `live_opening_disabled` before `router.submit` was called.

- [ ] **Step 6: Manual guarded opening-order demo check**

Progress note on 2026-06-17: the default-disabled opening guard was verified in OKX demo mode. The cap, allowed small-order, and post-fill refresh subchecks below remain unrun because they require intentionally enabling live opening orders in demo mode.

Progress note on 2026-06-18: local notional-cap rejection was verified in OKX demo mode, and the adapter now defaults non-spot orders to `tdMode: cross`. OKX order precheck for the smallest `SOL-USDT-SWAP` opening order still fails before order creation with `51010` / `You can't complete this request under your current account mode.` for the current demo account configuration (`acctLv=1`, `posMode=net_mode`), so the allowed small-order and post-fill refresh subchecks remain blocked until the OKX demo account is switched to a swap-compatible account mode. An API attempt to switch the demo account to `acctLv=2` failed with OKX `51070` / `Please upgrade your account mode on the OKX website or app`; a follow-up config check confirmed the account remained `acctLv=1`. After the manual switch attempt, runtime settings were restored to `live + swap + demo=true`; OKX still reported zero open positions/orders but the sanitized account config remained `acctLv=1`, `posMode=net_mode`, and the smallest `SOL-USDT-SWAP` precheck still failed with `51010`, so no simulated order was submitted.

Only in OKX demo mode:

1. Set `Allow Live Opening Orders` enabled.
2. Set `Live Max Order Notional` to a small value such as `50`.
3. Trigger an order whose notional exceeds the cap.
4. Confirm the order is rejected with reason code `live_order_notional_exceeded`.
5. Raise the cap only in demo mode and confirm a small order can pass risk checks.
6. Confirm a post-fill state refresh updates account/positions from OKX instead of paper accounting.

- [ ] **Step 7: Real live mode gate**

Before setting `OKX Demo Trading` off:

1. Confirm all tests in Steps 1-4 passed.
2. Confirm the user explicitly asks to use real live mode.
3. Confirm the OKX API key permissions are scoped to the intended account and market.
4. Confirm `Allow Live Opening Orders` and `Live Max Order Notional` are set intentionally.
5. Confirm no `.env`, local YAML settings, screenshots with credentials, or generated logs containing secrets are staged for git.

- [ ] **Step 8: Checkpoint commit if explicitly approved**

Run only after the user explicitly approves committing and after checking staged files:

```bash
git status --short
git add src/core/types.py src/core/config.py src/exchange/base.py src/exchange/factory.py src/exchange/live_sync.py src/order/manager.py src/web/api/strategies.py src/web/api/trading.py src/web/api/settings.py src/web/api/market.py tests/unit/test_exchange_base.py tests/unit/test_live_sync.py tests/unit/test_order_router.py tests/integration/test_web_api.py frontend/src/types/settings.ts frontend/src/types/market.ts frontend/src/services/market.ts frontend/src/views/Settings.vue frontend/src/views/Market.vue frontend/src/locales/en.ts frontend/src/locales/zh-CN.ts frontend/src/services/settings.test.ts
git commit -m "feat: complete guarded OKX live integration"
```

---

## Self-review checklist

- Spec coverage:
  - OKX private account/position sync: Tasks 1, 2, and 6.
  - Live opening safety controls: Task 5.
  - `market_type` and `demo` frontend settings: Task 7.
  - Option/derivative live handler and market data support: Tasks 3 and 8.
  - Stop-loss/take-profit/conditional order support: Task 9.
  - Tests and verification: every task includes targeted tests; Task 10 covers full checks.
- Placeholder scan:
  - No unspecified code blocks are required to complete the tasks.
  - Every new function/type referenced by a later task is defined in an earlier task or in the same task.
- Type consistency:
  - `AccountSnapshot` and `PositionSnapshot` live in `src/core/types.py` and are consumed by `src/exchange/base.py` and `src/exchange/live_sync.py`.
  - `LiveStateSyncService.refresh()` returns `LiveStateSyncResult` with `AccountRecord` and `PositionRecord` objects used by trading API serializers.
  - `UnifiedOrderManager` receives `live_state_refresher`, `allow_live_open_orders`, and `live_max_order_notional` from `src/web/api/strategies.py`.
  - Frontend `ExchangeSettingsView` / `ExchangeSettingsUpdate` match backend `ExchangeSettingsUpdate` fields.
  - Frontend `RiskSettings` matches backend `RiskSettings` fields.
