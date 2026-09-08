import asyncio
import contextlib
import threading

import pytest

from src.core.engine import BotEngine
from src.core.types import Bar


class FakeMarketDataService:
    def __init__(self) -> None:
        self.subscriptions = {}
        self.start_count = 0
        self.stop_count = 0
        self._running = False

    def subscribe(self, symbol, timeframe, callback):
        self.subscriptions.setdefault((symbol, timeframe), []).append(callback)

    def unsubscribe(self, symbol, timeframe, callback):
        callbacks = self.subscriptions.get((symbol, timeframe), [])
        self.subscriptions[(symbol, timeframe)] = [
            existing for existing in callbacks if existing is not callback
        ]
        if not self.subscriptions[(symbol, timeframe)]:
            self.subscriptions.pop((symbol, timeframe))

    async def wait_until_ready(self, symbol, timeframe, *, timeout=10, min_bars=1):
        return None

    async def start(self):
        self.start_count += 1
        self._running = True

    async def stop(self):
        self.stop_count += 1
        self._running = False


class FeedWorkerMarketDataService(FakeMarketDataService):
    def __init__(self):
        super().__init__()
        self._feed_tasks = {}
        self.feed_workers = set()
        self.stop_callers = []

    async def stop(self):
        self.stop_count += 1
        current = asyncio.current_task()
        self.stop_callers.append(current)
        if current in self._feed_tasks.values():
            raise RuntimeError("market-data stop ran in a feed worker")
        self._running = False

    def dispatch_from_feed(self, key, callback):
        async def dispatch():
            current = asyncio.current_task()
            self.feed_workers.add(current)
            self._feed_tasks[key] = current
            try:
                await callback(make_bar())
            finally:
                if self._feed_tasks.get(key) is current:
                    self._feed_tasks.pop(key)

        return asyncio.create_task(dispatch())


class RecordingStrategy:
    def __init__(self, name, symbol="BTC-USDT", timeframe="1m") -> None:
        self.name = name
        self.symbol = symbol
        self.timeframe = timeframe
        self.bars = []
        self.shutdown_count = 0

    async def on_init(self):
        pass

    async def on_bar(self, bar):
        self.bars.append(bar)

    async def on_shutdown(self):
        self.shutdown_count += 1


class FailingStrategy(RecordingStrategy):
    async def on_bar(self, bar):
        raise RuntimeError("boom")


def make_bar() -> Bar:
    return Bar(
        timestamp=1700000000000,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1.0,
    )


async def test_engine_subscribes_strategies_by_symbol_timeframe_and_fans_out_bars():
    market_data = FakeMarketDataService()
    first = RecordingStrategy("first")
    second = RecordingStrategy("second")
    engine = BotEngine([first, second], market_data_service=market_data)

    await engine.start()
    for callback in market_data.subscriptions[("BTC-USDT", "1m")]:
        await callback(make_bar())

    assert market_data.start_count == 1
    assert len(market_data.subscriptions[("BTC-USDT", "1m")]) == 2
    assert len(first.bars) == 1
    assert len(second.bars) == 1


async def test_engine_start_is_idempotent_for_market_data_subscription():
    market_data = FakeMarketDataService()
    strategy = RecordingStrategy("ma_cross")
    engine = BotEngine([strategy], market_data_service=market_data)

    await engine.start()
    await engine.start()

    assert len(market_data.subscriptions[("BTC-USDT", "1m")]) == 1


async def test_engine_stop_unsubscribes_strategy_callback():
    market_data = FakeMarketDataService()
    strategy = RecordingStrategy("ma_cross")
    engine = BotEngine([strategy], market_data_service=market_data)

    await engine.start()
    await engine.stop()

    assert ("BTC-USDT", "1m") not in market_data.subscriptions


@pytest.mark.parametrize(
    "shutdown_error",
    [RuntimeError("shutdown failed"), asyncio.CancelledError()],
    ids=["error", "cancellation"],
)
async def test_engine_stop_finishes_cleanup_before_propagating_shutdown_failure(
    shutdown_error,
):
    events = []

    class BlockingOwnedMarketData(FakeMarketDataService):
        async def start(self):
            self.start_count += 1
            self._running = True
            try:
                await asyncio.Event().wait()
            finally:
                self._running = False
                events.append("runtime-drained")

        async def stop(self):
            self.stop_count += 1
            events.append("market-data-stopped")

    class FailingShutdownStrategy(RecordingStrategy):
        async def on_shutdown(self):
            self.shutdown_count += 1
            events.append("first-shutdown")
            raise shutdown_error

    class CompletingShutdownStrategy(RecordingStrategy):
        async def on_shutdown(self):
            self.shutdown_count += 1
            events.append("second-shutdown")
            callback_completion.set_result(None)

    market_data = BlockingOwnedMarketData()
    first = FailingShutdownStrategy("first")
    second = CompletingShutdownStrategy("second")
    engine = BotEngine([first, second], market_data_service=market_data)

    await engine.start()
    callback_completion = asyncio.get_running_loop().create_future()
    engine._bar_callback_tasks.add(callback_completion)
    engine._processing_bar_callbacks += 1
    propagated = None

    try:
        try:
            await engine.stop()
        except BaseException as exc:
            propagated = exc
            events.append("stop-raised")

        assert propagated is shutdown_error
        assert first.shutdown_count == 1
        assert second.shutdown_count == 1
        assert callback_completion.done()
        assert engine._bar_callback_tasks == set()
        assert engine._processing_bar_callbacks == 0
        assert market_data.stop_count == 1
        assert engine._market_data_task is not None
        assert engine._market_data_task.done()
        assert engine._market_data_task.cancelled()
        assert events == [
            "first-shutdown",
            "second-shutdown",
            "market-data-stopped",
            "runtime-drained",
            "stop-raised",
        ]
    finally:
        if not callback_completion.done():
            callback_completion.set_result(None)
        runtime = engine._market_data_task
        if runtime is not None and not runtime.done():
            runtime.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await runtime


@pytest.mark.parametrize(
    "unsubscribe_error",
    [RuntimeError("unsubscribe failed"), asyncio.CancelledError()],
    ids=["error", "cancellation"],
)
async def test_engine_stop_retains_unsubscribe_error_and_finishes_later_cleanup(
    unsubscribe_error,
):
    events = []

    class MutatingFailingUnsubscribeMarketData(FakeMarketDataService):
        async def start(self):
            self.start_count += 1
            try:
                await asyncio.Event().wait()
            finally:
                events.append("runtime-drained")

        def unsubscribe(self, symbol, timeframe, callback):
            super().unsubscribe(symbol, timeframe, callback)
            events.append("unsubscribe-failed")
            raise unsubscribe_error

        async def stop(self):
            self.stop_count += 1
            events.append("market-data-stopped")

    class CleanupStrategy(RecordingStrategy):
        async def on_shutdown(self):
            await super().on_shutdown()
            events.append(f"{self.name}-shutdown")
            if self.name == "second":
                callback_completion.set_result(None)

    market_data = MutatingFailingUnsubscribeMarketData()
    first = CleanupStrategy("first")
    second = CleanupStrategy("second")
    engine = BotEngine([first, second], market_data_service=market_data)

    await engine.start()
    callback_completion = asyncio.get_running_loop().create_future()
    engine._bar_callback_tasks.add(callback_completion)
    engine._processing_bar_callbacks += 1
    propagated = None

    try:
        try:
            await engine.stop()
        except BaseException as exc:
            propagated = exc

        assert propagated is unsubscribe_error
        assert market_data.subscriptions == {}
        assert first.shutdown_count == 1
        assert second.shutdown_count == 1
        assert engine._strategy_phases == {"first": "inactive", "second": "inactive"}
        assert engine._pending_bars == {}
        assert all(future.done() for future in engine._strategy_shutdowns.values())
        assert callback_completion.done()
        assert engine._bar_callback_tasks == set()
        assert engine._processing_bar_callbacks == 0
        assert market_data.stop_count == 1
        assert engine._market_data_task is not None
        assert engine._market_data_task.done()
        assert engine._market_data_task.cancelled()
        assert events == [
            "unsubscribe-failed",
            "unsubscribe-failed",
            "first-shutdown",
            "second-shutdown",
            "market-data-stopped",
            "runtime-drained",
        ]
    finally:
        if not callback_completion.done():
            callback_completion.set_result(None)
        runtime = engine._market_data_task
        if runtime is not None and not runtime.done():
            runtime.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await runtime


async def test_restart_retries_retained_unsubscribe_before_fresh_subscription():
    unsubscribe_error = RuntimeError("first unsubscribe failed")

    class RetryableMutatingUnsubscribeMarketData(FakeMarketDataService):
        def __init__(self):
            super().__init__()
            self.unsubscribe_count = 0

        def unsubscribe(self, symbol, timeframe, callback):
            self.unsubscribe_count += 1
            super().unsubscribe(symbol, timeframe, callback)
            if self.unsubscribe_count == 1:
                raise unsubscribe_error

    market_data = RetryableMutatingUnsubscribeMarketData()
    strategy = RecordingStrategy("restart-after-unsubscribe-failure")
    engine = BotEngine([strategy], market_data_service=market_data)

    await engine.start()
    original_callback = market_data.subscriptions[(strategy.symbol, strategy.timeframe)][0]

    with pytest.raises(RuntimeError) as exc_info:
        await engine.stop()

    assert exc_info.value is unsubscribe_error
    assert strategy.shutdown_count == 1
    assert strategy.name in engine._market_data_subscriptions

    await asyncio.wait_for(engine.start(), timeout=0.1)

    callbacks = market_data.subscriptions[(strategy.symbol, strategy.timeframe)]
    assert market_data.unsubscribe_count == 2
    assert len(callbacks) == 1
    assert callbacks[0] is not original_callback
    assert strategy.shutdown_count == 1
    assert engine.running is True


async def test_later_stop_retries_retained_unsubscribe_without_repeating_shutdown():
    unsubscribe_error = RuntimeError("first unsubscribe failed")

    class RetryableMutatingUnsubscribeMarketData(FakeMarketDataService):
        def __init__(self):
            super().__init__()
            self.unsubscribe_count = 0

        def unsubscribe(self, symbol, timeframe, callback):
            self.unsubscribe_count += 1
            super().unsubscribe(symbol, timeframe, callback)
            if self.unsubscribe_count == 1:
                raise unsubscribe_error

    market_data = RetryableMutatingUnsubscribeMarketData()
    strategy = RecordingStrategy("retry-stop-cleanup")
    engine = BotEngine([strategy], market_data_service=market_data)

    await engine.start()
    with pytest.raises(RuntimeError) as exc_info:
        await engine.stop()

    assert exc_info.value is unsubscribe_error

    await engine.stop()

    assert market_data.unsubscribe_count == 2
    assert strategy.shutdown_count == 1
    assert strategy.name not in engine._market_data_subscriptions
    assert engine.cleanup_errors == [unsubscribe_error]


async def test_engine_stop_records_all_cleanup_failures_and_raises_first():
    unsubscribe_error = RuntimeError("unsubscribe failed")
    shutdown_error = RuntimeError("shutdown failed")
    shutdown_cancellation = asyncio.CancelledError()
    market_stop_error = RuntimeError("market stop failed")
    runtime_drain_error = RuntimeError("runtime drain failed")

    class MultipleFailureMarketData(FakeMarketDataService):
        def __init__(self):
            super().__init__()
            self.unsubscribe_count = 0

        async def start(self):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise runtime_drain_error

        def unsubscribe(self, symbol, timeframe, callback):
            self.unsubscribe_count += 1
            super().unsubscribe(symbol, timeframe, callback)
            if self.unsubscribe_count == 1:
                raise unsubscribe_error

        async def stop(self):
            self.stop_count += 1
            raise market_stop_error

    class ErrorShutdownStrategy(RecordingStrategy):
        async def on_shutdown(self):
            self.shutdown_count += 1
            raise shutdown_error

    class CancelledShutdownStrategy(RecordingStrategy):
        async def on_shutdown(self):
            self.shutdown_count += 1
            raise shutdown_cancellation

    market_data = MultipleFailureMarketData()
    first = ErrorShutdownStrategy("first-cleanup-failure")
    second = CancelledShutdownStrategy("second-cleanup-failure")
    engine = BotEngine([first, second], market_data_service=market_data)

    await engine.start()

    with pytest.raises(RuntimeError) as exc_info:
        await engine.stop()

    assert exc_info.value is unsubscribe_error
    assert first.shutdown_count == 1
    assert second.shutdown_count == 1
    assert engine._market_data_task is not None
    assert engine._market_data_task.done()
    assert engine.cleanup_errors == [
        unsubscribe_error,
        shutdown_error,
        shutdown_cancellation,
        market_stop_error,
        runtime_drain_error,
    ]


async def test_engine_error_unsubscribes_strategy_callback():
    market_data = FakeMarketDataService()
    strategy = FailingStrategy("ma_cross")
    engine = BotEngine([strategy], market_data_service=market_data)

    await engine.start()
    callback = market_data.subscriptions[("BTC-USDT", "1m")][0]
    await callback(make_bar())

    assert ("BTC-USDT", "1m") not in market_data.subscriptions


async def test_stopping_one_engine_keeps_shared_market_data_task_running():
    class BlockingMarketDataService(FakeMarketDataService):
        async def start(self):
            self.start_count += 1
            self._running = True
            try:
                await asyncio.Event().wait()
            finally:
                self._running = False

    market_data = BlockingMarketDataService()
    first_engine = BotEngine(
        [RecordingStrategy("first")],
        market_data_service=market_data,
        stop_market_data_on_stop=False,
    )
    second_engine = BotEngine(
        [RecordingStrategy("second")],
        market_data_service=market_data,
        stop_market_data_on_stop=False,
    )

    await first_engine.start()
    await second_engine.start()
    await first_engine.stop()

    assert market_data.start_count == 1
    assert first_engine._market_data_task is not None
    assert not first_engine._market_data_task.done()

    first_engine._market_data_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await first_engine._market_data_task


def test_simultaneous_first_legacy_acquisition_is_process_wide_atomic():
    class BlockingLegacyMarketData:
        __slots__ = ("start_count", "_count_lock")
        __hash__ = None

        def __init__(self):
            self.start_count = 0
            self._count_lock = threading.Lock()

        async def start(self):
            with self._count_lock:
                self.start_count += 1
            await asyncio.Event().wait()

    class CoordinatedLegacyCache(list):
        def __init__(self, members):
            super().__init__(members)
            self.first_scan = threading.Event()
            self._condition = threading.Condition()
            self._scan_count = 0
            self._serialized_contender = False

        def note_serialized_contender(self):
            with self._condition:
                self._serialized_contender = True
                self._condition.notify_all()

        def __iter__(self):
            snapshot = self.copy()
            with self._condition:
                if self._scan_count == 0:
                    self._scan_count = 1
                    self.first_scan.set()
                    coordinated = self._condition.wait_for(
                        lambda: self._scan_count >= 2 or self._serialized_contender,
                        timeout=2,
                    )
                    if not coordinated:
                        raise RuntimeError("legacy cache acquisition rendezvous timed out")
                elif self._scan_count == 1:
                    self._scan_count = 2
                    self._condition.notify_all()
            return iter(snapshot)

    original_cache = BotEngine._legacy_market_data_tasks
    original_members = tuple(original_cache)
    coordinated_cache = CoordinatedLegacyCache(original_members)
    BotEngine._legacy_market_data_tasks = coordinated_cache

    market_data = BlockingLegacyMarketData()
    engines = [
        BotEngine([], market_data_service=market_data),
        BotEngine([], market_data_service=market_data),
    ]
    ready = threading.Barrier(3)
    begin = [threading.Event(), threading.Event()]
    acquired = [threading.Event(), threading.Event()]
    cleanup = threading.Event()
    outcomes = [None, None]
    worker_errors = []

    def process_lock_is_held():
        candidates = list(vars(BotEngine).values())
        candidates.extend(BotEngine._ensure_market_data_started.__globals__.values())
        seen = set()
        for candidate in candidates:
            candidate_id = id(candidate)
            if candidate_id in seen or candidate is coordinated_cache:
                continue
            seen.add(candidate_id)
            locked = getattr(candidate, "locked", None)
            acquire = getattr(candidate, "acquire", None)
            release = getattr(candidate, "release", None)
            if not all(callable(method) for method in (locked, acquire, release)):
                continue
            with contextlib.suppress(Exception):
                if locked():
                    return True
        return False

    def worker(index):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            ready.wait(timeout=2)
            if not begin[index].wait(timeout=2):
                raise RuntimeError("legacy acquisition worker was not released")
            if index == 1 and process_lock_is_held():
                coordinated_cache.note_serialized_contender()
            try:
                task = loop.run_until_complete(engines[index]._ensure_market_data_started())
            except RuntimeError as exc:
                outcomes[index] = ("error", exc)
            else:
                outcomes[index] = ("success", task)
                loop.run_until_complete(asyncio.sleep(0))
            acquired[index].set()
            if not cleanup.wait(timeout=5):
                raise RuntimeError("legacy acquisition cleanup was not released")
        except BaseException as exc:
            worker_errors.append(exc)
            acquired[index].set()
        finally:
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            asyncio.set_event_loop(None)
            loop.close()

    threads = [
        threading.Thread(target=worker, args=(index,), name=f"legacy-owner-{index}")
        for index in range(2)
    ]
    test_error = None

    try:
        for thread in threads:
            thread.start()
        ready.wait(timeout=2)
        begin[0].set()
        assert coordinated_cache.first_scan.wait(timeout=2)
        begin[1].set()
        assert all(event.wait(timeout=3) for event in acquired)
        assert worker_errors == []

        successful_tasks = [
            outcome[1] for outcome in outcomes if outcome is not None and outcome[0] == "success"
        ]
        acquisition_errors = [
            outcome[1] for outcome in outcomes if outcome is not None and outcome[0] == "error"
        ]
        service_records = [
            runtime for runtime in coordinated_cache if runtime.service is market_data
        ]

        assert market_data.start_count == 1
        assert len(service_records) == 1
        assert len(service_records[0].leases) == 1
        assert successful_tasks == [service_records[0].task]
        assert len(acquisition_errors) == 1
        assert type(acquisition_errors[0]) is RuntimeError
        assert str(acquisition_errors[0]) == (
            "cached legacy market data task belongs to a different event loop"
        )
    except BaseException as exc:
        test_error = exc
    finally:
        cleanup.set()
        for thread in threads:
            thread.join(timeout=5)
        BotEngine._legacy_market_data_tasks = original_cache

    assert BotEngine._legacy_market_data_tasks is original_cache
    restored_members = tuple(BotEngine._legacy_market_data_tasks)
    assert len(restored_members) == len(original_members)
    assert all(
        restored is original for restored, original in zip(restored_members, original_members)
    )
    assert all(not thread.is_alive() for thread in threads)
    if worker_errors:
        raise worker_errors[0]
    if test_error is not None:
        raise test_error.with_traceback(test_error.__traceback__)


def test_public_start_rejects_cached_legacy_runtime_from_different_event_loop():
    class BlockingLegacyMarketData(FakeMarketDataService):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.start_loops: list[asyncio.AbstractEventLoop] = []
            self.stop_loops: list[asyncio.AbstractEventLoop] = []
            self.finally_loops: list[asyncio.AbstractEventLoop] = []
            self.finally_tasks: list[asyncio.Task[None] | None] = []

        async def start(self):
            self.start_count += 1
            self._running = True
            self.start_loops.append(asyncio.get_running_loop())
            self.started.set()
            try:
                await self.release.wait()
            finally:
                self._running = False
                self.finally_loops.append(asyncio.get_running_loop())
                self.finally_tasks.append(asyncio.current_task())

        async def stop(self):
            self.stop_count += 1
            self.stop_loops.append(asyncio.get_running_loop())
            self._running = False

    loop_a = asyncio.new_event_loop()
    loop_b = asyncio.new_event_loop()
    first_start = None
    runtime = None
    market_data = None
    first_engine = None
    second_engine = None

    try:
        asyncio.set_event_loop(loop_a)
        market_data = BlockingLegacyMarketData()
        first_engine = BotEngine(
            [RecordingStrategy("first-owner")],
            market_data_service=market_data,
        )
        first_start = loop_a.create_task(first_engine.start())
        loop_a.run_until_complete(asyncio.wait_for(market_data.started.wait(), timeout=1))
        runtime = first_engine._market_data_task

        assert runtime is not None
        assert runtime.get_loop() is loop_a
        assert market_data.start_count == 1
        assert first_engine._market_data_task is runtime
        assert not runtime.done()
        assert [
            cached.task
            for cached in BotEngine._legacy_market_data_tasks
            if cached.service is market_data
        ] == [runtime]

        asyncio.set_event_loop(loop_b)
        second_engine = BotEngine(
            [RecordingStrategy("second-owner")],
            market_data_service=market_data,
        )

        with pytest.raises(RuntimeError) as exc_info:
            loop_b.run_until_complete(second_engine.start())

        assert str(exc_info.value) == (
            "cached legacy market data task belongs to a different event loop"
        )
        assert market_data.start_count == 1
        assert second_engine._market_data_task is None
        assert runtime is first_engine._market_data_task
        assert not runtime.done()
        assert [
            cached.task
            for cached in BotEngine._legacy_market_data_tasks
            if cached.service is market_data
        ] == [runtime]

        asyncio.set_event_loop(loop_a)
        loop_a.run_until_complete(asyncio.wait_for(first_engine.stop(), timeout=1))

        assert market_data.stop_count == 1
        assert runtime.done()
        assert runtime.cancelled()
        assert market_data.start_loops == [loop_a]
        assert market_data.stop_loops == [loop_a]
        assert market_data.finally_loops == [loop_a]
        assert market_data.finally_tasks == [runtime]
        assert first_engine._market_data_task is runtime
        assert first_engine._market_data_task.done()
        assert first_start is not None and first_start.done()
    finally:
        asyncio.set_event_loop(None)
        if market_data is not None:
            market_data.release.set()
        for loop, engine in ((loop_a, first_engine), (loop_b, second_engine)):
            if engine is not None and engine.running:
                asyncio.set_event_loop(loop)
                with contextlib.suppress(Exception):
                    loop.run_until_complete(asyncio.wait_for(engine.stop(), timeout=1))
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            if pending:
                for task in pending:
                    task.cancel()
                with contextlib.suppress(Exception):
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
        if market_data is not None:
            BotEngine._legacy_market_data_tasks = [
                cached
                for cached in BotEngine._legacy_market_data_tasks
                if cached.service is not market_data
            ]


def test_public_stop_rejects_cached_legacy_runtime_from_different_event_loop():
    class BlockingLegacyMarketData(FakeMarketDataService):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.start_loops: list[asyncio.AbstractEventLoop] = []
            self.stop_loops: list[asyncio.AbstractEventLoop] = []
            self.finally_loops: list[asyncio.AbstractEventLoop] = []
            self.finally_tasks: list[asyncio.Task[None] | None] = []

        async def start(self):
            self.start_count += 1
            self._running = True
            self.start_loops.append(asyncio.get_running_loop())
            self.started.set()
            try:
                await self.release.wait()
            finally:
                self._running = False
                self.finally_loops.append(asyncio.get_running_loop())
                self.finally_tasks.append(asyncio.current_task())

        async def stop(self):
            self.stop_count += 1
            self.stop_loops.append(asyncio.get_running_loop())
            self._running = False

    loop_a = asyncio.new_event_loop()
    loop_b = asyncio.new_event_loop()
    legacy_cache_baseline = list(BotEngine._legacy_market_data_tasks)
    market_data = None
    first_engine = None
    second_engine = None
    first_start = None
    runtime = None
    runtime_record = None
    lease = None
    callback = None
    stop_cleanup_task_before = None
    stop_preparation_complete_before = None
    lifecycle_owner_before = None
    lifecycle_participants_before = None
    lifecycle_participants_members_before = ()
    lifecycle_depths_before = None
    lifecycle_depths_items_before = ()
    legacy_tasks_before = None
    legacy_tasks_members_before = ()
    runtime_record_before = None
    close_complete_before = None

    try:
        asyncio.set_event_loop(loop_a)
        market_data = BlockingLegacyMarketData()
        strategy = RecordingStrategy("legacy-owner")
        first_engine = BotEngine([strategy], market_data_service=market_data)
        first_start = loop_a.create_task(first_engine.start())
        loop_a.run_until_complete(asyncio.wait_for(market_data.started.wait(), timeout=1))
        loop_a.run_until_complete(asyncio.wait_for(first_start, timeout=1))
        runtime = first_engine._market_data_task
        lease = first_engine._market_data_lease
        runtime_record = next(
            cached
            for cached in BotEngine._legacy_market_data_tasks
            if cached.service is market_data
        )
        callback = market_data.subscriptions[(strategy.symbol, strategy.timeframe)][0]
        stop_cleanup_task_before = first_engine._stop_cleanup_task
        stop_preparation_complete_before = first_engine._stop_preparation_complete
        lifecycle_owner_before = first_engine._lifecycle_owner
        lifecycle_participants_before = first_engine._lifecycle_participants
        lifecycle_participants_members_before = tuple(lifecycle_participants_before)
        lifecycle_depths_before = first_engine._lifecycle_depths
        lifecycle_depths_items_before = tuple(lifecycle_depths_before.items())
        legacy_tasks_before = BotEngine._legacy_market_data_tasks
        legacy_tasks_members_before = tuple(legacy_tasks_before)
        runtime_record_before = runtime_record
        close_complete_before = runtime_record.close_complete

        assert runtime is not None
        assert lease is not None
        assert runtime_record.task is runtime
        assert runtime_record.leases == [lease]
        assert lease.task is runtime
        assert lease.service is market_data
        assert first_engine._market_data_task is runtime
        assert first_engine._market_data_lease is lease
        assert first_engine._owns_market_data_task is True
        assert first_engine.running is True
        assert strategy.shutdown_count == 0
        assert market_data.stop_count == 0
        assert not runtime.done()
        assert not runtime.cancelled()
        assert not runtime_record.closing
        assert not runtime_record.close_complete.done()
        assert market_data.subscriptions[(strategy.symbol, strategy.timeframe)] == [callback]

        asyncio.set_event_loop(loop_b)
        with pytest.raises(RuntimeError) as exc_info:
            loop_b.run_until_complete(first_engine.stop())

        assert str(exc_info.value) == (
            "cached legacy market data task belongs to a different event loop"
        )
        assert first_engine.running is True
        assert first_engine._market_data_task is runtime
        assert first_engine._market_data_lease is lease
        assert first_engine._owns_market_data_task is True
        assert runtime_record.task is runtime
        assert runtime_record.leases == [lease]
        assert not runtime_record.closing
        assert not runtime_record.close_complete.done()
        assert first_engine._stop_cleanup_task is stop_cleanup_task_before
        assert first_engine._stop_preparation_complete is stop_preparation_complete_before
        assert first_engine._lifecycle_owner is lifecycle_owner_before
        assert first_engine._lifecycle_participants is lifecycle_participants_before
        assert tuple(first_engine._lifecycle_participants) == lifecycle_participants_members_before
        assert first_engine._lifecycle_depths is lifecycle_depths_before
        assert tuple(first_engine._lifecycle_depths.items()) == lifecycle_depths_items_before
        assert BotEngine._legacy_market_data_tasks is legacy_tasks_before
        assert tuple(BotEngine._legacy_market_data_tasks) == legacy_tasks_members_before
        assert runtime_record is runtime_record_before
        assert runtime_record.close_complete is close_complete_before
        assert market_data.stop_count == 0
        assert strategy.shutdown_count == 0
        assert not runtime.done()
        assert not runtime.cancelled()
        assert market_data.subscriptions[(strategy.symbol, strategy.timeframe)] == [callback]

        second_engine = BotEngine(
            [RecordingStrategy("legacy-second-owner")],
            market_data_service=market_data,
        )
        with pytest.raises(RuntimeError) as exc_info:
            loop_b.run_until_complete(second_engine.start())

        assert str(exc_info.value) == (
            "cached legacy market data task belongs to a different event loop"
        )
        assert market_data.start_count == 1
        assert second_engine._market_data_task is None
        assert second_engine.running is False
        assert runtime_record.task is runtime
        assert runtime_record.leases == [lease]
        assert not runtime_record.closing
        assert not runtime_record.close_complete.done()
        assert market_data.stop_count == 0
        assert strategy.shutdown_count == 0
        assert not runtime.done()
        assert not runtime.cancelled()
        assert market_data.subscriptions[(strategy.symbol, strategy.timeframe)] == [callback]

        asyncio.set_event_loop(loop_a)
        loop_a.run_until_complete(asyncio.wait_for(first_engine.stop(), timeout=1))
        assert first_engine.running is False
        assert market_data.stop_count == 1
        assert market_data.stop_loops == [loop_a]
        assert strategy.shutdown_count == 1
        assert first_engine._market_data_task is runtime
        assert first_engine._market_data_lease is None
        assert first_engine._owns_market_data_task is False
        assert first_engine._stop_cleanup_task is not None
        assert first_engine._stop_cleanup_task.done()
        assert first_engine._stop_preparation_complete is not None
        assert first_engine._stop_preparation_complete.is_set()
        assert first_engine._lifecycle_owner is None
        assert first_engine._lifecycle_participants == set()
        assert first_engine._lifecycle_depths == {}
        assert runtime.done()
        assert runtime.cancelled()
        assert runtime_record is runtime_record_before
        assert runtime_record.close_complete is close_complete_before
        assert runtime_record.close_complete.done()
        assert runtime_record.closing is True
        assert runtime_record not in BotEngine._legacy_market_data_tasks
        assert len(BotEngine._legacy_market_data_tasks) == len(legacy_cache_baseline)
        assert all(
            current is expected
            for current, expected in zip(BotEngine._legacy_market_data_tasks, legacy_cache_baseline)
        )
        assert market_data.start_count == 1
        assert market_data.finally_loops == [loop_a]
        assert market_data.finally_tasks == [runtime]
    finally:
        if first_engine is not None and first_engine.running:
            asyncio.set_event_loop(loop_a)
            with contextlib.suppress(Exception):
                loop_a.run_until_complete(asyncio.wait_for(first_engine.stop(), timeout=1))
        asyncio.set_event_loop(None)
        for loop in (loop_b, loop_a):
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            if pending:
                for task in pending:
                    task.cancel()
                with contextlib.suppress(Exception):
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        BotEngine._legacy_market_data_tasks[:] = legacy_cache_baseline
        for loop in (loop_b, loop_a):
            loop.close()


async def test_opted_out_legacy_runtime_remains_reusable_after_final_lease_released():
    class BlockingMarketDataService(FakeMarketDataService):
        async def start(self):
            self.start_count += 1
            self._running = True
            try:
                await asyncio.Event().wait()
            finally:
                self._running = False

    market_data = BlockingMarketDataService()
    first_engine = BotEngine(
        [RecordingStrategy("first-opt-out")],
        market_data_service=market_data,
        stop_market_data_on_stop=False,
    )
    second_engine = BotEngine(
        [RecordingStrategy("second-opt-out")],
        market_data_service=market_data,
        stop_market_data_on_stop=False,
    )
    original_runtime = None

    try:
        await first_engine.start()
        original_runtime = first_engine._market_data_task
        assert original_runtime is not None

        await first_engine.stop()

        assert market_data.stop_count == 0
        assert not original_runtime.done()

        await second_engine.start()

        assert second_engine._market_data_task is original_runtime
        assert market_data.start_count == 1
    finally:
        if first_engine.running:
            await first_engine.stop()
        if second_engine.running:
            await second_engine.stop()
        runtimes = {
            task
            for task in (
                original_runtime,
                first_engine._market_data_task,
                second_engine._market_data_task,
            )
            if task is not None
        }
        for runtime in runtimes:
            if not runtime.done():
                runtime.cancel()
        if runtimes:
            await asyncio.gather(*runtimes, return_exceptions=True)


async def test_final_legacy_runtime_shutdown_blocks_replacement_generation_for_same_service():
    class BlockingStopMarketDataService(FakeMarketDataService):
        def __init__(self):
            super().__init__()
            self.first_generation_started = asyncio.Event()
            self.second_generation_started = asyncio.Event()
            self.stop_entered = asyncio.Event()
            self.release_stop = asyncio.Event()
            self.generation_exits: dict[int, asyncio.Event] = {}
            self.stopped_generation = None

        async def start(self):
            self.start_count += 1
            generation = self.start_count
            exit_event = asyncio.Event()
            self.generation_exits[generation] = exit_event
            if generation == 1:
                self.first_generation_started.set()
            elif generation == 2:
                self.second_generation_started.set()
            self._running = True
            try:
                await asyncio.Event().wait()
            finally:
                if self.start_count == generation:
                    self._running = False
                exit_event.set()

        async def stop(self):
            self.stop_count += 1
            self.stop_entered.set()
            await self.release_stop.wait()
            self.stopped_generation = self.start_count
            self._running = False

    class BlockingInitStrategy(RecordingStrategy):
        def __init__(self, name):
            super().__init__(name)
            self.init_entered = asyncio.Event()
            self.release_init = asyncio.Event()

        async def on_init(self):
            self.init_entered.set()
            await self.release_init.wait()

    market_data = BlockingStopMarketDataService()
    first_engine = BotEngine(
        [RecordingStrategy("first-owner")],
        market_data_service=market_data,
    )
    second_strategy = BlockingInitStrategy("second-owner")
    second_engine = BotEngine([second_strategy], market_data_service=market_data)
    first_stop = None
    second_start = None

    try:
        await first_engine.start()
        assert market_data.start_count == 1

        first_stop = asyncio.create_task(first_engine.stop())
        await market_data.stop_entered.wait()

        second_start = asyncio.create_task(second_engine.start())
        await second_strategy.init_entered.wait()
        assert not market_data.second_generation_started.is_set()

        second_strategy.release_init.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert not market_data.second_generation_started.is_set(), (
            "a replacement legacy market-data generation started before the "
            "previous final stop/drain completed for the same service object"
        )

        market_data.release_stop.set()
        await first_stop
        await asyncio.wait_for(second_start, timeout=1)

        assert market_data.generation_exits[1].is_set()
        assert market_data.stopped_generation == 1
        assert market_data.second_generation_started.is_set()
        assert market_data.start_count == 2
    finally:
        second_strategy.release_init.set()
        market_data.release_stop.set()
        if second_start is not None and not second_start.done():
            second_start.cancel()
            await asyncio.gather(second_start, return_exceptions=True)
        if first_stop is not None:
            await asyncio.gather(first_stop, return_exceptions=True)
        if second_start is not None and second_start.done() and second_engine.running:
            await asyncio.gather(second_engine.stop(), return_exceptions=True)
        runtimes = {
            task
            for task in (first_engine._market_data_task, second_engine._market_data_task)
            if task is not None
        }
        for runtime in runtimes:
            if hasattr(runtime, "done") and not runtime.done():
                runtime.cancel()
        if runtimes:
            await asyncio.gather(*runtimes, return_exceptions=True)
        BotEngine._legacy_market_data_tasks = [
            runtime
            for runtime in BotEngine._legacy_market_data_tasks
            if runtime.service is not market_data
        ]


async def test_shared_legacy_market_data_runtime_stops_after_last_engine():
    class BlockingMarketDataService(FakeMarketDataService):
        def __init__(self):
            super().__init__()
            self.runtime_drain_count = 0

        async def start(self):
            self.start_count += 1
            self._running = True
            try:
                await asyncio.Event().wait()
            finally:
                self._running = False
                self.runtime_drain_count += 1

    market_data = BlockingMarketDataService()
    first_engine = BotEngine(
        [RecordingStrategy("first-owner")],
        market_data_service=market_data,
    )
    second_engine = BotEngine(
        [RecordingStrategy("second-owner")],
        market_data_service=market_data,
    )

    await first_engine.start()
    await second_engine.start()
    runtime = first_engine._market_data_task

    try:
        assert runtime is not None
        assert runtime is second_engine._market_data_task
        assert market_data.start_count == 1
        assert not runtime.done()

        await first_engine.stop()

        assert market_data.stop_count == 0
        assert not runtime.done()
        assert second_engine.running is True

        await second_engine.stop()

        assert market_data.stop_count == 1
        assert runtime.done()
        assert market_data.runtime_drain_count == 1
    finally:
        if first_engine.running:
            await first_engine.stop()
        if second_engine.running:
            await second_engine.stop()
        if runtime is not None and not runtime.done():
            runtime.cancel()
            await asyncio.gather(runtime, return_exceptions=True)


async def test_creator_startup_rollback_keeps_shared_legacy_runtime_leased_by_second_engine():
    startup_error = RuntimeError("creator readiness failed")

    class BlockingMarketDataService(FakeMarketDataService):
        def __init__(self):
            super().__init__()
            self.runtime_drain_count = 0
            self.creator_readiness_entered = asyncio.Event()
            self.release_creator_readiness = asyncio.Event()

        async def start(self):
            self.start_count += 1
            self._running = True
            try:
                await asyncio.Event().wait()
            finally:
                self._running = False
                self.runtime_drain_count += 1

        async def wait_until_ready(self, symbol, timeframe, *, timeout=10, min_bars=1):
            del timeout, min_bars
            if symbol == creator_strategy.symbol:
                self.creator_readiness_entered.set()
                await self.release_creator_readiness.wait()
                raise startup_error
            if symbol == second_strategy.symbol:
                return None
            raise AssertionError(f"unexpected readiness for {symbol} {timeframe}")

    market_data = BlockingMarketDataService()
    creator_strategy = RecordingStrategy(
        "creator-startup-fails",
        symbol="BTC-USDT",
    )
    second_strategy = RecordingStrategy(
        "second-keeps-runtime",
        symbol="ETH-USDT",
    )
    creator_engine = BotEngine([creator_strategy], market_data_service=market_data)
    second_engine = BotEngine([second_strategy], market_data_service=market_data)
    creator_start = asyncio.create_task(creator_engine.start())
    runtime = None

    try:
        await market_data.creator_readiness_entered.wait()
        runtime = creator_engine._market_data_task
        assert runtime is not None
        assert market_data.start_count == 1
        assert not runtime.done()

        await second_engine.start()

        assert second_engine.running is True
        assert second_engine._market_data_task is runtime
        assert market_data.start_count == 1
        assert not runtime.done()

        market_data.release_creator_readiness.set()
        with pytest.raises(RuntimeError) as exc_info:
            await creator_start

        assert exc_info.value is startup_error
        assert creator_engine.running is False
        assert market_data.stop_count == 0
        assert not runtime.done()
        assert market_data.runtime_drain_count == 0
        assert second_engine.running is True

        await second_engine.stop()

        assert market_data.stop_count == 1
        assert runtime.done()
        assert market_data.runtime_drain_count == 1
    finally:
        market_data.release_creator_readiness.set()
        if not creator_start.done():
            creator_start.cancel()
        await asyncio.gather(creator_start, return_exceptions=True)
        if creator_engine.running:
            await creator_engine.stop()
        if second_engine.running:
            await second_engine.stop()
        if runtime is not None and not runtime.done():
            runtime.cancel()
            await asyncio.gather(runtime, return_exceptions=True)


async def test_engine_stop_raises_already_failed_owned_runtime():
    runtime_error = RuntimeError("owned runtime failed")

    class FailingOwnedMarketData(FakeMarketDataService):
        def __init__(self):
            super().__init__()
            self.fail_runtime = asyncio.Event()

        async def start(self):
            self.start_count += 1
            await self.fail_runtime.wait()
            raise runtime_error

    market_data = FailingOwnedMarketData()
    strategy = RecordingStrategy("already-failed-runtime")
    engine = BotEngine([strategy], market_data_service=market_data)

    await engine.start()
    market_data.fail_runtime.set()
    await asyncio.sleep(0)
    assert engine._market_data_task is not None
    await asyncio.wait({engine._market_data_task})

    with pytest.raises(RuntimeError) as exc_info:
        await engine.stop()

    assert exc_info.value is runtime_error
    assert strategy.shutdown_count == 1
    assert market_data.stop_count == 1


async def test_engine_stop_raises_owned_runtime_failure_during_cancellation_drain():
    runtime_error = RuntimeError("owned runtime failed during drain")

    class RacingOwnedMarketData(FakeMarketDataService):
        def __init__(self):
            super().__init__()
            self.fail_runtime = asyncio.Event()

        async def start(self):
            self.start_count += 1
            await self.fail_runtime.wait()
            raise runtime_error

    class DrainBoundaryEngine(BotEngine):
        async def _cancel_and_drain(self, task):
            if task is self._market_data_task:
                market_data.fail_runtime.set()
                await asyncio.sleep(0)
            await super()._cancel_and_drain(task)

    market_data = RacingOwnedMarketData()
    strategy = RecordingStrategy("racing-runtime")
    engine = DrainBoundaryEngine([strategy], market_data_service=market_data)

    await engine.start()

    with pytest.raises(RuntimeError) as exc_info:
        await engine.stop()

    assert exc_info.value is runtime_error
    assert strategy.shutdown_count == 1
    assert market_data.stop_count == 1


async def test_engine_start_fails_when_market_data_runtime_stops_before_readiness():
    class NormallyTerminatingMarketData(FakeMarketDataService):
        def __init__(self):
            super().__init__()
            self.readiness_started = asyncio.Event()
            self.release_runtime = asyncio.Event()
            self.release_readiness = asyncio.Event()

        async def start(self):
            self.start_count += 1
            self._running = True
            try:
                await self.release_runtime.wait()
            finally:
                self._running = False

        async def wait_until_ready(self, symbol, timeframe, *, timeout=10, min_bars=1):
            self.readiness_started.set()
            await self.release_readiness.wait()

    market_data = NormallyTerminatingMarketData()
    strategy = RecordingStrategy("runtime-stopped-before-readiness")
    engine = BotEngine([strategy], market_data_service=market_data)
    start_task = asyncio.create_task(engine.start())

    try:
        await market_data.readiness_started.wait()
        runtime = engine._market_data_task
        assert runtime is not None

        market_data.release_runtime.set()
        await runtime
        await asyncio.sleep(0)
        market_data.release_readiness.set()

        with pytest.raises(
            RuntimeError,
            match="market data runtime stopped before readiness",
        ):
            await start_task

        assert engine.running is False
        assert market_data.subscriptions == {}
        assert strategy.shutdown_count == 1
        assert market_data.stop_count == 1
    finally:
        market_data.release_runtime.set()
        market_data.release_readiness.set()
        if not start_task.done():
            start_task.cancel()
        await asyncio.gather(start_task, return_exceptions=True)
        if engine.running or market_data.subscriptions:
            await engine.stop()


async def test_engine_start_drains_readiness_when_market_data_runtime_fails():
    runtime_error = RuntimeError("market data runtime failed")

    class FailingMarketData(FakeMarketDataService):
        def __init__(self):
            super().__init__()
            self.readiness_started = asyncio.Event()
            self.readiness_cancelled = asyncio.Event()
            self.readiness_finished = asyncio.Event()
            self.release_runtime = asyncio.Event()
            self.release_readiness = asyncio.Event()

        async def start(self):
            self.start_count += 1
            self._running = True
            await self.release_runtime.wait()
            self._running = False
            raise runtime_error

        async def wait_until_ready(self, symbol, timeframe, *, timeout=10, min_bars=1):
            self.readiness_started.set()
            try:
                await self.release_readiness.wait()
            except asyncio.CancelledError:
                self.readiness_cancelled.set()
                raise
            finally:
                self.readiness_finished.set()

    market_data = FailingMarketData()
    strategy = RecordingStrategy("runtime-failed-before-readiness")
    engine = BotEngine([strategy], market_data_service=market_data)
    start_task = asyncio.create_task(engine.start())

    try:
        await market_data.readiness_started.wait()
        runtime = engine._market_data_task
        assert runtime is not None

        market_data.release_runtime.set()
        await asyncio.wait({runtime})
        await asyncio.sleep(0)

        with pytest.raises(RuntimeError) as exc_info:
            await start_task

        assert exc_info.value is runtime_error
        assert market_data.readiness_cancelled.is_set()
        assert market_data.readiness_finished.is_set()
        assert engine.running is False
        assert market_data.subscriptions == {}
        assert strategy.shutdown_count == 1
        assert market_data.stop_count == 1
    finally:
        market_data.release_runtime.set()
        market_data.release_readiness.set()
        if not start_task.done():
            start_task.cancel()
        await asyncio.gather(start_task, return_exceptions=True)
        if engine.running or market_data.subscriptions:
            await engine.stop()


async def test_engine_start_preserves_runtime_failure_when_readiness_cleanup_fails():
    runtime_error = RuntimeError("market data runtime failed")
    readiness_error = RuntimeError("readiness cleanup failed")

    class FailingMarketData(FakeMarketDataService):
        def __init__(self):
            super().__init__()
            self.readiness_started = asyncio.Event()
            self.readiness_cancelled = asyncio.Event()
            self.readiness_finished = asyncio.Event()
            self.release_runtime = asyncio.Event()
            self.release_readiness = asyncio.Event()

        async def start(self):
            self.start_count += 1
            self._running = True
            await self.release_runtime.wait()
            self._running = False
            raise runtime_error

        async def wait_until_ready(self, symbol, timeframe, *, timeout=10, min_bars=1):
            self.readiness_started.set()
            try:
                await self.release_readiness.wait()
            except asyncio.CancelledError:
                self.readiness_cancelled.set()
                raise readiness_error
            finally:
                self.readiness_finished.set()

    market_data = FailingMarketData()
    strategy = RecordingStrategy("runtime-failure-preserved")
    engine = BotEngine([strategy], market_data_service=market_data)
    start_task = asyncio.create_task(engine.start())

    try:
        await market_data.readiness_started.wait()
        runtime = engine._market_data_task
        assert runtime is not None

        market_data.release_runtime.set()
        await asyncio.wait({runtime})
        await asyncio.sleep(0)

        with pytest.raises(RuntimeError) as exc_info:
            await start_task

        assert exc_info.value is runtime_error
        assert market_data.readiness_cancelled.is_set()
        assert market_data.readiness_finished.is_set()
        assert readiness_error in engine.cleanup_errors
    finally:
        market_data.release_runtime.set()
        market_data.release_readiness.set()
        if not start_task.done():
            start_task.cancel()
        await asyncio.gather(start_task, return_exceptions=True)
        if engine.running or market_data.subscriptions:
            await engine.stop()


async def test_engine_stop_does_not_cancel_non_owned_pending_runtime():
    class SharedRuntimeMarketData(FakeMarketDataService):
        def __init__(self):
            super().__init__()
            self.runtime = asyncio.create_task(asyncio.Event().wait())

        def ensure_started(self):
            return self.runtime

        async def stop(self):
            self.stop_count += 1
            self.runtime.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.runtime

    market_data = SharedRuntimeMarketData()
    engine = BotEngine(
        [RecordingStrategy("shared")],
        market_data_service=market_data,
    )

    try:
        await engine.start()
        await engine.stop()

        assert market_data.stop_count == 0
        assert not market_data.runtime.done()
    finally:
        if not market_data.runtime.done():
            market_data.runtime.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await market_data.runtime


async def test_startup_rollback_does_not_cancel_non_owned_pending_runtime():
    startup_error = RuntimeError("startup readiness failed")

    class SharedRuntimeMarketData(FakeMarketDataService):
        def __init__(self):
            super().__init__()
            self.runtime = asyncio.create_task(asyncio.Event().wait())

        def ensure_started(self):
            return self.runtime

        async def wait_until_ready(self, symbol, timeframe, *, timeout=10, min_bars=1):
            raise startup_error

        async def stop(self):
            self.stop_count += 1
            self.runtime.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.runtime

    market_data = SharedRuntimeMarketData()
    engine = BotEngine(
        [RecordingStrategy("shared-rollback")],
        market_data_service=market_data,
    )

    try:
        with pytest.raises(RuntimeError) as exc_info:
            await engine.start()

        assert exc_info.value is startup_error
        assert market_data.stop_count == 0
        assert not market_data.runtime.done()
    finally:
        if not market_data.runtime.done():
            market_data.runtime.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await market_data.runtime


async def test_engine_waits_for_initial_callback_when_market_data_already_running():
    class RunningMarketDataService(FakeMarketDataService):
        def __init__(self) -> None:
            super().__init__()
            self._running = True
            self.tasks = []

        def subscribe(self, symbol, timeframe, callback):
            super().subscribe(symbol, timeframe, callback)
            self.tasks.append(asyncio.create_task(callback(make_bar())))

    class DelayedFailingStrategy(FailingStrategy):
        async def on_bar(self, bar):
            await asyncio.sleep(0)
            raise RuntimeError("boom")

    market_data = RunningMarketDataService()
    errors = []

    async def on_strategy_error(name, error):
        errors.append((name, str(error)))

    engine = BotEngine(
        [DelayedFailingStrategy("failing")],
        market_data_service=market_data,
        on_strategy_error=on_strategy_error,
        stop_market_data_on_stop=False,
    )

    await engine.start()

    assert errors == [("failing", "boom")]


async def test_engine_isolates_observer_failures_while_draining_deferred_startup_errors():
    class RunningMarketDataService(FakeMarketDataService):
        def __init__(self) -> None:
            super().__init__()
            self._running = True
            self.tasks = []

        def subscribe(self, symbol, timeframe, callback):
            super().subscribe(symbol, timeframe, callback)
            self.tasks.append(asyncio.create_task(callback(make_bar())))

    class DelayedFailingStrategy(RecordingStrategy):
        def __init__(self, name, message):
            super().__init__(name)
            self.message = message

        async def on_bar(self, bar):
            await asyncio.sleep(0)
            raise RuntimeError(self.message)

    market_data = RunningMarketDataService()
    first = DelayedFailingStrategy("first", "first boom")
    second = DelayedFailingStrategy("second", "second boom")
    errors = []

    async def on_strategy_error(name, error):
        errors.append((name, str(error)))
        if len(errors) == 1:
            raise RuntimeError("observer failed")

    engine = BotEngine(
        [first, second],
        market_data_service=market_data,
        on_strategy_error=on_strategy_error,
        stop_market_data_on_stop=False,
    )

    try:
        await engine.start()

        assert errors == [("first", "first boom"), ("second", "second boom")]
        assert first.shutdown_count == 1
        assert second.shutdown_count == 1
    finally:
        await engine.stop()


async def test_engine_stops_only_failing_strategy_and_reports_error():
    market_data = FakeMarketDataService()
    errors = []
    failing = FailingStrategy("failing")
    healthy = RecordingStrategy("healthy")

    async def on_strategy_error(name, error):
        errors.append((name, str(error)))

    engine = BotEngine(
        [failing, healthy],
        market_data_service=market_data,
        on_strategy_error=on_strategy_error,
    )

    await engine.start()
    callbacks = market_data.subscriptions[("BTC-USDT", "1m")]
    await callbacks[0](make_bar())
    await callbacks[0](make_bar())
    await callbacks[1](make_bar())

    assert engine.running is True
    assert errors == [("failing", "boom")]
    assert failing.shutdown_count == 1
    assert len(healthy.bars) == 1
    assert healthy.shutdown_count == 0


async def test_engine_runs_before_strategy_bar_before_on_bar():
    class EventStrategy(RecordingStrategy):
        async def on_bar(self, bar):
            events.append(("on_bar", self.name, len(self.bars)))
            await super().on_bar(bar)

    market_data = FakeMarketDataService()
    events = []
    strategy = EventStrategy("ma_cross")

    async def before_strategy_bar(strategy, bar):
        events.append(("before", strategy.name, len(strategy.bars)))

    engine = BotEngine(
        [strategy],
        market_data_service=market_data,
        before_strategy_bar=before_strategy_bar,
    )

    await engine.start()
    await market_data.subscriptions[("BTC-USDT", "1m")][0](make_bar())

    assert events == [("before", "ma_cross", 0), ("on_bar", "ma_cross", 0)]
    assert len(strategy.bars) == 1


async def test_engine_before_strategy_bar_failure_uses_strategy_error_path():
    market_data = FakeMarketDataService()
    strategy = RecordingStrategy("ma_cross")
    errors = []

    async def before_strategy_bar(strategy, bar):
        raise RuntimeError("mark failed")

    async def on_strategy_error(name, error):
        errors.append((name, str(error)))

    engine = BotEngine(
        [strategy],
        market_data_service=market_data,
        before_strategy_bar=before_strategy_bar,
        on_strategy_error=on_strategy_error,
    )

    await engine.start()
    callback = market_data.subscriptions[("BTC-USDT", "1m")][0]
    await callback(make_bar())
    await callback(make_bar())

    assert errors == [("ma_cross", "mark failed")]
    assert strategy.bars == []
    assert strategy.shutdown_count == 1


async def test_failing_callback_shutdown_can_reenter_stop_without_self_deadlock():
    errors = []

    class ReentrantShutdownStrategy(RecordingStrategy):
        def __init__(self, name):
            super().__init__(name)
            self.engine = None
            self.reentrant_stop_returned = False

        async def on_bar(self, pending_bar):
            raise RuntimeError("live callback failed")

        async def on_shutdown(self):
            self.shutdown_count += 1
            asyncio.get_running_loop().call_soon(unrelated_completion.set_result, None)
            await self.engine.stop()
            self.reentrant_stop_returned = True

    async def on_strategy_error(name, error):
        errors.append((name, str(error)))

    market_data = FakeMarketDataService()
    strategy = ReentrantShutdownStrategy("reentrant")
    engine = BotEngine(
        [strategy],
        market_data_service=market_data,
        on_strategy_error=on_strategy_error,
    )
    strategy.engine = engine

    await engine.start()
    callback = market_data.subscriptions[(strategy.symbol, strategy.timeframe)][0]
    unrelated_completion = asyncio.get_running_loop().create_future()
    engine._bar_callback_tasks.add(unrelated_completion)
    engine._processing_bar_callbacks += 1

    callback_task = asyncio.create_task(callback(make_bar()))
    done, _ = await asyncio.wait({callback_task}, timeout=0.1)

    assert done == {callback_task}
    await callback_task
    assert strategy.shutdown_count == 1
    assert strategy.reentrant_stop_returned is True
    assert errors == [(strategy.name, "live callback failed")]
    assert engine.running is False
    assert engine._bar_callback_tasks == set()
    assert engine._bar_callback_owners == {}
    assert engine._processing_bar_callbacks == 0


async def test_live_strategy_error_reports_original_when_unsubscribe_cleanup_fails():
    strategy_error = RuntimeError("live callback failed")
    unsubscribe_error = RuntimeError("unsubscribe failed")
    errors = []

    class MutatingFailingUnsubscribeMarketData(FakeMarketDataService):
        def unsubscribe(self, symbol, timeframe, callback):
            super().unsubscribe(symbol, timeframe, callback)
            raise unsubscribe_error

    class ExactFailingStrategy(RecordingStrategy):
        async def on_bar(self, pending_bar):
            raise strategy_error

    async def on_strategy_error(name, error):
        errors.append((name, error))

    market_data = MutatingFailingUnsubscribeMarketData()
    strategy = ExactFailingStrategy("failing-cleanup")
    engine = BotEngine(
        [strategy],
        market_data_service=market_data,
        on_strategy_error=on_strategy_error,
    )

    await engine.start()
    callback = market_data.subscriptions[(strategy.symbol, strategy.timeframe)][0]
    await callback(make_bar())

    assert errors == [(strategy.name, strategy_error)]
    assert strategy.shutdown_count == 1
    assert engine._bar_callback_tasks == set()
    assert engine._processing_bar_callbacks == 0
    assert market_data.subscriptions == {}


async def test_inflight_callback_skips_on_bar_after_stop_during_before_hook():
    market_data = FakeMarketDataService()
    strategy = RecordingStrategy("ma_cross")
    before_entered = asyncio.Event()
    allow_before_to_finish = asyncio.Event()

    async def before_strategy_bar(strategy, bar):
        before_entered.set()
        await allow_before_to_finish.wait()

    engine = BotEngine(
        [strategy],
        market_data_service=market_data,
        before_strategy_bar=before_strategy_bar,
    )

    await engine.start()
    callback = market_data.subscriptions[("BTC-USDT", "1m")][0]
    callback_task = asyncio.create_task(callback(make_bar()))
    await before_entered.wait()

    stop_task = asyncio.create_task(engine.stop())
    await asyncio.sleep(0)

    assert not stop_task.done()

    allow_before_to_finish.set()
    await asyncio.gather(callback_task, stop_task)

    assert strategy.bars == []
    assert strategy.shutdown_count == 1


async def test_callback_initiated_stop_from_before_live_hook_does_not_self_deadlock():
    market_data = FakeMarketDataService()
    strategy = RecordingStrategy("callback-local-stop")
    hook_entered = asyncio.Event()
    stop_returned = asyncio.Event()
    events = []

    async def before_live_strategy_bar(hook_strategy, bar):
        assert hook_strategy is strategy
        events.append("hook-entered")
        hook_entered.set()
        await engine.stop()
        events.append("stop-returned")
        stop_returned.set()

    engine = BotEngine(
        [strategy],
        market_data_service=market_data,
        before_live_strategy_bar=before_live_strategy_bar,
    )
    callback_task = None

    try:
        await engine.start()
        callback = market_data.subscriptions[(strategy.symbol, strategy.timeframe)][0]
        callback_task = asyncio.create_task(callback(make_bar()))
        await asyncio.wait_for(hook_entered.wait(), timeout=0.1)
        await asyncio.sleep(0)

        assert engine._stop_cleanup_task is not None

        done, _ = await asyncio.wait({callback_task}, timeout=0.1)
        assert done == {callback_task}
        await callback_task
        await engine.stop()

        assert events == ["hook-entered", "stop-returned"]
        assert stop_returned.is_set()
        assert engine.running is False
        assert strategy.shutdown_count == 1
        assert market_data.stop_count == 1
        assert engine._bar_callback_tasks == set()
        assert engine._processing_bar_callbacks == 0
        assert strategy.bars == []
    finally:
        cleanup_task = engine._stop_cleanup_task
        if cleanup_task is not None and not cleanup_task.done():
            cleanup_task.cancel()
            if callback_task is not None:
                callback_completion = engine._bar_callback_owners.get(callback_task)
                if callback_completion is not None:
                    engine._complete_bar_callback(callback_completion)
            await asyncio.gather(cleanup_task, return_exceptions=True)
        if callback_task is not None and not callback_task.done():
            callback_task.cancel()
            await asyncio.gather(callback_task, return_exceptions=True)
        runtime = engine._market_data_task
        if runtime is not None and hasattr(runtime, "done") and not runtime.done():
            runtime.cancel()
            await asyncio.gather(runtime, return_exceptions=True)


async def test_callback_initiated_stop_blocks_restart_until_detached_cleanup_finishes():
    events = []

    class BlockingShutdownStrategy(RecordingStrategy):
        def __init__(self, name):
            super().__init__(name)
            self.shutdown_entered = asyncio.Event()
            self.release_shutdown = asyncio.Event()

        async def on_shutdown(self):
            self.shutdown_count += 1
            events.append("strategy-shutdown-entered")
            self.shutdown_entered.set()
            await self.release_shutdown.wait()
            events.append("strategy-shutdown-released")

    class BlockingOwnedMarketData(FakeMarketDataService):
        def __init__(self):
            super().__init__()
            self.stop_entered = asyncio.Event()
            self.release_stop = asyncio.Event()

        async def stop(self):
            self.stop_count += 1
            events.append("market-stop-entered")
            self.stop_entered.set()
            await self.release_stop.wait()
            events.append("market-stop-released")
            self._running = False

    async def before_live_strategy_bar(hook_strategy, bar):
        assert hook_strategy is strategy
        events.append("hook-entered")
        await engine.stop()
        events.append("callback-local-stop-returned")

    market_data = BlockingOwnedMarketData()
    strategy = BlockingShutdownStrategy("callback-stop-restart")
    engine = BotEngine(
        [strategy],
        market_data_service=market_data,
        before_live_strategy_bar=before_live_strategy_bar,
    )
    callback_task = None
    restart_task = None

    try:
        await engine.start()
        callback = market_data.subscriptions[(strategy.symbol, strategy.timeframe)][0]
        callback_task = asyncio.create_task(callback(make_bar()))

        done, _ = await asyncio.wait({callback_task}, timeout=0.1)
        assert done == {callback_task}
        await callback_task
        original_cleanup_task = engine._stop_cleanup_task
        assert original_cleanup_task is not None
        assert not original_cleanup_task.done()

        await asyncio.wait_for(strategy.shutdown_entered.wait(), timeout=0.1)
        restart_task = asyncio.create_task(engine.start())
        await asyncio.sleep(0)

        strategy.release_shutdown.set()
        await asyncio.wait_for(market_data.stop_entered.wait(), timeout=0.1)

        done, _ = await asyncio.wait({restart_task}, timeout=0.05)
        assert done == set()
        assert engine._stop_cleanup_task is original_cleanup_task

        market_data.release_stop.set()
        done, _ = await asyncio.wait({restart_task}, timeout=0.1)
        assert done == {restart_task}
        await restart_task

        assert original_cleanup_task.done()
        assert engine.running is True
        assert strategy.shutdown_count == 1
        assert market_data.stop_count == 1
        assert events == [
            "hook-entered",
            "callback-local-stop-returned",
            "strategy-shutdown-entered",
            "strategy-shutdown-released",
            "market-stop-entered",
            "market-stop-released",
        ]
    finally:
        strategy.release_shutdown.set()
        market_data.release_stop.set()
        cleanup_task = engine._stop_cleanup_task
        tasks = [task for task in (callback_task, restart_task, cleanup_task) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        runtime = engine._market_data_task
        if runtime is not None and hasattr(runtime, "done") and not runtime.done():
            runtime.cancel()
            await asyncio.gather(runtime, return_exceptions=True)


async def test_startup_rollback_with_reentrant_shutdown_stops_owned_market_data_once():
    startup_error = RuntimeError("startup readiness failed")
    events = []

    class ReadinessFailingMarketData(FakeMarketDataService):
        async def wait_until_ready(self, symbol, timeframe, *, timeout=10, min_bars=1):
            assert (symbol, timeframe) == (strategy.symbol, strategy.timeframe)
            assert min_bars == 1
            events.append("readiness-failed")
            raise startup_error

    class ReentrantShutdownStrategy(RecordingStrategy):
        def __init__(self, name):
            super().__init__(name)
            self.engine = None
            self.reentrant_stop_returned = False

        async def on_shutdown(self):
            self.shutdown_count += 1
            events.append("shutdown-entered")
            await self.engine.stop()
            self.reentrant_stop_returned = True
            events.append("reentrant-stop-returned")

    market_data = ReadinessFailingMarketData()
    strategy = ReentrantShutdownStrategy("startup-rollback-reentrant")
    engine = BotEngine([strategy], market_data_service=market_data)
    strategy.engine = engine

    with pytest.raises(RuntimeError) as exc_info:
        await asyncio.wait_for(engine.start(), timeout=0.1)

    cleanup_task = engine._stop_cleanup_task
    if cleanup_task is not None:
        await asyncio.wait_for(cleanup_task, timeout=0.1)

    assert exc_info.value is startup_error
    assert events == [
        "readiness-failed",
        "shutdown-entered",
        "reentrant-stop-returned",
    ]
    assert strategy.shutdown_count == 1
    assert strategy.reentrant_stop_returned is True
    assert market_data.stop_count == 1


async def test_startup_rollback_rejects_nested_start_from_strategy_shutdown():
    startup_error = RuntimeError("startup readiness failed")

    class InitiallyFailingMarketData(FakeMarketDataService):
        def __init__(self):
            super().__init__()
            self.readiness_count = 0

        async def wait_until_ready(self, symbol, timeframe, *, timeout=10, min_bars=1):
            self.readiness_count += 1
            if self.readiness_count == 1:
                raise startup_error

    class RestartingShutdownStrategy(RecordingStrategy):
        def __init__(self, name):
            super().__init__(name)
            self.engine = None
            self.initialize_count = 0
            self.active = False
            self.nested_start_error = None

        async def on_init(self):
            self.initialize_count += 1
            self.active = True

        async def on_shutdown(self):
            self.shutdown_count += 1
            self.active = False
            try:
                await self.engine.start()
            except BaseException as exc:
                self.nested_start_error = exc

    market_data = InitiallyFailingMarketData()
    strategy = RestartingShutdownStrategy("rollback-nested-start")
    engine = BotEngine([strategy], market_data_service=market_data)
    strategy.engine = engine
    start_task = asyncio.create_task(engine.start())

    try:
        done, _ = await asyncio.wait({start_task}, timeout=0.1)
        assert done == {start_task}, "startup rollback did not complete within the bound"
        with pytest.raises(RuntimeError) as exc_info:
            await start_task

        assert exc_info.value is startup_error
        assert {
            "running": engine.running,
            "initialize_count": strategy.initialize_count,
            "shutdown_count": strategy.shutdown_count,
            "market_data_start_count": market_data.start_count,
            "market_data_stop_count": market_data.stop_count,
            "subscriptions": market_data.subscriptions,
            "strategy_phase": engine._strategy_phases[strategy.name],
            "strategy_active": strategy.active,
        } == {
            "running": False,
            "initialize_count": 1,
            "shutdown_count": 1,
            "market_data_start_count": 1,
            "market_data_stop_count": 1,
            "subscriptions": {},
            "strategy_phase": "inactive",
            "strategy_active": False,
        }
    finally:
        if not start_task.done():
            start_task.cancel()
        rollback_task = engine._start_rollback_task
        if rollback_task is not None and not rollback_task.done():
            rollback_task.cancel()
        pending = [
            task for task in (start_task, rollback_task) if task is not None and not task.done()
        ]
        if pending:
            await asyncio.wait(pending, timeout=0.1)
        if engine.running or market_data.subscriptions:
            await asyncio.wait_for(engine.stop(), timeout=0.1)
        runtime = engine._market_data_task
        if runtime is not None and hasattr(runtime, "done") and not runtime.done():
            runtime.cancel()
            await asyncio.gather(runtime, return_exceptions=True)


async def test_stop_during_slow_start_does_not_leave_running_subscription():
    class SlowInitStrategy(RecordingStrategy):
        def __init__(self, name):
            super().__init__(name)
            self.init_started = asyncio.Event()
            self.finish_init = asyncio.Event()

        async def on_init(self):
            self.init_started.set()
            await self.finish_init.wait()

    market_data = FakeMarketDataService()
    strategy = SlowInitStrategy("ma_cross")
    engine = BotEngine([strategy], market_data_service=market_data)

    start_task = asyncio.create_task(engine.start())
    await strategy.init_started.wait()
    stop_task = asyncio.create_task(engine.stop())
    await asyncio.sleep(0)

    strategy.finish_init.set()
    await asyncio.gather(start_task, stop_task)

    assert engine.running is False
    assert ("BTC-USDT", "1m") not in market_data.subscriptions
    assert market_data.start_count == 1
    assert market_data.stop_count == 1


async def test_concurrent_engine_starts_create_one_market_data_runtime():
    class SlowMarketDataService(FakeMarketDataService):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()

        async def start(self):
            self.start_count += 1
            await self.started.wait()
            self._running = True

    class SlowInitStrategy(RecordingStrategy):
        async def on_init(self):
            await asyncio.sleep(0)

    market_data = SlowMarketDataService()
    strategy = SlowInitStrategy("ma_cross")
    engine = BotEngine([strategy], market_data_service=market_data)

    await asyncio.gather(engine.start(), engine.start())
    market_data.started.set()
    if engine._market_data_task is not None:
        await engine._market_data_task

    assert market_data.start_count == 1
    assert len(market_data.subscriptions[("BTC-USDT", "1m")]) == 1


async def test_concurrent_shared_market_data_starts_create_one_runtime():
    class SlowMarketDataService(FakeMarketDataService):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()

        async def start(self):
            self.start_count += 1
            await self.started.wait()
            self._running = True

    market_data = SlowMarketDataService()
    first_engine = BotEngine(
        [RecordingStrategy("first")],
        market_data_service=market_data,
        stop_market_data_on_stop=False,
    )
    second_engine = BotEngine(
        [RecordingStrategy("second")],
        market_data_service=market_data,
        stop_market_data_on_stop=False,
    )

    await asyncio.gather(first_engine.start(), second_engine.start())
    market_data.started.set()
    runtime_tasks = {
        task
        for task in (first_engine._market_data_task, second_engine._market_data_task)
        if task is not None
    }
    await asyncio.gather(*runtime_tasks)

    assert market_data.start_count == 1
    assert len(runtime_tasks) == 1


async def test_equal_legacy_services_use_distinct_runtime_tasks():
    class EqualBlockingMarketDataService(FakeMarketDataService):
        def __eq__(self, other):
            return isinstance(other, EqualBlockingMarketDataService)

        def __hash__(self):
            return 1

        async def start(self):
            self.start_count += 1
            await asyncio.Event().wait()

    first_service = EqualBlockingMarketDataService()
    second_service = EqualBlockingMarketDataService()
    first_engine = BotEngine(
        [RecordingStrategy("first")],
        market_data_service=first_service,
        stop_market_data_on_stop=False,
    )
    second_engine = BotEngine(
        [RecordingStrategy("second")],
        market_data_service=second_service,
        stop_market_data_on_stop=False,
    )

    await first_engine.start()
    await second_engine.start()

    assert first_service.start_count == 1
    assert second_service.start_count == 1
    assert first_engine._market_data_task is not second_engine._market_data_task

    for task in (first_engine._market_data_task, second_engine._market_data_task):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_unhashable_non_weakref_legacy_service_starts():
    class UnhashableNonWeakrefMarketDataService:
        __slots__ = ("subscriptions", "start_count")
        __hash__ = None

        def __init__(self):
            self.subscriptions = {}
            self.start_count = 0

        def subscribe(self, symbol, timeframe, callback):
            self.subscriptions.setdefault((symbol, timeframe), []).append(callback)

        def unsubscribe(self, symbol, timeframe, callback):
            callbacks = self.subscriptions.get((symbol, timeframe), [])
            self.subscriptions[(symbol, timeframe)] = [
                existing for existing in callbacks if existing is not callback
            ]

        async def wait_until_ready(self, symbol, timeframe, *, timeout=10, min_bars=1):
            return None

        async def start(self):
            self.start_count += 1
            await asyncio.Event().wait()

        async def stop(self):
            pass

    market_data = UnhashableNonWeakrefMarketDataService()
    engine = BotEngine(
        [RecordingStrategy("unhashable")],
        market_data_service=market_data,
        stop_market_data_on_stop=False,
    )

    await engine.start()

    assert market_data.start_count == 1
    assert engine._market_data_task is not None
    assert not engine._market_data_task.done()

    engine._market_data_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await engine._market_data_task


async def test_completed_legacy_market_data_runtime_restarts_after_stop():
    market_data = FakeMarketDataService()
    strategy = RecordingStrategy("ma_cross")
    engine = BotEngine([strategy], market_data_service=market_data)

    await engine.start()
    await engine.stop()
    await engine.start()

    assert engine.running is True
    assert market_data.start_count == 2
    assert len(market_data.subscriptions[("BTC-USDT", "1m")]) == 1


async def test_cancelled_stop_finishes_shared_cleanup_before_propagating_cancellation():
    class BlockingStrategy(RecordingStrategy):
        def __init__(self, name):
            super().__init__(name)
            self.bar_started = asyncio.Event()
            self.release_bar = asyncio.Event()

        async def on_bar(self, pending_bar):
            self.bar_started.set()
            await self.release_bar.wait()

    market_data = FakeMarketDataService()
    strategy = BlockingStrategy("blocking-cancelled-stop")
    engine = BotEngine([strategy], market_data_service=market_data)

    await engine.start()
    callback = market_data.subscriptions[(strategy.symbol, strategy.timeframe)][0]
    callback_task = asyncio.create_task(callback(make_bar()))
    await strategy.bar_started.wait()

    stop_task = asyncio.create_task(engine.stop())
    await asyncio.sleep(0)
    stop_task.cancel()
    await asyncio.sleep(0)

    assert not stop_task.done()
    assert strategy.shutdown_count == 0

    strategy.release_bar.set()
    await callback_task
    with pytest.raises(asyncio.CancelledError):
        await stop_task

    assert strategy.shutdown_count == 1
    assert market_data.stop_count == 1
    assert engine.running is False


async def test_stop_waits_for_live_on_bar_before_calling_shutdown():
    class BlockingStrategy(RecordingStrategy):
        def __init__(self, name):
            super().__init__(name)
            self.bar_started = asyncio.Event()
            self.release_bar = asyncio.Event()
            self.shutdown_started = asyncio.Event()

        async def on_bar(self, pending_bar):
            self.bar_started.set()
            await self.release_bar.wait()
            await super().on_bar(pending_bar)

        async def on_shutdown(self):
            self.shutdown_started.set()
            await super().on_shutdown()

    market_data = FakeMarketDataService()
    strategy = BlockingStrategy("blocking")
    engine = BotEngine([strategy], market_data_service=market_data)

    await engine.start()
    callback = market_data.subscriptions[(strategy.symbol, strategy.timeframe)][0]
    callback_task = asyncio.create_task(callback(make_bar()))
    await strategy.bar_started.wait()
    stop_task = asyncio.create_task(engine.stop())
    await asyncio.sleep(0)

    assert not strategy.shutdown_started.is_set()
    assert not stop_task.done()

    strategy.release_bar.set()
    await asyncio.gather(callback_task, stop_task)

    assert strategy.shutdown_count == 1


async def test_cancelled_outer_callback_cancels_inner_task_before_first_turn():
    market_data = FakeMarketDataService()
    strategy = RecordingStrategy("cancelled-callback")
    engine = BotEngine([strategy], market_data_service=market_data)

    await engine.start()
    callback = market_data.subscriptions[(strategy.symbol, strategy.timeframe)][0]
    outer_task = asyncio.create_task(callback(make_bar()))
    outer_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await outer_task
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert strategy.bars == []
    assert engine._processing_bar_callbacks == 0
    assert engine._bar_callback_tasks == set()


@pytest.mark.parametrize(
    "shutdown_error",
    [RuntimeError("shutdown failed"), asyncio.CancelledError()],
    ids=["error", "cancellation"],
)
async def test_concurrent_callback_failure_and_stop_share_shutdown_failure(shutdown_error):
    shutdown_entered = asyncio.Event()
    release_shutdown = asyncio.Event()
    strategy_error = RuntimeError("callback failed")
    errors = []

    class CoordinatedFailingStrategy(RecordingStrategy):
        async def on_bar(self, pending_bar):
            raise strategy_error

        async def on_shutdown(self):
            self.shutdown_count += 1
            shutdown_entered.set()
            await release_shutdown.wait()
            raise shutdown_error

    async def on_strategy_error(name, error):
        errors.append((name, error))

    market_data = FakeMarketDataService()
    strategy = CoordinatedFailingStrategy("coordinated-shutdown-failure")
    engine = BotEngine(
        [strategy],
        market_data_service=market_data,
        on_strategy_error=on_strategy_error,
    )

    await engine.start()
    callback = market_data.subscriptions[(strategy.symbol, strategy.timeframe)][0]
    callback_task = asyncio.create_task(callback(make_bar()))
    await shutdown_entered.wait()
    stop_task = asyncio.create_task(engine.stop())
    await asyncio.sleep(0)

    release_shutdown.set()
    await callback_task
    with pytest.raises(type(shutdown_error)) as exc_info:
        await stop_task

    if not isinstance(shutdown_error, asyncio.CancelledError):
        assert exc_info.value is shutdown_error
    assert strategy.shutdown_count == 1
    assert errors == [(strategy.name, strategy_error)]


async def test_concurrent_failure_and_stop_shutdown_and_report_once():
    shutdown_entered = asyncio.Event()
    release_shutdown = asyncio.Event()
    errors = []

    class CoordinatedFailingStrategy(RecordingStrategy):
        async def on_bar(self, pending_bar):
            raise RuntimeError("boom")

        async def on_shutdown(self):
            self.shutdown_count += 1
            shutdown_entered.set()
            await release_shutdown.wait()

    async def on_strategy_error(name, error):
        errors.append((name, str(error)))

    market_data = FakeMarketDataService()
    strategy = CoordinatedFailingStrategy("coordinated")
    engine = BotEngine(
        [strategy],
        market_data_service=market_data,
        on_strategy_error=on_strategy_error,
    )

    await engine.start()
    callback = market_data.subscriptions[(strategy.symbol, strategy.timeframe)][0]
    callback_task = asyncio.create_task(callback(make_bar()))
    await shutdown_entered.wait()
    stop_task = asyncio.create_task(engine.stop())
    await asyncio.sleep(0)

    assert strategy.shutdown_count == 1

    release_shutdown.set()
    await asyncio.gather(callback_task, stop_task)

    assert strategy.shutdown_count == 1
    assert errors == [(strategy.name, "boom")]


async def test_failing_feed_worker_reentrant_stop_detaches_full_cleanup():
    errors = []

    class ReentrantShutdownStrategy(RecordingStrategy):
        def __init__(self, name):
            super().__init__(name)
            self.engine = None
            self.bar_failure_count = 0
            self.reentrant_stop_returned = False

        async def on_bar(self, pending_bar):
            self.bar_failure_count += 1
            raise RuntimeError("feed callback failed")

        async def on_shutdown(self):
            self.shutdown_count += 1
            await self.engine.stop()
            self.reentrant_stop_returned = True

    async def on_strategy_error(name, error):
        errors.append((name, error))

    market_data = FeedWorkerMarketDataService()
    strategy = ReentrantShutdownStrategy("feed-worker-reentrant-stop")
    engine = BotEngine(
        [strategy],
        market_data_service=market_data,
        on_strategy_error=on_strategy_error,
    )
    strategy.engine = engine
    feed_task = None

    try:
        await engine.start()
        callback = market_data.subscriptions[(strategy.symbol, strategy.timeframe)][0]
        feed_task = market_data.dispatch_from_feed("feed", callback)

        await asyncio.wait_for(feed_task, timeout=0.1)
        stop_result = await asyncio.wait_for(
            asyncio.gather(engine.stop(), return_exceptions=True),
            timeout=0.1,
        )

        assert stop_result == [None]
        assert strategy.bar_failure_count == 1
        assert strategy.shutdown_count == 1
        assert strategy.reentrant_stop_returned is True
        assert len(errors) == 1
        assert errors[0][0] == strategy.name
        assert str(errors[0][1]) == "feed callback failed"
        assert engine.running is False
        assert market_data.stop_count == 1
        assert all(caller not in market_data.feed_workers for caller in market_data.stop_callers)
    finally:
        if feed_task is not None and not feed_task.done():
            feed_task.cancel()
            await asyncio.gather(feed_task, return_exceptions=True)
        cleanup_task = engine._stop_cleanup_task
        if cleanup_task is not None and not cleanup_task.done():
            cleanup_task.cancel()
            await asyncio.gather(cleanup_task, return_exceptions=True)


async def test_simultaneous_feed_worker_shutdown_owners_do_not_deadlock_cleanup():
    shutdown_barrier = asyncio.Event()
    shutdown_entered = 0
    errors = []

    class ReentrantFailingStrategy(RecordingStrategy):
        def __init__(self, name, symbol):
            super().__init__(name, symbol=symbol)
            self.engine = None
            self.reentrant_stop_returned = False

        async def on_bar(self, pending_bar):
            raise RuntimeError(f"{self.name} failed")

        async def on_shutdown(self):
            nonlocal shutdown_entered
            self.shutdown_count += 1
            shutdown_entered += 1
            if shutdown_entered == 2:
                shutdown_barrier.set()
            await shutdown_barrier.wait()
            await self.engine.stop()
            self.reentrant_stop_returned = True

    async def on_strategy_error(name, error):
        errors.append((name, error))

    market_data = FeedWorkerMarketDataService()
    strategies = [
        ReentrantFailingStrategy("first-feed-owner", "BTC-USDT"),
        ReentrantFailingStrategy("second-feed-owner", "ETH-USDT"),
    ]
    engine = BotEngine(
        strategies,
        market_data_service=market_data,
        on_strategy_error=on_strategy_error,
    )
    for strategy in strategies:
        strategy.engine = engine
    feed_tasks = []

    try:
        await engine.start()
        for index, strategy in enumerate(strategies):
            callback = market_data.subscriptions[(strategy.symbol, strategy.timeframe)][0]
            feed_tasks.append(market_data.dispatch_from_feed(f"feed-{index}", callback))

        done, _ = await asyncio.wait(feed_tasks, timeout=0.1)
        assert done == set(feed_tasks)
        await asyncio.gather(*feed_tasks)
        await asyncio.wait_for(engine.stop(), timeout=0.1)

        assert shutdown_entered == 2
        assert [strategy.shutdown_count for strategy in strategies] == [1, 1]
        assert all(strategy.reentrant_stop_returned for strategy in strategies)
        assert {name for name, _ in errors} == {strategy.name for strategy in strategies}
        assert engine.running is False
        assert market_data.stop_count == 1
        assert all(caller not in market_data.feed_workers for caller in market_data.stop_callers)
    finally:
        shutdown_barrier.set()
        for task in feed_tasks:
            if not task.done():
                task.cancel()
        if feed_tasks:
            await asyncio.gather(*feed_tasks, return_exceptions=True)
        cleanup_task = engine._stop_cleanup_task
        if cleanup_task is not None and not cleanup_task.done():
            cleanup_task.cancel()
            await asyncio.gather(cleanup_task, return_exceptions=True)
