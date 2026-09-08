import asyncio
import contextlib
from dataclasses import dataclass

import pytest

from src.core.engine import BotEngine
from src.core.types import Bar
from src.market.health import MarketFeedHealth
from src.market.service import MarketDataService
from src.strategy.base import BaseStrategy


@dataclass(frozen=True)
class Health:
    buffered_bars: int


class FakeMarketData:
    def __init__(self, bars=None, on_ready=None):
        self.bars = list(bars or [])
        self.on_ready = on_ready
        self.callbacks = {}
        self.ensure_started_count = 0
        self.stop_count = 0
        self.ready_requests = []
        self.recent_requests = []

    def subscribe(self, symbol, timeframe, callback):
        self.callbacks.setdefault((symbol, timeframe), []).append(callback)

    def unsubscribe(self, symbol, timeframe, callback):
        callbacks = self.callbacks.get((symbol, timeframe), [])
        self.callbacks[(symbol, timeframe)] = [item for item in callbacks if item is not callback]
        if not self.callbacks[(symbol, timeframe)]:
            self.callbacks.pop((symbol, timeframe))

    def ensure_started(self):
        self.ensure_started_count += 1
        return asyncio.create_task(asyncio.sleep(0))

    async def wait_until_ready(self, symbol, timeframe, *, timeout=10, min_bars=1):
        self.ready_requests.append((symbol, timeframe, min_bars))
        if self.on_ready is not None:
            await self.on_ready(self)
        return Health(len(self.bars))

    def get_recent_bars(self, symbol, timeframe, count=100):
        self.recent_requests.append((symbol, timeframe, count))
        return self.bars[-count:]

    async def stop(self):
        self.stop_count += 1

    async def emit(self, bar):
        self.bars.append(bar)
        for callback in list(self.callbacks.get(("BTC-USDT", "1m"), [])):
            await callback(bar)


class StableRuntimeFakeMarketData(FakeMarketData):
    def __init__(self, bars=None, on_ready=None):
        super().__init__(bars, on_ready)
        self.runtime = None

    def ensure_started(self):
        self.ensure_started_count += 1
        self.runtime = asyncio.create_task(asyncio.Event().wait())
        return self.runtime

    async def cancel_runtime(self):
        if self.runtime is not None:
            self.runtime.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.runtime


class WarmupStrategy:
    name = "warmup"
    symbol = "BTC-USDT"
    timeframe = "1m"

    def __init__(self, required=2):
        self.required = required
        self.warmed = []
        self.live = []
        self.shutdown_count = 0

    def required_warmup_bars(self):
        return self.required

    async def on_init(self):
        pass

    async def warmup(self, bars):
        self.warmed.extend(bar.timestamp for bar in bars)

    async def on_bar(self, bar):
        self.live.append(bar.timestamp)

    async def on_shutdown(self):
        self.shutdown_count += 1


class LegacyStrategy:
    name = "legacy"
    symbol = "BTC-USDT"
    timeframe = "1m"

    def __init__(self):
        self.live = []
        self.shutdown_count = 0

    async def on_init(self):
        pass

    async def on_bar(self, pending_bar):
        self.live.append(pending_bar.timestamp)

    async def on_shutdown(self):
        self.shutdown_count += 1


class BlockingWarmupStrategy(WarmupStrategy):
    def __init__(self, required=2):
        super().__init__(required)
        self.warmup_started = asyncio.Event()
        self.release_warmup = asyncio.Event()
        self.warmup_calls = 0

    async def warmup(self, bars):
        self.warmup_calls += 1
        if self.warmup_calls == 1:
            self.warmup_started.set()
            await self.release_warmup.wait()
        await super().warmup(bars)


class CatchUpBlockingWarmupStrategy(WarmupStrategy):
    def __init__(self, required=2):
        super().__init__(required)
        self.catch_up_started = asyncio.Event()
        self.release_catch_up = asyncio.Event()
        self.warmup_calls = 0

    async def warmup(self, bars):
        self.warmup_calls += 1
        if self.warmup_calls == 2:
            self.catch_up_started.set()
            await self.release_catch_up.wait()
        await super().warmup(bars)


class FailingInitStrategy(WarmupStrategy):
    async def on_init(self):
        raise RuntimeError("init failed")


class FailingWarmupStrategy(WarmupStrategy):
    async def warmup(self, bars):
        raise RuntimeError("warmup failed")


class BlockingShutdownStrategy(WarmupStrategy):
    def __init__(self, required=1):
        super().__init__(required)
        self.shutdown_started = asyncio.Event()
        self.release_shutdown = asyncio.Event()

    async def on_shutdown(self):
        self.shutdown_started.set()
        await self.release_shutdown.wait()
        await super().on_shutdown()


def bar(timestamp):
    return Bar(timestamp, 1, 1, 1, 1, 1)


@pytest.mark.asyncio
async def test_engine_waits_for_feed_readiness_and_warmup_before_running():
    service = FakeMarketData([bar(1), bar(2)])
    strategy = WarmupStrategy(required=2)
    engine = BotEngine([strategy], market_data_service=service)

    await engine.start()

    assert engine.running is True
    assert service.ensure_started_count == 1
    assert service.ready_requests == [("BTC-USDT", "1m", 2)]
    assert strategy.warmed == [1, 2]
    assert strategy.live == []


@pytest.mark.asyncio
async def test_engine_serializes_transition_and_deduplicates_canonical_recent_bar():
    service = FakeMarketData([bar(1), bar(2)])
    strategy = WarmupStrategy(required=2)

    async def publish_during_readiness(market_data):
        await market_data.emit(bar(3))

    service.on_ready = publish_during_readiness
    engine = BotEngine([strategy], market_data_service=service)

    await engine.start()
    await service.emit(bar(4))

    assert strategy.warmed == [2, 3]
    assert strategy.live == [4]


@pytest.mark.asyncio
async def test_callback_waiting_for_warmup_is_order_disabled_and_drained_before_running():
    class OrderManager:
        def __init__(self):
            self.submissions = []

        async def submit(self, **order):
            self.submissions.append(order)
            return order

    class OrderingStrategy(BaseStrategy):
        name = "ordering"
        symbol = "BTC-USDT"
        timeframe = "1m"

        def __init__(self):
            super().__init__()
            self.engine = None
            self.seen = []
            self.running_states = []
            self.warmup_started = asyncio.Event()
            self.release_initial_warmup = asyncio.Event()
            self.callback_started = asyncio.Event()
            self.release_callback = asyncio.Event()
            self.warmup_calls = 0

        def required_warmup_bars(self):
            return 2

        async def warmup(self, bars):
            self.warmup_calls += 1
            if self.warmup_calls == 1:
                self.warmup_started.set()
                await self.release_initial_warmup.wait()
            await super().warmup(bars)

        async def on_bar(self, pending_bar):
            self.seen.append(pending_bar.timestamp)
            self.running_states.append(self.engine.running)
            await self.buy(self.symbol, 1)
            if pending_bar.timestamp == 3:
                self.callback_started.set()
                await self.release_callback.wait()

    service = FakeMarketData([bar(1), bar(2)])
    strategy = OrderingStrategy()
    order_manager = OrderManager()
    strategy.set_order_manager(order_manager)
    engine = BotEngine([strategy], market_data_service=service)
    strategy.engine = engine

    start_task = asyncio.create_task(engine.start())
    await strategy.warmup_started.wait()
    emit_task = asyncio.create_task(service.emit(bar(3)))
    await asyncio.sleep(0)
    strategy.release_initial_warmup.set()
    await strategy.callback_started.wait()

    assert engine.running is False
    assert not start_task.done()
    assert order_manager.submissions == []

    strategy.release_callback.set()
    await start_task

    assert emit_task.done()
    await emit_task
    assert strategy.seen == [1, 2, 3]
    assert strategy.running_states == [False, False, False]
    assert order_manager.submissions == []
    assert engine.running is True


@pytest.mark.asyncio
async def test_base_strategy_callback_catch_up_runs_hook_before_order_disabled_warmup():
    events = []
    published = False

    class HookedBaseStrategy(BaseStrategy):
        name = "hooked-base"
        symbol = "BTC-USDT"
        timeframe = "1m"

        def required_warmup_bars(self):
            return 1

        async def warmup(self, bars):
            events.append(("warmup", [pending_bar.timestamp for pending_bar in bars]))
            await super().warmup(bars)

        async def on_bar(self, pending_bar):
            events.append(("on_bar", pending_bar.timestamp, self._orders_enabled))

    async def publish_catch_up(market_data):
        nonlocal published
        if published:
            return
        published = True
        callback = market_data.callbacks[(strategy.symbol, strategy.timeframe)][0]
        await callback(bar(2))

    async def before_strategy_bar(_strategy, pending_bar):
        events.append(("before", pending_bar.timestamp))

    service = FakeMarketData([bar(1)], on_ready=publish_catch_up)
    strategy = HookedBaseStrategy()
    engine = BotEngine(
        [strategy],
        market_data_service=service,
        before_strategy_bar=before_strategy_bar,
    )

    await engine.start()

    assert events == [
        ("before", 1),
        ("warmup", [1]),
        ("on_bar", 1, False),
        ("before", 2),
        ("warmup", [2]),
        ("on_bar", 2, False),
    ]
    assert engine._last_processed_timestamps[strategy.name] == 2


@pytest.mark.asyncio
async def test_historical_hook_deactivation_skips_processing_and_checkpoint():
    events = []
    service = FakeMarketData([bar(1)])
    strategy = WarmupStrategy(required=1)
    engine = None

    async def before_strategy_bar(_strategy, pending_bar):
        events.append(("before", pending_bar.timestamp))
        engine._strategy_phases[strategy.name] = "inactive"

    engine = BotEngine(
        [strategy],
        market_data_service=service,
        before_strategy_bar=before_strategy_bar,
    )

    await engine.start()

    assert events == [("before", 1)]
    assert strategy.warmed == []
    assert strategy.live == []
    assert strategy.name not in engine._last_processed_timestamps


@pytest.mark.asyncio
async def test_live_only_hook_skips_history_and_runs_for_catch_up_and_live_bars():
    events = []
    published = False

    class HookOrderingStrategy(WarmupStrategy):
        async def warmup(self, bars):
            events.append(("warmup", bars[0].timestamp))
            await super().warmup(bars)

        async def on_bar(self, pending_bar):
            events.append(("on_bar", pending_bar.timestamp))
            await super().on_bar(pending_bar)

    async def publish_catch_up(market_data):
        nonlocal published
        if published:
            return
        published = True
        callback = market_data.callbacks[(strategy.symbol, strategy.timeframe)][0]
        await callback(bar(2))

    async def before_strategy_bar(_strategy, pending_bar):
        events.append(("general", pending_bar.timestamp))

    async def before_live_strategy_bar(_strategy, pending_bar):
        events.append(("live_only", pending_bar.timestamp))

    service = FakeMarketData([bar(1)], on_ready=publish_catch_up)
    strategy = HookOrderingStrategy(required=1)
    engine = BotEngine(
        [strategy],
        market_data_service=service,
        before_strategy_bar=before_strategy_bar,
        before_live_strategy_bar=before_live_strategy_bar,
    )

    await engine.start()
    await service.emit(bar(3))

    assert events == [
        ("general", 1),
        ("warmup", 1),
        ("general", 2),
        ("live_only", 2),
        ("warmup", 2),
        ("general", 3),
        ("live_only", 3),
        ("on_bar", 3),
    ]


@pytest.mark.asyncio
async def test_live_only_hook_deactivation_skips_live_processing_and_checkpoint():
    service = FakeMarketData([bar(1)])
    strategy = WarmupStrategy(required=1)
    engine = None

    async def before_live_strategy_bar(_strategy, _pending_bar):
        engine._active_strategies[strategy.name] = False
        engine._strategy_phases[strategy.name] = "inactive"

    engine = BotEngine(
        [strategy],
        market_data_service=service,
        before_live_strategy_bar=before_live_strategy_bar,
    )
    await engine.start()
    await service.emit(bar(2))

    assert strategy.warmed == [1]
    assert strategy.live == []
    assert engine._last_processed_timestamps[strategy.name] == 1


@pytest.mark.asyncio
async def test_callback_catch_up_skips_warmup_when_hook_deactivates_strategy():
    events = []

    class DeactivatedBaseStrategy(BaseStrategy):
        name = "deactivated-base"

        async def warmup(self, bars):
            events.append(("warmup", [pending_bar.timestamp for pending_bar in bars]))
            await super().warmup(bars)

        async def on_bar(self, pending_bar):
            events.append(("on_bar", pending_bar.timestamp))

    strategy = DeactivatedBaseStrategy()
    engine = BotEngine([strategy])
    engine._strategy_phases[strategy.name] = "activating"

    async def before_strategy_bar(_strategy, pending_bar):
        events.append(("before", pending_bar.timestamp))
        engine._strategy_phases[strategy.name] = "inactive"

    engine.before_strategy_bar = before_strategy_bar

    await engine._replay_strategy_bars(
        strategy,
        [bar(2)],
        strategy.warmup,
        live=True,
    )

    assert events == [("before", 2)]
    assert strategy.name not in engine._last_processed_timestamps


@pytest.mark.asyncio
async def test_final_drain_includes_synchronously_registered_callback_before_running():
    class BoundaryEngine(BotEngine):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.drain_count = 0

        async def _drain_bar_callbacks(self):
            await super()._drain_bar_callbacks()
            self.drain_count += 1
            if self.drain_count == 2:
                callback = service.callbacks[(strategy.symbol, strategy.timeframe)][0]
                asyncio.create_task(callback(bar(2)))

    class BoundaryStrategy(BaseStrategy):
        name = "boundary"
        symbol = "BTC-USDT"
        timeframe = "1m"

        def __init__(self):
            super().__init__()
            self.engine = None
            self.seen = []
            self.running_states = []
            self.catch_up_started = asyncio.Event()
            self.release_catch_up = asyncio.Event()

        def required_warmup_bars(self):
            return 1

        async def on_bar(self, pending_bar):
            self.seen.append(pending_bar.timestamp)
            self.running_states.append(self.engine.running)
            await self.buy(self.symbol, 1)
            if pending_bar.timestamp == 2:
                self.catch_up_started.set()
                await self.release_catch_up.wait()

    class OrderManager:
        def __init__(self):
            self.submissions = []

        async def submit(self, **order):
            self.submissions.append(order)
            return order

    service = FakeMarketData([bar(1)])
    strategy = BoundaryStrategy()
    order_manager = OrderManager()
    strategy.set_order_manager(order_manager)
    engine = BoundaryEngine([strategy], market_data_service=service)
    strategy.engine = engine

    start_task = asyncio.create_task(engine.start())
    await strategy.catch_up_started.wait()

    assert engine.running is False
    assert not start_task.done()
    assert order_manager.submissions == []

    strategy.release_catch_up.set()
    await start_task

    assert strategy.seen == [1, 2]
    assert strategy.running_states == [False, False]
    assert order_manager.submissions == []
    assert engine.running is True


@pytest.mark.asyncio
async def test_callback_registered_after_final_drain_is_caught_up_before_running():
    callback_awaitables = []

    class BoundaryEngine(BotEngine):
        async def _finish_strategy_activation(self, strategies):
            await super()._finish_strategy_activation(strategies)
            callback = service.callbacks[(strategy.symbol, strategy.timeframe)][0]
            callback_awaitables.append(callback(bar(2)))

    class BoundaryStrategy(BaseStrategy):
        name = "boundary-after-drain"
        symbol = "BTC-USDT"
        timeframe = "1m"

        def __init__(self):
            super().__init__()
            self.engine = None
            self.seen = []
            self.running_states = []

        def required_warmup_bars(self):
            return 1

        async def on_bar(self, pending_bar):
            self.seen.append(pending_bar.timestamp)
            self.running_states.append(self.engine.running)
            await self.buy(self.symbol, 1)

    class OrderManager:
        def __init__(self):
            self.submissions = []

        async def submit(self, **order):
            self.submissions.append(order)
            return order

    service = FakeMarketData([bar(1)])
    strategy = BoundaryStrategy()
    order_manager = OrderManager()
    strategy.set_order_manager(order_manager)
    engine = BoundaryEngine([strategy], market_data_service=service)
    strategy.engine = engine

    await engine.start()
    await asyncio.gather(*callback_awaitables)

    assert strategy.seen == [1, 2]
    assert strategy.running_states == [False, False]
    assert order_manager.submissions == []
    assert engine.running is True


@pytest.mark.asyncio
async def test_runtime_failure_while_final_activation_lock_is_blocked_rolls_back():
    runtime_error = RuntimeError("feed failed during final activation")

    class FailingRuntimeMarketData(FakeMarketData):
        def __init__(self):
            super().__init__([bar(1)])
            self.fail_runtime = asyncio.Event()
            self.runtime = None

        def ensure_started(self):
            async def run():
                await self.fail_runtime.wait()
                raise runtime_error

            self.runtime = asyncio.create_task(run())
            return self.runtime

    class LockBoundaryEngine(BotEngine):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.lock_held = asyncio.Event()
            self.release_lock = asyncio.Event()
            self.lock_holder = None

        async def _finish_strategy_activation(self, strategies):
            await super()._finish_strategy_activation(strategies)
            lock = self._strategy_locks[strategy.name]

            async def hold_lock():
                async with lock:
                    self.lock_held.set()
                    await self.release_lock.wait()

            self.lock_holder = asyncio.create_task(hold_lock())
            await self.lock_held.wait()

    service = FailingRuntimeMarketData()
    strategy = WarmupStrategy(required=1)
    engine = LockBoundaryEngine([strategy], market_data_service=service)

    start_task = asyncio.create_task(engine.start())
    await engine.lock_held.wait()
    service.fail_runtime.set()
    await asyncio.sleep(0)
    engine.release_lock.set()

    with pytest.raises(RuntimeError) as exc_info:
        await start_task

    assert exc_info.value is runtime_error
    assert engine.running is False
    assert service.callbacks == {}
    assert strategy.shutdown_count == 1
    assert service.stop_count == 0
    assert engine.lock_holder is not None
    await engine.lock_holder


@pytest.mark.asyncio
async def test_legacy_zero_warmup_waits_for_first_callback_without_history_replay():
    class LegacyMarketData:
        def __init__(self):
            self.callbacks = {}
            self.recent_requests = []
            self.runtime = None
            self.stop_count = 0

        def subscribe(self, symbol, timeframe, callback):
            self.callbacks.setdefault((symbol, timeframe), []).append(callback)

        def unsubscribe(self, symbol, timeframe, callback):
            callbacks = self.callbacks.get((symbol, timeframe), [])
            self.callbacks[(symbol, timeframe)] = [
                item for item in callbacks if item is not callback
            ]
            if not self.callbacks[(symbol, timeframe)]:
                self.callbacks.pop((symbol, timeframe))

        def ensure_started(self):
            self.runtime = asyncio.create_task(asyncio.Event().wait())
            return self.runtime

        def get_recent_bars(self, symbol, timeframe, count=100):
            self.recent_requests.append((symbol, timeframe, count))
            raise AssertionError("zero warm-up must not fetch history")

        async def stop(self):
            self.stop_count += 1
            if self.runtime is not None:
                self.runtime.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.runtime

    service = LegacyMarketData()
    strategy = LegacyStrategy()
    engine = BotEngine([strategy], market_data_service=service)

    start_task = asyncio.create_task(engine.start())
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(asyncio.shield(start_task), timeout=0.01)

    assert engine.running is False
    assert not start_task.done()

    callback = service.callbacks[(strategy.symbol, strategy.timeframe)][0]
    await callback(bar(1))
    await start_task
    await callback(bar(1))

    assert engine.running is True
    assert service.recent_requests == []
    assert strategy.live == [1]

    await engine.stop()


@pytest.mark.asyncio
async def test_restart_does_not_replay_unchanged_historical_buffer():
    service = FakeMarketData([bar(1), bar(2)])
    strategy = WarmupStrategy(required=2)
    engine = BotEngine([strategy], market_data_service=service)

    await engine.start()
    await engine.stop()
    await engine.start()

    assert strategy.warmed == [1, 2]
    assert engine._last_processed_timestamps[strategy.name] == 2


@pytest.mark.asyncio
async def test_restart_waits_for_fresh_feed_generation_and_drains_downtime_bar():
    class GenerationHealth:
        def __init__(self, buffered_bars, generation):
            self.symbol = "BTC-USDT"
            self.timeframe = "1m"
            self.buffered_bars = buffered_bars
            self.generation = generation

    class DelayedInitialPollMarketData(FakeMarketData):
        def __init__(self):
            super().__init__([bar(1), bar(2)])
            self.generation = 1
            self.release_initial_poll = asyncio.Event()
            self.initial_poll_started = asyncio.Event()
            self.listeners = []
            self.runtime = None

        def get_feed_health(self, symbol, timeframe):
            return GenerationHealth(len(self.bars), self.generation)

        def add_health_listener(self, listener):
            self.listeners.append(listener)

            def unregister():
                self.listeners.remove(listener)

            return unregister

        def ensure_started(self):
            self.ensure_started_count += 1

            async def run():
                self.initial_poll_started.set()
                await self.release_initial_poll.wait()
                await self.emit(bar(3))
                self.generation += 1
                health = self.get_feed_health("BTC-USDT", "1m")
                for listener in list(self.listeners):
                    listener(health)
                await asyncio.Event().wait()

            self.runtime = asyncio.create_task(run())
            return self.runtime

        async def stop(self):
            self.stop_count += 1
            if self.runtime is not None:
                self.runtime.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.runtime

    class RestartStrategy(WarmupStrategy):
        def __init__(self):
            super().__init__(required=2)
            self.engine = None
            self.running_states = []

        async def warmup(self, bars):
            self.running_states.extend(self.engine.running for _ in bars)
            await super().warmup(bars)

    service = DelayedInitialPollMarketData()
    strategy = RestartStrategy()
    engine = BotEngine([strategy], market_data_service=service)
    strategy.engine = engine
    engine._last_processed_timestamps[strategy.name] = 2

    start_task = asyncio.create_task(engine.start())
    await service.initial_poll_started.wait()

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(asyncio.shield(start_task), timeout=0.01)
    assert engine.running is False

    service.release_initial_poll.set()
    await start_task

    assert strategy.warmed == [3]
    assert strategy.running_states == [False]
    assert engine.running is True

    await engine.stop()


@pytest.mark.asyncio
async def test_restart_synchronizes_after_successful_poll_with_repeated_clock_marker():
    class FakeExchange:
        async def fetch_ohlcv(self, symbol, timeframe):
            return [[3, 1, 1, 1, 1, 1]]

        async def close(self):
            pass

    class FrozenClockMarketData(MarketDataService):
        def _create_exchange(self):
            return FakeExchange()

        def _current_time(self):
            return 100.0

    service = FrozenClockMarketData("", "", "")
    key = service._health_key("BTC-USDT", "1m")
    service._ensure_feed_state(key).extend([bar(1), bar(2)])
    service._last_bar_timestamps[key] = 2
    service._health[key] = MarketFeedHealth(
        key=key,
        symbol="BTC-USDT",
        timeframe="1m",
        status="ready",
        buffered_bars=2,
        consecutive_failures=0,
        total_failures=0,
        last_success_at=100.0,
        last_failure_at=None,
        last_bar_timestamp=2,
        error_code=None,
        public_message=None,
        generation=1,
        success_generation=0,
    )
    strategy = WarmupStrategy(required=2)
    engine = BotEngine([strategy], market_data_service=service)
    engine._last_processed_timestamps[strategy.name] = 2

    await asyncio.wait_for(engine.start(), timeout=0.2)

    assert engine.running is True
    assert strategy.warmed == [3]
    assert service.get_feed_health("BTC-USDT", "1m").last_success_at == 100.0

    await engine.stop()


@pytest.mark.asyncio
async def test_restart_does_not_synchronize_on_failed_generation_after_stale_readiness():
    class HealthSnapshot:
        def __init__(self, buffered_bars, generation, last_success_at):
            self.symbol = "BTC-USDT"
            self.timeframe = "1m"
            self.buffered_bars = buffered_bars
            self.generation = generation
            self.last_success_at = last_success_at

    class FailingInitialPollMarketData(FakeMarketData):
        def __init__(self):
            super().__init__([bar(1), bar(2)])
            self.generation = 1
            self.last_success_at = 100.0
            self.initial_poll_started = asyncio.Event()
            self.failure_published = asyncio.Event()
            self.listeners = []
            self.runtime = None

        def get_feed_health(self, symbol, timeframe):
            return HealthSnapshot(len(self.bars), self.generation, self.last_success_at)

        def add_health_listener(self, listener):
            self.listeners.append(listener)

            def unregister():
                self.listeners.remove(listener)

            return unregister

        def ensure_started(self):
            self.ensure_started_count += 1

            async def run():
                self.initial_poll_started.set()
                self.generation += 1
                health = self.get_feed_health("BTC-USDT", "1m")
                for listener in list(self.listeners):
                    listener(health)
                self.failure_published.set()
                await asyncio.Event().wait()

            self.runtime = asyncio.create_task(run())
            return self.runtime

        async def stop(self):
            self.stop_count += 1
            if self.runtime is not None:
                self.runtime.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.runtime

    service = FailingInitialPollMarketData()
    strategy = WarmupStrategy(required=2)
    engine = BotEngine([strategy], market_data_service=service)
    engine._last_processed_timestamps[strategy.name] = 2

    start_task = asyncio.create_task(engine.start())
    try:
        await service.failure_published.wait()

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(start_task, timeout=0.01)

        assert engine.running is False
        assert service.callbacks == {}
        assert strategy.warmed == []
        assert strategy.shutdown_count == 1
        assert service.stop_count == 0
        assert service.runtime is not None
        assert not service.runtime.done()
        assert not service.runtime.cancelled()
    finally:
        if service.runtime is not None:
            service.runtime.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await service.runtime


@pytest.mark.asyncio
async def test_start_rollback_drains_registered_callback_before_returning_failure():
    startup_error = RuntimeError("startup failed")
    callback_registered = asyncio.Event()
    callback_lock_entered = asyncio.Event()
    release_callback_lock = asyncio.Event()
    release_outer_task = asyncio.Event()
    outer_tasks = []

    class CallbackLock:
        async def __aenter__(self):
            callback_lock_entered.set()
            await release_callback_lock.wait()

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    class NoShutdownStrategy:
        name = "no-shutdown"
        symbol = "BTC-USDT"
        timeframe = "1m"

        async def on_init(self):
            pass

        async def on_bar(self, pending_bar):
            raise AssertionError("rollback callback must remain order-disabled")

    class FailingAfterRegistrationEngine(BotEngine):
        async def _finish_strategy_activation(self, strategies):
            self._strategy_locks[strategy.name] = CallbackLock()
            callback = service.callbacks[(strategy.symbol, strategy.timeframe)][0]
            registered_callback = callback(bar(2))
            callback_registered.set()

            async def await_after_release():
                await release_outer_task.wait()
                await registered_callback

            outer_tasks.append(asyncio.create_task(await_after_release()))
            raise startup_error

    service = FakeMarketData([bar(1)])
    strategy = NoShutdownStrategy()
    engine = FailingAfterRegistrationEngine([strategy], market_data_service=service)

    start_task = asyncio.create_task(engine.start())
    await callback_registered.wait()
    await asyncio.sleep(0)

    assert callback_lock_entered.is_set()
    assert not start_task.done()

    release_callback_lock.set()
    with pytest.raises(RuntimeError) as exc_info:
        await start_task

    assert exc_info.value is startup_error
    assert engine.running is False

    release_outer_task.set()
    await asyncio.gather(*outer_tasks)


@pytest.mark.asyncio
async def test_engine_start_rolls_back_subscriptions_strategies_and_shared_service():
    service = FakeMarketData([bar(1)])
    initialized = WarmupStrategy(required=1)
    failing = FailingInitStrategy(required=1)
    engine = BotEngine([initialized, failing], market_data_service=service)

    with pytest.raises(RuntimeError, match="init failed"):
        await engine.start()

    assert engine.running is False
    assert service.callbacks == {}
    assert initialized.shutdown_count == 1
    assert failing.shutdown_count == 0
    assert service.stop_count == 0


@pytest.mark.asyncio
async def test_zero_warmup_uses_one_bar_for_readiness_without_historical_replay():
    service = FakeMarketData([bar(1)])
    strategy = WarmupStrategy(required=0)
    engine = BotEngine([strategy], market_data_service=service)

    await engine.start()

    assert engine.running is True
    assert service.ready_requests == [("BTC-USDT", "1m", 1)]
    assert service.recent_requests == []
    assert strategy.warmed == []


@pytest.mark.asyncio
async def test_bar_waiting_behind_catch_up_lock_is_warmed_once_before_running():
    service = FakeMarketData([bar(1), bar(2)])
    strategy = CatchUpBlockingWarmupStrategy(required=2)

    async def publish_pending_bar(market_data):
        callbacks = market_data.callbacks[("BTC-USDT", "1m")]
        await callbacks[0](bar(3))

    service.on_ready = publish_pending_bar
    engine = BotEngine([strategy], market_data_service=service)

    start_task = asyncio.create_task(engine.start())
    await strategy.catch_up_started.wait()
    live_emit_task = asyncio.create_task(service.emit(bar(4)))
    await asyncio.sleep(0)
    strategy.release_catch_up.set()
    await asyncio.gather(start_task, live_emit_task)
    await service.emit(bar(4))
    await service.emit(bar(5))

    assert strategy.warmed == [1, 2, 3, 4]
    assert strategy.live == [5]
    assert engine._pending_bars.get(strategy.name) in (None, [])


@pytest.mark.asyncio
async def test_warmup_failure_rolls_back_every_initialized_strategy_and_shared_service():
    service = FakeMarketData([bar(1)])
    healthy = WarmupStrategy(required=1)
    healthy.name = "healthy"
    failing = FailingWarmupStrategy(required=1)
    failing.name = "failing"
    engine = BotEngine([healthy, failing], market_data_service=service)

    with pytest.raises(RuntimeError, match="warmup failed"):
        await engine.start()

    assert engine.running is False
    assert service.callbacks == {}
    assert healthy.shutdown_count == 1
    assert failing.shutdown_count == 1
    assert service.stop_count == 0


@pytest.mark.asyncio
async def test_callback_catch_up_warmup_failure_isolates_to_failing_strategy():
    catch_up_error = RuntimeError("catch-up failed")
    historical_warmup_complete = asyncio.Event()
    b_ready = asyncio.Event()
    readiness_count = 0

    class CatchUpFailingWarmupStrategy(WarmupStrategy):
        async def warmup(self, bars):
            timestamps = [bar.timestamp for bar in bars]
            self.warmed.extend(timestamps)
            if timestamps == [3]:
                raise catch_up_error
            historical_warmup_complete.set()

    async def publish_while_second_strategy_waits(market_data):
        nonlocal readiness_count
        readiness_count += 1
        if readiness_count == 2:
            callback = market_data.callbacks[(failing.symbol, failing.timeframe)][0]
            await callback(bar(3))
            await b_ready.wait()

    service = StableRuntimeFakeMarketData(
        [bar(1), bar(2)], on_ready=publish_while_second_strategy_waits
    )
    failing = CatchUpFailingWarmupStrategy(required=2)
    failing.name = "failing"
    healthy = WarmupStrategy(required=1)
    healthy.name = "healthy"
    errors = []

    async def on_strategy_error(name, error):
        errors.append((name, error))

    engine = BotEngine(
        [failing, healthy],
        market_data_service=service,
        on_strategy_error=on_strategy_error,
    )

    start_task = asyncio.create_task(engine.start())
    try:
        await historical_warmup_complete.wait()
        await asyncio.sleep(0)

        assert failing.warmed == [1, 2]
        assert not start_task.done()

        b_ready.set()
        await start_task

        assert engine.running is True
        assert failing.warmed == [1, 2, 3]
        assert healthy.warmed == [2]
        assert failing.shutdown_count == 1
        assert healthy.shutdown_count == 0
        assert errors == [("failing", catch_up_error)]
        assert engine._active_strategies[failing.name] is False
        assert engine._strategy_phases[failing.name] == "inactive"
        assert failing.name not in engine._market_data_subscriptions
        assert engine._active_strategies[healthy.name] is True
        assert engine._strategy_phases[healthy.name] == "active"
        assert healthy.name in engine._market_data_subscriptions
        assert len(service.callbacks[(healthy.symbol, healthy.timeframe)]) == 1
    finally:
        await service.cancel_runtime()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "shutdown_error",
    [RuntimeError("shutdown failed"), asyncio.CancelledError()],
    ids=["error", "cancellation"],
)
async def test_callback_catch_up_error_is_reported_when_shutdown_fails(shutdown_error):
    catch_up_error = RuntimeError("catch-up failed")
    historical_warmup_complete = asyncio.Event()
    healthy_ready = asyncio.Event()
    readiness_count = 0

    class CatchUpAndShutdownFailingStrategy(WarmupStrategy):
        async def warmup(self, bars):
            timestamps = [pending_bar.timestamp for pending_bar in bars]
            self.warmed.extend(timestamps)
            if timestamps == [3]:
                raise catch_up_error
            historical_warmup_complete.set()

        async def on_shutdown(self):
            self.shutdown_count += 1
            raise shutdown_error

    async def publish_while_healthy_waits(market_data):
        nonlocal readiness_count
        readiness_count += 1
        if readiness_count == 2:
            callback = market_data.callbacks[(failing.symbol, failing.timeframe)][0]
            await callback(bar(3))
            await healthy_ready.wait()

    service = StableRuntimeFakeMarketData(
        [bar(1), bar(2)],
        on_ready=publish_while_healthy_waits,
    )
    failing = CatchUpAndShutdownFailingStrategy(required=2)
    failing.name = "failing"
    healthy = WarmupStrategy(required=1)
    healthy.name = "healthy"
    errors = []

    async def on_strategy_error(name, error):
        errors.append((name, error))

    engine = BotEngine(
        [failing, healthy],
        market_data_service=service,
        on_strategy_error=on_strategy_error,
    )

    start_task = asyncio.create_task(engine.start())
    try:
        await historical_warmup_complete.wait()
        await asyncio.sleep(0)
        healthy_ready.set()
        await start_task

        assert errors == [(failing.name, catch_up_error)]
        assert engine.running is True
        assert failing.shutdown_count == 1
        assert engine._active_strategies[failing.name] is False
        assert engine._strategy_phases[failing.name] == "inactive"
        assert failing.name not in engine._market_data_subscriptions
        assert engine._active_strategies[healthy.name] is True
        assert engine._strategy_phases[healthy.name] == "active"
        assert healthy.name in engine._market_data_subscriptions
        assert len(service.callbacks[(healthy.symbol, healthy.timeframe)]) == 1
    finally:
        await service.cancel_runtime()


@pytest.mark.asyncio
async def test_two_strategies_share_one_feed_runtime_with_exact_individual_replay():
    service = FakeMarketData([bar(1), bar(2), bar(3)])
    first = WarmupStrategy(required=2)
    first.name = "first"
    second = WarmupStrategy(required=1)
    second.name = "second"
    engine = BotEngine([first, second], market_data_service=service)

    await engine.start()
    await service.emit(bar(3))
    await service.emit(bar(4))

    assert service.ensure_started_count == 1
    assert service.ready_requests == [
        ("BTC-USDT", "1m", 2),
        ("BTC-USDT", "1m", 1),
    ]
    assert service.recent_requests == [
        ("BTC-USDT", "1m", 2),
        ("BTC-USDT", "1m", 1),
    ]
    assert first.warmed == [2, 3]
    assert second.warmed == [3]
    assert first.live == [4]
    assert second.live == [4]


@pytest.mark.asyncio
async def test_cancelled_start_finishes_rollback_despite_second_cancellation():
    readiness_started = asyncio.Event()

    async def block_readiness(_market_data):
        readiness_started.set()
        await asyncio.Event().wait()

    service = StableRuntimeFakeMarketData([bar(1)], on_ready=block_readiness)
    earlier = WarmupStrategy(required=1)
    earlier.name = "earlier"
    blocking = BlockingShutdownStrategy(required=1)
    blocking.name = "blocking"
    engine = BotEngine([earlier, blocking], market_data_service=service)

    start_task = asyncio.create_task(engine.start())
    try:
        await readiness_started.wait()
        start_task.cancel()
        await blocking.shutdown_started.wait()
        start_task.cancel()
        blocking.release_shutdown.set()

        with pytest.raises(asyncio.CancelledError):
            await start_task

        assert engine.running is False
        assert service.callbacks == {}
        assert earlier.shutdown_count == 1
        assert blocking.shutdown_count == 1
        assert service.stop_count == 0
        assert service.runtime is not None
        assert not service.runtime.done()
        assert not service.runtime.cancelled()
    finally:
        await service.cancel_runtime()


@pytest.mark.asyncio
async def test_startup_failure_preserves_primary_when_health_listener_cleanup_fails():
    startup_error = RuntimeError("warmup failed")
    listener_cleanup_error = RuntimeError("listener cleanup failed")

    class ListenerCleanupFailureMarketData(FakeMarketData):
        def __init__(self):
            super().__init__([bar(1)])
            self.listeners = []

        def get_feed_health(self, symbol, timeframe):
            return type(
                "HealthSnapshot",
                (),
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "buffered_bars": len(self.bars),
                    "generation": 0,
                },
            )()

        def add_health_listener(self, listener):
            self.listeners.append(listener)

            def unregister():
                self.listeners.remove(listener)
                raise listener_cleanup_error

            return unregister

        def ensure_started(self):
            self.ensure_started_count += 1
            health = self.get_feed_health("BTC-USDT", "1m")
            health.generation = 1
            for listener in list(self.listeners):
                listener(health)
            return asyncio.create_task(asyncio.sleep(0))

    class ExactFailingWarmupStrategy(WarmupStrategy):
        async def warmup(self, bars):
            raise startup_error

    service = ListenerCleanupFailureMarketData()
    strategy = ExactFailingWarmupStrategy(required=1)
    engine = BotEngine([strategy], market_data_service=service)

    with pytest.raises(RuntimeError) as exc_info:
        await engine.start()

    assert exc_info.value is startup_error
    assert engine.running is False
    assert service.listeners == []
    assert service.callbacks == {}
    assert strategy.shutdown_count == 1
    assert service.stop_count == 0
    assert engine.cleanup_errors == [listener_cleanup_error]


@pytest.mark.asyncio
async def test_health_listener_cleanup_failure_after_activation_rolls_back_start():
    listener_cleanup_error = RuntimeError("listener cleanup failed")

    class ListenerCleanupFailureMarketData(FakeMarketData):
        def __init__(self):
            super().__init__([bar(1)])
            self.listeners = []

        def get_feed_health(self, symbol, timeframe):
            return type(
                "HealthSnapshot",
                (),
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "buffered_bars": len(self.bars),
                    "generation": 0,
                },
            )()

        def add_health_listener(self, listener):
            self.listeners.append(listener)

            def unregister():
                self.listeners.remove(listener)
                raise listener_cleanup_error

            return unregister

        def ensure_started(self):
            self.ensure_started_count += 1
            health = self.get_feed_health("BTC-USDT", "1m")
            health.generation = 1
            for listener in list(self.listeners):
                listener(health)
            return asyncio.create_task(asyncio.sleep(0))

    service = ListenerCleanupFailureMarketData()
    strategy = WarmupStrategy(required=1)
    engine = BotEngine([strategy], market_data_service=service)

    with pytest.raises(RuntimeError) as exc_info:
        await engine.start()

    assert exc_info.value is listener_cleanup_error
    assert engine.running is False
    assert service.listeners == []
    assert service.callbacks == {}
    assert strategy.shutdown_count == 1
    assert service.stop_count == 0
    assert engine.cleanup_errors == [listener_cleanup_error]


@pytest.mark.asyncio
async def test_cleanup_hook_errors_and_cancellation_do_not_skip_remaining_rollback():
    shutdown_error = RuntimeError("cleanup failed")
    shutdown_cancellation = asyncio.CancelledError()
    market_stop_error = RuntimeError("market stop failed")
    runtime_drain_error = RuntimeError("runtime drain failed")

    class ErrorShutdownStrategy(WarmupStrategy):
        async def on_shutdown(self):
            raise shutdown_error

    class CancelledShutdownStrategy(WarmupStrategy):
        async def on_shutdown(self):
            raise shutdown_cancellation

    class FailingCleanupMarketData(FakeMarketData):
        def __init__(self):
            super().__init__([bar(1)])
            self.ensure_started = None

        async def start(self):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise runtime_drain_error

        async def stop(self):
            self.stop_count += 1
            raise market_stop_error

    service = FailingCleanupMarketData()
    survivor = WarmupStrategy(required=1)
    survivor.name = "survivor"
    erroring = ErrorShutdownStrategy(required=1)
    erroring.name = "erroring"
    cancelled = CancelledShutdownStrategy(required=1)
    cancelled.name = "cancelled"
    failing = FailingWarmupStrategy(required=1)
    failing.name = "failing"
    engine = BotEngine(
        [survivor, erroring, cancelled, failing],
        market_data_service=service,
    )

    with pytest.raises(RuntimeError, match="warmup failed"):
        await engine.start()

    assert survivor.shutdown_count == 1
    assert service.callbacks == {}
    assert service.stop_count == 1
    assert engine.cleanup_errors == [
        shutdown_cancellation,
        shutdown_error,
        market_stop_error,
        runtime_drain_error,
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ["failure", "cancellation"])
async def test_shared_already_running_service_is_not_stopped_on_start_rollback(failure_mode):
    readiness_started = asyncio.Event()

    async def block_readiness(_market_data):
        readiness_started.set()
        await asyncio.Event().wait()

    service = FakeMarketData(
        [bar(1)],
        on_ready=block_readiness if failure_mode == "cancellation" else None,
    )
    strategy = (
        FailingWarmupStrategy(required=1)
        if failure_mode == "failure"
        else WarmupStrategy(required=1)
    )
    engine = BotEngine(
        [strategy],
        market_data_service=service,
        stop_market_data_on_stop=False,
    )

    if failure_mode == "failure":
        with pytest.raises(RuntimeError, match="warmup failed"):
            await engine.start()
    else:
        start_task = asyncio.create_task(engine.start())
        await readiness_started.wait()
        start_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await start_task

    assert service.stop_count == 0
    assert service.callbacks == {}
    assert strategy.shutdown_count == 1


@pytest.mark.asyncio
async def test_immediately_failing_legacy_runtime_rolls_back_startup():
    class FailingLegacyMarketData(FakeMarketData):
        def ensure_started(self):
            raise AttributeError

        async def start(self):
            raise RuntimeError("runtime failed")

    service = FailingLegacyMarketData([bar(1)])
    service.ensure_started = None
    strategy = WarmupStrategy(required=1)
    engine = BotEngine([strategy], market_data_service=service)
    runtime_error = RuntimeError("runtime failed")

    async def fail_start():
        raise runtime_error

    service.start = fail_start
    with pytest.raises(RuntimeError) as exc_info:
        await engine.start()

    assert exc_info.value is runtime_error

    assert engine.running is False
    assert service.callbacks == {}
    assert strategy.shutdown_count == 1
    assert service.stop_count == 1


@pytest.mark.asyncio
async def test_warmup_failure_cancels_owned_pending_legacy_runtime_after_inert_stop():
    warmup_error = RuntimeError("warmup failed")

    class InertStopLegacyMarketData(FakeMarketData):
        def __init__(self):
            super().__init__([bar(1)])
            self.runtime_started = asyncio.Event()

        async def start(self):
            self.runtime_started.set()
            await asyncio.Event().wait()

        async def stop(self):
            self.stop_count += 1

    class ExactFailingWarmupStrategy(WarmupStrategy):
        async def warmup(self, bars):
            raise warmup_error

    service = InertStopLegacyMarketData()
    service.ensure_started = None
    strategy = ExactFailingWarmupStrategy(required=1)
    engine = BotEngine([strategy], market_data_service=service)

    with pytest.raises(RuntimeError) as exc_info:
        await engine.start()

    assert exc_info.value is warmup_error
    assert service.runtime_started.is_set()
    assert service.stop_count == 1
    assert engine._market_data_task is not None
    assert engine._market_data_task.done()
    assert engine._market_data_task.cancelled()


@pytest.mark.asyncio
async def test_readiness_blocked_runtime_failure_rolls_back_startup():
    runtime_error = RuntimeError("feed crashed")

    class RuntimeHandle:
        def __init__(self, task):
            self.task = task

        def __await__(self):
            return asyncio.shield(self.task).__await__()

        def done(self):
            return self.task.done()

        def exception(self):
            return self.task.exception()

    class FailingReadyMarketData(FakeMarketData):
        def __init__(self):
            super().__init__([bar(1)])
            self.readiness_started = asyncio.Event()
            self.fail_runtime = asyncio.Event()

        def ensure_started(self):
            async def run():
                await self.fail_runtime.wait()
                raise runtime_error

            return RuntimeHandle(asyncio.create_task(run()))

        async def wait_until_ready(self, symbol, timeframe, *, timeout=10, min_bars=1):
            self.readiness_started.set()
            await asyncio.Event().wait()

    service = FailingReadyMarketData()
    strategy = WarmupStrategy(required=1)
    engine = BotEngine([strategy], market_data_service=service)

    start_task = asyncio.create_task(engine.start())
    await service.readiness_started.wait()
    service.fail_runtime.set()

    with pytest.raises(RuntimeError) as exc_info:
        await start_task

    assert exc_info.value is runtime_error

    assert engine.running is False
    assert service.callbacks == {}
    assert strategy.shutdown_count == 1
    assert service.stop_count == 0


@pytest.mark.asyncio
async def test_start_rollback_discards_queued_startup_callback_before_shutdown():
    service = StableRuntimeFakeMarketData([bar(1)])
    active = WarmupStrategy(required=1)
    active.name = "active"
    failing = FailingWarmupStrategy(required=1)
    failing.name = "failing"
    callback_tasks = []
    before_calls = []

    async def before_strategy_bar(strategy, pending_bar):
        before_calls.append((strategy.name, pending_bar.timestamp))

    readiness_count = 0

    async def publish_while_starting(_market_data):
        nonlocal readiness_count
        readiness_count += 1
        if readiness_count == 2:
            callback = service.callbacks[(active.symbol, active.timeframe)][0]
            callback_tasks.append(asyncio.create_task(callback(bar(2))))
            await asyncio.sleep(0)

    service.on_ready = publish_while_starting
    engine = BotEngine(
        [active, failing],
        market_data_service=service,
        before_strategy_bar=before_strategy_bar,
    )

    try:
        with pytest.raises(RuntimeError, match="warmup failed"):
            await engine.start()

        assert callback_tasks and callback_tasks[0].done()
        assert before_calls == [("active", 1), ("failing", 1)]
        assert active.live == []
        assert active.shutdown_count == 1
        assert engine.running is False
    finally:
        await service.cancel_runtime()


@pytest.mark.asyncio
async def test_historical_warmup_bars_are_sorted_and_deduplicated():
    service = FakeMarketData([bar(3), bar(1), bar(2), bar(2)])
    strategy = WarmupStrategy(required=4)
    engine = BotEngine([strategy], market_data_service=service)

    await engine.start()

    assert strategy.warmed == [1, 2, 3]
    assert engine._last_processed_timestamps[strategy.name] == 3


@pytest.mark.asyncio
async def test_legacy_pending_bars_are_sorted_and_deduplicated():
    class PublishingLegacyMarketData:
        def __init__(self):
            self.callbacks = {}
            self.stop_count = 0

        def subscribe(self, symbol, timeframe, callback):
            self.callbacks.setdefault((symbol, timeframe), []).append(callback)
            for pending_bar in [bar(3), bar(2), bar(3), bar(1)]:
                asyncio.create_task(callback(pending_bar))

        def unsubscribe(self, symbol, timeframe, callback):
            callbacks = self.callbacks.get((symbol, timeframe), [])
            self.callbacks[(symbol, timeframe)] = [
                item for item in callbacks if item is not callback
            ]
            if not self.callbacks[(symbol, timeframe)]:
                self.callbacks.pop((symbol, timeframe))

        async def start(self):
            return None

        async def stop(self):
            self.stop_count += 1

    service = PublishingLegacyMarketData()
    strategy = LegacyStrategy()
    engine = BotEngine([strategy], market_data_service=service)

    await engine.start()

    assert strategy.live == [1, 2, 3]
    assert engine._last_processed_timestamps[strategy.name] == 3


@pytest.mark.asyncio
async def test_legacy_pending_activation_stops_after_callback_error():
    class PublishingLegacyMarketData:
        def __init__(self):
            self.callbacks = {}

        def subscribe(self, symbol, timeframe, callback):
            self.callbacks.setdefault((symbol, timeframe), []).append(callback)
            for pending_bar in [bar(3), bar(2), bar(1)]:
                asyncio.create_task(callback(pending_bar))

        def unsubscribe(self, symbol, timeframe, callback):
            callbacks = self.callbacks.get((symbol, timeframe), [])
            self.callbacks[(symbol, timeframe)] = [
                item for item in callbacks if item is not callback
            ]
            if not self.callbacks[(symbol, timeframe)]:
                self.callbacks.pop((symbol, timeframe))

        async def start(self):
            return None

        async def stop(self):
            pass

    service = PublishingLegacyMarketData()
    strategy = LegacyStrategy()
    errors = []

    async def before_strategy_bar(_strategy, pending_bar):
        if pending_bar.timestamp == 2:
            raise RuntimeError("legacy callback failed")

    async def on_strategy_error(name, error):
        errors.append((name, str(error)))

    engine = BotEngine(
        [strategy],
        market_data_service=service,
        before_strategy_bar=before_strategy_bar,
        on_strategy_error=on_strategy_error,
    )

    await engine.start()

    assert engine.running is True
    assert strategy.live == [1]
    assert strategy.shutdown_count == 1
    assert errors == [(strategy.name, "legacy callback failed")]
    assert engine._last_processed_timestamps[strategy.name] == 1


@pytest.mark.asyncio
async def test_mutating_failed_subscription_is_immediately_rolled_back():
    subscribe_error = RuntimeError("subscribe failed")

    class MutatingFailingSubscribeMarketData(FakeMarketData):
        def subscribe(self, symbol, timeframe, callback):
            super().subscribe(symbol, timeframe, callback)
            raise subscribe_error

    service = MutatingFailingSubscribeMarketData([bar(1)])
    strategy = WarmupStrategy(required=1)
    engine = BotEngine([strategy], market_data_service=service)

    with pytest.raises(RuntimeError) as exc_info:
        await engine.start()

    assert exc_info.value is subscribe_error

    assert service.callbacks == {}
    assert strategy.shutdown_count == 1
    assert engine.running is False
    assert engine._market_data_subscriptions == {}


@pytest.mark.asyncio
async def test_failed_subscription_cleanup_is_retried_and_observable():
    subscribe_error = RuntimeError("subscribe failed")
    cleanup_error = RuntimeError("first unsubscribe failed")

    class RetryablePartialSubscribeMarketData(FakeMarketData):
        def __init__(self):
            super().__init__([bar(1)])
            self.unsubscribe_count = 0

        def subscribe(self, symbol, timeframe, callback):
            super().subscribe(symbol, timeframe, callback)
            raise subscribe_error

        def unsubscribe(self, symbol, timeframe, callback):
            self.unsubscribe_count += 1
            if self.unsubscribe_count == 1:
                raise cleanup_error
            super().unsubscribe(symbol, timeframe, callback)

    service = RetryablePartialSubscribeMarketData()
    strategy = WarmupStrategy(required=1)
    engine = BotEngine([strategy], market_data_service=service)

    with pytest.raises(RuntimeError) as exc_info:
        await engine.start()

    assert exc_info.value is subscribe_error
    assert service.unsubscribe_count == 2
    assert service.callbacks == {}
    assert engine._market_data_subscriptions == {}
    assert engine.cleanup_errors == [cleanup_error]


@pytest.mark.asyncio
async def test_historical_warmup_can_await_engine_stop_without_deadlock():
    class CountingMarketData(FakeMarketData):
        def __init__(self, bars):
            super().__init__(bars)
            self.unsubscribe_count = 0

        def unsubscribe(self, symbol, timeframe, callback):
            self.unsubscribe_count += 1
            super().unsubscribe(symbol, timeframe, callback)

    class ReentrantStopWarmupStrategy(WarmupStrategy):
        def __init__(self):
            super().__init__(required=1)
            self.engine = None
            self.stop_returned = asyncio.Event()

        async def warmup(self, bars):
            await self.engine.stop()
            assert self.engine.running is False
            assert service.callbacks == {}
            assert service.unsubscribe_count == 1
            assert self.shutdown_count == 0
            self.stop_returned.set()

    service = CountingMarketData([bar(1)])
    strategy = ReentrantStopWarmupStrategy()
    engine = BotEngine([strategy], market_data_service=service)
    strategy.engine = engine
    start_task = asyncio.create_task(engine.start())

    try:
        done, _ = await asyncio.wait({start_task}, timeout=0.1)
        assert done == {start_task}, "reentrant stop deadlocked startup cleanup"
        await start_task

        assert strategy.stop_returned.is_set()
        assert engine.running is False
        assert service.callbacks == {}
        assert service.unsubscribe_count == 1
        assert strategy.shutdown_count == 1
    finally:
        cleanup_task = engine._stop_cleanup_task
        if cleanup_task is not None and not cleanup_task.done():
            cleanup_task.cancel()
            await asyncio.gather(cleanup_task, return_exceptions=True)
        if not start_task.done():
            start_task.cancel()
            await asyncio.gather(start_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_startup_error_hook_can_await_engine_stop_without_deadlock():
    class ReentrantCatchUpFailure(WarmupStrategy):
        async def warmup(self, bars):
            for pending_bar in bars:
                self.warmed.append(pending_bar.timestamp)
                if pending_bar.timestamp == 2:
                    raise RuntimeError("catch-up failed")

    published = False

    async def publish_catch_up(market_data):
        nonlocal published
        if published:
            return
        published = True
        callback = market_data.callbacks[(strategy.symbol, strategy.timeframe)][0]
        await callback(bar(2))

    service = FakeMarketData([bar(1)], on_ready=publish_catch_up)
    strategy = ReentrantCatchUpFailure(required=1)
    healthy = WarmupStrategy(required=1)
    healthy.name = "healthy"
    engine = None

    async def on_strategy_error(_name, _error):
        await asyncio.wait_for(engine.stop(), timeout=0.1)

    engine = BotEngine(
        [strategy, healthy],
        market_data_service=service,
        on_strategy_error=on_strategy_error,
    )

    await asyncio.wait_for(engine.start(), timeout=0.2)

    assert engine.running is False
    assert strategy.shutdown_count == 1
    assert healthy.shutdown_count == 1


@pytest.mark.asyncio
async def test_isolated_startup_shutdown_is_not_repeated_by_later_global_rollback():
    catch_up_error = RuntimeError("catch-up failed")
    historical_error = RuntimeError("historical failed")
    readiness_count = 0

    class IsolatedFailure(WarmupStrategy):
        async def warmup(self, bars):
            for pending_bar in bars:
                self.warmed.append(pending_bar.timestamp)
                if pending_bar.timestamp == 2:
                    raise catch_up_error

    class HistoricalFailure(WarmupStrategy):
        async def warmup(self, bars):
            raise historical_error

    async def publish_for_first_strategy(market_data):
        nonlocal readiness_count
        readiness_count += 1
        if readiness_count == 1:
            callback = market_data.callbacks[(isolated.symbol, isolated.timeframe)][0]
            await callback(bar(2))

    service = FakeMarketData([bar(1)], on_ready=publish_for_first_strategy)
    isolated = IsolatedFailure(required=1)
    isolated.name = "isolated"
    historical = HistoricalFailure(required=1)
    historical.name = "historical"
    engine = BotEngine([isolated, historical], market_data_service=service)

    with pytest.raises(RuntimeError) as exc_info:
        await engine.start()

    assert exc_info.value is historical_error
    assert isolated.shutdown_count == 1
    assert historical.shutdown_count == 1


@pytest.mark.asyncio
async def test_historical_warmup_checkpoints_each_successful_prefix_bar():
    class PrefixFailingWarmup(WarmupStrategy):
        def __init__(self):
            super().__init__(required=3)
            self.fail = True

        async def warmup(self, bars):
            for pending_bar in bars:
                if self.fail and pending_bar.timestamp == 3:
                    raise RuntimeError("third bar failed")
                self.warmed.append(pending_bar.timestamp)

    service = FakeMarketData([bar(1), bar(2), bar(3)])
    strategy = PrefixFailingWarmup()
    engine = BotEngine([strategy], market_data_service=service)

    with pytest.raises(RuntimeError, match="third bar failed"):
        await engine.start()

    assert strategy.warmed == [1, 2]
    assert engine._last_processed_timestamps[strategy.name] == 2

    strategy.fail = False
    await engine.start()

    assert strategy.warmed == [1, 2, 3]
    assert engine._last_processed_timestamps[strategy.name] == 3


@pytest.mark.asyncio
async def test_callback_catch_up_checkpoints_each_successful_prefix_bar():
    class PrefixFailingCatchUp(WarmupStrategy):
        def __init__(self):
            super().__init__(required=1)
            self.fail = True

        async def warmup(self, bars):
            for pending_bar in bars:
                if self.fail and pending_bar.timestamp == 4:
                    raise RuntimeError("fourth bar failed")
                self.warmed.append(pending_bar.timestamp)

    published = False

    async def publish_catch_up(market_data):
        nonlocal published
        if published:
            return
        published = True
        callback = market_data.callbacks[(strategy.symbol, strategy.timeframe)][0]
        await callback(bar(2))
        await callback(bar(3))
        await callback(bar(4))

    service = FakeMarketData([bar(1)], on_ready=publish_catch_up)
    strategy = PrefixFailingCatchUp()
    healthy = WarmupStrategy(required=1)
    healthy.name = "healthy"
    engine = BotEngine([strategy, healthy], market_data_service=service)

    await engine.start()

    assert strategy.warmed == [1, 2, 3]
    assert engine._last_processed_timestamps[strategy.name] == 3
    assert engine._strategy_phases[strategy.name] == "inactive"

    await engine.stop()
    service.bars.extend([bar(2), bar(3), bar(4)])
    strategy.fail = False
    await engine.start()

    assert strategy.warmed == [1, 2, 3, 4]
    assert engine._last_processed_timestamps[strategy.name] == 4
