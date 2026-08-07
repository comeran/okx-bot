# Strategy Runtime Hardening Implementation Plan

> **For agentic workers:** This plan is complete. Do not re-execute checked steps or infer authorization for excluded work; create a separate plan for any newly requested strategy changes.

**Goal:** Add explicit all-built-in acceptance evidence for persisted strategy instantiation, lifecycle, status synchronization, and shared safety contracts without changing working production behavior.

**Architecture:** Treat the existing CRUD, registry, API, order-manager, and engine implementation as the baseline. Add parameterized contract tests for all four built-ins, using the real `BotEngine` with hermetic local dependencies; modify shared production paths only if a focused test exposes a genuine divergence.

**Tech Stack:** Python 3.12+, FastAPI, SQLModel, pytest, pytest-asyncio, httpx ASGI transport, uv, Ruff.

**Status:** Completed on 2026-08-07.

## Completion Record

- All four built-ins now have registry, configuration API, real-engine lifecycle, disabled-start, and shared mutation-safety acceptance coverage.
- A focused regression exposed that an edit could replace a persisted configuration's `strategy_type`; the shared update path in `src/web/api/strategies.py` now rejects that change with the standard HTTP 422 validation envelope before repository mutation or WebSocket broadcast.
- Verification completed with the strategy-type regression (`2 passed`), the combined six-contract matrix (`18 passed`), the focused backend suite (`233 passed, 3 warnings`), and the full backend suite (`456 passed, 3 warnings`). The three warnings were non-failing environment/deprecation cleanup warnings.
- Ruff reported `All checks passed!`, `git diff --check` was clean, and the final independent implementation review returned `APPROVED`.
- No external OKX call, real-money action, commit, push, destructive Git operation, secret exposure, or unrelated-file cleanup was performed.

---

## Baseline, Scope, and Locked Constraints

Strategy CRUD is complete and already browser-verified. The repository also already has a generic persisted execution path: `src/web/api/strategies.py` loads a persisted record, calls `StrategyRegistry.create_instance(...)`, injects a `UnifiedOrderManager`, constructs a real `BotEngine`, and uses the same start, stop, status, and error reconciliation for every registered built-in.

The registered built-ins covered by this plan are:

- `ma_cross`
- `rsi_mean_reversion`
- `bollinger_mean_reversion`
- `donchian_breakout`

`ma_cross` must remain the only implicit legacy runtime strategy. The other three built-ins must remain persisted-only instances created through the registry. Do not add built-in-specific lifecycle branches.

This plan is contract hardening, not a runtime rewrite. The first focused test run may pass immediately. If it does, production code must remain unchanged. If a failure is caused by a fixture, assertion, or test assumption, correct the test. Modify production code only when a focused test demonstrates a genuine shared-path divergence, and then make the smallest change in the shared registry, API, engine, or order-manager path.

The following requirements are mandatory:

- Do not commit or push unless the user explicitly asks.
- No automated test may make an external OKX call.
- Manual OKX demo/private API smoke is separately gated and must not be executed without explicit user authorization.
- Real-money enablement is outside the current roadmap’s executable scope and requires explicit user authorization plus a separate plan.
- API credentials, secrets, passphrases, Telegram tokens, `.env` values, and local secret settings must not be exposed.
- `/api/settings` credentials must remain masked.
- Real-money work must not be silently merged into any milestone.
- Do not reset, discard, overwrite, or clean existing unrelated dirty files.
- Do not use destructive Git commands.
- Automatic checkpoint progression does not waive authorization requirements for commits, pushes, external private API calls, real-money work, or shared/external actions.

The hermetic lifecycle tests must use this configuration:

```python
AppConfig(
    mode="paper",
    exchange=ExchangeConfig(demo=True),
    risk=RiskConfig(
        allow_live_open_orders=False,
        live_max_order_notional=0.0,
    ),
)
```

The plan explicitly excludes frontend CRUD changes, Dashboard work, accounting expansion, deployment, remote access, migration work, the legacy YAML strategy DSL, startup auto-start, failure auto-restart, external OKX smoke, and real-money enablement.

## File Map

- Modify `tests/unit/test_strategy_registry.py:1-11,401-416`
  - Import all concrete built-in classes.
  - Replace the MA-only `create_instance` test with a parameterized all-built-in normalization and identity contract.
- Modify `tests/integration/test_web_api.py:1-35,584-667,800-982,1688-1714`
  - Add the reusable built-in case matrix.
  - Add the local fake market-data service and hermetic router fixture.
  - Add configuration API, real-engine lifecycle, and disabled-start acceptance tests.
  - Strengthen exact lifecycle-safety error assertions.
  - Rename the misleading unsupported-type test.
- Conditionally modify one shared production path only if focused evidence requires it:
  - `src/strategy/registry.py:163-183`
  - `src/web/api/strategies.py:300-341,523-629,910-1016`
  - `src/core/engine.py:14-149`
  - `src/order/manager.py`

No production file is scheduled for an unconditional change.

### Task 1: Parameterize Registry Instance Contracts Across All Built-ins

**Files:**
- Modify: `tests/unit/test_strategy_registry.py:1-11,401-416`
- Test: `tests/unit/test_strategy_registry.py`

- [x] **Step 1: Import the three concrete classes not currently imported**

Keep the existing MA import and add:

```python
from src.strategy.builtin.bollinger_mean_reversion import (
    BollingerMeanReversionStrategy,
)
from src.strategy.builtin.donchian_breakout import (
    DonchianBreakoutStrategy,
)
from src.strategy.builtin.rsi_mean_reversion import (
    RSIMeanReversionStrategy,
)
```

- [x] **Step 2: Replace the MA-only test with the all-built-in matrix**

Replace `test_create_instance_uses_normalized_values_and_assigns_identity` with:

```python
@pytest.mark.parametrize(
    (
        "strategy_type",
        "strategy_class",
        "params",
        "expected_params",
        "expected_types",
    ),
    [
        (
            "ma_cross",
            MACrossStrategy,
            {
                "fast_window": 3.0,
                "slow_window": 4.0,
                "amount": 0.25,
            },
            {
                "fast_window": 3,
                "slow_window": 4,
                "amount": 0.25,
            },
            {
                "fast_window": int,
                "slow_window": int,
                "amount": float,
            },
        ),
        (
            "rsi_mean_reversion",
            RSIMeanReversionStrategy,
            {
                "period": 14.0,
                "oversold": 28,
                "overbought": 72.5,
                "amount": 0.15,
            },
            {
                "period": 14,
                "oversold": 28.0,
                "overbought": 72.5,
                "amount": 0.15,
            },
            {
                "period": int,
                "oversold": float,
                "overbought": float,
                "amount": float,
            },
        ),
        (
            "bollinger_mean_reversion",
            BollingerMeanReversionStrategy,
            {
                "window": 21.0,
                "stddev_multiplier": 2,
                "amount": 0.2,
            },
            {
                "window": 21,
                "stddev_multiplier": 2.0,
                "amount": 0.2,
            },
            {
                "window": int,
                "stddev_multiplier": float,
                "amount": float,
            },
        ),
        (
            "donchian_breakout",
            DonchianBreakoutStrategy,
            {
                "entry_window": 20.0,
                "exit_window": 9.0,
                "amount": 0.3,
            },
            {
                "entry_window": 20,
                "exit_window": 9,
                "amount": 0.3,
            },
            {
                "entry_window": int,
                "exit_window": int,
                "amount": float,
            },
        ),
    ],
)
def test_create_instance_supports_all_builtin_strategy_types_with_normalized_identity(
    strategy_type,
    strategy_class,
    params,
    expected_params,
    expected_types,
) -> None:
    registry = make_registry()

    strategy = registry.create_instance(
        name=f"  persisted_{strategy_type}  ",
        strategy_type=strategy_type,
        symbol="  ETH-USDT  ",
        timeframe="  15m  ",
        params=params,
    )

    assert isinstance(strategy, strategy_class)
    assert strategy.name == f"persisted_{strategy_type}"
    assert strategy.symbol == "ETH-USDT"
    assert strategy.timeframe == "15m"
    for field, expected_value in expected_params.items():
        assert getattr(strategy, field) == expected_value
        assert type(getattr(strategy, field)) is expected_types[field]
    assert registry.list_implicit_strategies() == ["ma_cross"]
```

Use exact `type(...) is ...` assertions so boolean or integer subclasses cannot accidentally satisfy the numeric contract.

- [x] **Step 3: Run the registry contract**

Run:

```bash
uv run --group dev pytest \
  "tests/unit/test_strategy_registry.py::test_create_instance_supports_all_builtin_strategy_types_with_normalized_identity" \
  -q
```

Expected: four parameter cases pass. A passing first run is valid characterization evidence and requires no production change.

### Task 2: Add the Shared Integration Matrix and Hermetic Runtime Fixture

**Files:**
- Modify: `tests/integration/test_web_api.py:1-35,584-667`
- Test: `tests/integration/test_web_api.py`

- [x] **Step 1: Add imports for concrete runtime assertions**

Add:

```python
from src.core.engine import BotEngine
from src.order.manager import UnifiedOrderManager
from src.strategy.builtin.bollinger_mean_reversion import (
    BollingerMeanReversionStrategy,
)
from src.strategy.builtin.donchian_breakout import (
    DonchianBreakoutStrategy,
)
from src.strategy.builtin.ma_cross import MACrossStrategy
from src.strategy.builtin.rsi_mean_reversion import (
    RSIMeanReversionStrategy,
)
```

Retain the existing `AppConfig`, `ExchangeConfig`, `RiskConfig`, `SimpleNamespace`, `yaml`, `FastAPI`, `ASGITransport`, and `AsyncClient` imports.

- [x] **Step 2: Define one reusable four-strategy case matrix**

Place this near `strategy_config_payload`:

```python
PERSISTED_BUILTIN_STRATEGY_CASES = [
    pytest.param(
        {
            "name": "persisted_ma_cross",
            "strategy_type": "ma_cross",
            "strategy_class": MACrossStrategy,
            "symbol": "ETH-USDT",
            "timeframe": "15m",
            "params": {
                "fast_window": 3.0,
                "slow_window": 4.0,
                "amount": 0.25,
            },
            "expected_params": {
                "fast_window": 3,
                "slow_window": 4,
                "amount": 0.25,
            },
        },
        id="ma-cross",
    ),
    pytest.param(
        {
            "name": "persisted_rsi_mean_reversion",
            "strategy_type": "rsi_mean_reversion",
            "strategy_class": RSIMeanReversionStrategy,
            "symbol": "ETH-USDT",
            "timeframe": "15m",
            "params": {
                "period": 14.0,
                "oversold": 28,
                "overbought": 72.5,
                "amount": 0.15,
            },
            "expected_params": {
                "period": 14,
                "oversold": 28.0,
                "overbought": 72.5,
                "amount": 0.15,
            },
        },
        id="rsi-mean-reversion",
    ),
    pytest.param(
        {
            "name": "persisted_bollinger_mean_reversion",
            "strategy_type": "bollinger_mean_reversion",
            "strategy_class": BollingerMeanReversionStrategy,
            "symbol": "ETH-USDT",
            "timeframe": "15m",
            "params": {
                "window": 21.0,
                "stddev_multiplier": 2,
                "amount": 0.2,
            },
            "expected_params": {
                "window": 21,
                "stddev_multiplier": 2.0,
                "amount": 0.2,
            },
        },
        id="bollinger-mean-reversion",
    ),
    pytest.param(
        {
            "name": "persisted_donchian_breakout",
            "strategy_type": "donchian_breakout",
            "strategy_class": DonchianBreakoutStrategy,
            "symbol": "ETH-USDT",
            "timeframe": "15m",
            "params": {
                "entry_window": 20.0,
                "exit_window": 9.0,
                "amount": 0.3,
            },
            "expected_params": {
                "entry_window": 20,
                "exit_window": 9,
                "amount": 0.3,
            },
        },
        id="donchian-breakout",
    ),
]
```

Each test must copy nested dictionaries before adding `enabled` so parameter cases cannot mutate one another.

- [x] **Step 3: Add a local market-data service with observable lifecycle state**

Place this after `IsolatedStrategyConfigRepository`:

```python
class FakeMarketDataService:
    def __init__(self):
        self.subscriptions = []
        self.unsubscriptions = []
        self.start_calls = 0
        self.stop_calls = 0
        self._running = False

    def subscribe(
        self,
        symbol,
        timeframe,
        callback,
    ):
        self.subscriptions.append(
            (
                symbol,
                timeframe,
                callback,
            )
        )

    def unsubscribe(
        self,
        symbol,
        timeframe,
        callback,
    ):
        self.unsubscriptions.append(
            (
                symbol,
                timeframe,
                callback,
            )
        )

    def get_recent_bars(
        self,
        symbol,
        timeframe,
        count=1,
    ):
        return []

    async def start(self):
        self.start_calls += 1
        self._running = True

    async def stop(self):
        self.stop_calls += 1
        self._running = False
```

This fake must not create sockets, tasks outside the engine, credentials, or exchange adapters.

- [x] **Step 4: Add `hermetic_persisted_builtin_strategy_api`**

Patch every external-facing dependency before `create_router(...)`, because the router captures its repository and notifier immediately:

```python
@pytest.fixture
def hermetic_persisted_builtin_strategy_api(monkeypatch):
    IsolatedStrategyConfigRepository.configs = {}
    IsolatedStrategyConfigRepository.positions = {}
    IsolatedStrategyConfigRepository.fail_next_create = False
    IsolatedStrategyConfigRepository.fail_next_update = False
    IsolatedStrategyConfigRepository.fail_next_delete = False

    safe_settings = AppConfig(
        mode="paper",
        exchange=ExchangeConfig(demo=True),
        risk=RiskConfig(
            allow_live_open_orders=False,
            live_max_order_notional=0.0,
        ),
    )
    market_data = FakeMarketDataService()
    notifier_factory_calls = []

    def create_noop_risk_event_notifier():
        notifier_factory_calls.append(True)
        return None

    def fail_live_order_handler(settings):
        raise AssertionError(
            "Live order handler must not be created"
        )

    async def fail_live_sync(*args, **kwargs):
        raise AssertionError(
            "Live state synchronization must not run"
        )

    monkeypatch.setattr(
        strategy_api,
        "Repository",
        IsolatedStrategyConfigRepository,
        raising=False,
    )
    monkeypatch.setattr(
        strategy_api,
        "load_runtime_settings",
        lambda: safe_settings,
    )
    monkeypatch.setattr(
        strategy_api,
        "create_market_data_service",
        lambda: market_data,
    )
    monkeypatch.setattr(
        strategy_api,
        "create_risk_event_notifier",
        create_noop_risk_event_notifier,
    )
    monkeypatch.setattr(
        strategy_api,
        "create_live_order_handler",
        fail_live_order_handler,
    )
    monkeypatch.setattr(
        strategy_api,
        "refresh_okx_live_state",
        fail_live_sync,
    )
    monkeypatch.setattr(
        strategy_api,
        "current_timestamp_ms",
        lambda: 1700000000000,
    )

    messages = []

    async def broadcast(message):
        messages.append(message)

    runtime = strategy_api.StrategyRuntimeState()
    app = FastAPI()
    app.include_router(
        strategy_api.create_router(
            broadcast=broadcast,
            runtime=runtime,
        ),
        prefix="/api/strategies",
    )
    return SimpleNamespace(
        app=app,
        runtime=runtime,
        messages=messages,
        market_data=market_data,
        notifier_factory_calls=notifier_factory_calls,
    )
```

The fixture must retain the real `BotEngine`, `UnifiedOrderManager`, `OrderRouter`, risk manager, and repository-backed paper handler. Only external-facing market data, notifier construction, live adapter creation, and live-state synchronization are replaced.

The router constructed inside this fixture captures the patched no-op notifier factory, so requests sent through `environment.app` remain local and hermetic. This fixture does not prove that notifier-related settings were never read earlier when `src.web.api.strategies` created its module-level router during import. Do not refactor production merely to make that stronger import-time claim unless a focused hermeticity test demonstrates a genuine shared-path defect.

- [x] **Step 5: Run the existing isolated CRUD and lifecycle tests after introducing the fixture**

Run:

```bash
uv run --group dev pytest \
  "tests/integration/test_web_api.py::test_strategy_config_lifecycle_safety_isolated_from_real_runtime" \
  -q
```

Expected: the existing test still passes. The new fixture must not change the behavior of `isolated_strategy_config_api`.

### Task 3: Accept Every Registered Built-in Through the Configuration API

**Files:**
- Modify: `tests/integration/test_web_api.py`
- Test: `tests/integration/test_web_api.py`

- [x] **Step 1: Add the parameterized configuration API acceptance test**

Add:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    PERSISTED_BUILTIN_STRATEGY_CASES,
)
async def test_strategy_config_api_accepts_all_registered_builtin_types(
    hermetic_persisted_builtin_strategy_api,
    case,
):
    environment = hermetic_persisted_builtin_strategy_api
    payload = {
        "name": case["name"],
        "strategy_type": case["strategy_type"],
        "symbol": case["symbol"],
        "timeframe": case["timeframe"],
        "params": dict(case["params"]),
        "enabled": True,
    }
    expected_config = {
        "name": case["name"],
        "strategy_type": case["strategy_type"],
        "symbol": case["symbol"],
        "timeframe": case["timeframe"],
        "params": case["expected_params"],
        "enabled": True,
    }

    transport = ASGITransport(app=environment.app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        validate_response = await client.post(
            "/api/strategies/configs/validate",
            json=payload,
        )
        create_response = await client.post(
            "/api/strategies/configs",
            json=payload,
        )
        fetch_response = await client.get(
            f"/api/strategies/configs/{case['name']}"
        )
        configs_response = await client.get(
            "/api/strategies/configs"
        )
        statuses_response = await client.get(
            "/api/strategies"
        )
        types_response = await client.get(
            "/api/strategies/types"
        )

    assert validate_response.status_code == 200
    assert create_response.status_code == 201
    assert fetch_response.status_code == 200
    assert configs_response.status_code == 200
    assert statuses_response.status_code == 200
    assert types_response.status_code == 200

    validation_result = validate_response.json()
    validated = validation_result["config"]
    validation_yaml = validation_result["yaml"]
    created = create_response.json()
    fetched = fetch_response.json()
    configs = configs_response.json()
    statuses = statuses_response.json()
    strategy_types = types_response.json()

    for field, expected_value in expected_config.items():
        assert validated[field] == expected_value
        assert created[field] == expected_value
        assert fetched[field] == expected_value

    parsed_yaml = yaml.safe_load(validation_yaml)
    assert parsed_yaml == expected_config
    assert any(
        config["name"] == case["name"]
        and all(
            config[field] == expected_value
            for field, expected_value in expected_config.items()
        )
        for config in configs
    )
    assert {
        "name": case["name"],
        "status": "stopped",
    } in statuses
    assert case["strategy_type"] in {
        definition["strategy_type"]
        for definition in strategy_types
    }
```

- [x] **Step 2: Run the configuration API matrix**

Run:

```bash
uv run --group dev pytest \
  "tests/integration/test_web_api.py::test_strategy_config_api_accepts_all_registered_builtin_types" \
  -q
```

Expected: four parameter cases pass; every created configuration is stopped and every type is discoverable through `GET /api/strategies/types` using `strategy_type` rather than `type`.

### Task 4: Prove the Shared Real-Engine Start/Stop Lifecycle

**Files:**
- Modify: `tests/integration/test_web_api.py`
- Test: `tests/integration/test_web_api.py`

- [x] **Step 1: Add the parameterized real-engine lifecycle test**

Add:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    PERSISTED_BUILTIN_STRATEGY_CASES,
)
async def test_persisted_builtin_strategy_configs_share_start_stop_lifecycle(
    hermetic_persisted_builtin_strategy_api,
    case,
):
    environment = hermetic_persisted_builtin_strategy_api
    assert environment.runtime.strategy_status == {
        "ma_cross": "stopped",
    }
    assert environment.runtime.strategy_errors == {}
    assert environment.runtime.engines == {}
    assert environment.runtime.starting_engines == {}
    assert environment.runtime.lifecycle_locks == {}

    payload = {
        "name": case["name"],
        "strategy_type": case["strategy_type"],
        "symbol": case["symbol"],
        "timeframe": case["timeframe"],
        "params": dict(case["params"]),
        "enabled": True,
    }

    transport = ASGITransport(app=environment.app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        create_response = await client.post(
            "/api/strategies/configs",
            json=payload,
        )
        assert create_response.status_code == 201
        assert environment.runtime.strategy_status == {
            "ma_cross": "stopped",
            case["name"]: "stopped",
        }
        assert environment.runtime.strategy_errors == {}
        assert environment.runtime.engines == {}
        assert environment.runtime.starting_engines == {}
        assert set(environment.runtime.lifecycle_locks) == {
            case["name"],
        }
        lifecycle_lock = environment.runtime.lifecycle_locks[case["name"]]
        environment.messages.clear()

        start_response = await client.post(
            f"/api/strategies/{case['name']}/start"
        )

        assert start_response.status_code == 200
        assert start_response.json() == {
            "status": "started",
            "strategy": case["name"],
        }
        assert environment.runtime.strategy_status == {
            "ma_cross": "stopped",
            case["name"]: "running",
        }
        assert environment.runtime.strategy_errors == {}
        assert set(environment.runtime.engines) == {
            case["name"],
        }
        assert environment.runtime.starting_engines == {}
        assert set(environment.runtime.lifecycle_locks) == {
            case["name"],
        }
        assert (
            environment.runtime.lifecycle_locks[case["name"]]
            is lifecycle_lock
        )

        engine = environment.runtime.engines[case["name"]]
        assert isinstance(engine, BotEngine)
        assert len(engine.strategies) == 1
        strategy = engine.strategies[0]
        assert isinstance(strategy, case["strategy_class"])
        assert strategy.name == case["name"]
        assert strategy.symbol == case["symbol"]
        assert strategy.timeframe == case["timeframe"]
        for field, expected_value in case["expected_params"].items():
            assert getattr(strategy, field) == expected_value
        assert isinstance(
            strategy._order_manager,
            UnifiedOrderManager,
        )

        assert len(environment.market_data.subscriptions) == 1
        subscribed_symbol, subscribed_timeframe, subscribed_callback = (
            environment.market_data.subscriptions[0]
        )
        assert subscribed_symbol == case["symbol"]
        assert subscribed_timeframe == case["timeframe"]
        assert environment.market_data.start_calls == 1
        assert environment.messages == [
            {
                "type": "strategy_status",
                "strategy": case["name"],
                "status": "running",
                "timestamp": 1700000000000,
            }
        ]

        stop_response = await client.post(
            f"/api/strategies/{case['name']}/stop"
        )

    assert stop_response.status_code == 200
    assert stop_response.json() == {
        "status": "stopped",
        "strategy": case["name"],
    }
    assert environment.runtime.strategy_status == {
        "ma_cross": "stopped",
        case["name"]: "stopped",
    }
    assert environment.runtime.strategy_errors == {}
    assert environment.runtime.engines == {}
    assert environment.runtime.starting_engines == {}
    assert set(environment.runtime.lifecycle_locks) == {
        case["name"],
    }
    assert (
        environment.runtime.lifecycle_locks[case["name"]]
        is lifecycle_lock
    )
    assert len(environment.market_data.unsubscriptions) == 1
    unsubscribed_symbol, unsubscribed_timeframe, unsubscribed_callback = (
        environment.market_data.unsubscriptions[0]
    )
    assert unsubscribed_symbol == case["symbol"]
    assert unsubscribed_timeframe == case["timeframe"]
    assert unsubscribed_callback is subscribed_callback
    assert environment.market_data.stop_calls == 1
    assert environment.market_data._running is False
    assert environment.messages == [
        {
            "type": "strategy_status",
            "strategy": case["name"],
            "status": "running",
            "timestamp": 1700000000000,
        },
        {
            "type": "strategy_status",
            "strategy": case["name"],
            "status": "stopped",
            "timestamp": 1700000000000,
        },
    ]
    assert not any(
        message["type"] == "strategy_error"
        for message in environment.messages
    )
    assert environment.notifier_factory_calls == [True]
```

The fail-fast live adapter and live synchronization patches are part of the fixture. Reaching either path fails the test immediately. The notifier factory assertion proves that the router constructed inside this fixture captured and called the patched no-op factory; it does not make a claim about the module-level router created earlier during import. The local fake proves that requests through the fixture-created router perform engine subscription, shared market-data startup, callback-preserving unsubscribe, and router-level cleanup without a network call.

Do not feed bars or produce signals. This test is strictly a construction, manager-injection, subscription, status, stop, unsubscribe, and cleanup contract.

- [x] **Step 2: Run the real-engine lifecycle matrix**

Run:

```bash
uv run --group dev pytest \
  "tests/integration/test_web_api.py::test_persisted_builtin_strategy_configs_share_start_stop_lifecycle" \
  -q
```

Expected: four parameter cases pass using the real engine and real paper-mode order manager. Requests through the fixture-created router use the local market-data fake and patched no-op notifier factory; the fail-fast live adapter and private synchronization paths are not reached.

### Task 5: Prove Disabled Persisted Configurations Cannot Reach Runtime Construction

**Files:**
- Modify: `tests/integration/test_web_api.py`
- Test: `tests/integration/test_web_api.py`

- [x] **Step 1: Add the parameterized disabled-start test**

Add:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    PERSISTED_BUILTIN_STRATEGY_CASES,
)
async def test_disabled_persisted_builtin_configs_cannot_start(
    hermetic_persisted_builtin_strategy_api,
    monkeypatch,
    case,
):
    environment = hermetic_persisted_builtin_strategy_api
    payload = {
        "name": case["name"],
        "strategy_type": case["strategy_type"],
        "symbol": case["symbol"],
        "timeframe": case["timeframe"],
        "params": dict(case["params"]),
        "enabled": False,
    }

    transport = ASGITransport(app=environment.app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        create_response = await client.post(
            "/api/strategies/configs",
            json=payload,
        )
        assert create_response.status_code == 201
        environment.messages.clear()

        def fail_runtime_construction(*args, **kwargs):
            raise AssertionError(
                "Disabled strategy must not construct runtime dependencies"
            )

        monkeypatch.setattr(
            environment.runtime.registry,
            "create_instance",
            fail_runtime_construction,
        )
        monkeypatch.setattr(
            strategy_api,
            "Repository",
            fail_runtime_construction,
        )
        monkeypatch.setattr(
            strategy_api,
            "BotEngine",
            fail_runtime_construction,
        )
        monkeypatch.setattr(
            strategy_api,
            "create_order_manager",
            fail_runtime_construction,
        )

        start_response = await client.post(
            f"/api/strategies/{case['name']}/start"
        )

    assert start_response.status_code == 409
    assert (
        start_response.json()["detail"]
        == "Strategy config is disabled"
    )
    assert case["name"] not in environment.runtime.engines
    assert case["name"] not in environment.runtime.starting_engines
    assert environment.runtime.strategy_status[case["name"]] == "stopped"
    assert environment.messages == []
```

Install the tripwires only after configuration creation. Together they prove the disabled guard runs before persisted strategy instantiation, request-time repository construction, order-manager creation, and engine construction. Keep the safe paper settings because `current_order_router_mode()` still executes before the disabled guard.

- [x] **Step 2: Run the disabled-start matrix**

Run:

```bash
uv run --group dev pytest \
  "tests/integration/test_web_api.py::test_disabled_persisted_builtin_configs_cannot_start" \
  -q
```

Expected: four parameter cases return HTTP 409 with the exact detail; the registry, request-time repository, order-manager, and engine tripwires remain untouched; and both runtime engine maps remain empty.

### Task 6: Lock Exact Mutation Safety Responses and Correct Unsupported-Type Naming

**Files:**
- Modify: `tests/integration/test_web_api.py:800-982,1688-1714`
- Test: `tests/integration/test_web_api.py`

- [x] **Step 1: Strengthen the disabled assertion in `test_strategy_config_lifecycle_safety_isolated_from_real_runtime`**

Use:

```python
assert disabled_start_resp.status_code == 409
assert (
    disabled_start_resp.json()["detail"]
    == "Strategy config is disabled"
)
assert created_engines == []
```

- [x] **Step 2: Assert the exact active-strategy response for every active or starting mutation**

Use:

```python
for response in (
    active_update_resp,
    active_disable_resp,
    active_delete_resp,
    starting_update_resp,
    starting_delete_resp,
):
    assert response.status_code == 409
    assert response.json()["detail"] == "Strategy is active"
```

- [x] **Step 3: Assert exact open-position update and disable responses**

Use:

```python
for response in (
    open_update_resp,
    open_disable_resp,
):
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Cannot update strategy config "
        "with open positions"
    )
```

- [x] **Step 4: Assert the exact open-position delete response**

Use:

```python
assert open_delete_resp.status_code == 409
assert open_delete_resp.json()["detail"] == (
    "Cannot delete strategy config "
    "with open positions"
)
```

- [x] **Step 5: Assert the exact active-target response for create and clone**

Use:

```python
for response in (
    create_active_target_resp,
    clone_active_target_resp,
):
    assert response.status_code == 409
    assert response.json()["detail"] == "Strategy is active"
```

Retain the existing assertions that cloning an active source is allowed and every clone is created disabled:

```python
assert active_source_clone_resp.status_code == 201
assert active_source_clone_resp.json()["enabled"] is False
assert clone_resp.status_code == 201
assert clone_resp.json()["enabled"] is False
```

Do not change the edit-name/type immutability, running read-only behavior, or open-position guards in production.

- [x] **Step 6: Rename the misleading unsupported-type test**

Rename:

```python
async def test_strategy_config_api_rejects_non_ma_cross_strategy_type(
    monkeypatch,
):
```

To:

```python
async def test_strategy_config_api_rejects_unsupported_strategy_type(
    monkeypatch,
):
```

Keep the `grid` payload and `unsupported_strategy_type` issue assertion. Registered non-MA built-ins are supported, so the old name describes behavior that does not exist.

- [x] **Step 7: Run the exact safety and unsupported-type tests**

Run:

```bash
uv run --group dev pytest \
  "tests/integration/test_web_api.py::test_strategy_config_lifecycle_safety_isolated_from_real_runtime" \
  "tests/integration/test_web_api.py::test_strategy_config_api_rejects_unsupported_strategy_type" \
  -q
```

Expected: both tests pass with exact response details and unchanged clone behavior.

### Task 7: Apply the Conditional Shared-Path Fix Rule

**Files:**
- Conditionally modify only the shared path demonstrated by a focused failure.
- Test: the exact focused test exposing the divergence.

- [x] **Step 1: Run all new and strengthened contracts together**

Run:

```bash
uv run --group dev pytest \
  "tests/unit/test_strategy_registry.py::test_create_instance_supports_all_builtin_strategy_types_with_normalized_identity" \
  "tests/integration/test_web_api.py::test_strategy_config_api_accepts_all_registered_builtin_types" \
  "tests/integration/test_web_api.py::test_persisted_builtin_strategy_configs_share_start_stop_lifecycle" \
  "tests/integration/test_web_api.py::test_disabled_persisted_builtin_configs_cannot_start" \
  "tests/integration/test_web_api.py::test_strategy_config_lifecycle_safety_isolated_from_real_runtime" \
  "tests/integration/test_web_api.py::test_strategy_config_api_rejects_unsupported_strategy_type" \
  -q
```

Expected: all cases pass against the current shared implementation.

- [x] **Step 2: Classify any failure before editing production code**

Apply this order:

1. If the failure comes from an incorrect response-shape assumption, fixture ordering, shared fake lifecycle, or assertion, align the test with the established contract while preserving the required normalized fields, safety details, and no-network guarantees.
2. If the test exposes malformed test data, correct the case matrix.
3. Only if a valid built-in fails the same contract that another valid built-in satisfies may production code change.

A passing focused run ends this task with no production changes.

- [x] **Step 3: If and only if a genuine runtime divergence exists, make the smallest shared fix**

The allowed change must satisfy all of these rules:

- Change a shared registry, API, engine, or order-manager path rather than branching on `strategy_type`.
- Preserve `ma_cross` as the only implicit strategy.
- Preserve disabled-start, lifecycle locks, status/error events, kill-switch checks, risk gates, live safeguards, and open-position mutation guards.
- Keep the failing matrix case as the regression assertion.
- Do not add startup auto-start, failure auto-restart, schema migration, legacy YAML conversion, external smoke, or frontend work.

After a production fix, rerun the exact failing test, then rerun the complete command from Step 1. Both must pass before continuing.

### Task 8: Run Focused and Full Backend Verification

**Files:**
- Test only; do not modify unrelated files to silence environmental warnings.

- [x] **Step 1: Run the focused strategy regression**

Run:

```bash
uv run --group dev pytest \
  "tests/unit/test_strategy_registry.py" \
  "tests/unit/test_builtin_strategies.py" \
  "tests/unit/test_ma_cross.py" \
  "tests/unit/test_strategy_backtest_smoke.py" \
  "tests/integration/test_web_api.py" \
  -q
```

Expected: all focused strategy and API tests pass. Existing `aiohttp` or event-loop cleanup warnings may be reported separately, but no test failure is acceptable.

- [x] **Step 2: Run the full backend suite**

Run:

```bash
uv run --group dev pytest -q
```

Expected: the full backend suite passes with no regression.

- [x] **Step 3: Run Ruff**

Run:

```bash
uv run --group dev ruff check .
```

Expected:

```text
All checks passed!
```

- [x] **Step 4: Verify the final scope**

Confirm from the final diff that:

- The planned unit and integration tests are present.
- Any production change is backed by a focused failing contract and is in a shared path.
- No built-in-specific lifecycle branch was added.
- No frontend, Dashboard, accounting, deployment, migration, legacy-YAML, external OKX, real-money, startup auto-start, or failure auto-restart work was introduced.
- No secret or local settings value appears in output or changed files.
- No unrelated dirty file was reset, discarded, overwritten, or cleaned.
- No commit or push was performed.
