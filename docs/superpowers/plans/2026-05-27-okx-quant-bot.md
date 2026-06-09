# OKX Quantitative Trading Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build a modular monolith quantitative trading bot for OKX supporting backtesting, demo, and live trading across spot, perpetual swaps, delivery futures, and options.

**Architecture:** Python asyncio-based modular monolith with clean internal interfaces. Exchange adapters abstract OKX API via ccxt. Unified order router switches between backtest engine, OKX sandbox, and OKX production. FastAPI serves REST API + WebSocket; Vue 3 frontend provides web console.

**Tech Stack:** Python 3.12+, asyncio, ccxt, FastAPI, SQLModel, SQLite (WAL), Vue 3, Vite, Element Plus, ECharts, Monaco Editor, Pinia, uv

---

## File Structure

```
okx-bot/
├── pyproject.toml
├── config/
│   ├── settings.yaml
│   └── strategies/
│       └── example_ma_cross.yaml
├── src/
│   ├── __init__.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── types.py              # Bar, Order, Position dataclasses
│   │   ├── events.py             # EventBus for internal pub/sub
│   │   └── engine.py             # Main engine lifecycle
│   ├── exchange/
│   │   ├── __init__.py
│   │   ├── base.py               # ExchangeAdapter ABC
│   │   ├── okx_spot.py           # OKX spot adapter
│   │   ├── okx_swap.py           # OKX perpetual swap adapter
│   │   ├── okx_futures.py        # OKX delivery futures adapter
│   │   └── okx_options.py        # OKX options adapter
│   ├── strategy/
│   │   ├── __init__.py
│   │   ├── base.py               # BaseStrategy ABC
│   │   ├── registry.py           # Strategy registration
│   │   ├── yaml_strategy.py      # YAML DSL strategy executor
│   │   └── builtin/
│   │       ├── __init__.py
│   │       └── ma_cross.py       # Built-in MA cross strategy
│   ├── order/
│   │   ├── __init__.py
│   │   ├── manager.py            # UnifiedOrderManager
│   │   └── router.py             # OrderRouter (backtest/demo/live)
│   ├── backtest/
│   │   ├── __init__.py
│   │   ├── engine.py             # BacktestEngine
│   │   ├── datasource.py         # OKX data fetch + SQLite cache
│   │   ├── matcher.py            # Simulated order matching
│   │   └── report.py             # Backtest report generation
│   ├── risk/
│   │   ├── __init__.py
│   │   ├── manager.py            # RiskManager
│   │   └── rules.py              # Risk rule implementations
│   ├── data/
│   │   ├── __init__.py
│   │   ├── models.py             # SQLModel ORM models
│   │   └── repository.py         # Data access layer
│   ├── market/
│   │   ├── __init__.py
│   │   └── service.py            # MarketDataService (WebSocket)
│   ├── notify/
│   │   ├── __init__.py
│   │   └── telegram.py           # Telegram notification sender
│   └── web/
│       ├── __init__.py
│       ├── app.py                # FastAPI app factory
│       ├── deps.py               # Dependency injection
│       ├── api/
│       │   ├── __init__.py
│       │   ├── strategies.py     # Strategy CRUD + control
│       │   ├── backtest.py       # Backtest run + results
│       │   ├── trading.py        # Orders, positions, account
│       │   └── market.py         # Market data endpoints
│       └── ws.py                 # WebSocket push manager
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   └── src/
│       ├── main.ts
│       ├── App.vue
│       ├── router/
│       │   └── index.ts
│       ├── stores/
│       │   ├── dashboard.ts
│       │   ├── strategy.ts
│       │   ├── backtest.ts
│       │   └── market.ts
│       ├── views/
│       │   ├── Dashboard.vue
│       │   ├── Strategy.vue
│       │   ├── Backtest.vue
│       │   ├── Market.vue
│       │   └── Trades.vue
│       ├── components/
│       │   ├── CodeEditor.vue
│       │   ├── StrategyForm.vue
│       │   └── Candlestick.vue
│       └── composables/
│           └── useWebSocket.ts
├── tests/
│   ├── conftest.py
│   ├── unit/
│   │   ├── test_types.py
│   │   ├── test_events.py
│   │   ├── test_strategy_base.py
│   │   ├── test_yaml_strategy.py
│   │   ├── test_order_router.py
│   │   ├── test_backtest_matcher.py
│   │   ├── test_risk_rules.py
│   │   └── test_report.py
│   └── integration/
│       ├── test_backtest_flow.py
│       ├── test_exchange_adapter.py
│       └── test_web_api.py
└── docs/
    └── superpowers/
        └── specs/
            └── 2026-05-27-okx-quant-bot-design.md
```

---

## Task 1: Project Scaffolding + Core Types

**Files:**
- Create: `pyproject.toml`
- Create: `src/__init__.py`
- Create: `src/core/__init__.py`
- Create: `src/core/types.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/unit/__init__.py`
- Create: `tests/unit/test_types.py`

- [x] **Step 1: Initialize project with uv**

```bash
cd /Users/zane/Documents/Self/Project/Self/okx-bot
uv init --name okx-bot --python 3.12
```

- [x] **Step 2: Add dependencies**

```bash
uv add ccxt sqlmodel fastapi uvicorn[standard] pydantic pyyaml python-dotenv httpx websockets
uv add --dev pytest pytest-asyncio ruff
```

- [x] **Step 3: Create directory structure**

```bash
mkdir -p src/core src/exchange src/strategy/builtin src/order src/backtest src/risk src/data src/market src/notify src/web/api frontend/src/{router,stores,views,components,composables} tests/unit tests/integration
touch src/__init__.py src/core/__init__.py src/exchange/__init__.py src/strategy/__init__.py src/strategy/builtin/__init__.py src/order/__init__.py src/backtest/__init__.py src/risk/__init__.py src/data/__init__.py src/market/__init__.py src/notify/__init__.py src/web/__init__.py src/web/api/__init__.py tests/__init__.py tests/unit/__init__.py tests/integration/__init__.py
```

- [x] **Step 4: Write failing test for core types**

```python
# tests/unit/test_types.py
from src.core.types import Bar, Order, Position, OrderSide, OrderType, OrderStatus, PositionSide


def test_bar_creation():
    bar = Bar(
        timestamp=1700000000000,
        open=50000.0,
        high=51000.0,
        low=49000.0,
        close=50500.0,
        volume=100.5,
    )
    assert bar.timestamp == 1700000000000
    assert bar.open == 50000.0
    assert bar.close == 50500.0


def test_order_creation():
    order = Order(
        id="test-001",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=0.1,
        price=50000.0,
        stop_loss=49000.0,
        take_profit=52000.0,
    )
    assert order.id == "test-001"
    assert order.status == OrderStatus.PENDING
    assert order.fill_price is None


def test_position_creation():
    pos = Position(
        symbol="BTC-USDT-SWAP",
        side=PositionSide.LONG,
        amount=0.5,
        entry_price=50000.0,
        unrealized_pnl=250.0,
        leverage=10,
    )
    assert pos.symbol == "BTC-USDT-SWAP"
    assert pos.leverage == 10
```

- [x] **Step 5: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_types.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.core.types'`

- [x] **Step 6: Implement core types**

```python
# src/core/types.py
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class PositionSide(str, Enum):
    LONG = "long"
    SHORT = "short"


@dataclass
class Bar:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Order:
    id: str
    symbol: str
    side: OrderSide
    type: OrderType
    amount: float
    price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    status: OrderStatus = OrderStatus.PENDING
    fill_price: float | None = None
    fill_time: int | None = None


@dataclass
class Position:
    symbol: str
    side: PositionSide
    amount: float
    entry_price: float
    unrealized_pnl: float
    leverage: int = 1
```

- [x] **Step 7: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_types.py -v
```

Expected: 3 passed

- [x] **Step 8: Configure pytest and ruff**

```toml
# pyproject.toml - add these sections
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP"]
```

- [x] **Step 9: Run linter**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

- [x] **Step 10: Commit**

```bash
git add pyproject.toml uv.lock src/ tests/
git commit -m "feat: project scaffolding and core types"
```

---

## Task 2: Event Bus

**Files:**
- Create: `src/core/events.py`
- Create: `tests/unit/test_events.py`

- [x] **Step 1: Write failing tests for EventBus**

```python
# tests/unit/test_events.py
import asyncio

import pytest

from src.core.events import EventBus, Event


@pytest.mark.asyncio
async def test_subscribe_and_emit():
    bus = EventBus()
    received = []

    async def handler(event: Event):
        received.append(event)

    bus.subscribe("bar", handler)
    await bus.emit(Event(type="bar", data={"close": 50000}))

    assert len(received) == 1
    assert received[0].data["close"] == 50000


@pytest.mark.asyncio
async def test_multiple_subscribers():
    bus = EventBus()
    count = {"a": 0, "b": 0}

    async def handler_a(event: Event):
        count["a"] += 1

    async def handler_b(event: Event):
        count["b"] += 1

    bus.subscribe("order", handler_a)
    bus.subscribe("order", handler_b)
    await bus.emit(Event(type="order", data={}))

    assert count["a"] == 1
    assert count["b"] == 1


@pytest.mark.asyncio
async def test_unsubscribe():
    bus = EventBus()
    received = []

    async def handler(event: Event):
        received.append(event)

    token = bus.subscribe("bar", handler)
    bus.unsubscribe(token)
    await bus.emit(Event(type="bar", data={}))

    assert len(received) == 0


@pytest.mark.asyncio
async def test_emit_no_subscribers():
    bus = EventBus()
    # Should not raise
    await bus.emit(Event(type="unknown", data={}))
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_events.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.core.events'`

- [x] **Step 3: Implement EventBus**

```python
# src/core/events.py
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable
from uuid import uuid4


@dataclass
class Event:
    type: str
    data: dict[str, Any]


EventHandler = Callable[[Event], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, dict[str, EventHandler]] = {}

    def subscribe(self, event_type: str, handler: EventHandler) -> str:
        token = uuid4().hex
        self._handlers.setdefault(event_type, {})[token] = handler
        return token

    def unsubscribe(self, token: str) -> None:
        for handlers in self._handlers.values():
            handlers.pop(token, None)

    async def emit(self, event: Event) -> None:
        handlers = self._handlers.get(event.type, {})
        tasks = [handler(event) for handler in handlers.values()]
        if tasks:
            await asyncio.gather(*tasks)
```

- [x] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_events.py -v
```

Expected: 4 passed

- [x] **Step 5: Commit**

```bash
git add src/core/events.py tests/unit/test_events.py
git commit -m "feat: add async event bus for internal pub/sub"
```

---

## Task 3: Data Models + Repository

**Files:**
- Create: `src/data/models.py`
- Create: `src/data/repository.py`
- Create: `tests/unit/test_repository.py`

- [x] **Step 1: Write failing tests for data models and repository**

```python
# tests/unit/test_repository.py
import pytest
from sqlmodel import SQLModel, create_engine, Session

from src.data.models import TradeRecord, OrderRecord, PositionRecord, KlineCache
from src.data.repository import Repository


@pytest.fixture
def repo():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    return Repository(engine)


def test_save_and_get_trade(repo: Repository):
    trade = TradeRecord(
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        amount=0.1,
        price=50000.0,
        fee=2.5,
        timestamp=1700000000000,
    )
    repo.save_trade(trade)
    trades = repo.get_trades(strategy="ma_cross")
    assert len(trades) == 1
    assert trades[0].symbol == "BTC-USDT"


def test_save_and_get_order(repo: Repository):
    order = OrderRecord(
        order_id="ord-001",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=0.1,
        price=50000.0,
        status="filled",
        fill_price=50000.0,
        timestamp=1700000000000,
    )
    repo.save_order(order)
    orders = repo.get_orders(order_id="ord-001")
    assert len(orders) == 1
    assert orders[0].status == "filled"


def test_save_and_get_position(repo: Repository):
    pos = PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=0.5,
        entry_price=50000.0,
        leverage=10,
        timestamp=1700000000000,
    )
    repo.save_position(pos)
    positions = repo.get_positions(strategy="ma_cross")
    assert len(positions) == 1
    assert positions[0].leverage == 10


def test_kline_cache(repo: Repository):
    kline = KlineCache(
        symbol="BTC-USDT",
        timeframe="1h",
        timestamp=1700000000000,
        open=50000.0,
        high=51000.0,
        low=49000.0,
        close=50500.0,
        volume=100.0,
    )
    repo.save_kline(kline)
    klines = repo.get_klines("BTC-USDT", "1h", 1700000000000, 1700003600000)
    assert len(klines) == 1


def test_get_trades_filters(repo: Repository):
    for i in range(5):
        repo.save_trade(TradeRecord(
            strategy="strat_a" if i < 3 else "strat_b",
            symbol="BTC-USDT",
            side="buy",
            amount=0.1,
            price=50000.0 + i * 100,
            fee=2.5,
            timestamp=1700000000000 + i * 1000,
        ))
    assert len(repo.get_trades(strategy="strat_a")) == 3
    assert len(repo.get_trades(strategy="strat_b")) == 2
    assert len(repo.get_trades()) == 5
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_repository.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement ORM models**

```python
# src/data/models.py
from __future__ import annotations

from sqlmodel import SQLModel, Field


class TradeRecord(SQLModel, table=True):
    __tablename__ = "trades"
    id: int | None = Field(default=None, primary_key=True)
    strategy: str
    symbol: str
    side: str
    amount: float
    price: float
    fee: float
    timestamp: int


class OrderRecord(SQLModel, table=True):
    __tablename__ = "orders"
    id: int | None = Field(default=None, primary_key=True)
    order_id: str = Field(index=True)
    strategy: str
    symbol: str
    side: str
    type: str
    amount: float
    price: float | None = None
    status: str
    fill_price: float | None = None
    timestamp: int


class PositionRecord(SQLModel, table=True):
    __tablename__ = "positions"
    id: int | None = Field(default=None, primary_key=True)
    strategy: str
    symbol: str
    side: str
    amount: float
    entry_price: float
    leverage: int
    timestamp: int


class KlineCache(SQLModel, table=True):
    __tablename__ = "kline_cache"
    id: int | None = Field(default=None, primary_key=True)
    symbol: str = Field(index=True)
    timeframe: str
    timestamp: int = Field(index=True)
    open: float
    high: float
    low: float
    close: float
    volume: float
```

- [x] **Step 4: Implement repository**

```python
# src/data/repository.py
from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine, col, select

from src.data.models import TradeRecord, OrderRecord, PositionRecord, KlineCache


class Repository:
    def __init__(self, engine=None, db_path: str = "data/bot.db"):
        if engine is None:
            engine = create_engine(f"sqlite:///{db_path}", echo=False)
            SQLModel.metadata.create_all(engine)
        self._engine = engine

    def _session(self) -> Session:
        return Session(self._engine)

    def save_trade(self, trade: TradeRecord) -> None:
        with self._session() as session:
            session.add(trade)
            session.commit()

    def get_trades(self, strategy: str | None = None) -> list[TradeRecord]:
        with self._session() as session:
            stmt = select(TradeRecord)
            if strategy:
                stmt = stmt.where(TradeRecord.strategy == strategy)
            return list(session.exec(stmt).all())

    def save_order(self, order: OrderRecord) -> None:
        with self._session() as session:
            session.add(order)
            session.commit()

    def get_orders(self, order_id: str | None = None, strategy: str | None = None) -> list[OrderRecord]:
        with self._session() as session:
            stmt = select(OrderRecord)
            if order_id:
                stmt = stmt.where(OrderRecord.order_id == order_id)
            if strategy:
                stmt = stmt.where(OrderRecord.strategy == strategy)
            return list(session.exec(stmt).all())

    def save_position(self, pos: PositionRecord) -> None:
        with self._session() as session:
            session.add(pos)
            session.commit()

    def get_positions(self, strategy: str | None = None) -> list[PositionRecord]:
        with self._session() as session:
            stmt = select(PositionRecord)
            if strategy:
                stmt = stmt.where(PositionRecord.strategy == strategy)
            return list(session.exec(stmt).all())

    def save_kline(self, kline: KlineCache) -> None:
        with self._session() as session:
            session.add(kline)
            session.commit()

    def get_klines(self, symbol: str, timeframe: str, start: int, end: int) -> list[KlineCache]:
        with self._session() as session:
            stmt = (
                select(KlineCache)
                .where(KlineCache.symbol == symbol)
                .where(KlineCache.timeframe == timeframe)
                .where(col(KlineCache.timestamp) >= start)
                .where(col(KlineCache.timestamp) <= end)
                .order_by(KlineCache.timestamp)
            )
            return list(session.exec(stmt).all())
```

- [x] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_repository.py -v
```

Expected: 5 passed

- [x] **Step 6: Commit**

```bash
git add src/data/ tests/unit/test_repository.py
git commit -m "feat: add SQLModel data models and repository"
```

---

## Task 4: Configuration Loader

**Files:**
- Create: `config/settings.yaml`
- Create: `src/core/config.py`
- Create: `tests/unit/test_config.py`

- [x] **Step 1: Write failing tests for config loader**

```python
# tests/unit/test_config.py
import os
import pytest
import tempfile
import yaml

from src.core.config import load_config


def test_load_basic_config():
    config_data = {
        "mode": "backtest",
        "exchange": {
            "api_key": "test-key",
            "secret": "test-secret",
            "passphrase": "test-pass",
        },
        "backtest": {
            "initial_capital": 100000,
            "fee_rate": 0.0005,
            "slippage": 0.001,
        },
        "risk": {
            "max_daily_loss_pct": 0.05,
            "max_drawdown_pct": 0.15,
        },
        "web": {"host": "0.0.0.0", "port": 8080},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(config_data, f)
        path = f.name

    config = load_config(path)
    assert config.mode == "backtest"
    assert config.exchange.api_key == "test-key"
    assert config.backtest.initial_capital == 100000
    assert config.risk.max_daily_loss_pct == 0.05
    os.unlink(path)


def test_env_var_substitution():
    os.environ["TEST_OKX_KEY"] = "env-api-key"
    config_data = {
        "mode": "live",
        "exchange": {
            "api_key": "${TEST_OKX_KEY}",
            "secret": "s",
            "passphrase": "p",
        },
        "backtest": {"initial_capital": 50000},
        "risk": {"max_daily_loss_pct": 0.1},
        "web": {"host": "0.0.0.0", "port": 8080},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(config_data, f)
        path = f.name

    config = load_config(path)
    assert config.exchange.api_key == "env-api-key"
    del os.environ["TEST_OKX_KEY"]
    os.unlink(path)


def test_defaults():
    config_data = {
        "mode": "backtest",
        "exchange": {"api_key": "k", "secret": "s", "passphrase": "p"},
        "backtest": {"initial_capital": 100000},
        "risk": {"max_daily_loss_pct": 0.05},
        "web": {"host": "0.0.0.0", "port": 8080},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(config_data, f)
        path = f.name

    config = load_config(path)
    assert config.backtest.fee_rate == 0.0005
    assert config.backtest.slippage == 0.001
    assert config.risk.max_drawdown_pct == 0.15
    os.unlink(path)
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_config.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement config loader**

```python
# src/core/config.py
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

import yaml


@dataclass
class ExchangeConfig:
    api_key: str = ""
    secret: str = ""
    passphrase: str = ""


@dataclass
class BacktestConfig:
    initial_capital: float = 100000
    fee_rate: float = 0.0005
    slippage: float = 0.001
    data_cache_dir: str = "./data"


@dataclass
class RiskConfig:
    max_daily_loss_pct: float = 0.05
    max_drawdown_pct: float = 0.15
    max_total_position_pct: float = 0.8


@dataclass
class NotifyConfig:
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


@dataclass
class WebConfig:
    host: str = "0.0.0.0"
    port: int = 8080


@dataclass
class AppConfig:
    mode: str = "backtest"
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    web: WebConfig = field(default_factory=WebConfig)


_ENV_PATTERN = re.compile(r"\$\{(\w+)\}")


def _substitute_env(value: Any) -> Any:
    if isinstance(value, str):
        def replacer(m: re.Match) -> str:
            return os.environ.get(m.group(1), "")
        return _ENV_PATTERN.sub(replacer, value)
    if isinstance(value, dict):
        return {k: _substitute_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env(v) for v in value]
    return value


def load_config(path: str) -> AppConfig:
    with open(path) as f:
        raw = yaml.safe_load(f)

    raw = _substitute_env(raw)

    return AppConfig(
        mode=raw.get("mode", "backtest"),
        exchange=ExchangeConfig(**raw.get("exchange", {})),
        backtest=BacktestConfig(**raw.get("backtest", {})),
        risk=RiskConfig(**raw.get("risk", {})),
        notify=NotifyConfig(**raw.get("notify", {})),
        web=WebConfig(**raw.get("web", {})),
    )
```

- [x] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_config.py -v
```

Expected: 3 passed

- [x] **Step 5: Create default settings.yaml**

```yaml
# config/settings.yaml
mode: backtest

exchange:
  api_key: ${OKX_API_KEY}
  secret: ${OKX_SECRET}
  passphrase: ${OKX_PASSPHRASE}

backtest:
  initial_capital: 100000
  fee_rate: 0.0005
  slippage: 0.001
  data_cache_dir: ./data

risk:
  max_daily_loss_pct: 0.05
  max_drawdown_pct: 0.15
  max_total_position_pct: 0.8

notify:
  telegram_bot_token: ${TG_BOT_TOKEN}
  telegram_chat_id: ${TG_CHAT_ID}

web:
  host: 0.0.0.0
  port: 8080
```

- [x] **Step 6: Commit**

```bash
git add src/core/config.py config/settings.yaml tests/unit/test_config.py
git commit -m "feat: add YAML config loader with env var substitution"
```

---

## Task 5: Strategy Base Class + Registry

**Files:**
- Create: `src/strategy/base.py`
- Create: `src/strategy/registry.py`
- Create: `tests/unit/test_strategy_base.py`

- [x] **Step 1: Write failing tests**

```python
# tests/unit/test_strategy_base.py
import pytest

from src.core.types import Bar, Order, OrderSide, OrderType, OrderStatus
from src.strategy.base import BaseStrategy
from src.strategy.registry import StrategyRegistry


class DummyStrategy(BaseStrategy):
    name = "dummy"

    def __init__(self):
        super().__init__()
        self.bars_received = []

    async def on_bar(self, bar: Bar):
        self.bars_received.append(bar)
        if bar.close > 50000:
            await self.buy("BTC-USDT", 0.1, price=bar.close)


@pytest.mark.asyncio
async def test_strategy_receives_bars():
    strat = DummyStrategy()
    bar = Bar(timestamp=1000, open=49000, high=51000, low=48000, close=50500, volume=10)
    await strat.on_bar(bar)
    assert len(strat.bars_received) == 1


def test_strategy_registry():
    registry = StrategyRegistry()
    registry.register("dummy", DummyStrategy)
    assert "dummy" in registry.list_strategies()
    instance = registry.create("dummy")
    assert instance.name == "dummy"


def test_strategy_registry_unknown():
    registry = StrategyRegistry()
    with pytest.raises(KeyError):
        registry.create("nonexistent")
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_strategy_base.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement BaseStrategy**

```python
# src/strategy/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from src.core.types import Bar, Order, OrderSide, OrderType, Position

if TYPE_CHECKING:
    from src.order.manager import UnifiedOrderManager


class BaseStrategy(ABC):
    name: str = ""

    def __init__(self) -> None:
        self._order_manager: UnifiedOrderManager | None = None
        self._capital_pct: float = 0.1

    def set_order_manager(self, manager: UnifiedOrderManager) -> None:
        self._order_manager = manager

    async def on_init(self) -> None:
        pass

    @abstractmethod
    async def on_bar(self, bar: Bar) -> None:
        ...

    async def on_order(self, order: Order) -> None:
        pass

    async def on_position(self, pos: Position) -> None:
        pass

    async def buy(
        self,
        symbol: str,
        amount: float,
        price: float | None = None,
        sl: float | None = None,
        tp: float | None = None,
    ) -> Order:
        if self._order_manager is None:
            raise RuntimeError("Order manager not set")
        order_type = OrderType.LIMIT if price is not None else OrderType.MARKET
        return await self._order_manager.submit(
            symbol=symbol,
            side=OrderSide.BUY,
            order_type=order_type,
            amount=amount,
            price=price,
            stop_loss=sl,
            take_profit=tp,
            strategy_name=self.name,
        )

    async def sell(
        self,
        symbol: str,
        amount: float,
        price: float | None = None,
    ) -> Order:
        if self._order_manager is None:
            raise RuntimeError("Order manager not set")
        order_type = OrderType.LIMIT if price is not None else OrderType.MARKET
        return await self._order_manager.submit(
            symbol=symbol,
            side=OrderSide.SELL,
            order_type=order_type,
            amount=amount,
            price=price,
            strategy_name=self.name,
        )

    async def cancel(self, order_id: str) -> bool:
        if self._order_manager is None:
            raise RuntimeError("Order manager not set")
        return await self._order_manager.cancel(order_id)

    def get_position(self, symbol: str) -> Position | None:
        if self._order_manager is None:
            return None
        return self._order_manager.get_position(self.name, symbol)

    def get_balance(self) -> float:
        if self._order_manager is None:
            return 0.0
        return self._order_manager.get_balance(self.name)
```

- [x] **Step 4: Implement StrategyRegistry**

```python
# src/strategy/registry.py
from __future__ import annotations

from typing import Type

from src.strategy.base import BaseStrategy


class StrategyRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, Type[BaseStrategy]] = {}

    def register(self, name: str, cls: Type[BaseStrategy]) -> None:
        self._strategies[name] = cls

    def create(self, name: str) -> BaseStrategy:
        if name not in self._strategies:
            raise KeyError(f"Strategy '{name}' not found")
        return self._strategies[name]()

    def list_strategies(self) -> list[str]:
        return list(self._strategies.keys())
```

- [x] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_strategy_base.py -v
```

Expected: 3 passed

- [x] **Step 6: Commit**

```bash
git add src/strategy/base.py src/strategy/registry.py tests/unit/test_strategy_base.py
git commit -m "feat: add BaseStrategy ABC and StrategyRegistry"
```

---

## Task 6: Backtest Matcher

**Files:**
- Create: `src/backtest/matcher.py`
- Create: `tests/unit/test_backtest_matcher.py`

- [x] **Step 1: Write failing tests**

```python
# tests/unit/test_backtest_matcher.py
import pytest

from src.core.types import Bar, Order, OrderSide, OrderType, OrderStatus
from src.backtest.matcher import OrderMatcher


@pytest.fixture
def bar():
    return Bar(timestamp=1000, open=50000, high=52000, low=48000, close=51000, volume=100)


def test_market_order_fills_at_open(bar):
    matcher = OrderMatcher(slippage=0.0, fee_rate=0.001)
    order = Order(id="1", symbol="BTC-USDT", side=OrderSide.BUY, type=OrderType.MARKET, amount=0.1)
    result = matcher.match(order, bar)
    assert result.status == OrderStatus.FILLED
    assert result.fill_price == 50000


def test_limit_order_fills_within_range(bar):
    matcher = OrderMatcher(slippage=0.0, fee_rate=0.0)
    order = Order(id="2", symbol="BTC-USDT", side=OrderSide.BUY, type=OrderType.LIMIT, amount=0.1, price=50000)
    result = matcher.match(order, bar)
    assert result.status == OrderStatus.FILLED
    assert result.fill_price == 50000


def test_limit_order_not_fills_outside_range(bar):
    matcher = OrderMatcher(slippage=0.0, fee_rate=0.0)
    order = Order(id="3", symbol="BTC-USDT", side=OrderSide.BUY, type=OrderType.LIMIT, amount=0.1, price=47000)
    result = matcher.match(order, bar)
    assert result.status == OrderStatus.PENDING


def test_slippage_applied_buy(bar):
    matcher = OrderMatcher(slippage=0.001, fee_rate=0.0)
    order = Order(id="4", symbol="BTC-USDT", side=OrderSide.BUY, type=OrderType.MARKET, amount=0.1)
    result = matcher.match(order, bar)
    assert result.fill_price == pytest.approx(50050.0)


def test_slippage_applied_sell(bar):
    matcher = OrderMatcher(slippage=0.001, fee_rate=0.0)
    order = Order(id="5", symbol="BTC-USDT", side=OrderSide.SELL, type=OrderType.MARKET, amount=0.1)
    result = matcher.match(order, bar)
    assert result.fill_price == pytest.approx(49950.0)


def test_fee_deducted(bar):
    matcher = OrderMatcher(slippage=0.0, fee_rate=0.001)
    order = Order(id="6", symbol="BTC-USDT", side=OrderSide.BUY, type=OrderType.MARKET, amount=1.0)
    result = matcher.match(order, bar)
    assert result.fee == pytest.approx(50.0)


def test_stop_order_triggers_when_price_crosses(bar):
    matcher = OrderMatcher(slippage=0.0, fee_rate=0.0)
    order = Order(id="7", symbol="BTC-USDT", side=OrderSide.SELL, type=OrderType.STOP, amount=0.1, price=49000)
    result = matcher.match(order, bar)
    assert result.status == OrderStatus.FILLED
    assert result.fill_price == 49000


def test_stop_order_not_triggered(bar):
    matcher = OrderMatcher(slippage=0.0, fee_rate=0.0)
    order = Order(id="8", symbol="BTC-USDT", side=OrderSide.SELL, type=OrderType.STOP, amount=0.1, price=47000)
    result = matcher.match(order, bar)
    assert result.status == OrderStatus.PENDING
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_backtest_matcher.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement OrderMatcher**

```python
# src/backtest/matcher.py
from __future__ import annotations

from dataclasses import dataclass
from src.core.types import Bar, Order, OrderSide, OrderType, OrderStatus


@dataclass
class MatchResult:
    status: OrderStatus
    fill_price: float | None = None
    fee: float = 0.0


class OrderMatcher:
    def __init__(self, slippage: float = 0.001, fee_rate: float = 0.0005):
        self.slippage = slippage
        self.fee_rate = fee_rate

    def match(self, order: Order, bar: Bar) -> MatchResult:
        if order.type == OrderType.MARKET:
            return self._match_market(order, bar)
        elif order.type == OrderType.LIMIT:
            return self._match_limit(order, bar)
        elif order.type == OrderType.STOP:
            return self._match_stop(order, bar)
        return MatchResult(status=OrderStatus.PENDING)

    def _apply_slippage(self, price: float, side: OrderSide) -> float:
        if side == OrderSide.BUY:
            return price * (1 + self.slippage)
        return price * (1 - self.slippage)

    def _calc_fee(self, price: float, amount: float) -> float:
        return price * amount * self.fee_rate

    def _match_market(self, order: Order, bar: Bar) -> MatchResult:
        base_price = bar.open
        fill_price = self._apply_slippage(base_price, order.side)
        fee = self._calc_fee(fill_price, order.amount)
        return MatchResult(status=OrderStatus.FILLED, fill_price=fill_price, fee=fee)

    def _match_limit(self, order: Order, bar: Bar) -> MatchResult:
        price = order.price
        if price is None:
            return MatchResult(status=OrderStatus.PENDING)
        if bar.low <= price <= bar.high:
            fee = self._calc_fee(price, order.amount)
            return MatchResult(status=OrderStatus.FILLED, fill_price=price, fee=fee)
        return MatchResult(status=OrderStatus.PENDING)

    def _match_stop(self, order: Order, bar: Bar) -> MatchResult:
        price = order.price
        if price is None:
            return MatchResult(status=OrderStatus.PENDING)
        triggered = False
        if order.side == OrderSide.SELL and bar.low <= price:
            triggered = True
        elif order.side == OrderSide.BUY and bar.high >= price:
            triggered = True
        if triggered:
            fee = self._calc_fee(price, order.amount)
            return MatchResult(status=OrderStatus.FILLED, fill_price=price, fee=fee)
        return MatchResult(status=OrderStatus.PENDING)
```

- [x] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_backtest_matcher.py -v
```

Expected: 8 passed

- [x] **Step 5: Commit**

```bash
git add src/backtest/matcher.py tests/unit/test_backtest_matcher.py
git commit -m "feat: add backtest order matcher with slippage and fees"
```

---

## Task 7: Backtest DataSource

**Files:**
- Create: `src/backtest/datasource.py`
- Create: `tests/unit/test_datasource.py`

- [x] **Step 1: Write failing tests**

```python
# tests/unit/test_datasource.py
import pytest
from unittest.mock import AsyncMock, patch

from src.core.types import Bar
from src.backtest.datasource import BacktestDataSource
from src.data.repository import Repository
from src.data.models import KlineCache
from sqlmodel import SQLModel, create_engine


@pytest.fixture
def repo():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    return Repository(engine)


def test_get_bars_from_cache(repo: Repository):
    for i in range(5):
        repo.save_kline(KlineCache(
            symbol="BTC-USDT",
            timeframe="1h",
            timestamp=1000 + i * 3600000,
            open=50000 + i * 100,
            high=50500 + i * 100,
            low=49500 + i * 100,
            close=50200 + i * 100,
            volume=100.0,
        ))
    ds = BacktestDataSource(repo=repo, symbol="BTC-USDT", timeframe="1h")
    bars = ds.get_cached_bars(start=1000, end=1000 + 4 * 3600000)
    assert len(bars) == 5
    assert isinstance(bars[0], Bar)
    assert bars[0].open == 50000


def test_save_bars_to_cache(repo: Repository):
    ds = BacktestDataSource(repo=repo, symbol="BTC-USDT", timeframe="1h")
    bars = [
        Bar(timestamp=1000, open=50000, high=51000, low=49000, close=50500, volume=100),
        Bar(timestamp=4600000, open=50500, high=52000, low=50000, close=51500, volume=120),
    ]
    ds.save_bars_to_cache(bars)
    cached = repo.get_klines("BTC-USDT", "1h", 1000, 4600000)
    assert len(cached) == 2
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_datasource.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement BacktestDataSource**

```python
# src/backtest/datasource.py
from __future__ import annotations

from src.core.types import Bar
from src.data.repository import Repository
from src.data.models import KlineCache


class BacktestDataSource:
    def __init__(self, repo: Repository, symbol: str, timeframe: str):
        self.repo = repo
        self.symbol = symbol
        self.timeframe = timeframe

    def get_cached_bars(self, start: int, end: int) -> list[Bar]:
        klines = self.repo.get_klines(self.symbol, self.timeframe, start, end)
        return [
            Bar(
                timestamp=k.timestamp,
                open=k.open,
                high=k.high,
                low=k.low,
                close=k.close,
                volume=k.volume,
            )
            for k in klines
        ]

    def save_bars_to_cache(self, bars: list[Bar]) -> None:
        for bar in bars:
            self.repo.save_kline(KlineCache(
                symbol=self.symbol,
                timeframe=self.timeframe,
                timestamp=bar.timestamp,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
            ))
```

- [x] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_datasource.py -v
```

Expected: 2 passed

- [x] **Step 5: Commit**

```bash
git add src/backtest/datasource.py tests/unit/test_datasource.py
git commit -m "feat: add backtest data source with SQLite cache"
```

---

## Task 8: Risk Rules

**Files:**
- Create: `src/risk/rules.py`
- Create: `src/risk/manager.py`
- Create: `tests/unit/test_risk_rules.py`

- [x] **Step 1: Write failing tests**

```python
# tests/unit/test_risk_rules.py
import pytest

from src.core.types import Order, OrderSide, OrderType
from src.risk.rules import (
    MaxPositionRule,
    MaxDailyLossRule,
    MaxDrawdownRule,
    StopLossRequiredRule,
)
from src.risk.manager import RiskManager


def test_max_position_rule_pass():
    rule = MaxPositionRule(max_position_pct=0.1)
    assert rule.check(current_position_value=5000, total_equity=100000, order_value=5000) is True


def test_max_position_rule_fail():
    rule = MaxPositionRule(max_position_pct=0.1)
    assert rule.check(current_position_value=8000, total_equity=100000, order_value=5000) is False


def test_max_daily_loss_rule_pass():
    rule = MaxDailyLossRule(max_loss_pct=0.05)
    assert rule.check(daily_pnl=-2000, total_equity=100000) is True


def test_max_daily_loss_rule_fail():
    rule = MaxDailyLossRule(max_loss_pct=0.05)
    assert rule.check(daily_pnl=-6000, total_equity=100000) is False


def test_max_drawdown_rule_pass():
    rule = MaxDrawdownRule(max_drawdown_pct=0.15)
    assert rule.check(peak_equity=100000, current_equity=90000) is True


def test_max_drawdown_rule_fail():
    rule = MaxDrawdownRule(max_drawdown_pct=0.15)
    assert rule.check(peak_equity=100000, current_equity=80000) is False


def test_stop_loss_required_rule_pass():
    rule = StopLossRequiredRule()
    order = Order(id="1", symbol="BTC", side=OrderSide.BUY, type=OrderType.MARKET, amount=1, stop_loss=49000)
    assert rule.check(order) is True


def test_stop_loss_required_rule_fail():
    rule = StopLossRequiredRule()
    order = Order(id="1", symbol="BTC", side=OrderSide.BUY, type=OrderType.MARKET, amount=1)
    assert rule.check(order) is False


def test_risk_manager_integration():
    manager = RiskManager(
        max_position_pct=0.1,
        max_daily_loss_pct=0.05,
        max_drawdown_pct=0.15,
        require_stop_loss=True,
    )
    order = Order(id="1", symbol="BTC", side=OrderSide.BUY, type=OrderType.MARKET, amount=1, stop_loss=49000)
    result = manager.check_order(
        order=order,
        current_position_value=0,
        total_equity=100000,
        order_value=50000,
        daily_pnl=0,
        peak_equity=100000,
        current_equity=100000,
    )
    assert result.passed is True


def test_risk_manager_rejects_no_stop_loss():
    manager = RiskManager(
        max_position_pct=0.1,
        max_daily_loss_pct=0.05,
        max_drawdown_pct=0.15,
        require_stop_loss=True,
    )
    order = Order(id="1", symbol="BTC", side=OrderSide.BUY, type=OrderType.MARKET, amount=1)
    result = manager.check_order(
        order=order,
        current_position_value=0,
        total_equity=100000,
        order_value=50000,
        daily_pnl=0,
        peak_equity=100000,
        current_equity=100000,
    )
    assert result.passed is False
    assert "stop_loss" in result.reason.lower()
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_risk_rules.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement risk rules**

```python
# src/risk/rules.py
from __future__ import annotations

from src.core.types import Order


class MaxPositionRule:
    def __init__(self, max_position_pct: float):
        self.max_position_pct = max_position_pct

    def check(self, current_position_value: float, total_equity: float, order_value: float) -> bool:
        if total_equity <= 0:
            return False
        new_position = current_position_value + order_value
        return (new_position / total_equity) <= self.max_position_pct


class MaxDailyLossRule:
    def __init__(self, max_loss_pct: float):
        self.max_loss_pct = max_loss_pct

    def check(self, daily_pnl: float, total_equity: float) -> bool:
        if total_equity <= 0:
            return False
        if daily_pnl >= 0:
            return True
        return (abs(daily_pnl) / total_equity) <= self.max_loss_pct


class MaxDrawdownRule:
    def __init__(self, max_drawdown_pct: float):
        self.max_drawdown_pct = max_drawdown_pct

    def check(self, peak_equity: float, current_equity: float) -> bool:
        if peak_equity <= 0:
            return False
        drawdown = (peak_equity - current_equity) / peak_equity
        return drawdown <= self.max_drawdown_pct


class StopLossRequiredRule:
    def check(self, order: Order) -> bool:
        return order.stop_loss is not None
```

- [x] **Step 4: Implement RiskManager**

```python
# src/risk/manager.py
from __future__ import annotations

from dataclasses import dataclass

from src.core.types import Order
from src.risk.rules import (
    MaxPositionRule,
    MaxDailyLossRule,
    MaxDrawdownRule,
    StopLossRequiredRule,
)


@dataclass
class RiskCheckResult:
    passed: bool
    reason: str = ""


class RiskManager:
    def __init__(
        self,
        max_position_pct: float = 0.8,
        max_daily_loss_pct: float = 0.05,
        max_drawdown_pct: float = 0.15,
        require_stop_loss: bool = False,
    ):
        self.position_rule = MaxPositionRule(max_position_pct)
        self.daily_loss_rule = MaxDailyLossRule(max_daily_loss_pct)
        self.drawdown_rule = MaxDrawdownRule(max_drawdown_pct)
        self.stop_loss_rule = StopLossRequiredRule() if require_stop_loss else None

    def check_order(
        self,
        order: Order,
        current_position_value: float,
        total_equity: float,
        order_value: float,
        daily_pnl: float,
        peak_equity: float,
        current_equity: float,
    ) -> RiskCheckResult:
        if not self.position_rule.check(current_position_value, total_equity, order_value):
            return RiskCheckResult(passed=False, reason="Position limit exceeded")

        if not self.daily_loss_rule.check(daily_pnl, total_equity):
            return RiskCheckResult(passed=False, reason="Daily loss limit exceeded")

        if not self.drawdown_rule.check(peak_equity, current_equity):
            return RiskCheckResult(passed=False, reason="Max drawdown exceeded")

        if self.stop_loss_rule and not self.stop_loss_rule.check(order):
            return RiskCheckResult(passed=False, reason="Stop loss required")

        return RiskCheckResult(passed=True)
```

- [x] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_risk_rules.py -v
```

Expected: 10 passed

- [x] **Step 6: Commit**

```bash
git add src/risk/ tests/unit/test_risk_rules.py
git commit -m "feat: add risk rules and RiskManager"
```

---

## Task 9: Order Router + Unified Order Manager

**Files:**
- Create: `src/order/router.py`
- Create: `src/order/manager.py`
- Create: `tests/unit/test_order_router.py`

- [x] **Step 1: Write failing tests**

```python
# tests/unit/test_order_router.py
import pytest
from unittest.mock import AsyncMock

from src.core.types import Order, OrderSide, OrderType, OrderStatus
from src.order.router import OrderRouter, OrderHandler
from src.order.manager import UnifiedOrderManager


class MockHandler(OrderHandler):
    def __init__(self):
        self.submitted = []
        self.cancelled = []

    async def submit(self, order: Order) -> Order:
        self.submitted.append(order)
        order.status = OrderStatus.FILLED
        order.fill_price = 50000
        return order

    async def cancel(self, order_id: str) -> bool:
        self.cancelled.append(order_id)
        return True


@pytest.mark.asyncio
async def test_router_backtest():
    handler = MockHandler()
    router = OrderRouter(backtest=handler, mode="backtest")
    order = Order(id="1", symbol="BTC-USDT", side=OrderSide.BUY, type=OrderType.MARKET, amount=0.1)
    result = await router.submit(order)
    assert result.status == OrderStatus.FILLED
    assert len(handler.submitted) == 1


@pytest.mark.asyncio
async def test_router_mode_switch():
    bt_handler = MockHandler()
    live_handler = MockHandler()
    router = OrderRouter(backtest=bt_handler, live=live_handler, mode="backtest")

    order = Order(id="1", symbol="BTC-USDT", side=OrderSide.BUY, type=OrderType.MARKET, amount=0.1)
    await router.submit(order)
    assert len(bt_handler.submitted) == 1
    assert len(live_handler.submitted) == 0

    router.mode = "live"
    order2 = Order(id="2", symbol="BTC-USDT", side=OrderSide.BUY, type=OrderType.MARKET, amount=0.1)
    await router.submit(order2)
    assert len(live_handler.submitted) == 1


@pytest.mark.asyncio
async def test_order_manager_submit():
    handler = MockHandler()
    router = OrderRouter(backtest=handler, mode="backtest")
    manager = UnifiedOrderManager(router=router)
    order = await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.1,
        strategy_name="test",
    )
    assert order.status == OrderStatus.FILLED
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_order_router.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement OrderRouter**

```python
# src/order/router.py
from __future__ import annotations

from abc import ABC, abstractmethod
from src.core.types import Order


class OrderHandler(ABC):
    @abstractmethod
    async def submit(self, order: Order) -> Order:
        ...

    @abstractmethod
    async def cancel(self, order_id: str) -> bool:
        ...


class OrderRouter:
    def __init__(
        self,
        backtest: OrderHandler,
        demo: OrderHandler | None = None,
        live: OrderHandler | None = None,
        mode: str = "backtest",
    ):
        self._handlers: dict[str, OrderHandler] = {"backtest": backtest}
        if demo:
            self._handlers["demo"] = demo
        if live:
            self._handlers["live"] = live
        self.mode = mode

    def _get_handler(self) -> OrderHandler:
        handler = self._handlers.get(self.mode)
        if handler is None:
            raise ValueError(f"No handler registered for mode '{self.mode}'")
        return handler

    async def submit(self, order: Order) -> Order:
        return await self._get_handler().submit(order)

    async def cancel(self, order_id: str) -> bool:
        return await self._get_handler().cancel(order_id)
```

- [x] **Step 4: Implement UnifiedOrderManager**

```python
# src/order/manager.py
from __future__ import annotations

from src.core.types import Order, OrderSide, OrderType, Position
from src.order.router import OrderRouter


class UnifiedOrderManager:
    def __init__(self, router: OrderRouter):
        self.router = router
        self._positions: dict[str, dict[str, Position]] = {}
        self._balances: dict[str, float] = {}

    async def submit(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        amount: float,
        price: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        strategy_name: str = "",
    ) -> Order:
        order = Order(
            id=f"{strategy_name}-{symbol}-{id(self)}",
            symbol=symbol,
            side=side,
            type=order_type,
            amount=amount,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        return await self.router.submit(order)

    async def cancel(self, order_id: str) -> bool:
        return await self.router.cancel(order_id)

    def get_position(self, strategy_name: str, symbol: str) -> Position | None:
        return self._positions.get(strategy_name, {}).get(symbol)

    def get_balance(self, strategy_name: str) -> float:
        return self._balances.get(strategy_name, 0.0)

    def set_balance(self, strategy_name: str, amount: float) -> None:
        self._balances[strategy_name] = amount
```

- [x] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_order_router.py -v
```

Expected: 3 passed

- [x] **Step 6: Commit**

```bash
git add src/order/ tests/unit/test_order_router.py
git commit -m "feat: add order router and unified order manager"
```

---

## Task 10: Backtest Engine

**Files:**
- Create: `src/backtest/engine.py`
- Create: `src/backtest/report.py`
- Create: `tests/unit/test_backtest_engine.py`
- Create: `tests/unit/test_report.py`

- [x] **Step 1: Write failing tests for backtest engine**

```python
# tests/unit/test_backtest_engine.py
import pytest

from src.core.types import Bar, Order, OrderSide, OrderType, OrderStatus
from src.backtest.engine import BacktestEngine
from src.backtest.matcher import OrderMatcher
from src.backtest.report import BacktestReport


class SimpleStrategy:
    """Test strategy that buys on every bar."""
    name = "simple"

    def __init__(self):
        self.bars = []
        self.orders = []

    async def on_init(self):
        pass

    async def on_bar(self, bar: Bar):
        self.bars.append(bar)
        self.orders.append(
            Order(id=f"o-{len(self.orders)}", symbol="BTC-USDT", side=OrderSide.BUY, type=OrderType.MARKET, amount=0.01)
        )
        return self.orders[-1]

    async def on_order(self, order: Order):
        pass


@pytest.mark.asyncio
async def test_backtest_engine_runs():
    bars = [
        Bar(timestamp=i * 1000, open=50000 + i * 100, high=51000 + i * 100, low=49000 + i * 100, close=50500 + i * 100, volume=100)
        for i in range(10)
    ]
    matcher = OrderMatcher(slippage=0.0, fee_rate=0.001)
    engine = BacktestEngine(
        initial_capital=100000,
        matcher=matcher,
    )
    strategy = SimpleStrategy()
    report = await engine.run(strategy, bars)
    assert isinstance(report, BacktestReport)
    assert report.total_trades == 10
    assert report.final_equity < 100000  # fees deducted
```

- [x] **Step 2: Write failing tests for report**

```python
# tests/unit/test_report.py
import pytest
from src.backtest.report import BacktestReport, generate_report


def test_report_calculates_metrics():
    trades = [
        {"pnl": 100, "timestamp": 1},
        {"pnl": -50, "timestamp": 2},
        {"pnl": 200, "timestamp": 3},
        {"pnl": -30, "timestamp": 4},
    ]
    report = generate_report(
        initial_capital=10000,
        trades=trades,
        equity_curve=[10000, 10100, 10050, 10250, 10220],
    )
    assert report.total_return == pytest.approx(0.022)
    assert report.total_trades == 4
    assert report.win_rate == pytest.approx(0.5)
    assert report.max_drawdown >= 0


def test_report_empty_trades():
    report = generate_report(initial_capital=10000, trades=[], equity_curve=[10000])
    assert report.total_return == 0
    assert report.total_trades == 0
    assert report.win_rate == 0
```

- [x] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_backtest_engine.py tests/unit/test_report.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 4: Implement BacktestReport**

```python
# src/backtest/report.py
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class BacktestReport:
    initial_capital: float = 0
    final_equity: float = 0
    total_return: float = 0
    annualized_return: float = 0
    sharpe_ratio: float = 0
    max_drawdown: float = 0
    win_rate: float = 0
    profit_factor: float = 0
    total_trades: int = 0
    trades: list[dict] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)


def generate_report(
    initial_capital: float,
    trades: list[dict],
    equity_curve: list[float],
) -> BacktestReport:
    final_equity = equity_curve[-1] if equity_curve else initial_capital
    total_return = (final_equity - initial_capital) / initial_capital if initial_capital > 0 else 0

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    total_trades = len(trades)
    win_rate = len(wins) / total_trades if total_trades > 0 else 0

    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0

    max_dd = 0.0
    peak = equity_curve[0] if equity_curve else initial_capital
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    returns = []
    for i in range(1, len(equity_curve)):
        if equity_curve[i - 1] > 0:
            returns.append((equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1])

    sharpe = 0.0
    if len(returns) > 1:
        mean_r = sum(returns) / len(returns)
        std_r = math.sqrt(sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1))
        if std_r > 0:
            sharpe = (mean_r / std_r) * math.sqrt(252)

    return BacktestReport(
        initial_capital=initial_capital,
        final_equity=final_equity,
        total_return=total_return,
        sharpe_ratio=sharpe,
        max_drawdown=max_dd,
        win_rate=win_rate,
        profit_factor=profit_factor,
        total_trades=total_trades,
        trades=trades,
        equity_curve=equity_curve,
    )
```

- [x] **Step 5: Implement BacktestEngine**

```python
# src/backtest/engine.py
from __future__ import annotations

from src.core.types import Bar, Order, OrderStatus
from src.backtest.matcher import OrderMatcher
from src.backtest.report import BacktestReport, generate_report


class BacktestEngine:
    def __init__(self, initial_capital: float, matcher: OrderMatcher):
        self.initial_capital = initial_capital
        self.matcher = matcher

    async def run(self, strategy, bars: list[Bar]) -> BacktestReport:
        equity = self.initial_capital
        equity_curve = [equity]
        trades = []

        await strategy.on_init()

        for bar in bars:
            order = await strategy.on_bar(bar)
            if order is None:
                equity_curve.append(equity)
                continue

            result = self.matcher.match(order, bar)
            order.status = result.status

            if result.status == OrderStatus.FILLED and result.fill_price is not None:
                cost = result.fill_price * order.amount
                fee = result.fee
                pnl = 0
                if order.side.value == "buy":
                    equity -= (cost + fee)
                else:
                    equity += (cost - fee)
                trades.append({"pnl": pnl, "fee": fee, "timestamp": bar.timestamp})

            await strategy.on_order(order)
            equity_curve.append(equity)

        return generate_report(
            initial_capital=self.initial_capital,
            trades=trades,
            equity_curve=equity_curve,
        )
```

- [x] **Step 6: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_backtest_engine.py tests/unit/test_report.py -v
```

Expected: 3 passed

- [x] **Step 7: Commit**

```bash
git add src/backtest/engine.py src/backtest/report.py tests/unit/test_backtest_engine.py tests/unit/test_report.py
git commit -m "feat: add backtest engine and report generation"
```

---

## Task 11: Exchange Adapters (ccxt)

**Files:**
- Create: `src/exchange/base.py`
- Create: `src/exchange/okx_spot.py`
- Create: `src/exchange/okx_swap.py`
- Create: `src/exchange/okx_futures.py`
- Create: `src/exchange/okx_options.py`
- Create: `tests/integration/test_exchange_adapter.py`

- [x] **Step 1: Write failing tests for exchange adapter**

```python
# tests/integration/test_exchange_adapter.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.types import Order, OrderSide, OrderType, OrderStatus
from src.exchange.base import ExchangeAdapter
from src.exchange.okx_spot import OKXSpotAdapter


@pytest.fixture
def mock_ccxt():
    with patch("src.exchange.okx_spot.ccxt") as mock:
        exchange = AsyncMock()
        exchange.create_order = AsyncMock(return_value={
            "id": "ord-001",
            "status": "closed",
            "average": 50000.0,
            "fee": {"cost": 2.5},
        })
        exchange.cancel_order = AsyncMock(return_value={"id": "ord-001", "status": "canceled"})
        mock.okx.return_value = exchange
        yield exchange


@pytest.mark.asyncio
async def test_spot_adapter_submit(mock_ccxt):
    adapter = OKXSpotAdapter(api_key="k", secret="s", passphrase="p")
    order = Order(id="1", symbol="BTC-USDT", side=OrderSide.BUY, type=OrderType.MARKET, amount=0.1)
    result = await adapter.submit(order)
    assert result.status == OrderStatus.FILLED
    mock_ccxt.create_order.assert_called_once()


@pytest.mark.asyncio
async def test_spot_adapter_cancel(mock_ccxt):
    adapter = OKXSpotAdapter(api_key="k", secret="s", passphrase="p")
    result = await adapter.cancel("ord-001")
    assert result is True
    mock_ccxt.cancel_order.assert_called_once()
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/integration/test_exchange_adapter.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement ExchangeAdapter base**

```python
# src/exchange/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from src.core.types import Order
from src.order.router import OrderHandler


class ExchangeAdapter(OrderHandler, ABC):
    def __init__(self, api_key: str, secret: str, passphrase: str, sandbox: bool = False):
        self.api_key = api_key
        self.secret = secret
        self.passphrase = passphrase
        self.sandbox = sandbox
```

- [x] **Step 4: Implement OKXSpotAdapter**

```python
# src/exchange/okx_spot.py
from __future__ import annotations

import ccxt.async_support as ccxt

from src.core.types import Order, OrderSide, OrderType, OrderStatus
from src.exchange.base import ExchangeAdapter


class OKXSpotAdapter(ExchangeAdapter):
    def __init__(self, api_key: str, secret: str, passphrase: str, sandbox: bool = False):
        super().__init__(api_key, secret, passphrase, sandbox)
        self._exchange = ccxt.okx({
            "apiKey": api_key,
            "secret": secret,
            "password": passphrase,
            "sandbox": sandbox,
        })

    async def submit(self, order: Order) -> Order:
        side = "buy" if order.side == OrderSide.BUY else "sell"
        order_type = "market" if order.type == OrderType.MARKET else "limit"
        params = {}
        if order.stop_loss:
            params["stopLoss"] = {"triggerPrice": order.stop_loss}
        if order.take_profit:
            params["takeProfit"] = {"triggerPrice": order.take_profit}

        result = await self._exchange.create_order(
            symbol=order.symbol,
            type=order_type,
            side=side,
            amount=order.amount,
            price=order.price,
            params=params,
        )

        status_map = {
            "closed": OrderStatus.FILLED,
            "open": OrderStatus.PENDING,
            "canceled": OrderStatus.CANCELLED,
        }
        order.status = status_map.get(result.get("status", ""), OrderStatus.PENDING)
        order.fill_price = result.get("average")
        return order

    async def cancel(self, order_id: str) -> bool:
        try:
            await self._exchange.cancel_order(order_id)
            return True
        except Exception:
            return False

    async def close(self):
        await self._exchange.close()
```

- [x] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/integration/test_exchange_adapter.py -v
```

Expected: 2 passed

- [x] **Step 6: Implement swap/futures/options adapters (same pattern)**

```python
# src/exchange/okx_swap.py
from src.exchange.okx_spot import OKXSpotAdapter


class OKXSwapAdapter(OKXSpotAdapter):
    """Perpetual swap adapter - uses same ccxt interface with SWAP symbols."""
    pass
```

```python
# src/exchange/okx_futures.py
from src.exchange.okx_spot import OKXSpotAdapter


class OKXFuturesAdapter(OKXSpotAdapter):
    """Delivery futures adapter - uses same ccxt interface with FUTURES symbols."""
    pass
```

```python
# src/exchange/okx_options.py
from src.exchange.okx_spot import OKXSpotAdapter


class OKXOptionsAdapter(OKXSpotAdapter):
    """Options adapter - uses same ccxt interface with OPTION symbols."""
    pass
```

- [x] **Step 7: Commit**

```bash
git add src/exchange/ tests/integration/test_exchange_adapter.py
git commit -m "feat: add exchange adapters for spot, swap, futures, options"
```

---

## Task 12: Telegram Notifications

**Files:**
- Create: `src/notify/telegram.py`
- Create: `tests/unit/test_telegram.py`

- [x] **Step 1: Write failing tests**

```python
# tests/unit/test_telegram.py
import pytest
from unittest.mock import AsyncMock, patch

from src.notify.telegram import TelegramNotifier


@pytest.mark.asyncio
async def test_send_notification():
    with patch("httpx.AsyncClient") as mock_client:
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
        mock_client.return_value.__aexit__ = AsyncMock()
        mock_client.return_value.post = AsyncMock(return_value=mock_response)

        notifier = TelegramNotifier(bot_token="test-token", chat_id="123")
        await notifier.send("Test message")

        mock_client.return_value.post.assert_called_once()


def test_format_position_opened():
    notifier = TelegramNotifier(bot_token="t", chat_id="c")
    msg = notifier.format_position_opened(
        strategy="MA_Cross",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=0.1,
        price=50000,
        stop_loss=49000,
    )
    assert "MA_Cross" in msg
    assert "BTC-USDT-SWAP" in msg
    assert "50,000" in msg


def test_format_risk_alert():
    notifier = TelegramNotifier(bot_token="t", chat_id="c")
    msg = notifier.format_risk_alert(
        alert_type="Daily Loss",
        detail="Loss reached 5.2%, exceeding 5% threshold",
    )
    assert "Daily Loss" in msg
    assert "5.2%" in msg
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_telegram.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement TelegramNotifier**

```python
# src/notify/telegram.py
from __future__ import annotations

from datetime import datetime

import httpx


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

    async def send(self, text: str) -> None:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{self.base_url}/sendMessage",
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
            )

    def format_position_opened(
        self,
        strategy: str,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        stop_loss: float | None = None,
    ) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "Position Opened",
            f"Strategy: {strategy}",
            f"Symbol: {symbol}",
            f"Side: {side.title()}",
            f"Amount: {amount}",
            f"Price: {price:,.2f} USDT",
        ]
        if stop_loss:
            lines.append(f"Stop-Loss: {stop_loss:,.2f} USDT")
        lines.append(f"Time: {now}")
        return "\n".join(lines)

    def format_risk_alert(self, alert_type: str, detail: str) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"Risk Alert: {alert_type}\n{detail}\nTime: {now}"
```

- [x] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_telegram.py -v
```

Expected: 3 passed

- [x] **Step 5: Commit**

```bash
git add src/notify/telegram.py tests/unit/test_telegram.py
git commit -m "feat: add Telegram notification sender"
```

---

## Task 13: Market Data Service

**Files:**
- Create: `src/market/service.py`
- Create: `tests/unit/test_market_service.py`

- [x] **Step 1: Write failing tests**

```python
# tests/unit/test_market_service.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from collections import deque

from src.core.types import Bar
from src.market.service import MarketDataService


@pytest.mark.asyncio
async def test_market_service_subscribe():
    with patch("src.market.service.ccxt") as mock_ccxt:
        exchange = AsyncMock()
        exchange.watch_ohlcv = AsyncMock(return_value=[
            [1700000000000, 50000, 51000, 49000, 50500, 100],
            [1700003600000, 50500, 52000, 50000, 51500, 120],
        ])
        mock_ccxt.okx.return_value = exchange

        service = MarketDataService(api_key="k", secret="s", passphrase="p")
        service._exchange = exchange

        bars = []
        async def on_bar(bar: Bar):
            bars.append(bar)

        service.subscribe("BTC-USDT", "1h", on_bar)
        await service._poll_once("BTC-USDT", "1h")
        assert len(bars) == 2
        assert bars[0].open == 50000


def test_bar_buffer():
    service = MarketDataService(api_key="k", secret="s", passphrase="p")
    service._buffers["BTC-USDT:1h"] = deque(maxlen=100)
    for i in range(5):
        service._buffers["BTC-USDT:1h"].append(
            Bar(timestamp=i, open=50000, high=51000, low=49000, close=50500, volume=100)
        )
    bars = service.get_recent_bars("BTC-USDT", "1h", count=3)
    assert len(bars) == 3
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_market_service.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement MarketDataService**

```python
# src/market/service.py
from __future__ import annotations

import asyncio
from collections import deque
from typing import Callable, Awaitable

import ccxt.async_support as ccxt

from src.core.types import Bar


BarCallback = Callable[[Bar], Awaitable[None]]


class MarketDataService:
    def __init__(self, api_key: str, secret: str, passphrase: str):
        self._exchange = ccxt.okx({
            "apiKey": api_key,
            "secret": secret,
            "password": passphrase,
        })
        self._subscriptions: dict[str, list[BarCallback]] = {}
        self._buffers: dict[str, deque[Bar]] = {}
        self._running = False

    def subscribe(self, symbol: str, timeframe: str, callback: BarCallback) -> None:
        key = f"{symbol}:{timeframe}"
        self._subscriptions.setdefault(key, []).append(callback)
        if key not in self._buffers:
            self._buffers[key] = deque(maxlen=1000)

    def get_recent_bars(self, symbol: str, timeframe: str, count: int = 100) -> list[Bar]:
        key = f"{symbol}:{timeframe}"
        buf = self._buffers.get(key, deque())
        return list(buf)[-count:]

    async def _poll_once(self, symbol: str, timeframe: str) -> None:
        key = f"{symbol}:{timeframe}"
        ohlcv = await self._exchange.watch_ohlcv(symbol, timeframe)
        for row in ohlcv:
            bar = Bar(
                timestamp=int(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
            self._buffers.setdefault(key, deque(maxlen=1000)).append(bar)
            for callback in self._subscriptions.get(key, []):
                await callback(bar)

    async def start(self) -> None:
        self._running = True
        while self._running:
            for key in list(self._subscriptions.keys()):
                symbol, timeframe = key.split(":", 1)
                try:
                    await self._poll_once(symbol, timeframe)
                except Exception:
                    await asyncio.sleep(1)

    async def stop(self) -> None:
        self._running = False
        await self._exchange.close()
```

- [x] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_market_service.py -v
```

Expected: 2 passed

- [x] **Step 5: Commit**

```bash
git add src/market/service.py tests/unit/test_market_service.py
git commit -m "feat: add market data service with WebSocket subscription"
```

---

## Task 14: YAML Strategy Executor

**Files:**
- Create: `src/strategy/yaml_strategy.py`
- Create: `config/strategies/example_ma_cross.yaml`
- Create: `tests/unit/test_yaml_strategy.py`

- [x] **Step 1: Write failing tests**

```python
# tests/unit/test_yaml_strategy.py
import pytest

from src.core.types import Bar
from src.strategy.yaml_strategy import YAMLStrategy, parse_condition


def test_parse_simple_condition():
    indicators = {"fast_ma": 50500, "slow_ma": 50000, "rsi": 65}
    assert parse_condition("fast_ma > slow_ma", indicators) is True
    assert parse_condition("fast_ma < slow_ma", indicators) is False
    assert parse_condition("rsi < 70", indicators) is True
    assert parse_condition("rsi > 70", indicators) is False


def test_parse_condition_with_literal():
    indicators = {"close": 50500}
    assert parse_condition("close > 50000", indicators) is True
    assert parse_condition("close < 50000", indicators) is False


def test_yaml_strategy_creation():
    config = {
        "name": "MA_Cross",
        "symbol": "BTC-USDT",
        "timeframe": "1h",
        "params": {"fast_period": 10, "slow_period": 30},
        "indicators": {
            "fast_ma": {"type": "SMA", "period": "{{ fast_period }}"},
            "slow_ma": {"type": "SMA", "period": "{{ slow_period }}"},
        },
        "conditions": {
            "buy": ["fast_ma > slow_ma"],
            "sell": ["fast_ma < slow_ma"],
        },
    }
    strategy = YAMLStrategy(config)
    assert strategy.name == "MA_Cross"
    assert strategy.symbol == "BTC-USDT"
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_yaml_strategy.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement YAMLStrategy**

```python
# src/strategy/yaml_strategy.py
from __future__ import annotations

import re
from typing import Any

from src.core.types import Bar
from src.strategy.base import BaseStrategy


def parse_condition(expr: str, values: dict[str, float]) -> bool:
    operators = [">=", "<=", "==", "!=", ">", "<"]
    for op in operators:
        if op in expr:
            left, right = expr.split(op, 1)
            left = left.strip()
            right = right.strip()
            left_val = values.get(left, _try_float(left))
            right_val = values.get(right, _try_float(right))
            if left_val is None or right_val is None:
                return False
            if op == ">":
                return left_val > right_val
            elif op == "<":
                return left_val < right_val
            elif op == ">=":
                return left_val >= right_val
            elif op == "<=":
                return left_val <= right_val
            elif op == "==":
                return left_val == right_val
            elif op == "!=":
                return left_val != right_val
    return False


def _try_float(val: str) -> float | None:
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


_PARAM_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")


class YAMLStrategy(BaseStrategy):
    def __init__(self, config: dict[str, Any]):
        super().__init__()
        self.name = config.get("name", "unnamed")
        self.symbol = config.get("symbol", "")
        self.timeframe = config.get("timeframe", "1h")
        self.params = config.get("params", {})
        self.indicator_configs = config.get("indicators", {})
        self.conditions = config.get("conditions", {})
        self._indicator_values: dict[str, float] = {}

    def _resolve_params(self, value: str) -> str:
        def replacer(m: re.Match) -> str:
            return str(self.params.get(m.group(1), m.group(0)))
        return _PARAM_PATTERN.sub(replacer, value)

    async def on_bar(self, bar: Bar) -> None:
        self._indicator_values["close"] = bar.close
        self._indicator_values["open"] = bar.open
        self._indicator_values["high"] = bar.high
        self._indicator_values["low"] = bar.low
        self._indicator_values["volume"] = bar.volume

        buy_conditions = self.conditions.get("buy", [])
        if all(parse_condition(c, self._indicator_values) for c in buy_conditions):
            await self.buy(self.symbol, 0.1)

        sell_conditions = self.conditions.get("sell", [])
        if all(parse_condition(c, self._indicator_values) for c in sell_conditions):
            await self.sell(self.symbol, 0.1)
```

- [x] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_yaml_strategy.py -v
```

Expected: 3 passed

- [x] **Step 5: Create example strategy config**

```yaml
# config/strategies/example_ma_cross.yaml
name: MA_Cross
symbol: BTC-USDT-SWAP
timeframe: 1h
capital_pct: 0.1
risk:
  max_position_pct: 0.1
  stop_loss_pct: 0.02
  take_profit_pct: 0.05
params:
  fast_period: 10
  slow_period: 30
indicators:
  fast_ma: { type: SMA, period: "{{ fast_period }}" }
  slow_ma: { type: SMA, period: "{{ slow_period }}" }
conditions:
  buy:
    - "fast_ma > slow_ma"
  sell:
    - "fast_ma < slow_ma"
```

- [x] **Step 6: Commit**

```bash
git add src/strategy/yaml_strategy.py config/strategies/ tests/unit/test_yaml_strategy.py
git commit -m "feat: add YAML strategy executor with condition DSL"
```

---

## Task 15: Built-in Strategy (MA Cross)

**Files:**
- Create: `src/strategy/builtin/ma_cross.py`
- Create: `tests/unit/test_ma_cross.py`

- [x] **Step 1: Write failing tests**

```python
# tests/unit/test_ma_cross.py
import pytest

from src.core.types import Bar
from src.strategy.builtin.ma_cross import MACrossStrategy


def _make_bars(closes: list[float]) -> list[Bar]:
    bars = []
    for i, c in enumerate(closes):
        bars.append(Bar(
            timestamp=1000 + i * 3600000,
            open=c - 100,
            high=c + 200,
            low=c - 200,
            close=c,
            volume=100,
        ))
    return bars


@pytest.mark.asyncio
async def test_ma_cross_generates_buy_signal():
    strategy = MACrossStrategy(fast_period=3, slow_period=5)
    await strategy.on_init()

    # Build up enough bars, then create a crossover
    closes = [50000, 50100, 50200, 50300, 50400, 50800, 51200]
    bars = _make_bars(closes)

    signals = []
    for bar in bars:
        strategy._pending_order = None
        await strategy.on_bar(bar)
        if strategy._pending_order:
            signals.append(strategy._pending_order)

    # Should have at least one buy signal when fast MA crosses above slow MA
    assert len(signals) >= 1


def test_sma_calculation():
    strategy = MACrossStrategy(fast_period=3, slow_period=5)
    values = [1, 2, 3, 4, 5]
    assert strategy._sma(values, 3) == pytest.approx((3 + 4 + 5) / 3)
    assert strategy._sma(values, 5) == pytest.approx(sum(values) / 5)
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_ma_cross.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement MACrossStrategy**

```python
# src/strategy/builtin/ma_cross.py
from __future__ import annotations

from src.core.types import Bar, Order, OrderSide, OrderType
from src.strategy.base import BaseStrategy


class MACrossStrategy(BaseStrategy):
    name = "MA_Cross"

    def __init__(self, fast_period: int = 10, slow_period: int = 30):
        super().__init__()
        self.fast_period = fast_period
        self.slow_period = slow_period
        self._closes: list[float] = []
        self._pending_order: Order | None = None

    @staticmethod
    def _sma(values: list[float], period: int) -> float:
        if len(values) < period:
            return 0.0
        return sum(values[-period:]) / period

    async def on_init(self) -> None:
        self._closes = []
        self._pending_order = None

    async def on_bar(self, bar: Bar) -> None:
        self._closes.append(bar.close)
        if len(self._closes) < self.slow_period + 1:
            return

        fast_now = self._sma(self._closes, self.fast_period)
        slow_now = self._sma(self._closes, self.slow_period)
        fast_prev = self._sma(self._closes[:-1], self.fast_period)
        slow_prev = self._sma(self._closes[:-1], self.slow_period)

        if fast_prev <= slow_prev and fast_now > slow_now:
            self._pending_order = Order(
                id=f"buy-{len(self._closes)}",
                symbol="",
                side=OrderSide.BUY,
                type=OrderType.MARKET,
                amount=0.01,
            )
        elif fast_prev >= slow_prev and fast_now < slow_now:
            self._pending_order = Order(
                id=f"sell-{len(self._closes)}",
                symbol="",
                side=OrderSide.SELL,
                type=OrderType.MARKET,
                amount=0.01,
            )
        else:
            self._pending_order = None
```

- [x] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_ma_cross.py -v
```

Expected: 2 passed

- [x] **Step 5: Commit**

```bash
git add src/strategy/builtin/ma_cross.py tests/unit/test_ma_cross.py
git commit -m "feat: add built-in MA cross strategy"
```

---

## Task 16: FastAPI Web API

**Files:**
- Create: `src/web/app.py`
- Create: `src/web/deps.py`
- Create: `src/web/api/strategies.py`
- Create: `src/web/api/backtest.py`
- Create: `src/web/api/trading.py`
- Create: `src/web/api/market.py`
- Create: `src/web/ws.py`
- Create: `tests/integration/test_web_api.py`

- [x] **Step 1: Write failing tests**

```python
# tests/integration/test_web_api.py
import pytest
from httpx import AsyncClient, ASGITransport

from src.web.app import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.mark.asyncio
async def test_health(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_get_strategies(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/strategies")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_run_backtest(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/backtest/run", json={
            "strategy": "ma_cross",
            "symbol": "BTC-USDT",
            "timeframe": "1h",
            "start_time": 1700000000000,
            "end_time": 1700100000000,
            "initial_capital": 100000,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "total_return" in data


@pytest.mark.asyncio
async def test_get_positions(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/trading/positions")
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_klines(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/market/klines?symbol=BTC-USDT&timeframe=1h&limit=100")
        assert resp.status_code == 200
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/integration/test_web_api.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [x] **Step 3: Implement FastAPI app and routes**

```python
# src/web/app.py
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.web.api import strategies, backtest, trading, market


def create_app() -> FastAPI:
    app = FastAPI(title="OKX Bot API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    app.include_router(strategies.router, prefix="/api/strategies", tags=["strategies"])
    app.include_router(backtest.router, prefix="/api/backtest", tags=["backtest"])
    app.include_router(trading.router, prefix="/api/trading", tags=["trading"])
    app.include_router(market.router, prefix="/api/market", tags=["market"])

    return app
```

```python
# src/web/api/strategies.py
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("")
async def list_strategies():
    return []


@router.post("/{name}/start")
async def start_strategy(name: str):
    return {"status": "started", "strategy": name}


@router.post("/{name}/stop")
async def stop_strategy(name: str):
    return {"status": "stopped", "strategy": name}
```

```python
# src/web/api/backtest.py
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class BacktestRequest(BaseModel):
    strategy: str
    symbol: str
    timeframe: str
    start_time: int
    end_time: int
    initial_capital: float


@router.post("/run")
async def run_backtest(req: BacktestRequest):
    return {
        "total_return": 0.0,
        "sharpe_ratio": 0.0,
        "max_drawdown": 0.0,
        "win_rate": 0.0,
        "total_trades": 0,
    }


@router.get("/results")
async def list_results():
    return []
```

```python
# src/web/api/trading.py
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/positions")
async def get_positions():
    return []


@router.get("/orders")
async def get_orders():
    return []


@router.get("/account")
async def get_account():
    return {"equity": 0, "daily_pnl": 0}
```

```python
# src/web/api/market.py
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/klines")
async def get_klines(symbol: str = "BTC-USDT", timeframe: str = "1h", limit: int = 100):
    return []


@router.get("/tickers")
async def get_tickers():
    return []
```

```python
# src/web/ws.py
from __future__ import annotations

import asyncio
from fastapi import WebSocket


class WebSocketManager:
    def __init__(self):
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket):
        self._connections.remove(ws)

    async def broadcast(self, data: dict):
        for conn in self._connections:
            try:
                await conn.send_json(data)
            except Exception:
                pass
```

- [x] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/integration/test_web_api.py -v
```

Expected: 5 passed

- [x] **Step 5: Commit**

```bash
git add src/web/ tests/integration/test_web_api.py
git commit -m "feat: add FastAPI web API with strategy, backtest, trading, market routes"
```

---

## Task 17: Frontend Scaffolding

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/src/main.ts`
- Create: `frontend/src/App.vue`
- Create: `frontend/src/router/index.ts`

- [x] **Step 1: Initialize Vue 3 project**

```bash
cd /Users/zane/Documents/Self/Project/Self/okx-bot
npm create vite@latest frontend -- --template vue-ts
cd frontend
npm install
npm install vue-router@4 pinia element-plus @element-plus/icons-vue echarts axios
npm install -D @types/node
```

- [x] **Step 2: Configure vite**

```typescript
// frontend/vite.config.ts
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:8080',
        ws: true,
      },
    },
  },
})
```

- [x] **Step 3: Create router**

```typescript
// frontend/src/router/index.ts
import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'Dashboard', component: () => import('@/views/Dashboard.vue') },
  { path: '/strategies', name: 'Strategies', component: () => import('@/views/Strategy.vue') },
  { path: '/backtest', name: 'Backtest', component: () => import('@/views/Backtest.vue') },
  { path: '/market', name: 'Market', component: () => import('@/views/Market.vue') },
  { path: '/trades', name: 'Trades', component: () => import('@/views/Trades.vue') },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})
```

- [x] **Step 4: Create App.vue with sidebar layout**

```vue
<!-- frontend/src/App.vue -->
<template>
  <el-container style="height: 100vh">
    <el-aside width="200px">
      <el-menu :default-active="route.path" router>
        <el-menu-item index="/">Dashboard</el-menu-item>
        <el-menu-item index="/strategies">Strategies</el-menu-item>
        <el-menu-item index="/backtest">Backtest</el-menu-item>
        <el-menu-item index="/market">Market</el-menu-item>
        <el-menu-item index="/trades">Trades</el-menu-item>
      </el-menu>
    </el-aside>
    <el-main>
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup lang="ts">
import { useRoute } from 'vue-router'
const route = useRoute()
</script>
```

- [x] **Step 5: Create main.ts**

```typescript
// frontend/src/main.ts
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import { router } from './router'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(ElementPlus)
app.mount('#app')
```

- [x] **Step 6: Create placeholder views**

```vue
<!-- frontend/src/views/Dashboard.vue -->
<template>
  <div>
    <h2>Dashboard</h2>
    <el-row :gutter="20">
      <el-col :span="8"><el-card>Total Equity: --</el-card></el-col>
      <el-col :span="8"><el-card>Daily PnL: --</el-card></el-col>
      <el-col :span="8"><el-card>Active Strategies: --</el-card></el-col>
    </el-row>
  </div>
</template>
```

```vue
<!-- frontend/src/views/Strategy.vue -->
<template>
  <div><h2>Strategy Management</h2><p>Strategy list and editor will go here.</p></div>
</template>
```

```vue
<!-- frontend/src/views/Backtest.vue -->
<template>
  <div><h2>Backtest Center</h2><p>Backtest configuration and results will go here.</p></div>
</template>
```

```vue
<!-- frontend/src/views/Market.vue -->
<template>
  <div><h2>Market Data</h2><p>K-line charts will go here.</p></div>
</template>
```

```vue
<!-- frontend/src/views/Trades.vue -->
<template>
  <div><h2>Trade History</h2><p>Order and trade history will go here.</p></div>
</template>
```

- [x] **Step 7: Verify frontend builds**

```bash
cd frontend && npm run build
```

Expected: Build succeeds

- [x] **Step 8: Commit**

```bash
git add frontend/
git commit -m "feat: add Vue 3 frontend scaffolding with router and layout"
```

---

## Task 18: Frontend - WebSocket Composable + Dashboard Store

**Files:**
- Create: `frontend/src/composables/useWebSocket.ts`
- Create: `frontend/src/stores/dashboard.ts`
- Modify: `frontend/src/views/Dashboard.vue`

- [x] **Step 1: Create WebSocket composable**

```typescript
// frontend/src/composables/useWebSocket.ts
import { ref, onUnmounted } from 'vue'

export function useWebSocket(url: string) {
  const data = ref<any>(null)
  const connected = ref(false)
  let ws: WebSocket | null = null
  let reconnectTimer: number | null = null

  function connect() {
    ws = new WebSocket(url)
    ws.onopen = () => { connected.value = true }
    ws.onmessage = (e) => { data.value = JSON.parse(e.data) }
    ws.onclose = () => {
      connected.value = false
      reconnectTimer = window.setTimeout(connect, 3000)
    }
  }

  connect()

  onUnmounted(() => {
    ws?.close()
    if (reconnectTimer) clearTimeout(reconnectTimer)
  })

  return { data, connected }
}
```

- [x] **Step 2: Create dashboard store**

```typescript
// frontend/src/stores/dashboard.ts
import { defineStore } from 'pinia'
import { ref } from 'vue'
import axios from 'axios'

export const useDashboardStore = defineStore('dashboard', () => {
  const equity = ref(0)
  const dailyPnl = ref(0)
  const activeStrategies = ref(0)
  const alerts = ref<any[]>([])

  async function fetchDashboard() {
    const { data } = await axios.get('/api/trading/account')
    equity.value = data.equity
    dailyPnl.value = data.daily_pnl
  }

  return { equity, dailyPnl, activeStrategies, alerts, fetchDashboard }
})
```

- [x] **Step 3: Update Dashboard.vue**

```vue
<!-- frontend/src/views/Dashboard.vue -->
<template>
  <div>
    <h2>Dashboard</h2>
    <el-row :gutter="20">
      <el-col :span="8">
        <el-card>
          <template #header>Total Equity</template>
          <div style="font-size: 24px">{{ equity.toFixed(2) }} USDT</div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card>
          <template #header>Daily PnL</template>
          <div :style="{ color: dailyPnl >= 0 ? 'green' : 'red', fontSize: '24px' }">
            {{ dailyPnl.toFixed(2) }} USDT
          </div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card>
          <template #header>Active Strategies</template>
          <div style="font-size: 24px">{{ activeStrategies }}</div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { storeToRefs } from 'pinia'
import { onMounted } from 'vue'
import { useDashboardStore } from '@/stores/dashboard'

const store = useDashboardStore()
const { equity, dailyPnl, activeStrategies } = storeToRefs(store)

onMounted(() => store.fetchDashboard())
</script>
```

- [x] **Step 4: Verify frontend builds**

```bash
cd frontend && npm run build
```

- [x] **Step 5: Commit**

```bash
git add frontend/src/composables/ frontend/src/stores/ frontend/src/views/Dashboard.vue
git commit -m "feat: add WebSocket composable, dashboard store, and dashboard view"
```

---

## Task 19: Frontend - Candlestick Chart Component

**Files:**
- Create: `frontend/src/components/Candlestick.vue`
- Modify: `frontend/src/views/Market.vue`

- [x] **Step 1: Create Candlestick component**

```vue
<!-- frontend/src/components/Candlestick.vue -->
<template>
  <div ref="chartRef" style="width: 100%; height: 500px"></div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import * as echarts from 'echarts'

interface Kline {
  timestamp: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

const props = defineProps<{ klines: Kline[] }>()
const chartRef = ref<HTMLElement>()
let chart: echarts.ECharts | null = null

function render() {
  if (!chart || !props.klines.length) return

  const dates = props.klines.map(k => new Date(k.timestamp).toLocaleDateString())
  const ohlc = props.klines.map(k => [k.open, k.close, k.low, k.high])
  const volumes = props.klines.map(k => k.volume)

  chart.setOption({
    tooltip: { trigger: 'axis' },
    grid: [
      { left: '10%', right: '8%', height: '50%' },
      { left: '10%', right: '8%', top: '70%', height: '20%' },
    ],
    xAxis: [
      { type: 'category', data: dates, gridIndex: 0 },
      { type: 'category', data: dates, gridIndex: 1 },
    ],
    yAxis: [
      { scale: true, gridIndex: 0 },
      { scale: true, gridIndex: 1 },
    ],
    series: [
      {
        type: 'candlestick',
        data: ohlc,
        xAxisIndex: 0,
        yAxisIndex: 0,
      },
      {
        type: 'bar',
        data: volumes,
        xAxisIndex: 1,
        yAxisIndex: 1,
      },
    ],
  })
}

onMounted(() => {
  if (chartRef.value) {
    chart = echarts.init(chartRef.value)
    render()
  }
})

watch(() => props.klines, render, { deep: true })
</script>
```

- [x] **Step 2: Update Market.vue**

```vue
<!-- frontend/src/views/Market.vue -->
<template>
  <div>
    <h2>Market Data</h2>
    <Candlestick :klines="klines" />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import axios from 'axios'
import Candlestick from '@/components/Candlestick.vue'

const klines = ref<any[]>([])

onMounted(async () => {
  const { data } = await axios.get('/api/market/klines?symbol=BTC-USDT&timeframe=1h&limit=100')
  klines.value = data
})
</script>
```

- [x] **Step 3: Verify frontend builds**

```bash
cd frontend && npm run build
```

- [x] **Step 4: Commit**

```bash
git add frontend/src/components/Candlestick.vue frontend/src/views/Market.vue
git commit -m "feat: add candlestick chart component and market view"
```

---

## Task 20: Frontend - Code Editor + Strategy Form

**Files:**
- Create: `frontend/src/components/CodeEditor.vue`
- Create: `frontend/src/components/StrategyForm.vue`
- Modify: `frontend/src/views/Strategy.vue`

- [x] **Step 1: Install Monaco Editor**

```bash
cd frontend && npm install monaco-editor
```

- [x] **Step 2: Create CodeEditor component**

```vue
<!-- frontend/src/components/CodeEditor.vue -->
<template>
  <div ref="editorRef" style="height: 400px; border: 1px solid #ccc"></div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import * as monaco from 'monaco-editor'

const props = defineProps<{ modelValue: string; language?: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const editorRef = ref<HTMLElement>()
let editor: monaco.editor.IStandaloneCodeEditor | null = null

onMounted(() => {
  if (editorRef.value) {
    editor = monaco.editor.create(editorRef.value, {
      value: props.modelValue,
      language: props.language || 'python',
      theme: 'vs-dark',
      minimap: { enabled: false },
      automaticLayout: true,
    })
    editor.onDidChangeModelContent(() => {
      emit('update:modelValue', editor!.getValue())
    })
  }
})

watch(() => props.modelValue, (val) => {
  if (editor && editor.getValue() !== val) {
    editor.setValue(val)
  }
})
</script>
```

- [x] **Step 3: Create StrategyForm component**

```vue
<!-- frontend/src/components/StrategyForm.vue -->
<template>
  <el-form :model="form" label-width="120px">
    <el-form-item label="Name">
      <el-input v-model="form.name" />
    </el-form-item>
    <el-form-item label="Symbol">
      <el-input v-model="form.symbol" placeholder="BTC-USDT-SWAP" />
    </el-form-item>
    <el-form-item label="Timeframe">
      <el-select v-model="form.timeframe">
        <el-option label="1m" value="1m" />
        <el-option label="5m" value="5m" />
        <el-option label="15m" value="15m" />
        <el-option label="1h" value="1h" />
        <el-option label="4h" value="4h" />
        <el-option label="1d" value="1d" />
      </el-select>
    </el-form-item>
    <el-form-item label="Capital %">
      <el-input-number v-model="form.capital_pct" :min="0.01" :max="1" :step="0.01" />
    </el-form-item>
    <el-form-item label="Stop Loss %">
      <el-input-number v-model="form.stop_loss_pct" :min="0.001" :max="0.5" :step="0.001" />
    </el-form-item>
    <el-form-item label="Take Profit %">
      <el-input-number v-model="form.take_profit_pct" :min="0.001" :max="1" :step="0.001" />
    </el-form-item>
    <el-form-item>
      <el-button type="primary" @click="$emit('submit', form)">Save</el-button>
    </el-form-item>
  </el-form>
</template>

<script setup lang="ts">
import { reactive } from 'vue'

defineEmits<{ submit: [form: any] }>()

const form = reactive({
  name: '',
  symbol: 'BTC-USDT-SWAP',
  timeframe: '1h',
  capital_pct: 0.1,
  stop_loss_pct: 0.02,
  take_profit_pct: 0.05,
})
</script>
```

- [x] **Step 4: Update Strategy.vue**

```vue
<!-- frontend/src/views/Strategy.vue -->
<template>
  <div>
    <h2>Strategy Management</h2>
    <el-tabs v-model="activeTab">
      <el-tab-pane label="Form Mode" name="form">
        <StrategyForm @submit="onFormSubmit" />
      </el-tab-pane>
      <el-tab-pane label="Code Mode" name="code">
        <CodeEditor v-model="code" language="python" />
        <el-button type="primary" style="margin-top: 10px" @click="onCodeSave">Save</el-button>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import CodeEditor from '@/components/CodeEditor.vue'
import StrategyForm from '@/components/StrategyForm.vue'

const activeTab = ref('form')
const code = ref(`from src.strategy.base import BaseStrategy
from src.core.types import Bar

class MyStrategy(BaseStrategy):
    name = "MyStrategy"

    async def on_bar(self, bar: Bar):
        # Your strategy logic here
        pass
`)

function onFormSubmit(form: any) {
  console.log('Form submitted:', form)
}

function onCodeSave() {
  console.log('Code saved:', code.value)
}
</script>
```

- [x] **Step 5: Verify frontend builds**

```bash
cd frontend && npm run build
```

- [x] **Step 6: Commit**

```bash
git add frontend/src/components/ frontend/src/views/Strategy.vue frontend/package.json
git commit -m "feat: add Monaco code editor, strategy form, and strategy view"
```

---

## Task 21: Engine Lifecycle + Integration

**Files:**
- Create: `src/core/engine.py`
- Create: `tests/integration/test_backtest_flow.py`

- [x] **Step 1: Write failing integration test**

```python
# tests/integration/test_backtest_flow.py
import pytest

from src.core.types import Bar, OrderSide, OrderType
from src.backtest.engine import BacktestEngine
from src.backtest.matcher import OrderMatcher
from src.strategy.builtin.ma_cross import MACrossStrategy


@pytest.mark.asyncio
async def test_full_backtest_flow():
    """Integration test: run MA cross strategy through backtest engine."""
    strategy = MACrossStrategy(fast_period=3, slow_period=5)
    matcher = OrderMatcher(slippage=0.001, fee_rate=0.0005)
    engine = BacktestEngine(initial_capital=100000, matcher=matcher)

    # Generate trending bars (uptrend then downtrend)
    prices = [50000 + i * 200 for i in range(20)] + [54000 - i * 200 for i in range(20)]
    bars = [
        Bar(
            timestamp=1000 + i * 3600000,
            open=p - 100,
            high=p + 300,
            low=p - 300,
            close=p,
            volume=100,
        )
        for i, p in enumerate(prices)
    ]

    report = await engine.run(strategy, bars)
    assert report.initial_capital == 100000
    assert report.final_equity > 0
    assert len(report.equity_curve) == len(bars) + 1
```

- [x] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/integration/test_backtest_flow.py -v
```

Expected: FAIL (strategy doesn't submit orders through the engine properly yet)

- [x] **Step 3: Refactor BacktestEngine to support order-returning strategies**

Update `src/backtest/engine.py` to handle strategies that return orders from `on_bar`:

```python
# src/backtest/engine.py (updated)
from __future__ import annotations

from src.core.types import Bar, Order, OrderSide, OrderStatus
from src.backtest.matcher import OrderMatcher
from src.backtest.report import BacktestReport, generate_report


class BacktestEngine:
    def __init__(self, initial_capital: float, matcher: OrderMatcher):
        self.initial_capital = initial_capital
        self.matcher = matcher

    async def run(self, strategy, bars: list[Bar]) -> BacktestReport:
        equity = self.initial_capital
        equity_curve = [equity]
        trades = []
        total_fees = 0.0

        await strategy.on_init()

        for i, bar in enumerate(bars):
            order = await strategy.on_bar(bar)

            if order is not None and isinstance(order, Order):
                next_bar = bars[i + 1] if i + 1 < len(bars) else bar
                result = self.matcher.match(order, next_bar)
                order.status = result.status
                order.fill_price = result.fill_price

                if result.status == OrderStatus.FILLED and result.fill_price is not None:
                    cost = result.fill_price * order.amount
                    fee = result.fee
                    total_fees += fee
                    pnl = 0
                    if order.side == OrderSide.BUY:
                        pnl = -cost - fee
                    elif order.side == OrderSide.SELL:
                        pnl = cost - fee
                    equity += pnl
                    trades.append({
                        "pnl": pnl,
                        "fee": fee,
                        "side": order.side.value,
                        "price": result.fill_price,
                        "amount": order.amount,
                        "timestamp": bar.timestamp,
                    })

                await strategy.on_order(order)

            equity_curve.append(equity)

        return generate_report(
            initial_capital=self.initial_capital,
            trades=trades,
            equity_curve=equity_curve,
        )
```

- [x] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/integration/test_backtest_flow.py -v
```

Expected: PASS

- [x] **Step 5: Commit**

```bash
git add src/core/engine.py src/backtest/engine.py tests/integration/test_backtest_flow.py
git commit -m "feat: add engine lifecycle and full backtest integration test"
```

---

## Task 22: Final Integration + Lint + Build Verification

- [x] **Step 1: Run all tests**

```bash
uv run pytest tests/ -v
```

Expected: All tests pass

- [x] **Step 2: Run linter**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

- [x] **Step 3: Fix any lint issues**

```bash
uv run ruff check --fix src/ tests/
uv run ruff format src/ tests/
```

- [x] **Step 4: Verify frontend builds**

```bash
cd frontend && npm run build
```

- [x] **Step 5: Create .gitignore**

```
# Python
__pycache__/
*.pyc
.venv/
dist/
*.egg-info/
data/
.env

# Frontend
frontend/node_modules/
frontend/dist/

# IDE
.vscode/
.idea/
```

- [x] **Step 6: Final commit**

```bash
git add .gitignore
git commit -m "chore: add .gitignore and final cleanup"
```
