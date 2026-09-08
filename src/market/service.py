from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable

from ccxt.base.errors import NotSupported

from src.core.types import Bar
from src.exchange.okx_client import (
    OKX_RUNTIME_TIMEFRAME_MILLISECONDS,
    create_okx_client,
)
from src.market.health import (
    MARKET_FEED_ERROR_CODE,
    MARKET_FEED_PUBLIC_MESSAGE,
    HealthListener,
    MarketFeedHealth,
)

BarCallback = Callable[[Bar], Awaitable[None]]

logger = logging.getLogger(__name__)


class _CallbackDispatchError(RuntimeError):
    def __init__(self, original: Exception, errors: list[Exception] | None = None) -> None:
        super().__init__(f"Subscriber callback failed: {original}")
        self.original = original
        self.errors = tuple(errors or [original])


class _ServiceTaskHandle:
    def __init__(self, owner: asyncio.Task[None]) -> None:
        self._owner = owner

    def __await__(self):
        return asyncio.shield(self._owner).__await__()

    def cancel(self, msg: object | None = None) -> bool:
        return self._owner.cancel(msg)

    def cancelled(self) -> bool:
        return self._owner.cancelled()

    def done(self) -> bool:
        return self._owner.done()

    def exception(self) -> BaseException | None:
        return self._owner.exception()

    def get_loop(self) -> asyncio.AbstractEventLoop:
        return self._owner.get_loop()


class MarketDataService:
    _poll_interval_seconds = 1
    _max_retry_delay_seconds = 8

    def __init__(
        self,
        api_key: str,
        secret: str,
        passphrase: str,
        default_type: str = "spot",
        demo: bool = True,
    ):
        self._api_key = api_key
        self._secret = secret
        self._passphrase = passphrase
        self._default_type = default_type
        self._demo = demo
        self._exchange = self._create_exchange()
        self._exchange_usable = True
        self._retired_exchanges: list[object] = []
        self._exchange_closed = False
        self._subscriptions: dict[str, list[BarCallback]] = {}
        self._buffers: dict[str, deque[Bar]] = {}
        self._last_bar_timestamps: dict[str, int] = {}
        self._health: dict[str, MarketFeedHealth] = {}
        self._health_conditions: dict[str, asyncio.Condition] = {}
        self._health_listeners: dict[int, HealthListener] = {}
        self._next_health_listener_id = 0
        self._running = False
        self._service_task: asyncio.Task[None] | None = None
        self._service_handle: _ServiceTaskHandle | None = None
        self._feed_tasks: dict[str, asyncio.Task[None]] = {}
        self._subscriptions_changed: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._stopping = False
        self._shutdown_task: asyncio.Task[None] | None = None

    def _create_exchange(self):
        return create_okx_client(
            api_key=self._api_key,
            secret=self._secret,
            passphrase=self._passphrase,
            default_type=self._default_type,
            demo=self._demo,
        )

    def _bind_running_loop(self) -> asyncio.AbstractEventLoop:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise RuntimeError("MarketDataService is bound to a different event loop")
        return loop

    def _ensure_exchange_open(self) -> None:
        if self._exchange_closed:
            self._exchange_usable = False
        if not self._exchange_usable:
            self._exchange = self._create_exchange()
            self._exchange_usable = True
            self._exchange_closed = False

    def _retire_active_exchange(self) -> None:
        if not self._exchange_usable:
            return
        exchange = self._exchange
        if not any(existing is exchange for existing in self._retired_exchanges):
            self._retired_exchanges.append(exchange)
        self._exchange_usable = False
        self._exchange_closed = False

    def _remove_retired_exchange(self, exchange: object) -> None:
        self._retired_exchanges = [
            existing for existing in self._retired_exchanges if existing is not exchange
        ]

    async def _close_retired_exchanges(self) -> None:
        first_error: Exception | None = None
        for exchange in list(self._retired_exchanges):
            try:
                await exchange.close()
            except asyncio.CancelledError:
                self._exchange_closed = False
                raise
            except Exception as exc:
                if first_error is None:
                    first_error = exc
            else:
                self._remove_retired_exchange(exchange)
        self._exchange_closed = not self._retired_exchanges and not self._exchange_usable
        if first_error is not None:
            raise first_error

    def _health_key(self, symbol: str, timeframe: str) -> str:
        return f"{symbol}:{timeframe}"

    def _ensure_health_condition(self, key: str) -> asyncio.Condition:
        condition = self._health_conditions.get(key)
        if condition is None:
            condition = asyncio.Condition()
            self._health_conditions[key] = condition
        return condition

    def _ensure_feed_state(self, key: str) -> deque[Bar]:
        return self._buffers.setdefault(key, deque(maxlen=1000))

    def _buffered_bars(self, key: str) -> int:
        return len(self._ensure_feed_state(key))

    def _last_bar_timestamp(self, key: str, fallback: int | None = None) -> int | None:
        return self._last_bar_timestamps.get(key, fallback)

    def _ensure_health(
        self,
        symbol: str,
        timeframe: str,
        *,
        key: str | None = None,
    ) -> MarketFeedHealth:
        resolved_key = key or self._health_key(symbol, timeframe)
        self._ensure_feed_state(resolved_key)
        health = self._health.get(resolved_key)
        if health is None:
            health = MarketFeedHealth(
                key=resolved_key,
                symbol=symbol,
                timeframe=timeframe,
                status="pending",
                buffered_bars=0,
                consecutive_failures=0,
                total_failures=0,
                last_success_at=None,
                last_failure_at=None,
                last_bar_timestamp=None,
                error_code=None,
                public_message=None,
                generation=0,
                success_generation=0,
            )
            self._health[resolved_key] = health
        self._ensure_health_condition(resolved_key)
        return health

    def _current_time(self) -> float:
        return time.time()

    def _timeframe_milliseconds(self, timeframe: str) -> int:
        try:
            return OKX_RUNTIME_TIMEFRAME_MILLISECONDS[timeframe]
        except KeyError:
            raise ValueError("Unsupported OKX runtime timeframe") from None

    def _is_closed_bar(self, timestamp: int, timeframe: str) -> bool:
        return timestamp + self._timeframe_milliseconds(timeframe) <= int(
            self._current_time() * 1000
        )

    def _is_fresh_bar(self, timestamp: int | None, timeframe: str) -> bool:
        if timestamp is None:
            return False
        timeframe_ms = self._timeframe_milliseconds(timeframe)
        closed_at = timestamp + timeframe_ms
        return int(self._current_time() * 1000) < closed_at + timeframe_ms

    async def _publish_health(self, health: MarketFeedHealth) -> None:
        self._health[health.key] = health
        condition = self._ensure_health_condition(health.key)
        async with condition:
            condition.notify_all()
        for listener in list(self._health_listeners.values()):
            try:
                listener(health)
            except Exception:
                continue

    def _success_health_snapshot(self, key: str, symbol: str, timeframe: str) -> MarketFeedHealth:
        previous = self._ensure_health(symbol, timeframe, key=key)
        buffered_bars = self._buffered_bars(key)
        last_bar_timestamp = self._last_bar_timestamp(key, previous.last_bar_timestamp)
        success_generation = (previous.success_generation or 0) + 1
        is_fresh = self._is_fresh_bar(last_bar_timestamp, timeframe)
        if buffered_bars == 0:
            status = "pending"
        elif is_fresh:
            status = "ready"
        else:
            status = "degraded"
        return MarketFeedHealth(
            key=key,
            symbol=symbol,
            timeframe=timeframe,
            status=status,
            buffered_bars=buffered_bars,
            consecutive_failures=0,
            total_failures=previous.total_failures,
            last_success_at=self._current_time(),
            last_failure_at=previous.last_failure_at,
            last_bar_timestamp=last_bar_timestamp,
            error_code=None,
            public_message=None,
            generation=previous.generation + 1,
            success_generation=success_generation,
        )

    def _failure_status(self, previous: MarketFeedHealth) -> str:
        failure_count = previous.consecutive_failures + 1
        if failure_count >= 10:
            return "unavailable"
        if failure_count >= 3:
            return "degraded"
        if previous.status == "ready" and self._is_fresh_bar(
            previous.last_bar_timestamp, previous.timeframe
        ):
            return "ready"
        return "degraded" if previous.buffered_bars > 0 else "pending"

    def _failure_health_snapshot(self, key: str, symbol: str, timeframe: str) -> MarketFeedHealth:
        previous = self._ensure_health(symbol, timeframe, key=key)
        failure_count = previous.consecutive_failures + 1
        buffered_bars = self._buffered_bars(key)
        last_bar_timestamp = self._last_bar_timestamp(key, previous.last_bar_timestamp)
        return MarketFeedHealth(
            key=key,
            symbol=symbol,
            timeframe=timeframe,
            status=self._failure_status(previous),
            buffered_bars=buffered_bars,
            consecutive_failures=failure_count,
            total_failures=previous.total_failures + 1,
            last_success_at=previous.last_success_at,
            last_failure_at=self._current_time(),
            last_bar_timestamp=last_bar_timestamp,
            error_code=MARKET_FEED_ERROR_CODE,
            public_message=MARKET_FEED_PUBLIC_MESSAGE,
            generation=previous.generation + 1,
            success_generation=previous.success_generation,
        )

    async def _record_success(self, key: str, symbol: str, timeframe: str) -> None:
        await self._publish_health(self._success_health_snapshot(key, symbol, timeframe))

    async def _record_failure(self, key: str, symbol: str, timeframe: str) -> None:
        await self._publish_health(self._failure_health_snapshot(key, symbol, timeframe))

    def _validate_subscription_notification_loop(self) -> None:
        if self._loop is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            raise RuntimeError("MarketDataService is bound to a different event loop") from None
        if loop is not self._loop:
            raise RuntimeError("MarketDataService is bound to a different event loop")

    def subscribe(self, symbol: str, timeframe: str, callback: BarCallback) -> None:
        self._timeframe_milliseconds(timeframe)
        self._validate_subscription_notification_loop()
        key = self._health_key(symbol, timeframe)
        callbacks = self._subscriptions.setdefault(key, [])
        if not any(existing is callback for existing in callbacks):
            callbacks.append(callback)
        self._ensure_feed_state(key)
        self._ensure_health(symbol, timeframe, key=key)
        if self._subscriptions_changed is not None:
            self._subscriptions_changed.set()

    def unsubscribe(self, symbol: str, timeframe: str, callback: BarCallback) -> None:
        self._validate_subscription_notification_loop()
        key = self._health_key(symbol, timeframe)
        callbacks = self._subscriptions.get(key)
        if callbacks is None:
            return
        self._subscriptions[key] = [existing for existing in callbacks if existing is not callback]
        if not self._subscriptions[key]:
            self._subscriptions.pop(key)
        if self._subscriptions_changed is not None:
            self._subscriptions_changed.set()

    def get_recent_bars(self, symbol: str, timeframe: str, count: int = 100) -> list[Bar]:
        key = self._health_key(symbol, timeframe)
        return list(self._ensure_feed_state(key))[-count:]

    def get_feed_health(self, symbol: str, timeframe: str) -> MarketFeedHealth:
        return self._ensure_health(symbol, timeframe)

    def list_feed_health(self) -> list[MarketFeedHealth]:
        return [self._health[key] for key in sorted(self._health)]

    def add_health_listener(self, listener: HealthListener) -> Callable[[], None]:
        listener_id = self._next_health_listener_id
        self._next_health_listener_id += 1
        self._health_listeners[listener_id] = listener

        def unregister() -> None:
            self._health_listeners.pop(listener_id, None)

        return unregister

    async def wait_until_ready(
        self,
        symbol: str,
        timeframe: str,
        *,
        timeout: float = 10,
        min_bars: int = 1,
    ) -> MarketFeedHealth:
        self._timeframe_milliseconds(timeframe)
        loop = self._bind_running_loop()
        if min_bars < 1:
            raise ValueError("min_bars must be at least 1")
        key = self._health_key(symbol, timeframe)
        condition = self._ensure_health_condition(key)
        deadline = loop.time() + timeout

        async with condition:
            while True:
                health = self.get_feed_health(symbol, timeframe)
                if (
                    health.status == "ready"
                    and health.error_code is None
                    and health.buffered_bars >= min_bars
                    and self._is_fresh_bar(health.last_bar_timestamp, timeframe)
                ):
                    return health
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError
                await asyncio.wait_for(condition.wait(), timeout=remaining)

    async def _poll_once(self, symbol: str, timeframe: str) -> None:
        self._timeframe_milliseconds(timeframe)
        self._bind_running_loop()
        key = self._health_key(symbol, timeframe)

        try:
            async with self._lifecycle_lock:
                self._ensure_exchange_open()
                exchange = self._exchange
            watch_ohlcv = getattr(exchange, "watch_ohlcv", None)
            fetch_ohlcv = getattr(exchange, "fetch_ohlcv", None)
            if callable(watch_ohlcv):
                try:
                    rows = await watch_ohlcv(symbol, timeframe)
                except NotSupported:
                    if not callable(fetch_ohlcv):
                        raise RuntimeError("Exchange does not support OHLCV data") from None
                    rows = await fetch_ohlcv(symbol, timeframe)
            elif callable(fetch_ohlcv):
                rows = await fetch_ohlcv(symbol, timeframe)
            else:
                raise RuntimeError("Exchange does not support OHLCV data")
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._record_failure(key, symbol, timeframe)
            raise

        callback_errors: list[Exception] = []
        try:
            parsed_bars: list[Bar] = []
            for row in rows:
                if len(row) != 6:
                    raise ValueError("OHLCV row must contain exactly 6 values")
                timestamp = int(row[0])
                open_price = float(row[1])
                high = float(row[2])
                low = float(row[3])
                close = float(row[4])
                volume = float(row[5])
                if not all(
                    math.isfinite(value) for value in (open_price, high, low, close, volume)
                ):
                    raise ValueError("OHLCV values must be finite")
                parsed_bars.append(
                    Bar(
                        timestamp=timestamp,
                        open=open_price,
                        high=high,
                        low=low,
                        close=close,
                        volume=volume,
                    )
                )

            for bar in parsed_bars:
                if bar.timestamp <= self._last_bar_timestamps.get(key, 0):
                    continue
                if not self._is_closed_bar(bar.timestamp, timeframe):
                    continue
                self._last_bar_timestamps[key] = bar.timestamp
                self._ensure_feed_state(key).append(bar)
                for callback in list(self._subscriptions.get(key, [])):
                    if not any(
                        existing is callback for existing in self._subscriptions.get(key, [])
                    ):
                        continue
                    try:
                        await callback(bar)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        callback_errors.append(exc)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._record_failure(key, symbol, timeframe)
            raise

        await self._record_success(key, symbol, timeframe)
        if callback_errors:
            callback_error = _CallbackDispatchError(callback_errors[0], callback_errors)
            if len(callback_errors) == 1:
                raise callback_error from callback_errors[0]
            raise callback_error from ExceptionGroup(
                "Subscriber callback failures", callback_errors
            )

    def ensure_started(self) -> _ServiceTaskHandle:
        loop = asyncio.get_running_loop()
        task = self._service_task
        if task is not None and not task.done() and task.get_loop() is not loop:
            raise RuntimeError("Market data service task is owned by a different event loop")
        self._bind_running_loop()
        if task is not None and not task.done():
            if not self._stopping or task is not self._shutdown_task:
                handle = self._service_handle
                if handle is None or handle._owner is not task:
                    handle = _ServiceTaskHandle(task)
                    self._service_handle = handle
                return handle
        task = loop.create_task(self.start())
        self._service_task = task
        handle = _ServiceTaskHandle(task)
        self._service_handle = handle
        return handle

    async def _poll_feed(self, key: str, started: asyncio.Event) -> None:
        first_poll = True
        retry_delay = self._poll_interval_seconds
        try:
            while first_poll or (self._running and key in self._subscriptions):
                if key not in self._subscriptions:
                    started.set()
                    break
                first_poll = False
                symbol, timeframe = key.rsplit(":", 1)
                started.set()
                sleep_duration = self._poll_interval_seconds
                try:
                    await self._poll_once(symbol, timeframe)
                    retry_delay = self._poll_interval_seconds
                except asyncio.CancelledError:
                    raise
                except _CallbackDispatchError:
                    logger.warning(
                        "Subscriber callback failed while dispatching market data for %s",
                        key,
                        exc_info=True,
                    )
                    retry_delay = self._poll_interval_seconds
                except Exception:
                    sleep_duration = retry_delay
                    retry_delay = min(retry_delay * 2, self._max_retry_delay_seconds)
                if self._running and key in self._subscriptions:
                    await asyncio.sleep(sleep_duration)
        finally:
            if self._subscriptions_changed is not None:
                self._subscriptions_changed.set()

    async def _reconcile_feed_tasks(self) -> None:
        for key, task in list(self._feed_tasks.items()):
            if key not in self._subscriptions:
                task.cancel()
                try:
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                finally:
                    if self._feed_tasks.get(key) is task:
                        self._feed_tasks.pop(key)
                continue
            if task.done():
                if self._feed_tasks.get(key) is task:
                    self._feed_tasks.pop(key)
                await task
        started_events = []
        for key in self._subscriptions:
            if key not in self._feed_tasks:
                started = asyncio.Event()
                task = self._bind_running_loop().create_task(self._poll_feed(key, started))
                self._feed_tasks[key] = task
                started_events.append(started)
        if started_events:
            await asyncio.gather(*(started.wait() for started in started_events))

    async def _cancel_feed_tasks(self) -> None:
        tasks = list(self._feed_tasks.items())
        for _, task in tasks:
            if not task.done():
                task.cancel()
        try:
            if tasks:
                await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
        finally:
            for key, task in tasks:
                if self._feed_tasks.get(key) is task:
                    self._feed_tasks.pop(key)

    async def _run_polling_owner(self) -> None:
        event = self._subscriptions_changed
        assert event is not None
        try:
            while self._running:
                event.clear()
                await self._reconcile_feed_tasks()
                if not self._running:
                    break
                try:
                    await asyncio.wait_for(event.wait(), timeout=1)
                except TimeoutError:
                    pass
        finally:
            await self._cancel_feed_tasks()

    async def start(self) -> None:
        self._bind_running_loop()
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Market data service requires an asyncio task")

        owns_service = False
        try:
            async with self._lifecycle_lock:
                owner = self._service_task
                if owner is not None and owner is not task and not owner.done():
                    return
                self._service_task = task
                owns_service = True
                self._ensure_exchange_open()
                if self._subscriptions_changed is None:
                    self._subscriptions_changed = asyncio.Event()
                self._running = True

            await self._run_polling_owner()
        finally:
            if owns_service and self._service_task is task:
                self._running = False
                self._service_task = None
            if self._service_handle is not None and self._service_handle._owner is task:
                self._service_handle = None

    async def stop(self) -> None:
        current_task = asyncio.current_task()
        if current_task is not None and any(
            current_task is task for task in self._feed_tasks.values()
        ):
            raise RuntimeError(
                "MarketDataService.stop() cannot be awaited from a feed callback or worker"
            )
        self._bind_running_loop()
        async with self._lifecycle_lock:
            self._stopping = True
            self._running = False
            if self._subscriptions_changed is not None:
                self._subscriptions_changed.set()
            task = self._service_task
            self._shutdown_task = task
            current_task = asyncio.current_task()
            try:
                if task is not None and task is not current_task:
                    if task.done():
                        with contextlib.suppress(asyncio.CancelledError):
                            task.exception()
                    else:
                        if not task.cancelling():
                            task.cancel()
                        stop_cancellation_count = (
                            current_task.cancelling() if current_task is not None else 0
                        )
                        try:
                            await asyncio.shield(task)
                        except asyncio.CancelledError:
                            if (
                                current_task is not None
                                and current_task.cancelling() > stop_cancellation_count
                            ):
                                raise
                    if self._service_task is task:
                        self._service_task = None
                else:
                    await self._cancel_feed_tasks()
                self._retire_active_exchange()
                if self._exchange_closed:
                    return
                await self._close_retired_exchanges()
            finally:
                self._shutdown_task = None
                self._stopping = False
