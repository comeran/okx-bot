from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from src.core.types import Bar

StrategyErrorCallback = Callable[[str, Exception], Awaitable[None]]
BeforeStrategyBarCallback = Callable[[Any, Bar], Awaitable[None]]


class _CallbackCoroutine(Coroutine[Any, Any, None]):
    def __init__(self, coroutine_factory: Callable[[], Coroutine[Any, Any, None]]) -> None:
        self._coroutine_factory = coroutine_factory
        self._coroutine: Coroutine[Any, Any, None] | None = None
        self._iterator: Any | None = None
        self._closed = False

    def __await__(self):
        return self

    def __next__(self):
        return self.send(None)

    def _start(self) -> Any:
        if self._closed:
            raise RuntimeError("cannot reuse already awaited coroutine")
        if self._coroutine is None:
            self._coroutine = self._coroutine_factory()
            self._iterator = self._coroutine.__await__()
        return self._iterator

    def send(self, value: Any):
        return self._start().send(value)

    def throw(self, typ: Any, val: Any = None, tb: Any = None):
        iterator = self._start()
        if val is None:
            return iterator.throw(typ)
        if tb is None:
            return iterator.throw(typ, val)
        return iterator.throw(typ, val, tb)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._coroutine is not None:
            self._coroutine.close()


class _LegacyMarketDataLease:
    def __init__(self, service: Any, task: asyncio.Future, stop_on_stop: bool) -> None:
        self.service = service
        self.task = task
        self.stop_on_stop = stop_on_stop


class _LegacyMarketDataRuntime:
    def __init__(self, service: Any, task: asyncio.Future, stop_on_final: bool) -> None:
        self.service = service
        self.task = task
        self.stop_on_final = stop_on_final
        self.leases: list[_LegacyMarketDataLease] = []
        self.closing = False
        self.close_complete = task.get_loop().create_future()


class BotEngine:
    _legacy_market_data_tasks: list[_LegacyMarketDataRuntime] = []
    _legacy_market_data_lock = threading.Lock()

    def __init__(
        self,
        strategies: list[Any] | None = None,
        market_data_service: Any | None = None,
        on_strategy_error: StrategyErrorCallback | None = None,
        before_strategy_bar: BeforeStrategyBarCallback | None = None,
        stop_market_data_on_stop: bool = True,
        before_live_strategy_bar: BeforeStrategyBarCallback | None = None,
    ) -> None:
        self.strategies = strategies or []
        self.market_data_service = market_data_service
        self.on_strategy_error = on_strategy_error
        self.before_strategy_bar = before_strategy_bar
        self.before_live_strategy_bar = before_live_strategy_bar
        self.stop_market_data_on_stop = stop_market_data_on_stop
        self.running = False
        self._active_strategies: dict[str, bool] = {}
        self._strategy_phases: dict[str, str] = {}
        self._strategy_locks: dict[str, asyncio.Lock] = {}
        self._strategy_activation_owners: set[asyncio.Task[Any]] = set()
        self._pending_bars: dict[str, list[Bar]] = {}
        self._last_processed_timestamps: dict[str, int] = {}
        self._market_data_task: Any | None = None
        self._market_data_lease: _LegacyMarketDataLease | None = None
        self._owns_market_data_task = False
        self._has_market_data_subscription = False
        self._market_data_subscriptions: dict[str, tuple[str, str, Any]] = {}
        self._strategy_readiness_events: dict[str, asyncio.Event] = {}
        self._bar_callback_tasks: set[asyncio.Future[None]] = set()
        self._bar_callback_owners: dict[asyncio.Task[Any], asyncio.Future[None]] = {}
        self._processing_bar_callbacks = 0
        self._lifecycle_lock = asyncio.Lock()
        self._lifecycle_owner: asyncio.Task[Any] | None = None
        self._lifecycle_participants: set[asyncio.Task[Any]] = set()
        self._lifecycle_depths: dict[asyncio.Task[Any], int] = {}
        self._lifecycle_changed = asyncio.Event()
        self._lifecycle_changed.set()
        self._strategy_shutdowns: dict[str, asyncio.Future[BaseException | None]] = {}
        self._strategy_shutdown_owners: dict[str, asyncio.Task[Any]] = {}
        self._strategy_shutdown_running: set[str] = set()
        self._reported_strategy_errors: set[str] = set()
        self._deferred_strategy_errors: list[tuple[Any, Exception]] = []
        self._cleanup_errors: list[BaseException] = []
        self._stop_cleanup_task: asyncio.Task[BaseException | None] | None = None
        self._start_rollback_task: asyncio.Task[None] | None = None
        self._stop_preparation_complete: asyncio.Event | None = None

    @property
    def cleanup_errors(self) -> list[BaseException]:
        return list(self._cleanup_errors)

    def _record_cleanup_error(self, error: BaseException) -> None:
        if any(existing is error for existing in self._cleanup_errors):
            return
        self._cleanup_errors.append(error)

    async def _enter_lifecycle_transition(self) -> None:
        current = asyncio.current_task()
        if current is None:
            raise RuntimeError("lifecycle transition requires an asyncio task")
        while True:
            async with self._lifecycle_lock:
                if self._lifecycle_owner is None:
                    self._lifecycle_owner = current
                    self._lifecycle_participants = {current}
                    self._lifecycle_depths[current] = 1
                    self._lifecycle_changed.clear()
                    return
                if current in self._lifecycle_participants:
                    self._lifecycle_depths[current] = self._lifecycle_depths.get(current, 0) + 1
                    return
                changed = self._lifecycle_changed
            await changed.wait()

    async def _exit_lifecycle_transition(self) -> None:
        current = asyncio.current_task()
        if current is None:
            raise RuntimeError("lifecycle transition requires an asyncio task")
        async with self._lifecycle_lock:
            depth = self._lifecycle_depths[current] - 1
            if depth:
                self._lifecycle_depths[current] = depth
                return
            self._lifecycle_depths.pop(current)
            self._lifecycle_participants.discard(current)
            if current is self._lifecycle_owner:
                successor = next(
                    (
                        participant
                        for participant in self._lifecycle_participants
                        if self._lifecycle_depths.get(participant, 0) > 0
                    ),
                    None,
                )
                self._lifecycle_owner = successor
                if successor is None:
                    self._lifecycle_changed.set()

    async def start(self) -> None:
        current = asyncio.current_task()
        if current is self._start_rollback_task:
            raise RuntimeError("startup rollback cannot start the engine")
        await self._enter_lifecycle_transition()
        try:
            await self._start()
        finally:
            await self._exit_lifecycle_transition()

    async def _start(self) -> None:
        if self.running:
            return
        self._stop_cleanup_task = None
        self._stop_preparation_complete = None
        self._cleanup_errors = []
        initialized: list[Any] = []
        service_started = False
        feed_synchronizations: dict[tuple[str, str], asyncio.Future[None]] = {}
        unregister_health_listener: Callable[[], None] | None = None
        self._deferred_strategy_errors = []
        try:
            for strategy in self.strategies:
                await self._join_strategy_shutdown(strategy)
                prior_shutdown = self._strategy_shutdowns.get(strategy.name)
                if (
                    prior_shutdown is not None
                    and prior_shutdown.done()
                    and strategy.name in self._market_data_subscriptions
                ):
                    self._unsubscribe_strategy(strategy)
                self._strategy_shutdowns.pop(strategy.name, None)
                self._strategy_shutdown_owners.pop(strategy.name, None)
                self._strategy_shutdown_running.discard(strategy.name)
                self._reported_strategy_errors.discard(strategy.name)
                self._strategy_locks.setdefault(strategy.name, asyncio.Lock())
                self._strategy_phases[strategy.name] = "initializing"
                self._active_strategies[strategy.name] = False
                on_init = getattr(strategy, "on_init", None)
                if on_init is not None:
                    await on_init()
                initialized.append(strategy)
                self._strategy_phases[strategy.name] = "activating"
                self._subscribe_strategy(strategy)

            if self._has_market_data_subscription:
                (
                    feed_synchronizations,
                    unregister_health_listener,
                ) = self._prepare_feed_synchronizations(initialized)
                self._owns_market_data_task = False
                self._market_data_task = await self._ensure_market_data_started()
                service_started = True
                # Give a newly-created legacy task one scheduling turn without
                # awaiting a long-running market-data service.
                await asyncio.sleep(0)
                self._raise_if_market_data_runtime_failed()

            for strategy in initialized:
                if strategy.name not in self._market_data_subscriptions:
                    await self._activate_strategy(strategy, [])
                    continue
                wait_until_ready = getattr(self.market_data_service, "wait_until_ready", None)
                warmup_bars = self._required_warmup_bars(strategy)
                if wait_until_ready is None:
                    await self._wait_for_strategy_callback(strategy)
                else:
                    await self._wait_for_market_data_ready(
                        wait_until_ready,
                        strategy.symbol,
                        strategy.timeframe,
                        min_bars=max(1, warmup_bars),
                    )
                synchronization = feed_synchronizations.get((strategy.symbol, strategy.timeframe))
                if synchronization is not None:
                    await self._wait_for_feed_synchronization(
                        synchronization,
                        strategy.symbol,
                        strategy.timeframe,
                    )
                recent = (
                    self.market_data_service.get_recent_bars(
                        strategy.symbol,
                        strategy.timeframe,
                        count=warmup_bars,
                    )
                    if warmup_bars
                    else []
                )
                await self._activate_strategy(strategy, recent)
            await self._finish_strategy_activation(initialized)
            if self._stop_cleanup_task is not None:
                await self._await_stop_cleanup(self._stop_cleanup_task)
                return
            await self._commit_strategy_activation(initialized)
        except BaseException:
            if unregister_health_listener is not None:
                try:
                    unregister_health_listener()
                except BaseException as exc:
                    self._record_cleanup_error(exc)
            self.running = False
            await self._finish_start_rollback(initialized, service_started)
            raise
        if unregister_health_listener is not None:
            try:
                unregister_health_listener()
            except BaseException as exc:
                self._record_cleanup_error(exc)
                self.running = False
                await self._finish_start_rollback(initialized, service_started)
                raise
        await self._drain_deferred_strategy_errors()

    async def _finish_start_rollback(
        self,
        initialized: list[Any],
        service_started: bool,
    ) -> None:
        cleanup_task = asyncio.create_task(self._rollback_start(initialized, service_started))
        self._start_rollback_task = cleanup_task
        self._lifecycle_participants.add(cleanup_task)
        try:
            while not cleanup_task.done():
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError:
                    continue
            try:
                cleanup_task.result()
            except BaseException as exc:
                self._record_cleanup_error(exc)
        finally:
            if self._start_rollback_task is cleanup_task:
                self._start_rollback_task = None
            self._lifecycle_participants.discard(cleanup_task)
            self._lifecycle_depths.pop(cleanup_task, None)

    async def _cancel_and_drain(self, task: asyncio.Future) -> None:
        task.cancel()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        with contextlib.suppress(asyncio.CancelledError):
            task.result()

    async def _cancel_and_record_cleanup_error(self, task: asyncio.Future) -> None:
        try:
            await self._cancel_and_drain(task)
        except BaseException as exc:
            self._record_cleanup_error(exc)

    def _raise_if_market_data_runtime_failed(self) -> None:
        runtime = self._market_data_task
        if runtime is None or not hasattr(runtime, "done") or not runtime.done():
            return
        exception = runtime.exception()
        if exception is not None:
            raise exception

    def _prepare_feed_synchronizations(
        self,
        strategies: list[Any],
    ) -> tuple[
        dict[tuple[str, str], asyncio.Future[None]],
        Callable[[], None] | None,
    ]:
        get_feed_health = getattr(self.market_data_service, "get_feed_health", None)
        add_health_listener = getattr(self.market_data_service, "add_health_listener", None)
        if get_feed_health is None or add_health_listener is None:
            return {}, None
        loop = asyncio.get_running_loop()
        generation_baselines: dict[tuple[str, str], int] = {}
        success_generation_baselines: dict[tuple[str, str], int | None] = {}
        success_at_baselines: dict[tuple[str, str], float | None] = {}
        tracks_success_at: dict[tuple[str, str], bool] = {}
        synchronizations: dict[tuple[str, str], asyncio.Future[None]] = {}
        for strategy in strategies:
            feed = (strategy.symbol, strategy.timeframe)
            if feed in generation_baselines or strategy.name not in self._market_data_subscriptions:
                continue
            health = get_feed_health(*feed)
            generation_baselines[feed] = health.generation
            success_generation_baselines[feed] = getattr(
                health,
                "success_generation",
                None,
            )
            tracks_success_at[feed] = success_generation_baselines[feed] is None and hasattr(
                health, "last_success_at"
            )
            success_at_baselines[feed] = getattr(health, "last_success_at", None)
            synchronizations[feed] = loop.create_future()

        def mark_synchronized(health: Any) -> None:
            feed = (getattr(health, "symbol", None), getattr(health, "timeframe", None))
            synchronization = synchronizations.get(feed)
            if synchronization is None or synchronization.done():
                return
            if health.generation <= generation_baselines[feed]:
                return
            success_generation = getattr(health, "success_generation", None)
            success_generation_baseline = success_generation_baselines[feed]
            if success_generation_baseline is not None:
                if success_generation is None or success_generation <= success_generation_baseline:
                    return
            elif tracks_success_at[feed]:
                last_success_at = getattr(health, "last_success_at", None)
                if last_success_at is None or last_success_at == success_at_baselines[feed]:
                    return
            synchronization.set_result(None)

        return synchronizations, add_health_listener(mark_synchronized)

    async def _wait_for_feed_synchronization(
        self,
        synchronization: asyncio.Future[None],
        symbol: str,
        timeframe: str,
    ) -> None:
        async def wait_for_generation(
            _symbol: str,
            _timeframe: str,
            *,
            min_bars: int,
        ) -> None:
            del min_bars
            await asyncio.shield(synchronization)

        await self._wait_for_market_data_ready(
            wait_for_generation,
            symbol,
            timeframe,
            min_bars=1,
        )

    async def _wait_for_market_data_ready(
        self,
        wait_until_ready: Callable[..., Awaitable[Any]],
        symbol: str,
        timeframe: str,
        *,
        min_bars: int,
    ) -> Any:
        runtime = self._market_data_task
        if runtime is None or not hasattr(runtime, "done"):
            return await wait_until_ready(symbol, timeframe, min_bars=min_bars)
        readiness_task = asyncio.create_task(wait_until_ready(symbol, timeframe, min_bars=min_bars))
        if runtime.done():
            await asyncio.sleep(0)
            try:
                self._raise_if_market_data_runtime_failed()
                if not readiness_task.done():
                    raise RuntimeError("market data runtime stopped before readiness")
            except BaseException:
                await self._cancel_and_record_cleanup_error(readiness_task)
                raise
            return await readiness_task
        runtime_waiter = asyncio.ensure_future(asyncio.shield(runtime))
        try:
            done, _ = await asyncio.wait(
                (readiness_task, runtime_waiter),
                return_when=asyncio.FIRST_COMPLETED,
            )
        except BaseException:
            await self._cancel_and_record_cleanup_error(readiness_task)
            await self._cancel_and_record_cleanup_error(runtime_waiter)
            raise
        if runtime_waiter in done:
            waiter_error: BaseException | None = None
            try:
                await self._cancel_and_drain(runtime_waiter)
            except BaseException as exc:
                waiter_error = exc
            try:
                if waiter_error is not None:
                    raise waiter_error
                self._raise_if_market_data_runtime_failed()
                if not readiness_task.done():
                    raise RuntimeError("market data runtime stopped before readiness")
            except BaseException:
                await self._cancel_and_record_cleanup_error(readiness_task)
                raise
        else:
            await self._cancel_and_drain(runtime_waiter)
        return await readiness_task

    async def _wait_for_strategy_callback(self, strategy: Any) -> None:
        readiness = self._strategy_readiness_events[strategy.name]

        async def wait_until_callback(
            _symbol: str,
            _timeframe: str,
            *,
            min_bars: int,
        ) -> None:
            del min_bars
            await readiness.wait()

        await self._wait_for_market_data_ready(
            wait_until_callback,
            strategy.symbol,
            strategy.timeframe,
            min_bars=1,
        )

    async def _rollback_start(
        self,
        initialized: list[Any],
        service_started: bool,
    ) -> None:
        for strategy in self.strategies:
            try:
                self._prepare_strategy_shutdown(
                    strategy,
                    initialized=any(strategy is item for item in initialized),
                )
            except BaseException as exc:
                self._record_cleanup_error(exc)
        for strategy in reversed(initialized):
            try:
                await self._shutdown_strategy(strategy)
            except BaseException as exc:
                self._record_cleanup_error(exc)
        try:
            await self._drain_registered_bar_callbacks(ignore_failures=True)
        except BaseException as exc:
            self._record_cleanup_error(exc)
        if service_started or initialized:
            await self._release_market_data_runtime()

    async def stop(self) -> None:
        current = asyncio.current_task()
        lease = self._market_data_lease
        if lease is not None and lease.task.get_loop() is not asyncio.get_running_loop():
            raise RuntimeError("cached legacy market data task belongs to a different event loop")
        if current in self._strategy_shutdown_owners.values():
            if current is self._start_rollback_task:
                return
            async with self._lifecycle_lock:
                cleanup_task = self._stop_cleanup_task
                preparation_complete = self._stop_preparation_complete
                if cleanup_task is None or (
                    cleanup_task.done() and self._market_data_subscriptions
                ):
                    preparation_complete = asyncio.Event()
                    cleanup_task = asyncio.create_task(self._run_stop_cleanup(preparation_complete))
                    self._stop_cleanup_task = cleanup_task
                    self._stop_preparation_complete = preparation_complete
                    self._lifecycle_participants.add(cleanup_task)
                    self._lifecycle_depths[cleanup_task] = 1
                    if self._lifecycle_owner is None:
                        self._lifecycle_owner = cleanup_task
                        self._lifecycle_changed.clear()
            if preparation_complete is None:
                raise RuntimeError("stop cleanup preparation signal is missing")
            await preparation_complete.wait()
            return

        await self._enter_lifecycle_transition()
        cleanup_task: asyncio.Task[BaseException | None] | None = None
        try:
            current = asyncio.current_task()
            if current is self._stop_cleanup_task:
                await self._drain_registered_bar_callbacks(ignore_failures=True)
                return
            cleanup_task = self._stop_cleanup_task
            preparation_complete = self._stop_preparation_complete
            if cleanup_task is None or (cleanup_task.done() and self._market_data_subscriptions):
                preparation_complete = asyncio.Event()
                cleanup_task = asyncio.create_task(self._run_stop_cleanup(preparation_complete))
                self._stop_cleanup_task = cleanup_task
                self._stop_preparation_complete = preparation_complete
                self._lifecycle_participants.add(cleanup_task)
                self._lifecycle_depths[cleanup_task] = 1
            if current in self._bar_callback_owners or current in self._strategy_activation_owners:
                if preparation_complete is None:
                    raise RuntimeError("stop cleanup preparation signal is missing")
                await preparation_complete.wait()
            else:
                await self._await_stop_cleanup(cleanup_task)
        finally:
            await self._exit_lifecycle_transition()

    async def _run_stop_cleanup(
        self,
        preparation_complete: asyncio.Event,
    ) -> BaseException | None:
        try:
            await self._stop(preparation_complete)
        except BaseException as exc:
            return exc
        finally:
            preparation_complete.set()
            await self._exit_lifecycle_transition()
        return None

    async def _await_stop_cleanup(
        self,
        cleanup_task: asyncio.Task[BaseException | None],
    ) -> None:
        cancellation: asyncio.CancelledError | None = None
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError as exc:
                if cancellation is None:
                    cancellation = exc
            except BaseException:
                break
        cleanup_error: BaseException | None = None
        try:
            cleanup_error = cleanup_task.result()
        except BaseException as exc:
            cleanup_error = exc
        if cancellation is not None:
            raise cancellation
        if cleanup_error is not None:
            raise cleanup_error

    async def _stop(
        self,
        preparation_complete: asyncio.Event | None = None,
    ) -> None:
        self.running = False
        cleanup_error: BaseException | None = None
        for strategy in self.strategies:
            try:
                self._prepare_strategy_shutdown(strategy)
            except BaseException as exc:
                self._record_cleanup_error(exc)
                if cleanup_error is None:
                    cleanup_error = exc
        if preparation_complete is not None:
            preparation_complete.set()
        for strategy in self.strategies:
            try:
                await self._shutdown_strategy(strategy)
            except BaseException as exc:
                self._record_cleanup_error(exc)
                if cleanup_error is None:
                    cleanup_error = exc
        try:
            await self._drain_registered_bar_callbacks(ignore_failures=True)
        except BaseException as exc:
            self._record_cleanup_error(exc)
            if cleanup_error is None:
                cleanup_error = exc
        release_error = await self._release_market_data_runtime()
        if cleanup_error is None:
            cleanup_error = release_error
        if cleanup_error is not None:
            raise cleanup_error

    async def _release_market_data_runtime(self) -> BaseException | None:
        lease = self._market_data_lease
        if lease is None:
            self._owns_market_data_task = False
            return None
        self._market_data_lease = None
        self._owns_market_data_task = False

        cached_runtime = None
        for runtime in self._legacy_market_data_tasks:
            if runtime.service is lease.service and runtime.task is lease.task:
                cached_runtime = runtime
                break
        if cached_runtime is None:
            return None

        cached_runtime.leases = [
            existing for existing in cached_runtime.leases if existing is not lease
        ]
        if cached_runtime.leases:
            return None
        if not cached_runtime.stop_on_final:
            return None
        cached_runtime.closing = True

        cleanup_error: BaseException | None = None
        try:
            try:
                await lease.service.stop()
            except BaseException as exc:
                self._record_cleanup_error(exc)
                cleanup_error = exc
            runtime_task = cached_runtime.task
            if hasattr(runtime_task, "done"):
                try:
                    await self._cancel_and_drain(runtime_task)
                except BaseException as exc:
                    self._record_cleanup_error(exc)
                    if cleanup_error is None:
                        cleanup_error = exc
        finally:
            self._legacy_market_data_tasks[:] = [
                runtime
                for runtime in self._legacy_market_data_tasks
                if runtime is not cached_runtime
            ]
            if not cached_runtime.close_complete.done():
                cached_runtime.close_complete.set_result(None)
        return cleanup_error

    async def _ensure_market_data_started(self) -> Any:
        if self.market_data_service is None:
            return None
        ensure_started = getattr(self.market_data_service, "ensure_started", None)
        if ensure_started is not None:
            self._market_data_lease = None
            self._owns_market_data_task = False
            return ensure_started()
        loop = asyncio.get_running_loop()
        while True:
            close_complete: asyncio.Future[None] | None = None
            with self._legacy_market_data_lock:
                cached_index = None
                cached_runtime = None
                for index, runtime in enumerate(self._legacy_market_data_tasks):
                    if runtime.service is self.market_data_service:
                        cached_index = index
                        cached_runtime = runtime
                        break
                if cached_runtime is not None and cached_runtime.closing:
                    if cached_runtime.task.get_loop() is not loop:
                        raise RuntimeError(
                            "cached legacy market data task belongs to a different event loop"
                        )
                    close_complete = cached_runtime.close_complete
                elif cached_runtime is not None and not cached_runtime.task.done():
                    if cached_runtime.task.get_loop() is not loop:
                        raise RuntimeError(
                            "cached legacy market data task belongs to a different event loop"
                        )
                    lease = _LegacyMarketDataLease(
                        self.market_data_service,
                        cached_runtime.task,
                        self.stop_market_data_on_stop,
                    )
                    cached_runtime.leases.append(lease)
                    if not lease.stop_on_stop:
                        cached_runtime.stop_on_final = False
                    self._market_data_lease = lease
                    self._owns_market_data_task = True
                    return cached_runtime.task
                else:
                    task = asyncio.create_task(self.market_data_service.start())
                    lease = _LegacyMarketDataLease(
                        self.market_data_service,
                        task,
                        self.stop_market_data_on_stop,
                    )
                    runtime = _LegacyMarketDataRuntime(
                        self.market_data_service,
                        task,
                        self.stop_market_data_on_stop,
                    )
                    runtime.leases.append(lease)
                    self._market_data_lease = lease
                    self._owns_market_data_task = True
                    if cached_index is None:
                        self._legacy_market_data_tasks.append(runtime)
                    else:
                        self._legacy_market_data_tasks[cached_index] = runtime
                    return task
            if close_complete is not None:
                await asyncio.shield(close_complete)
                continue

    def _required_warmup_bars(self, strategy: Any) -> int:
        required = getattr(strategy, "required_warmup_bars", None)
        if required is None:
            return 0
        return max(0, int(required()))

    def _chronological_unique_bars(self, bars: list[Bar]) -> list[Bar]:
        by_timestamp: dict[int, Bar] = {}
        for bar in bars:
            by_timestamp.setdefault(bar.timestamp, bar)
        return [by_timestamp[timestamp] for timestamp in sorted(by_timestamp)]

    async def _replay_strategy_bars(
        self,
        strategy: Any,
        bars: list[Bar],
        warmup: Callable[[list[Bar]], Awaitable[None]] | None,
        *,
        live: bool,
    ) -> None:
        for bar in bars:
            if self._strategy_phases.get(strategy.name) != "activating":
                return
            if self.before_strategy_bar is not None:
                await self.before_strategy_bar(strategy, bar)
            if self._strategy_phases.get(strategy.name) != "activating":
                return
            if live and self.before_live_strategy_bar is not None:
                await self.before_live_strategy_bar(strategy, bar)
            if self._strategy_phases.get(strategy.name) != "activating":
                return
            if warmup is not None:
                await warmup([bar])
            else:
                await strategy.on_bar(bar)
            if self._strategy_phases.get(strategy.name) != "activating":
                return
            self._last_processed_timestamps[strategy.name] = bar.timestamp

    async def _activate_strategy(self, strategy: Any, recent: list[Bar]) -> None:
        current = asyncio.current_task()
        if current is None:
            raise RuntimeError("strategy activation requires an asyncio task")
        lock = self._strategy_locks[strategy.name]
        async with lock:
            self._strategy_activation_owners.add(current)
            try:
                if self._strategy_phases.get(strategy.name) != "activating":
                    return
                recent = self._chronological_unique_bars(recent)
                last_timestamp = self._last_processed_timestamps.get(strategy.name, 0)
                recent = [bar for bar in recent if bar.timestamp > last_timestamp]
                warmup = getattr(strategy, "warmup", None)
                await self._replay_strategy_bars(
                    strategy,
                    recent,
                    warmup,
                    live=False,
                )
                await self._drain_strategy_startup_bars(strategy, warmup)
            finally:
                self._strategy_activation_owners.discard(current)

    async def _drain_strategy_startup_bars(
        self,
        strategy: Any,
        warmup: Callable[[list[Bar]], Awaitable[None]] | None,
    ) -> None:
        pending = self._chronological_unique_bars(self._pending_bars.pop(strategy.name, []))
        last_timestamp = self._last_processed_timestamps.get(strategy.name, 0)
        catch_up = [bar for bar in pending if bar.timestamp > last_timestamp]
        if not catch_up:
            return
        try:
            await self._replay_strategy_bars(
                strategy,
                catch_up,
                warmup,
                live=True,
            )
        except Exception as exc:
            self._defer_strategy_error(strategy, exc)

    async def _finish_strategy_activation(self, strategies: list[Any]) -> None:
        # Let callbacks scheduled synchronously by subscribe() register before
        # deciding that the startup queue is empty.
        await asyncio.sleep(0)
        await self._drain_strategy_activation(strategies)

    async def _drain_strategy_activation(self, strategies: list[Any]) -> None:
        while True:
            await self._drain_bar_callbacks()
            for strategy in strategies:
                await self._activate_strategy(strategy, [])
            await self._drain_bar_callbacks()
            has_pending = any(
                self._strategy_phases.get(strategy.name) == "activating"
                and self._pending_bars.get(strategy.name)
                for strategy in strategies
            )
            if not has_pending and not self._bar_callback_tasks:
                return

    async def _commit_strategy_activation(self, strategies: list[Any]) -> None:
        while True:
            activation_locks = []
            try:
                for strategy in strategies:
                    lock = self._strategy_locks[strategy.name]
                    await lock.acquire()
                    activation_locks.append(lock)
                self._raise_if_market_data_runtime_failed()
                has_startup_work = bool(self._bar_callback_tasks) or any(
                    self._strategy_phases.get(strategy.name) == "activating"
                    and self._pending_bars.get(strategy.name)
                    for strategy in strategies
                )
                if not has_startup_work:
                    for strategy in strategies:
                        if self._strategy_phases.get(strategy.name) == "activating":
                            self._strategy_phases[strategy.name] = "active"
                            self._active_strategies[strategy.name] = True
                    self.running = True
                    return
            finally:
                for lock in reversed(activation_locks):
                    lock.release()
            await self._drain_strategy_activation(strategies)

    def _subscribe_strategy(self, strategy: Any) -> None:
        if self.market_data_service is None:
            return
        if strategy.name in self._market_data_subscriptions:
            return
        symbol = getattr(strategy, "symbol", None)
        timeframe = getattr(strategy, "timeframe", None)
        if symbol is None or timeframe is None:
            return
        callback = self._bar_callback(strategy)
        self._strategy_readiness_events[strategy.name] = asyncio.Event()
        self._market_data_subscriptions[strategy.name] = (symbol, timeframe, callback)
        self._has_market_data_subscription = True
        try:
            self.market_data_service.subscribe(symbol, timeframe, callback)
        except BaseException:
            with contextlib.suppress(BaseException):
                self._unsubscribe_strategy(strategy)
            raise

    def _unsubscribe_strategy(self, strategy: Any) -> None:
        if self.market_data_service is None:
            return
        subscription = self._market_data_subscriptions.get(strategy.name)
        if subscription is None:
            self._strategy_readiness_events.pop(strategy.name, None)
            return
        symbol, timeframe, callback = subscription
        unsubscribe = getattr(self.market_data_service, "unsubscribe", None)
        if unsubscribe is not None:
            try:
                unsubscribe(symbol, timeframe, callback)
            except BaseException as exc:
                self._record_cleanup_error(exc)
                raise
        self._market_data_subscriptions.pop(strategy.name, None)
        self._strategy_readiness_events.pop(strategy.name, None)
        self._has_market_data_subscription = bool(self._market_data_subscriptions)

    def _bar_callback(self, strategy: Any):
        async def handle_bar(bar: Bar) -> None:
            error: Exception | None = None
            lock = self._strategy_locks[strategy.name]
            async with lock:
                if not self._active_strategies.get(strategy.name, False):
                    phase = self._strategy_phases.get(strategy.name)
                    if phase == "activating":
                        self._pending_bars.setdefault(strategy.name, []).append(bar)
                    return
                if bar.timestamp <= self._last_processed_timestamps.get(strategy.name, 0):
                    return
                try:
                    if self.before_strategy_bar is not None:
                        await self.before_strategy_bar(strategy, bar)
                    if not self._active_strategies.get(strategy.name, False):
                        return
                    if self.before_live_strategy_bar is not None:
                        await self.before_live_strategy_bar(strategy, bar)
                    if not self._active_strategies.get(strategy.name, False):
                        return
                    await strategy.on_bar(bar)
                    if not self._active_strategies.get(strategy.name, False):
                        return
                    self._last_processed_timestamps[strategy.name] = bar.timestamp
                except Exception as exc:
                    if self._record_strategy_error(strategy):
                        error = exc
            if error is not None:
                await self._handle_strategy_error(strategy, error, already_recorded=True)

        def register_bar(bar: Bar) -> Coroutine[Any, Any, None]:
            readiness = self._strategy_readiness_events.get(strategy.name)
            if readiness is not None:
                readiness.set()
            if self._strategy_phases.get(strategy.name) == "activating":
                self._pending_bars.setdefault(strategy.name, []).append(bar)

                async def startup_bar_registered() -> None:
                    return None

                return _CallbackCoroutine(startup_bar_registered)

            async def run_callback() -> None:
                completion = asyncio.get_running_loop().create_future()
                current = asyncio.current_task()
                if current is None:
                    raise RuntimeError("bar callback requires an asyncio task")
                self._bar_callback_tasks.add(completion)
                self._bar_callback_owners[current] = completion
                self._processing_bar_callbacks += 1
                try:
                    await asyncio.sleep(0)
                    await handle_bar(bar)
                finally:
                    if self._bar_callback_owners.get(current) is completion:
                        self._bar_callback_owners.pop(current)
                    completion.set_result(None)
                    self._complete_bar_callback(completion)

            return _CallbackCoroutine(run_callback)

        return register_bar

    def _complete_bar_callback(self, task: asyncio.Future[None]) -> None:
        if task not in self._bar_callback_tasks:
            return
        self._bar_callback_tasks.remove(task)
        self._processing_bar_callbacks -= 1
        with contextlib.suppress(BaseException):
            task.exception()

    async def _drain_registered_bar_callbacks(
        self,
        *,
        ignore_failures: bool,
    ) -> None:
        while self._bar_callback_tasks:
            current = asyncio.current_task()
            current_completion = self._bar_callback_owners.get(current) if current else None
            tasks = tuple(
                task for task in self._bar_callback_tasks if task is not current_completion
            )
            if not tasks:
                return
            for task in tasks:
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    if not task.done() or not ignore_failures:
                        raise
                except BaseException:
                    if not ignore_failures:
                        raise
                finally:
                    if task.done():
                        self._complete_bar_callback(task)

    async def _drain_bar_callbacks(self) -> None:
        await self._drain_registered_bar_callbacks(ignore_failures=False)

    def _record_strategy_error(self, strategy: Any) -> bool:
        if strategy.name in self._reported_strategy_errors:
            return False
        self._reported_strategy_errors.add(strategy.name)
        try:
            self._prepare_strategy_shutdown(strategy)
        except BaseException as exc:
            self._record_cleanup_error(exc)
        return True

    def _defer_strategy_error(self, strategy: Any, error: Exception) -> None:
        if not self._record_strategy_error(strategy):
            return
        self._deferred_strategy_errors.append((strategy, error))

    async def _drain_deferred_strategy_errors(self) -> None:
        errors = self._deferred_strategy_errors
        self._deferred_strategy_errors = []
        for strategy, error in errors:
            await self._handle_strategy_error(strategy, error, already_recorded=True)

    async def _handle_strategy_error(
        self,
        strategy: Any,
        error: Exception,
        *,
        already_recorded: bool = False,
    ) -> None:
        if not already_recorded and not self._record_strategy_error(strategy):
            await self._join_strategy_shutdown(strategy)
            return
        try:
            await self._shutdown_strategy(strategy)
        except BaseException as exc:
            self._record_cleanup_error(exc)
        if self.on_strategy_error is not None:
            try:
                await self.on_strategy_error(strategy.name, error)
            except Exception as exc:
                self._record_cleanup_error(exc)

    def _prepare_strategy_shutdown(
        self,
        strategy: Any,
        *,
        initialized: bool = False,
    ) -> tuple[asyncio.Future[BaseException | None], bool] | None:
        existing = self._strategy_shutdowns.get(strategy.name)
        if existing is not None:
            if existing.done() and strategy.name in self._market_data_subscriptions:
                self._unsubscribe_strategy(strategy)
            is_owner = (
                not existing.done()
                and strategy.name not in self._strategy_shutdown_running
                and self._strategy_shutdown_owners.get(strategy.name) is asyncio.current_task()
            )
            return existing, is_owner
        was_initialized = (
            initialized
            or self._active_strategies.get(strategy.name, False)
            or (self._strategy_phases.get(strategy.name) == "activating")
        )
        self._active_strategies[strategy.name] = False
        self._strategy_phases[strategy.name] = "inactive"
        self._pending_bars.pop(strategy.name, None)
        unsubscribe_error: BaseException | None = None
        try:
            self._unsubscribe_strategy(strategy)
        except BaseException as exc:
            unsubscribe_error = exc
        coordination: tuple[asyncio.Future[BaseException | None], bool] | None = None
        if was_initialized:
            future = asyncio.get_running_loop().create_future()
            self._strategy_shutdowns[strategy.name] = future
            coordination = future, False
        if unsubscribe_error is not None:
            raise unsubscribe_error
        return coordination

    async def _join_strategy_shutdown(self, strategy: Any) -> None:
        future = self._strategy_shutdowns.get(strategy.name)
        if future is None or future.done():
            return
        if self._strategy_shutdown_owners.get(strategy.name) is asyncio.current_task():
            return
        await asyncio.shield(future)

    async def _shutdown_strategy(self, strategy: Any) -> None:
        coordination = self._prepare_strategy_shutdown(strategy)
        if coordination is None:
            return
        future, is_owner = coordination
        if future.done():
            error = future.result()
            if error is not None:
                raise error
            return
        if not is_owner and strategy.name not in self._strategy_shutdown_owners:
            current = asyncio.current_task()
            if current is None:
                raise RuntimeError("strategy shutdown requires an asyncio task")
            self._strategy_shutdown_owners[strategy.name] = current
            is_owner = True
        if not is_owner:
            await self._join_strategy_shutdown(strategy)
            if not future.done():
                return
            error = future.result()
            if error is not None:
                raise error
            return
        error: BaseException | None = None
        self._strategy_shutdown_running.add(strategy.name)
        try:
            lock = self._strategy_locks[strategy.name]
            async with lock:
                pass
            on_shutdown = getattr(strategy, "on_shutdown", None)
            if on_shutdown is not None:
                await on_shutdown()
        except BaseException as exc:
            error = exc
        finally:
            self._strategy_shutdown_running.discard(strategy.name)
            self._strategy_shutdown_owners.pop(strategy.name, None)
            if not future.done():
                future.set_result(error)
        if error is not None:
            raise error
