# OKX Quantitative Trading Bot - Design Spec

## Overview

A modular monolith quantitative trading bot for OKX, supporting backtesting, demo trading, and live trading across spot, perpetual swaps, delivery futures, and options markets.

**Key decisions:**
- Language: Python 3.12+, asyncio-based
- Architecture: Modular monolith with clean internal interfaces
- Strategy: K-line level first, architecture supports high-frequency extension
- Storage: SQLite (via SQLModel)
- Web: FastAPI backend + Vue 3 frontend
- Notifications: Telegram Bot (via Hermes)
- Deployment: Local first, Docker later

---

## 1. Project Structure

```
okx-bot/
├── pyproject.toml
├── config/
│   ├── settings.yaml              # Global config
│   └── strategies/                # Strategy config files (YAML)
│       └── example_ma_cross.yaml
├── src/
│   ├── core/
│   │   ├── engine.py              # Main engine (lifecycle management)
│   │   ├── events.py              # Event type definitions
│   │   └── types.py               # Common types (Order, Position, Bar...)
│   ├── exchange/
│   │   ├── adapter.py             # ccxt adapter base class
│   │   ├── spot.py                # Spot
│   │   ├── swap.py                # Perpetual swap
│   │   ├── futures.py             # Delivery futures
│   │   └── options.py             # Options
│   ├── strategy/
│   │   ├── base.py                # BaseStrategy abstract class
│   │   ├── registry.py            # Strategy registry/discovery
│   │   └── builtin/               # Built-in example strategies
│   │       └── ma_cross.py
│   ├── backtest/
│   │   ├── engine.py              # Backtest engine
│   │   ├── datasource.py          # OKX historical data fetch + cache
│   │   └── report.py              # Backtest report generation
│   ├── risk/
│   │   ├── manager.py             # Risk management controller
│   │   └── rules.py               # Risk rules (stop-loss, position, circuit breaker)
│   ├── order/
│   │   ├── manager.py             # Unified order manager
│   │   └── router.py              # Order router (backtest/demo/live)
│   ├── data/
│   │   ├── models.py              # SQLite ORM (SQLModel)
│   │   └── repository.py          # Data access layer
│   ├── notify/
│   │   └── telegram.py            # Telegram notifications
│   └── web/
│       ├── app.py                 # FastAPI application
│       ├── api/                   # REST API routes
│       │   ├── strategies.py
│       │   ├── backtest.py
│       │   ├── trading.py
│       │   └── market.py
│       ├── ws.py                  # WebSocket real-time push
│       └── frontend/              # Vue 3 frontend
│           ├── src/
│           │   ├── views/
│           │   │   ├── Dashboard.vue
│           │   │   ├── Backtest.vue
│           │   │   ├── Strategy.vue
│           │   │   └── Market.vue
│           │   └── components/
│           │       ├── CodeEditor.vue    # Monaco Editor
│           │       ├── StrategyForm.vue  # Form-based strategy config
│           │       └── Candlestick.vue   # K-line chart
│           └── package.json
└── tests/
```

### Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.12+ |
| Async runtime | asyncio |
| Exchange SDK | ccxt (REST + WebSocket) |
| Web backend | FastAPI + Uvicorn |
| ORM | SQLModel (SQLAlchemy + Pydantic) |
| DB | SQLite (WAL mode) |
| Frontend | Vue 3 + TypeScript + Vite |
| UI components | Element Plus |
| Charts | ECharts (K-line, equity curves) |
| Code editor | Monaco Editor |
| State management | Pinia |
| Dependency mgmt | uv |

---

## 2. Three Modes: Unified Interface

Strategy code is written once and switched between modes via config:

```yaml
# config/settings.yaml
mode: backtest   # backtest | demo | live
```

### Order Routing

```
Strategy calls: strategy.buy("BTC-USDT", 0.1, price=50000)
                │
                ▼
        OrderRouter (by mode)
        ┌───────┼───────┐
        │       │       │
   backtest   demo    live
        │       │       │
        ▼       ▼       ▼
   Backtest   ccxt    ccxt
   Engine    sandbox  production
```

- **Backtest**: Orders go to BacktestEngine, simulated against historical K-lines with slippage and fees
- **Demo**: Orders sent to OKX Sandbox via ccxt (exchange.set_sandbox_mode(True))
- **Live**: Orders sent to OKX production via ccxt

---

## 3. Core Abstractions

### Types (`core/types.py`)

```python
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
    side: str          # "buy" | "sell"
    type: str          # "market" | "limit" | "stop"
    amount: float
    price: float | None
    stop_loss: float | None
    take_profit: float | None
    status: str        # "pending" | "filled" | "cancelled" | "rejected"
    fill_price: float | None
    fill_time: int | None

@dataclass
class Position:
    symbol: str
    side: str          # "long" | "short"
    amount: float
    entry_price: float
    unrealized_pnl: float
    leverage: int
```

### Strategy Base Class (`strategy/base.py`)

```python
class BaseStrategy:
    name: str

    async def on_init(self) -> None: ...
    async def on_bar(self, bar: Bar) -> None: ...
    async def on_order(self, order: Order) -> None: ...
    async def on_position(self, pos: Position) -> None: ...

    # Order methods
    async def buy(self, symbol: str, amount: float,
                  price: float | None = None,
                  sl: float | None = None,
                  tp: float | None = None) -> Order: ...
    async def sell(self, symbol: str, amount: float,
                   price: float | None = None) -> Order: ...
    async def cancel(self, order_id: str) -> bool: ...

    # Data access
    def get_position(self, symbol: str) -> Position | None: ...
    def get_balance(self) -> float: ...
    def get_bars(self, symbol: str, timeframe: str,
                 count: int) -> list[Bar]: ...
```

---

## 4. Backtest Engine

### Workflow

```
1. Set parameters: time range, initial capital, fee rate, slippage
2. Fetch data: OKX API -> SQLite cache (avoid repeated requests)
3. Replay: push K-lines to strategy one by one in time order
4. Simulate matching: strategy orders filled against next bar's OHLC
5. Generate report: equity curve, Sharpe, drawdown, win rate, etc.
```

### Matching Model

- **Limit orders**: filled if bar.low <= price <= bar.high, at the limit price
- **Market orders**: filled at the next bar's open price
- **Slippage**: actual fill price = base price * (1 +/- slippage)
- **Fees**: calculated using OKX actual rates (maker 0.02%, taker 0.05%)
- **No future data**: on_bar only sees current and prior bars; orders match on the next bar

### Backtest Report Metrics

| Metric | Description |
|--------|-------------|
| Total Return | (final equity - initial) / initial |
| Annualized Return | Converted to yearly |
| Sharpe Ratio | Risk-adjusted return |
| Max Drawdown | Peak-to-trough max loss |
| Win Rate | Profitable trades / total trades |
| Profit Factor | Avg profit / avg loss |
| Trade Count | Total filled orders |

Web UI provides interactive equity curve, per-trade details, and position timeline.

---

## 5. Market Data

### Data Sources

- **Live/Demo**: OKX WebSocket subscriptions for real-time data
  - Public channels: tickers, candles, order book, trades
  - Private channels: orders, fills, positions, account balance
  - ccxt `watch_*` methods handle protocol details
- **Backtest**: Historical K-lines fetched from OKX REST API, cached in SQLite

### OKX WebSocket Limits

- 30 connections per channel per sub-account
- 480 subscribe/unsubscribe per hour per connection
- 30s idle timeout; send ping every <30s for keep-alive

### WebSocket Reconnection

- On disconnect, auto-reconnect with exponential backoff (1s, 2s, 4s, max 30s)
- After reconnection, resubscribe to all previously active channels
- On private channel reconnect, re-login before subscribing
- If reconnection fails after 5 consecutive attempts, pause all strategies and send Telegram alert
- Sequence numbers are tracked; missed messages during disconnect are fetched via REST API snapshot

### Data Caching

Historical K-lines fetched via OKX API are stored in SQLite to avoid redundant API calls. Cache is keyed by (symbol, timeframe, start_time, end_time). SQLite uses WAL mode to support concurrent reads from the Web API while the engine writes.

---

## 6. Risk Management

### Three-Layer Risk Control

```
┌─────────────────────────────────┐
│    Global Risk (Account-level)  │
│  Max daily loss 5% -> all pause │
│  Total position <= 80% equity   │
├─────────────────────────────────┤
│    Strategy Risk (Per-strategy) │
│  Max position 10% of equity     │
│  Max drawdown 15% -> pause      │
├─────────────────────────────────┤
│    Order Risk (Per-order)       │
│  Stop-loss/take-profit required  │
│  Single order <= 20% of strat   │
└─────────────────────────────────┘
```

### Risk Check Flow

```
Strategy submits order
        │
        ▼
  OrderManager
        │
        ▼
  RiskManager.check(order)
        │
        ├── Pass -> send to exchange
        │
        └── Reject -> log + Telegram alert
```

### Configuration

```yaml
# Global risk
risk:
  max_daily_loss_pct: 0.05
  max_drawdown_pct: 0.15
  max_total_position_pct: 0.8

# Per-strategy override
strategies:
  ma_cross:
    risk:
      max_position_pct: 0.1
      max_drawdown_pct: 0.10
```

### Circuit Breakers

- **Daily loss breaker**: Daily loss hits threshold -> pause all strategies, Telegram alert
- **Drawdown breaker**: Account drawdown hits threshold -> pause, manual resume via Web UI
- **Recovery**: Manual unlock via web console, or configure auto-recovery next day

### Futures/Swap-Specific Risk

- **Leverage management**: Configured per strategy, set via API before orders
- **Margin monitoring**: Subscribe to account balance via WebSocket, alert when margin ratio < threshold
- **Liquidation warning**: Alert at 150% margin ratio, auto-reduce position at 120%

---

## 7. Web Console

### Page Layout

```
┌──────────────────────────────────────────────┐
│  OKX Bot                        Settings     │
├──────────┬───────────────────────────────────┤
│ Sidebar  │  Page Content                     │
│          │                                   │
│ Dashboard│                                   │
│ Strategies                                   │
│ Backtest │                                   │
│ Market   │                                   │
│ Trades   │                                   │
└──────────┴───────────────────────────────────┘
```

### Pages

**Dashboard**
- Total equity, daily PnL, position summary
- Per-strategy status (running / paused / circuit-breaker)
- Real-time equity curve (WebSocket push)
- Recent alerts and notifications

**Strategy Management**
- Form mode: select indicators -> set parameters -> define buy/sell conditions -> allocate capital -> launch
- Code mode: Monaco Editor for Python strategy class with syntax highlighting and autocomplete
- Strategy list: view all, start/stop, modify parameters, view per-strategy PnL

**Backtest Center**
- Select strategy + time range + initial capital -> run
- Results: equity curve chart, metrics table, per-trade list
- Comparison: side-by-side backtest results for different parameters

**Market Data**
- K-line chart (ECharts) with multi-timeframe switching
- Overlaid technical indicators (MA, RSI, MACD, Bollinger Bands)
- Trading pair list with real-time prices

**Trade History**
- All order history (filter by strategy, time, symbol)
- Current position details
- Fund flow records

### Real-time Communication

- FastAPI WebSocket pushes: market data, order status, position changes, alerts
- Frontend Pinia store receives and updates UI reactively

---

## 8. Notification System

### Telegram Bot (via Hermes)

Trigger scenarios:
- Open/close position, stop-loss, take-profit
- Order rejected
- Risk alerts (daily loss, drawdown, insufficient margin)
- Strategy auto-pause (circuit breaker)
- System errors (WebSocket disconnect, API errors)
- Daily summary (PnL, positions)

### Message Format

```
🔔 Position Opened
Strategy: MA_Cross
Symbol: BTC-USDT-SWAP
Side: Long
Amount: 0.1 BTC
Price: 50,000 USDT
Stop-Loss: 49,000 USDT
Time: 2026-05-27 14:30:00
```

---

## 9. Configuration

### Main Config (`config/settings.yaml`)

```yaml
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

strategies: []
```

### Strategy Config (YAML for form-based strategies)

Form-based strategies are defined in YAML with indicator references and condition expressions. The strategy engine parses these into executable logic without writing Python code.

```yaml
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
  rsi: { type: RSI, period: 14 }
conditions:
  buy:
    - "fast_ma > slow_ma"              # golden cross
    - "rsi < 70"                        # not overbought
  sell:
    - "fast_ma < slow_ma"              # death cross
    - "rsi > 30"                        # not oversold
```

**Condition DSL rules:**
- Left/right operands: indicator names, numeric literals, or `close`/`open`/`high`/`low`/`volume`
- Operators: `>`, `<`, `>=`, `<=`, `==`, `crosses_above`, `crosses_below`
- Multiple conditions in a list are AND-ed
- `{{ param }}` syntax references strategy params

Environment variables are supported via `${VAR}` syntax in config files.

---

## 10. Lifecycle

### Startup

```
Load config -> Init exchange connection -> Start WebSocket market data
     -> Load strategies -> Start risk manager -> Start web server -> Ready
```

### Shutdown

```
Stop all strategies -> Wait for pending orders -> Close WebSocket
     -> Save state -> Graceful exit
```

---

## 11. Multi-Strategy

- Each strategy runs independently with its own capital allocation, risk limits, and PnL tracking
- Strategy execution is isolated: one strategy's error does not affect others
- Capital allocation is configured per strategy as a percentage of total equity
- Strategies can be started/stopped independently via Web UI or API
