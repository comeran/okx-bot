import asyncio
import contextlib
import threading
from collections import deque
from unittest.mock import AsyncMock, patch

import pytest
from ccxt.base.errors import NotSupported

from src.core.types import Bar
from src.market.health import MARKET_FEED_PUBLIC_MESSAGE
from src.market.service import MarketDataService, _CallbackDispatchError


async def test_poll_once_builds_bars_and_notifies_subscriber():
    with patch("src.market.service.create_okx_client") as ccxt:
        exchange = AsyncMock()
        exchange.watch_ohlcv.return_value = [
            [1700000000000, 50000, 51000, 49000, 50500, 100.5],
            [1700000060000, 50500, 51500, 50000, 51200, 80.25],
        ]
        ccxt.return_value = exchange
        callback = AsyncMock()

        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", callback)

        await service._poll_once("BTC-USDT-SWAP", "1m")

        bars = service.get_recent_bars("BTC-USDT-SWAP", "1m")
        assert len(bars) == 2
        assert bars[0].open == 50000.0
        assert callback.await_count == 2


async def test_poll_once_rejects_malformed_batch_without_committing_partial_state():
    with patch("src.market.service.create_okx_client") as ccxt:
        exchange = AsyncMock()
        valid_row = [1700000000000, 50000, 51000, 49000, 50500, 100.5]
        exchange.watch_ohlcv.side_effect = [
            [valid_row, [1700000060000, 50500, 51500, 50000, 51200]],
            [valid_row],
        ]
        ccxt.return_value = exchange
        callback = AsyncMock()
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", callback)

        with pytest.raises(ValueError, match="exactly 6 values"):
            await service._poll_once("BTC-USDT-SWAP", "1m")

        assert service.get_recent_bars("BTC-USDT-SWAP", "1m") == []
        callback.assert_not_awaited()

        await service._poll_once("BTC-USDT-SWAP", "1m")

    assert len(service.get_recent_bars("BTC-USDT-SWAP", "1m")) == 1
    callback.assert_awaited_once()


async def test_poll_once_falls_back_to_fetch_ohlcv_when_watch_is_unavailable():
    class FetchOnlyExchange:
        def __init__(self) -> None:
            self.fetch_ohlcv_called = False

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            self.fetch_ohlcv_called = True
            assert symbol == "BTC-USDT-SWAP"
            assert timeframe == "1m"
            return [[1700000000000, 50000, 51000, 49000, 50500, 100.5]]

        async def close(self) -> None:
            pass

    with patch("src.market.service.create_okx_client") as ccxt:
        exchange = FetchOnlyExchange()
        ccxt.return_value = exchange
        callback = AsyncMock()

        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", callback)

        await service._poll_once("BTC-USDT-SWAP", "1m")

        assert exchange.fetch_ohlcv_called is True
        assert len(service.get_recent_bars("BTC-USDT-SWAP", "1m")) == 1
        callback.assert_awaited_once()


async def test_poll_once_falls_back_to_fetch_ohlcv_when_watch_is_not_supported():
    class WatchUnsupportedExchange:
        def __init__(self) -> None:
            self.fetch_ohlcv_called = False

        async def watch_ohlcv(self, symbol: str, timeframe: str):
            raise NotSupported("okx watchOHLCV() is not supported yet")

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            self.fetch_ohlcv_called = True
            assert symbol == "BTC-USDT-SWAP"
            assert timeframe == "1m"
            return [[1700000000000, 50000, 51000, 49000, 50500, 100.5]]

        async def close(self) -> None:
            pass

    with patch("src.market.service.create_okx_client") as ccxt:
        exchange = WatchUnsupportedExchange()
        ccxt.return_value = exchange
        callback = AsyncMock()

        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", callback)

        await service._poll_once("BTC-USDT-SWAP", "1m")

        assert exchange.fetch_ohlcv_called is True
        assert len(service.get_recent_bars("BTC-USDT-SWAP", "1m")) == 1
        callback.assert_awaited_once()


async def test_poll_once_raises_when_exchange_has_no_ohlcv_method():
    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = object()
        service = MarketDataService("api-key", "secret", "passphrase")

        with pytest.raises(RuntimeError, match="does not support OHLCV"):
            await service._poll_once("BTC-USDT-SWAP", "1m")


async def test_poll_once_skips_open_candle_without_advancing_timestamp():
    class FetchExchange:
        def __init__(self) -> None:
            self.rows = [
                [
                    [1700000000000, 50000, 51000, 49000, 50500, 100.5],
                    [1700000060000, 50500, 52000, 50000, 51800, 40.0],
                ],
                [[1700000060000, 50500, 51500, 50000, 51200, 80.25]],
            ]

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            return self.rows.pop(0)

        async def close(self) -> None:
            pass

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = FetchExchange()
        callback = AsyncMock()
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", callback)

        service._current_time = lambda: 1700000119.999
        await service._poll_once("BTC-USDT-SWAP", "1m")
        service._current_time = lambda: 1700000120
        await service._poll_once("BTC-USDT-SWAP", "1m")

    bars = service.get_recent_bars("BTC-USDT-SWAP", "1m")
    assert [bar.timestamp for bar in bars] == [1700000000000, 1700000060000]
    assert bars[-1].close == 51200.0
    assert callback.await_count == 2


async def test_poll_once_skips_bars_already_seen_for_subscription():
    class FetchExchange:
        def __init__(self) -> None:
            self.rows = [
                [[1700000000000, 50000, 51000, 49000, 50500, 100.5]],
                [
                    [1700000000000, 50000, 51000, 49000, 50500, 100.5],
                    [1700000060000, 50500, 51500, 50000, 51200, 80.25],
                ],
            ]

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            return self.rows.pop(0)

        async def close(self) -> None:
            pass

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = FetchExchange()
        callback = AsyncMock()
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", callback)

        await service._poll_once("BTC-USDT-SWAP", "1m")
        await service._poll_once("BTC-USDT-SWAP", "1m")

    bars = service.get_recent_bars("BTC-USDT-SWAP", "1m")
    assert [bar.timestamp for bar in bars] == [1700000000000, 1700000060000]
    assert callback.await_count == 2


async def test_feed_worker_uses_capped_retry_backoff_and_resets_after_success(monkeypatch):
    service = MarketDataService("api-key", "secret", "passphrase")
    service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())
    service._running = True
    calls = 0
    sleep_durations = []

    async def poll_once(symbol: str, timeframe: str):
        nonlocal calls
        calls += 1
        if calls <= 5 or calls == 7:
            raise RuntimeError("temporary")

    async def sleep(duration: float):
        sleep_durations.append(duration)
        if calls == 7:
            service._running = False

    monkeypatch.setattr(service, "_poll_once", poll_once)
    monkeypatch.setattr("src.market.service.asyncio.sleep", sleep)

    await service._poll_feed("BTC-USDT-SWAP:1m", asyncio.Event())

    assert sleep_durations == [1, 2, 4, 8, 8, 1, 1]


async def test_feed_worker_splits_derivative_symbol_at_rightmost_separator(monkeypatch):
    service = MarketDataService("api-key", "secret", "passphrase")
    symbol = "BTC/USDT:USDT"
    timeframe = "1m"
    service.subscribe(symbol, timeframe, AsyncMock())
    service._running = True
    calls = []

    async def poll_once(received_symbol: str, received_timeframe: str):
        calls.append((received_symbol, received_timeframe))
        service._running = False

    monkeypatch.setattr(service, "_poll_once", poll_once)

    await service._poll_feed(service._health_key(symbol, timeframe), asyncio.Event())

    assert calls == [(symbol, timeframe)]


async def test_start_waits_between_successful_poll_cycles(monkeypatch):
    class FetchExchange:
        def __init__(self) -> None:
            self.calls = 0

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            self.calls += 1
            if self.calls == 2:
                service._running = False
            return [[1700000000000 + self.calls, 50000, 51000, 49000, 50500, 100.5]]

        async def close(self) -> None:
            pass

    with patch("src.market.service.create_okx_client") as ccxt:
        exchange = FetchExchange()
        ccxt.return_value = exchange
        sleep_durations = []
        service = MarketDataService("api-key", "secret", "passphrase")

        async def sleep(duration):
            sleep_durations.append(duration)
            service._running = False

        monkeypatch.setattr("src.market.service.asyncio.sleep", sleep)
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())

        await service.start()

    assert exchange.calls == 1
    assert sleep_durations == [1]


async def test_unsubscribe_removes_callback_and_unused_subscription_key():
    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = AsyncMock()
        service = MarketDataService("api-key", "secret", "passphrase")
        callback = AsyncMock()

        service.subscribe("BTC-USDT-SWAP", "1m", callback)
        service.unsubscribe("BTC-USDT-SWAP", "1m", callback)

    assert "BTC-USDT-SWAP:1m" not in service._subscriptions


async def test_subscriber_exception_does_not_mark_feed_failure_and_other_callbacks_run():
    with patch("src.market.service.create_okx_client") as ccxt:
        exchange = AsyncMock()
        exchange.watch_ohlcv.return_value = [[1700000000000, 50000, 51000, 49000, 50500, 100.5]]
        ccxt.return_value = exchange
        service = MarketDataService("api-key", "secret", "passphrase")
        service._current_time = lambda: 1700000060
        good_callback = AsyncMock()

        async def bad_callback(bar):
            raise RuntimeError("consumer failed")

        service.subscribe("BTC-USDT-SWAP", "1m", bad_callback)
        service.subscribe("BTC-USDT-SWAP", "1m", good_callback)

        with pytest.raises(RuntimeError, match="consumer failed"):
            await service._poll_once("BTC-USDT-SWAP", "1m")

    good_callback.assert_awaited_once()
    health = service.get_feed_health("BTC-USDT-SWAP", "1m")
    assert health.status == "ready"
    assert health.consecutive_failures == 0
    assert health.error_code is None


async def test_poll_once_preserves_all_callback_exceptions_and_dispatches_every_callback():
    class Exchange:
        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            return [
                [1700000000000, 50000, 51000, 49000, 50500, 100.5],
                [1700000060000, 50500, 51500, 50000, 51200, 80.25],
            ]

        async def close(self) -> None:
            pass

    delivered: list[Bar] = []
    first_error = RuntimeError("first callback failed")
    second_error = ValueError("second callback failed")

    async def first_failing_callback(bar: Bar) -> None:
        raise first_error

    async def second_failing_callback(bar: Bar) -> None:
        raise second_error

    async def succeeding_callback(bar: Bar) -> None:
        delivered.append(bar)

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = Exchange()
        service = MarketDataService("api-key", "secret", "passphrase")
        service._current_time = lambda: 1700000120
        service.subscribe("BTC-USDT-SWAP", "1m", first_failing_callback)
        service.subscribe("BTC-USDT-SWAP", "1m", succeeding_callback)
        service.subscribe("BTC-USDT-SWAP", "1m", second_failing_callback)

        with pytest.raises(_CallbackDispatchError) as raised:
            await service._poll_once("BTC-USDT-SWAP", "1m")

    assert [bar.timestamp for bar in delivered] == [1700000000000, 1700000060000]
    assert isinstance(raised.value.__cause__, ExceptionGroup)
    assert raised.value.__cause__.exceptions == (
        first_error,
        second_error,
        first_error,
        second_error,
    )
    assert raised.value.original is first_error
    health = service.get_feed_health("BTC-USDT-SWAP", "1m")
    assert health.status == "ready"
    assert health.consecutive_failures == 0
    assert health.error_code is None


async def test_feed_worker_uses_initial_retrieval_backoff_after_callback_only_failure(
    monkeypatch,
):
    service = MarketDataService("api-key", "secret", "passphrase")
    service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())
    service._running = True
    calls = 0
    sleep_durations: list[float] = []

    async def poll_once(symbol: str, timeframe: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            callback_error = RuntimeError("callback failed")
            raise _CallbackDispatchError(callback_error) from callback_error
        raise RuntimeError("retrieval failed")

    async def sleep(duration: float) -> None:
        sleep_durations.append(duration)
        if len(sleep_durations) == 2:
            service._running = False

    monkeypatch.setattr(service, "_poll_once", poll_once)
    monkeypatch.setattr("src.market.service.asyncio.sleep", sleep)

    await service._poll_feed("BTC-USDT-SWAP:1m", asyncio.Event())

    assert sleep_durations == [1, 1]


async def test_feed_worker_keeps_ordinary_interval_after_callback_only_failures(
    monkeypatch, caplog
):
    class Exchange:
        def __init__(self) -> None:
            self.calls = 0

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            self.calls += 1
            return [
                [
                    1700000000000 + self.calls * 60000,
                    50000,
                    51000,
                    49000,
                    50500,
                    100.5,
                ]
            ]

        async def close(self) -> None:
            pass

    delivered: list[Bar] = []
    sleep_durations: list[float] = []
    callback_error = RuntimeError("consumer failed every time")

    async def failing_callback(bar: Bar) -> None:
        raise callback_error

    async def succeeding_callback(bar: Bar) -> None:
        delivered.append(bar)

    async def sleep(duration: float):
        sleep_durations.append(duration)
        if len(sleep_durations) == 3:
            service._running = False

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = Exchange()
        service = MarketDataService("api-key", "secret", "passphrase")
        service._current_time = lambda: 1700000299.999
        service.subscribe("BTC-USDT-SWAP", "1m", failing_callback)
        service.subscribe("BTC-USDT-SWAP", "1m", succeeding_callback)
        service._running = True
        monkeypatch.setattr("src.market.service.asyncio.sleep", sleep)

        await service._poll_feed("BTC-USDT-SWAP:1m", asyncio.Event())

    assert [bar.timestamp for bar in delivered] == [
        1700000060000,
        1700000120000,
        1700000180000,
    ]
    assert sleep_durations == [1, 1, 1]
    health = service.get_feed_health("BTC-USDT-SWAP", "1m")
    assert health.status == "ready"
    assert health.consecutive_failures == 0
    assert health.error_code is None
    assert any(
        record.exc_info is not None
        and record.exc_info[1] is not None
        and record.exc_info[1].__cause__ is callback_error
        for record in caplog.records
    )


async def test_poll_once_skips_callback_unsubscribed_during_same_dispatch():
    with patch("src.market.service.create_okx_client") as ccxt:
        exchange = AsyncMock()
        exchange.watch_ohlcv.return_value = [[1700000000000, 50000, 51000, 49000, 50500, 100.5]]
        ccxt.return_value = exchange
        service = MarketDataService("api-key", "secret", "passphrase")
        callback_b = AsyncMock()

        async def callback_a(bar):
            service.unsubscribe("BTC-USDT-SWAP", "1m", callback_b)

        service.subscribe("BTC-USDT-SWAP", "1m", callback_a)
        service.subscribe("BTC-USDT-SWAP", "1m", callback_b)

        await service._poll_once("BTC-USDT-SWAP", "1m")

    callback_b.assert_not_awaited()


async def test_poll_once_recreates_exchange_after_stop_before_polling():
    class Exchange:
        def __init__(self, rows):
            self.rows = rows
            self.closed = False
            self.fetch_calls = 0

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            if self.closed:
                raise AssertionError("closed exchange was polled")
            self.fetch_calls += 1
            return self.rows

        async def close(self) -> None:
            self.closed = True

    with patch("src.market.service.create_okx_client") as ccxt:
        first_exchange = Exchange([[1700000000000, 50000, 51000, 49000, 50500, 100.5]])
        second_exchange = Exchange([[1700000060000, 50500, 51500, 50000, 51200, 80.25]])
        ccxt.side_effect = [first_exchange, second_exchange]
        callback = AsyncMock()
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", callback)

        await service._poll_once("BTC-USDT-SWAP", "1m")
        await service.stop()
        await service.stop()
        await service._poll_once("BTC-USDT-SWAP", "1m")

    assert first_exchange.closed is True
    assert first_exchange.fetch_calls == 1
    assert second_exchange.fetch_calls == 1
    assert ccxt.call_count == 2
    assert callback.await_count == 2


async def test_subscribe_accepts_supported_two_hour_timeframe():
    with patch("src.market.service.create_okx_client"):
        service = MarketDataService("api-key", "secret", "passphrase")
        callback = AsyncMock()

        service.subscribe("BTC-USDT-SWAP", "2h", callback)

    assert service._subscriptions == {"BTC-USDT-SWAP:2h": [callback]}
    assert set(service._buffers) == {"BTC-USDT-SWAP:2h"}
    assert set(service._health) == {"BTC-USDT-SWAP:2h"}
    assert set(service._health_conditions) == {"BTC-USDT-SWAP:2h"}


@pytest.mark.parametrize("timeframe", ["7m", "invalid", "1M", "3M"])
async def test_subscribe_rejects_unsupported_timeframe_without_mutating_state(timeframe):
    with patch("src.market.service.create_okx_client"):
        service = MarketDataService("api-key", "secret", "passphrase")
        notification = asyncio.Event()
        service._subscriptions_changed = notification

        with pytest.raises(ValueError, match="Unsupported OKX runtime timeframe"):
            service.subscribe("BTC-USDT-SWAP", timeframe, AsyncMock())

    assert service._subscriptions == {}
    assert service._buffers == {}
    assert service._last_bar_timestamps == {}
    assert service._health == {}
    assert service._health_conditions == {}
    assert service._feed_tasks == {}
    assert service._subscriptions_changed is notification
    assert notification.is_set() is False
    assert service._loop is None


@pytest.mark.parametrize("timeframe", ["7m", "invalid", "1M", "3M"])
async def test_wait_until_ready_rejects_unsupported_timeframe_before_binding_loop(timeframe):
    with patch("src.market.service.create_okx_client"):
        service = MarketDataService("api-key", "secret", "passphrase")

        with patch.object(
            service,
            "_bind_running_loop",
            side_effect=AssertionError("loop binding must not run"),
        ) as bind_loop:
            with pytest.raises(ValueError, match="Unsupported OKX runtime timeframe"):
                await service.wait_until_ready(
                    "BTC-USDT-SWAP",
                    timeframe,
                    timeout=60,
                )

    bind_loop.assert_not_called()
    assert service._loop is None
    assert service._buffers == {}
    assert service._health == {}
    assert service._health_conditions == {}


@pytest.mark.parametrize("timeframe", ["7m", "invalid", "1M", "3M"])
async def test_poll_once_rejects_unsupported_timeframe_before_exchange_or_health_access(
    timeframe,
):
    with patch("src.market.service.create_okx_client") as ccxt:
        exchange = AsyncMock()
        ccxt.return_value = exchange
        service = MarketDataService("api-key", "secret", "passphrase")
        service._exchange_usable = False
        service._exchange_closed = True
        listener = AsyncMock()
        service.add_health_listener(listener)

        with pytest.raises(ValueError, match="Unsupported OKX runtime timeframe"):
            await service._poll_once("BTC-USDT-SWAP", timeframe)

    assert service._loop is None
    assert service._buffers == {}
    assert service._last_bar_timestamps == {}
    assert service._health == {}
    assert service._health_conditions == {}
    assert service._feed_tasks == {}
    assert ccxt.call_count == 1
    exchange.watch_ohlcv.assert_not_awaited()
    exchange.fetch_ohlcv.assert_not_awaited()
    listener.assert_not_awaited()


async def test_subscribe_ignores_duplicate_callback_for_same_market():
    with patch("src.market.service.create_okx_client") as ccxt:
        exchange = AsyncMock()
        exchange.watch_ohlcv.return_value = [[1700000000000, 50000, 51000, 49000, 50500, 100.5]]
        ccxt.return_value = exchange
        service = MarketDataService("api-key", "secret", "passphrase")
        callback = AsyncMock()

        service.subscribe("BTC-USDT-SWAP", "1m", callback)
        service.subscribe("BTC-USDT-SWAP", "1m", callback)
        await service._poll_once("BTC-USDT-SWAP", "1m")

    callback.assert_awaited_once()


async def test_callback_deduplication_and_unsubscribe_use_identity_not_equality():
    class EqualCallback:
        def __init__(self) -> None:
            self.received: list[Bar] = []

        def __eq__(self, other: object) -> bool:
            return isinstance(other, EqualCallback)

        async def __call__(self, bar: Bar) -> None:
            self.received.append(bar)

    class Exchange:
        def __init__(self) -> None:
            self.calls = 0

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            self.calls += 1
            return [
                [
                    1700000000000 + self.calls * 60000,
                    50000,
                    51000,
                    49000,
                    50500,
                    100.5,
                ]
            ]

        async def close(self) -> None:
            pass

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = Exchange()
        service = MarketDataService("api-key", "secret", "passphrase")
        first = EqualCallback()
        second = EqualCallback()

        service.subscribe("BTC-USDT-SWAP", "1m", first)
        service.subscribe("BTC-USDT-SWAP", "1m", second)
        service.subscribe("BTC-USDT-SWAP", "1m", second)

        callbacks = service._subscriptions["BTC-USDT-SWAP:1m"]
        assert len(callbacks) == 2
        assert callbacks[0] is first
        assert callbacks[1] is second

        await service._poll_once("BTC-USDT-SWAP", "1m")
        assert len(first.received) == 1
        assert len(second.received) == 1

        service.unsubscribe("BTC-USDT-SWAP", "1m", first)
        callbacks = service._subscriptions["BTC-USDT-SWAP:1m"]
        assert len(callbacks) == 1
        assert callbacks[0] is second

        await service._poll_once("BTC-USDT-SWAP", "1m")

    assert len(first.received) == 1
    assert len(second.received) == 2


async def test_get_recent_bars_returns_requested_tail_from_buffer():
    service = MarketDataService("api-key", "secret", "passphrase")
    key = "BTC-USDT-SWAP:1m"
    service._buffers[key] = deque(maxlen=100)
    for index in range(5):
        service._buffers[key].append(
            Bar(
                timestamp=index,
                open=float(index),
                high=float(index),
                low=float(index),
                close=float(index),
                volume=float(index),
            )
        )

    bars = service.get_recent_bars("BTC-USDT-SWAP", "1m", count=3)

    assert len(bars) == 3
    assert [bar.timestamp for bar in bars] == [2, 3, 4]


async def test_subscription_starts_pending_and_success_becomes_ready():
    class Exchange:
        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            assert symbol == "BTC-USDT-SWAP"
            assert timeframe == "1m"
            return [[1700000000000, 50000, 51000, 49000, 50500, 100.5]]

        async def close(self) -> None:
            pass

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = Exchange()
        service = MarketDataService("api-key", "secret", "passphrase")
        service._current_time = lambda: 1700000060
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())

        pending = service.get_feed_health("BTC-USDT-SWAP", "1m")
        assert pending.status == "pending"
        assert pending.buffered_bars == 0

        await service._poll_once("BTC-USDT-SWAP", "1m")

        ready = service.get_feed_health("BTC-USDT-SWAP", "1m")
        assert ready.status == "ready"
        assert ready.buffered_bars == 1
        assert ready.last_bar_timestamp == 1700000000000
        assert [bar.timestamp for bar in service.get_recent_bars("BTC-USDT-SWAP", "1m")] == [
            1700000000000
        ]


async def test_feed_health_transitions_and_recovery_preserve_total_failures():
    class Exchange:
        def __init__(self) -> None:
            self.calls = 0

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            self.calls += 1
            if self.calls == 1:
                return [[1700000000000, 50000, 51000, 49000, 50500, 100.5]]
            if self.calls <= 11:
                raise RuntimeError("sensitive failure details")
            return [[1700000060000, 50500, 51500, 50000, 51200, 80.25]]

        async def close(self) -> None:
            pass

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = Exchange()
        service = MarketDataService("api-key", "secret", "passphrase")
        service._current_time = lambda: 1700000060
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())

        await service._poll_once("BTC-USDT-SWAP", "1m")
        for failure_count in range(1, 11):
            with pytest.raises(RuntimeError, match="sensitive failure details"):
                await service._poll_once("BTC-USDT-SWAP", "1m")
            health = service.get_feed_health("BTC-USDT-SWAP", "1m")
            assert health.consecutive_failures == failure_count
            assert health.total_failures == failure_count
            assert health.error_code == "market_feed_poll_failed"
            assert (
                health.public_message
                == "Market data feed is temporarily unavailable. Please retry shortly."
            )
            if failure_count <= 2:
                assert health.status == "ready"
            elif failure_count < 10:
                assert health.status == "degraded"
            else:
                assert health.status == "unavailable"

        service._current_time = lambda: 1700000120
        await service._poll_once("BTC-USDT-SWAP", "1m")

        health = service.get_feed_health("BTC-USDT-SWAP", "1m")
        assert health.status == "ready"
        assert health.consecutive_failures == 0
        assert health.total_failures == 10
        assert health.error_code is None
        assert health.public_message is None
        assert health.last_bar_timestamp == 1700000060000


@pytest.mark.parametrize("payload", [None, 123], ids=["none", "scalar"])
async def test_non_iterable_ohlcv_payload_records_sanitized_poll_failure(payload):
    class Exchange:
        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            return payload

        async def close(self) -> None:
            pass

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = Exchange()
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())

        with pytest.raises(TypeError):
            await service._poll_once("BTC-USDT-SWAP", "1m")

    health = service.get_feed_health("BTC-USDT-SWAP", "1m")
    assert health.status == "pending"
    assert health.consecutive_failures == 1
    assert health.total_failures == 1
    assert health.generation == 1
    assert health.error_code == "market_feed_poll_failed"
    assert health.public_message == MARKET_FEED_PUBLIC_MESSAGE


async def test_ohlcv_iteration_failure_records_sanitized_poll_failure():
    class FailingRows:
        def __iter__(self):
            return self

        def __next__(self):
            raise RuntimeError("iteration failed")

    class Exchange:
        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            return FailingRows()

        async def close(self) -> None:
            pass

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = Exchange()
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())

        with pytest.raises(RuntimeError, match="iteration failed"):
            await service._poll_once("BTC-USDT-SWAP", "1m")

    health = service.get_feed_health("BTC-USDT-SWAP", "1m")
    assert health.status == "pending"
    assert health.consecutive_failures == 1
    assert health.total_failures == 1
    assert health.generation == 1
    assert health.error_code == "market_feed_poll_failed"
    assert health.public_message == MARKET_FEED_PUBLIC_MESSAGE


async def test_malformed_ohlcv_row_records_sanitized_poll_failure():
    class Exchange:
        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            return [[1700000000000, 50000, 51000, 49000, "not-a-number", 100.5]]

        async def close(self) -> None:
            pass

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = Exchange()
        service = MarketDataService("api-key", "secret", "passphrase")
        service._current_time = lambda: 1700000060
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())

        with pytest.raises(ValueError, match="not-a-number"):
            await service._poll_once("BTC-USDT-SWAP", "1m")

    bars = service.get_recent_bars("BTC-USDT-SWAP", "1m")
    assert bars == []
    health = service.get_feed_health("BTC-USDT-SWAP", "1m")
    assert health.status == "pending"
    assert health.consecutive_failures == 1
    assert health.total_failures == 1
    assert health.generation == 1
    assert health.error_code == "market_feed_poll_failed"
    assert health.public_message == MARKET_FEED_PUBLIC_MESSAGE


@pytest.mark.parametrize(
    ("rows", "expected_exception", "expected_bars", "expected_status"),
    [
        pytest.param(
            [
                [1699999940000, 50000, 51000, 49000, 50500, 100.5],
                [1700000000000, 50000, 51000, 49000, "not-a-number", 100.5],
            ],
            ValueError,
            0,
            "pending",
            id="valid-then-malformed",
        ),
        pytest.param(
            [
                [1700000000000, 50000, 51000, 49000, 50500, 100.5],
                [1700000000000, 50000, 51000, 49000, "not-a-number", 100.5],
            ],
            ValueError,
            0,
            "pending",
            id="malformed-duplicate",
        ),
        pytest.param(
            [[1700000060000, 50000, 51000, 49000, "not-a-number", 100.5]],
            ValueError,
            0,
            "pending",
            id="malformed-open",
        ),
        pytest.param(
            [[1700000000000, 50000, 51000, 49000, 50500, 100.5, "extra"]],
            ValueError,
            0,
            "pending",
            id="overlong-row",
        ),
        pytest.param(
            [[1700000000000, "nan", 51000, 49000, 50500, 100.5]],
            ValueError,
            0,
            "pending",
            id="non-finite-open",
        ),
        pytest.param(
            [[1700000000000, 50000, "inf", 49000, 50500, 100.5]],
            ValueError,
            0,
            "pending",
            id="non-finite-high",
        ),
        pytest.param(
            [[1700000000000, 50000, 51000, "-inf", 50500, 100.5]],
            ValueError,
            0,
            "pending",
            id="non-finite-low",
        ),
        pytest.param(
            [[1700000000000, 50000, 51000, 49000, "NaN", 100.5]],
            ValueError,
            0,
            "pending",
            id="non-finite-close",
        ),
        pytest.param(
            [[1700000000000, 50000, 51000, 49000, 50500, "inf"]],
            ValueError,
            0,
            "pending",
            id="non-finite-volume",
        ),
        pytest.param(
            [[1700000000000, 50000]],
            ValueError,
            0,
            "pending",
            id="short-row",
        ),
        pytest.param(
            [["not-a-timestamp", 50000, 51000, 49000, 50500, 100.5]],
            ValueError,
            0,
            "pending",
            id="invalid-timestamp",
        ),
    ],
)
async def test_malformed_ohlcv_rows_are_not_silently_skipped(
    rows, expected_exception, expected_bars, expected_status
):
    class Exchange:
        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            return rows

        async def close(self) -> None:
            pass

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = Exchange()
        service = MarketDataService("api-key", "secret", "passphrase")
        service._current_time = lambda: 1700000060
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())

        with pytest.raises(expected_exception):
            await service._poll_once("BTC-USDT-SWAP", "1m")

    assert len(service.get_recent_bars("BTC-USDT-SWAP", "1m")) == expected_bars
    health = service.get_feed_health("BTC-USDT-SWAP", "1m")
    assert health.status == expected_status
    assert health.consecutive_failures == 1
    assert health.total_failures == 1
    assert health.generation == 1
    assert health.error_code == "market_feed_poll_failed"
    assert health.public_message == MARKET_FEED_PUBLIC_MESSAGE


async def test_public_health_snapshots_do_not_leak_raw_exception_details():
    class Exchange:
        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            raise RuntimeError("raw secret token 42")

        async def close(self) -> None:
            pass

    snapshots: list[object] = []

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = Exchange()
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())
        unregister = service.add_health_listener(snapshots.append)

        with pytest.raises(RuntimeError, match="raw secret token 42"):
            await service._poll_once("BTC-USDT-SWAP", "1m")

        snapshot = service.get_feed_health("BTC-USDT-SWAP", "1m")
        assert snapshot.error_code == "market_feed_poll_failed"
        assert (
            snapshot.public_message
            == "Market data feed is temporarily unavailable. Please retry shortly."
        )
        assert "raw secret token 42" not in repr(snapshot)
        assert "RuntimeError" not in repr(snapshot)
        assert snapshots and snapshots[-1] == snapshot
        assert "raw secret token 42" not in repr(snapshots[-1])

        unregister()


async def test_failing_feed_does_not_block_other_feed_polling():
    class Exchange:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            self.calls.append(symbol)
            if symbol == "BROKEN-USDT-SWAP":
                raise RuntimeError("broken feed")
            return [[1700000000000, 50000, 51000, 49000, 50500, 100.5]]

        async def close(self) -> None:
            pass

    async def stop_after_cycle(duration: float):
        service._running = False

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = Exchange()
        service = MarketDataService("api-key", "secret", "passphrase")
        service._current_time = lambda: 1700000060
        broken_callback = AsyncMock()
        good_callback = AsyncMock()
        service.subscribe("BROKEN-USDT-SWAP", "1m", broken_callback)
        service.subscribe("GOOD-USDT-SWAP", "1m", good_callback)

        with patch("src.market.service.asyncio.sleep", stop_after_cycle):
            await service.start()

        assert good_callback.await_count == 1
        assert service.get_feed_health("GOOD-USDT-SWAP", "1m").status == "ready"
        assert service.get_feed_health("BROKEN-USDT-SWAP", "1m").status == "pending"


async def test_success_at_exact_next_close_boundary_is_stale_not_ready():
    class Exchange:
        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            return [[1700000000000, 50000, 51000, 49000, 50500, 100.5]]

        async def close(self) -> None:
            pass

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = Exchange()
        service = MarketDataService("api-key", "secret", "passphrase")
        service._current_time = lambda: 1700000120
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())

        await service._poll_once("BTC-USDT-SWAP", "1m")

    health = service.get_feed_health("BTC-USDT-SWAP", "1m")
    assert health.status == "degraded"
    with pytest.raises(TimeoutError):
        await service.wait_until_ready("BTC-USDT-SWAP", "1m", timeout=0)


async def test_wait_until_ready_ignores_stale_duplicate_only_success():
    class Exchange:
        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            return [[1700000000000, 50000, 51000, 49000, 50500, 100.5]]

        async def close(self) -> None:
            pass

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = Exchange()
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())

        service._current_time = lambda: 1700000060
        await service._poll_once("BTC-USDT-SWAP", "1m")
        assert service.get_feed_health("BTC-USDT-SWAP", "1m").status == "ready"

        waiter = asyncio.create_task(
            service.wait_until_ready("BTC-USDT-SWAP", "1m", timeout=0.01, min_bars=1)
        )
        service._current_time = lambda: 1700000180.001
        await service._poll_once("BTC-USDT-SWAP", "1m")

        with pytest.raises(TimeoutError):
            await waiter
        health = service.get_feed_health("BTC-USDT-SWAP", "1m")
        assert health.status == "degraded"
        assert health.consecutive_failures == 0
        assert health.error_code is None


async def test_wait_until_ready_wakes_when_min_bars_arrive_and_validates_min_bars():
    class Exchange:
        def __init__(self) -> None:
            self.calls = 0

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            self.calls += 1
            if self.calls == 1:
                return [[1700000000000, 50000, 51000, 49000, 50500, 100.5]]
            return [[1700000060000, 50500, 51500, 50000, 51200, 80.25]]

        async def close(self) -> None:
            pass

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = Exchange()
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())

        waiter = asyncio.create_task(
            service.wait_until_ready("BTC-USDT-SWAP", "1m", timeout=1, min_bars=2)
        )
        service._current_time = lambda: 1700000060
        await service._poll_once("BTC-USDT-SWAP", "1m")
        assert not waiter.done()
        service._current_time = lambda: 1700000120
        await service._poll_once("BTC-USDT-SWAP", "1m")

        health = await waiter
        assert health.buffered_bars == 2
        assert [bar.timestamp for bar in service.get_recent_bars("BTC-USDT-SWAP", "1m")] == [
            1700000000000,
            1700000060000,
        ]

        with pytest.raises(ValueError):
            await service.wait_until_ready("BTC-USDT-SWAP", "1m", min_bars=0)


async def test_wait_until_ready_times_out_without_publications():
    service = MarketDataService("api-key", "secret", "passphrase")

    with pytest.raises(TimeoutError):
        await service.wait_until_ready("BTC-USDT-SWAP", "1m", timeout=0.01)


async def test_health_listener_receives_updates_and_unregister_is_idempotent():
    class Exchange:
        def __init__(self) -> None:
            self.calls = 0

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            self.calls += 1
            return [[1700000000000 + self.calls * 60000, 50000, 51000, 49000, 50500, 100.5]]

        async def close(self) -> None:
            pass

    snapshots: list[object] = []

    def good_listener(snapshot):
        snapshots.append(snapshot)

    def bad_listener(snapshot):
        raise RuntimeError("listener boom")

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = Exchange()
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())
        unregister_good = service.add_health_listener(good_listener)
        unregister_bad = service.add_health_listener(bad_listener)

        await service._poll_once("BTC-USDT-SWAP", "1m")
        assert len(snapshots) == 1

        unregister_good()
        unregister_good()
        unregister_bad()
        await service._poll_once("BTC-USDT-SWAP", "1m")
        assert len(snapshots) == 1


async def test_ensure_started_reuses_live_task_and_replaces_stale_tasks():
    class Exchange:
        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            await asyncio.sleep(3600)
            return [[1700000000000, 50000, 51000, 49000, 50500, 100.5]]

        async def close(self) -> None:
            pass

    async def boom() -> None:
        raise RuntimeError("boom")

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = Exchange()
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())

        live_task = service.ensure_started()
        assert service.ensure_started() is live_task
        await asyncio.sleep(0)
        await service.stop()

        done_task = asyncio.create_task(asyncio.sleep(0))
        await done_task
        service._service_task = done_task
        replacement = service.ensure_started()
        assert replacement is not done_task
        await service.stop()

        failed_task = asyncio.create_task(boom())
        with pytest.raises(RuntimeError):
            await failed_task
        service._service_task = failed_task
        replacement = service.ensure_started()
        assert replacement is not failed_task
        await service.stop()

        cancelled_task = asyncio.create_task(asyncio.sleep(3600))
        cancelled_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cancelled_task
        service._service_task = cancelled_task
        replacement = service.ensure_started()
        assert replacement is not cancelled_task
        await service.stop()


async def test_ensure_started_handle_isolates_waiter_cancellation():
    class BlockingExchange:
        def __init__(self) -> None:
            self.fetch_started = asyncio.Event()
            self.close_calls = 0

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            self.fetch_started.set()
            await asyncio.Event().wait()

        async def close(self) -> None:
            self.close_calls += 1

    async def wait_for(handle) -> None:
        await handle

    with patch("src.market.service.create_okx_client") as ccxt:
        exchange = BlockingExchange()
        ccxt.return_value = exchange
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())

        handle = service.ensure_started()
        assert service.ensure_started() is handle
        first_waiter = asyncio.create_task(wait_for(handle))
        second_waiter = asyncio.create_task(wait_for(handle))
        await exchange.fetch_started.wait()

        first_waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first_waiter

        assert not handle.done()
        assert not handle.cancelled()
        assert not second_waiter.done()

        await service.stop()
        with pytest.raises(asyncio.CancelledError):
            await second_waiter

    assert exchange.close_calls == 1


async def test_ensure_started_handle_cancel_explicitly_cancels_owner():
    class BlockingExchange:
        def __init__(self) -> None:
            self.fetch_started = asyncio.Event()
            self.worker_finished = asyncio.Event()
            self.close_calls = 0

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            self.fetch_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.worker_finished.set()

        async def close(self) -> None:
            assert self.worker_finished.is_set()
            self.close_calls += 1

    with patch("src.market.service.create_okx_client") as ccxt:
        exchange = BlockingExchange()
        ccxt.return_value = exchange
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())

        handle = service.ensure_started()
        await exchange.fetch_started.wait()

        assert handle.cancel() is True
        with pytest.raises(asyncio.CancelledError):
            await handle
        assert handle.done()
        assert handle.cancelled()
        assert handle.get_loop() is asyncio.get_running_loop()

        await service.stop()

    assert exchange.close_calls == 1


async def test_start_clears_running_on_cancellation_and_unexpected_exit():
    class Exchange:
        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            await asyncio.sleep(3600)
            return [[1700000000000, 50000, 51000, 49000, 50500, 100.5]]

        async def close(self) -> None:
            pass

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = Exchange()
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())

        task = asyncio.create_task(service.start())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service._running is False

    class FailingSleepExchange:
        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            return [[1700000000000, 50000, 51000, 49000, 50500, 100.5]]

        async def close(self) -> None:
            pass

    async def boom_sleep(duration: float):
        raise RuntimeError("sleep failed")

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = FailingSleepExchange()
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())
        with patch("src.market.service.asyncio.sleep", boom_sleep):
            with pytest.raises(RuntimeError, match="sleep failed"):
                await service.start()
        assert service._running is False


async def test_stop_cancels_owned_task_closes_once_and_recreates_exchange_on_restart():
    class BlockingExchange:
        def __init__(self) -> None:
            self.close_calls = 0

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            await asyncio.sleep(3600)
            return [[1700000000000, 50000, 51000, 49000, 50500, 100.5]]

        async def close(self) -> None:
            self.close_calls += 1

    class ReadyExchange:
        def __init__(self) -> None:
            self.close_calls = 0
            self.fetch_calls = 0

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            self.fetch_calls += 1
            return [[1700000060000, 50500, 51500, 50000, 51200, 80.25]]

        async def close(self) -> None:
            self.close_calls += 1

    with patch("src.market.service.create_okx_client") as ccxt:
        first_exchange = BlockingExchange()
        second_exchange = ReadyExchange()
        ccxt.side_effect = [first_exchange, second_exchange]
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())

        task = service.ensure_started()
        await asyncio.sleep(0)
        await service.stop()
        assert task.done()
        assert first_exchange.close_calls == 1

        await service.stop()
        assert first_exchange.close_calls == 1

        await service._poll_once("BTC-USDT-SWAP", "1m")
        assert ccxt.call_count == 2
        assert second_exchange.fetch_calls == 1
        assert second_exchange.close_calls == 0


async def test_health_query_initializes_normal_bounded_buffer_for_later_polling():
    class Exchange:
        def __init__(self) -> None:
            self.calls = 0

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            self.calls += 1
            return [
                [1700000000000 + index * 60000, 50000, 51000, 49000, 50500, 100.5]
                for index in range(1001)
            ]

        async def close(self) -> None:
            pass

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = Exchange()
        service = MarketDataService("api-key", "secret", "passphrase")

        health = service.get_feed_health("BTC-USDT-SWAP", "1m")
        assert health.status == "pending"
        assert service._buffers["BTC-USDT-SWAP:1m"].maxlen == 1000

        await service._poll_once("BTC-USDT-SWAP", "1m")

    buffer = service._buffers["BTC-USDT-SWAP:1m"]
    assert buffer.maxlen == 1000
    assert len(buffer) == 1000
    assert buffer[0].timestamp == 1700000060000


async def test_callback_failure_publishes_current_sanitized_metadata_and_wakes_waiters():
    class Exchange:
        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            return [[1700000000000, 50000, 51000, 49000, 50500, 100.5]]

        async def close(self) -> None:
            pass

    async def failing_callback(bar: Bar) -> None:
        raise RuntimeError("callback leaked secret")

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = Exchange()
        service = MarketDataService("api-key", "secret", "passphrase")
        service._current_time = lambda: 1700000060
        service.subscribe("BTC-USDT-SWAP", "1m", failing_callback)
        waiter = asyncio.create_task(
            service.wait_until_ready("BTC-USDT-SWAP", "1m", timeout=1, min_bars=1)
        )

        with pytest.raises(RuntimeError, match="callback leaked secret"):
            await service._poll_once("BTC-USDT-SWAP", "1m")

        health = service.get_feed_health("BTC-USDT-SWAP", "1m")
        assert health.status == "ready"
        assert health.buffered_bars == 1
        assert health.last_bar_timestamp == 1700000000000
        assert health.error_code is None
        assert health.public_message is None
        assert "callback leaked secret" not in repr(health)
        assert await waiter == health


async def test_cancelling_stop_caller_during_owner_cleanup_is_propagated_and_retryable():
    class SlowCleanupExchange:
        def __init__(self) -> None:
            self.fetch_started = asyncio.Event()
            self.cleanup_started = asyncio.Event()
            self.release_cleanup = asyncio.Event()
            self.cleanup_finished = asyncio.Event()
            self.close_calls = 0

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            self.fetch_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.cleanup_started.set()
                await self.release_cleanup.wait()
                self.cleanup_finished.set()

        async def close(self) -> None:
            assert self.cleanup_finished.is_set()
            self.close_calls += 1

    with patch("src.market.service.create_okx_client") as ccxt:
        exchange = SlowCleanupExchange()
        ccxt.return_value = exchange
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())
        service.ensure_started()
        await exchange.fetch_started.wait()

        stopping = asyncio.create_task(service.stop())
        await exchange.cleanup_started.wait()
        stopping.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stopping

        assert exchange.close_calls == 0
        later_stop = asyncio.create_task(service.stop())
        await asyncio.sleep(0)
        assert not later_stop.done()

        exchange.release_cleanup.set()
        await later_stop

    assert exchange.cleanup_finished.is_set()
    assert exchange.close_calls == 1
    assert service._exchange_closed is True


async def test_stop_ignores_stale_cancellation_count_from_earlier_caught_cancellation():
    class BlockingExchange:
        def __init__(self) -> None:
            self.fetch_started = asyncio.Event()
            self.worker_finished = asyncio.Event()
            self.close_calls = 0

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            self.fetch_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.worker_finished.set()

        async def close(self) -> None:
            assert self.worker_finished.is_set()
            self.close_calls += 1

    prior_wait_started = asyncio.Event()
    prior_cancellation_caught = asyncio.Event()

    async def stop_after_catching_prior_cancellation() -> None:
        try:
            prior_wait_started.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            prior_cancellation_caught.set()
        task = asyncio.current_task()
        assert task is not None
        assert task.cancelling() == 1
        await service.stop()

    with patch("src.market.service.create_okx_client") as ccxt:
        exchange = BlockingExchange()
        ccxt.return_value = exchange
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())
        service.ensure_started()
        await exchange.fetch_started.wait()

        stopping = asyncio.create_task(stop_after_catching_prior_cancellation())
        await prior_wait_started.wait()
        stopping.cancel()
        await prior_cancellation_caught.wait()
        await stopping

    assert exchange.close_calls == 1
    assert service._exchange_closed is True
    assert service._running is False
    assert service._service_task is None
    assert service._feed_tasks == {}


async def test_concurrent_stop_closes_exchange_once_and_both_callers_complete():
    class BlockingCloseExchange:
        def __init__(self) -> None:
            self.close_calls = 0
            self.close_started = asyncio.Event()
            self.release_close = asyncio.Event()

        async def close(self) -> None:
            self.close_calls += 1
            self.close_started.set()
            await self.release_close.wait()

    with patch("src.market.service.create_okx_client") as ccxt:
        exchange = BlockingCloseExchange()
        ccxt.return_value = exchange
        service = MarketDataService("api-key", "secret", "passphrase")

        first_stop = asyncio.create_task(service.stop())
        await exchange.close_started.wait()
        second_stop = asyncio.create_task(service.stop())
        await asyncio.sleep(0)
        exchange.release_close.set()
        await asyncio.gather(first_stop, second_stop)

    assert exchange.close_calls == 1
    assert service._exchange_closed is True


def test_ensure_started_without_running_loop_leaves_service_stopped():
    service = MarketDataService("api-key", "secret", "passphrase")

    with pytest.raises(RuntimeError):
        service.ensure_started()

    assert service._running is False
    assert service._service_task is None


async def test_exchange_recreation_failure_publishes_single_sanitized_failure():
    snapshots: list[object] = []

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.side_effect = [AsyncMock(), RuntimeError("raw secret recreation token")]
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())
        service.add_health_listener(snapshots.append)
        service._exchange_closed = True

        with pytest.raises(RuntimeError, match="raw secret recreation token"):
            await service._poll_once("BTC-USDT-SWAP", "1m")

    health = service.get_feed_health("BTC-USDT-SWAP", "1m")
    assert health.status == "pending"
    assert health.consecutive_failures == 1
    assert health.total_failures == 1
    assert health.generation == 1
    assert health.error_code == "market_feed_poll_failed"
    assert (
        health.public_message
        == "Market data feed is temporarily unavailable. Please retry shortly."
    )
    assert "raw secret recreation token" not in repr(health)
    assert snapshots == [health]


async def test_generation_increments_on_repeated_success_and_failure_publications():
    class Exchange:
        def __init__(self) -> None:
            self.calls = 0

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            self.calls += 1
            if self.calls <= 2:
                return [[1700000000000 + self.calls * 60000, 50000, 51000, 49000, 50500, 100.5]]
            raise RuntimeError("boom")

        async def close(self) -> None:
            pass

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = Exchange()
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())

        await service._poll_once("BTC-USDT-SWAP", "1m")
        assert service.get_feed_health("BTC-USDT-SWAP", "1m").generation == 1
        await service._poll_once("BTC-USDT-SWAP", "1m")
        assert service.get_feed_health("BTC-USDT-SWAP", "1m").generation == 2
        for generation in (3, 4):
            with pytest.raises(RuntimeError, match="boom"):
                await service._poll_once("BTC-USDT-SWAP", "1m")
            assert service.get_feed_health("BTC-USDT-SWAP", "1m").generation == generation


async def test_list_feed_health_returns_snapshots_sorted_by_key():
    service = MarketDataService("api-key", "secret", "passphrase")
    service.subscribe("ETH-USDT-SWAP", "5m", AsyncMock())
    service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())
    service.subscribe("ADA-USDT-SWAP", "15m", AsyncMock())

    assert [health.key for health in service.list_feed_health()] == [
        "ADA-USDT-SWAP:15m",
        "BTC-USDT-SWAP:1m",
        "ETH-USDT-SWAP:5m",
    ]


async def test_never_ready_feed_failure_thresholds():
    class Exchange:
        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            raise RuntimeError("not ready")

        async def close(self) -> None:
            pass

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = Exchange()
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())

        for failure_count in range(1, 11):
            with pytest.raises(RuntimeError, match="not ready"):
                await service._poll_once("BTC-USDT-SWAP", "1m")
            health = service.get_feed_health("BTC-USDT-SWAP", "1m")
            if failure_count <= 2:
                assert health.status == "pending"
            elif failure_count < 10:
                assert health.status == "degraded"
            else:
                assert health.status == "unavailable"


async def test_cancelled_error_does_not_record_failure_in_poll_once():
    class Exchange:
        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            raise asyncio.CancelledError

        async def close(self) -> None:
            pass

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = Exchange()
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())

        with pytest.raises(asyncio.CancelledError):
            await service._poll_once("BTC-USDT-SWAP", "1m")

    health = service.get_feed_health("BTC-USDT-SWAP", "1m")
    assert health.generation == 0
    assert health.total_failures == 0
    assert health.error_code is None


async def test_cancelled_error_does_not_record_failure_in_start_and_clears_running():
    class Exchange:
        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            raise asyncio.CancelledError

        async def close(self) -> None:
            pass

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = Exchange()
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())

        with pytest.raises(asyncio.CancelledError):
            await service.start()

    health = service.get_feed_health("BTC-USDT-SWAP", "1m")
    assert service._running is False
    assert health.generation == 0
    assert health.total_failures == 0
    assert health.error_code is None


async def test_start_polls_when_running_is_preset_and_cleans_up_on_stop():
    class Exchange:
        def __init__(self) -> None:
            self.calls = 0

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            self.calls += 1
            return [[1700000000000, 50000, 51000, 49000, 50500, 100.5]]

        async def close(self) -> None:
            pass

    with patch("src.market.service.create_okx_client") as ccxt:
        exchange = Exchange()
        ccxt.return_value = exchange
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())
        service._running = True

        task = asyncio.create_task(service.start())
        while exchange.calls == 0:
            await asyncio.sleep(0)
        await service.stop()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert exchange.calls >= 1
    assert service._running is False
    assert service._service_task is None


def test_ensure_started_synchronously_rejects_foreign_live_task_without_mutation():
    service = MarketDataService("api-key", "secret", "passphrase")
    loop_ready = threading.Event()
    stop_requested = threading.Event()
    holder: dict[str, object] = {}

    async def wait_for_stop() -> None:
        while not stop_requested.is_set():
            await asyncio.sleep(0.01)

    def run_loop() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        task = loop.create_task(wait_for_stop())
        holder["loop"] = loop
        holder["task"] = task
        loop_ready.set()
        try:
            loop.run_until_complete(task)
        finally:
            loop.close()

    thread = threading.Thread(target=run_loop)
    thread.start()
    assert loop_ready.wait(timeout=1)
    task = holder["task"]
    service._service_task = task  # type: ignore[assignment]
    service._running = False

    try:
        with pytest.raises(RuntimeError):
            service.ensure_started()
        assert service._service_task is task
        assert service._running is False
    finally:
        stop_requested.set()
        thread.join(timeout=1)

    assert not thread.is_alive()


async def test_ensure_started_rejects_live_task_owned_by_different_running_loop():
    service = MarketDataService("api-key", "secret", "passphrase")
    loop_ready = threading.Event()
    stop_requested = threading.Event()
    holder: dict[str, object] = {}

    async def wait_for_stop() -> None:
        while not stop_requested.is_set():
            await asyncio.sleep(0.01)

    def run_loop() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        task = loop.create_task(wait_for_stop())
        holder["task"] = task
        loop_ready.set()
        try:
            loop.run_until_complete(task)
        finally:
            loop.close()

    thread = threading.Thread(target=run_loop)
    thread.start()
    assert loop_ready.wait(timeout=1)
    task = holder["task"]
    service._service_task = task  # type: ignore[assignment]

    try:
        with pytest.raises(RuntimeError, match="different event loop"):
            service.ensure_started()
        assert service._service_task is task
    finally:
        stop_requested.set()
        thread.join(timeout=1)

    assert not thread.is_alive()


async def test_start_during_slow_close_waits_recreates_exchange_then_polls():
    class ClosingExchange:
        def __init__(self) -> None:
            self.fetch_calls = 0
            self.closing = False
            self.close_started = asyncio.Event()
            self.release_close = asyncio.Event()

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            if self.closing:
                raise AssertionError("closing exchange was polled")
            self.fetch_calls += 1
            return [[1700000000000, 50000, 51000, 49000, 50500, 100.5]]

        async def close(self) -> None:
            self.closing = True
            self.close_started.set()
            await self.release_close.wait()

    class ReplacementExchange:
        def __init__(self) -> None:
            self.polled = asyncio.Event()
            self.close_calls = 0

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            self.polled.set()
            return [[1700000060000, 50500, 51500, 50000, 51200, 80.25]]

        async def close(self) -> None:
            self.close_calls += 1

    with patch("src.market.service.create_okx_client") as ccxt:
        closing_exchange = ClosingExchange()
        replacement_exchange = ReplacementExchange()
        ccxt.side_effect = [closing_exchange, replacement_exchange]
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())

        original = service.ensure_started()
        while closing_exchange.fetch_calls == 0:
            await asyncio.sleep(0)

        stopping = asyncio.create_task(service.stop())
        await closing_exchange.close_started.wait()
        replacement = service.ensure_started()
        await asyncio.sleep(0)

        assert replacement is not original
        assert ccxt.call_count == 1
        assert closing_exchange.fetch_calls == 1
        assert not replacement_exchange.polled.is_set()

        closing_exchange.release_close.set()
        await stopping
        await asyncio.wait_for(replacement_exchange.polled.wait(), timeout=1)

        assert ccxt.call_count == 2
        assert service.ensure_started() is replacement
        assert service._service_task is not replacement
        assert service._running is True

        await service.stop()

    assert replacement_exchange.close_calls == 1


async def test_failed_close_retires_exchange_and_queued_restart_uses_fresh_client():
    class FailingClosingExchange:
        def __init__(self) -> None:
            self.fetch_calls = 0
            self.close_calls = 0
            self.closing = False
            self.close_started = asyncio.Event()
            self.release_close = asyncio.Event()

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            if self.closing:
                raise AssertionError("retired exchange was polled")
            self.fetch_calls += 1
            return [[1700000000000, 50000, 51000, 49000, 50500, 100.5]]

        async def close(self) -> None:
            self.close_calls += 1
            self.closing = True
            if self.close_calls == 1:
                self.close_started.set()
                await self.release_close.wait()
                raise RuntimeError("close failed")

    class ReplacementExchange:
        def __init__(self) -> None:
            self.polled = asyncio.Event()
            self.close_calls = 0

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            self.polled.set()
            return [[1700000060000, 50500, 51500, 50000, 51200, 80.25]]

        async def close(self) -> None:
            self.close_calls += 1

    with patch("src.market.service.create_okx_client") as ccxt:
        first = FailingClosingExchange()
        second = ReplacementExchange()
        ccxt.side_effect = [first, second]
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())
        service.ensure_started()
        while first.fetch_calls == 0:
            await asyncio.sleep(0)

        stopping = asyncio.create_task(service.stop())
        await first.close_started.wait()
        replacement = service.ensure_started()
        first.release_close.set()
        with pytest.raises(RuntimeError, match="close failed"):
            await stopping
        await asyncio.wait_for(second.polled.wait(), timeout=1)

        assert first.fetch_calls == 1
        assert ccxt.call_count == 2
        assert not replacement.done()
        assert service._exchange_closed is False

        await service.stop()

    assert first.close_calls == 2
    assert second.close_calls == 1
    assert service._exchange_closed is True


async def test_cancelled_close_retires_exchange_and_queued_restart_uses_fresh_client():
    class CancelledClosingExchange:
        def __init__(self) -> None:
            self.fetch_calls = 0
            self.close_calls = 0
            self.closing = False
            self.close_started = asyncio.Event()
            self.allow_close = asyncio.Event()

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            if self.closing:
                raise AssertionError("retired exchange was polled")
            self.fetch_calls += 1
            return [[1700000000000, 50000, 51000, 49000, 50500, 100.5]]

        async def close(self) -> None:
            self.close_calls += 1
            self.closing = True
            self.close_started.set()
            await self.allow_close.wait()

    class ReplacementExchange:
        def __init__(self) -> None:
            self.polled = asyncio.Event()
            self.close_calls = 0

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            self.polled.set()
            return [[1700000060000, 50500, 51500, 50000, 51200, 80.25]]

        async def close(self) -> None:
            self.close_calls += 1

    with patch("src.market.service.create_okx_client") as ccxt:
        first = CancelledClosingExchange()
        second = ReplacementExchange()
        ccxt.side_effect = [first, second]
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())
        service.ensure_started()
        while first.fetch_calls == 0:
            await asyncio.sleep(0)

        stopping = asyncio.create_task(service.stop())
        await first.close_started.wait()
        replacement = service.ensure_started()
        stopping.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stopping
        await asyncio.wait_for(second.polled.wait(), timeout=1)

        assert first.fetch_calls == 1
        assert ccxt.call_count == 2
        assert not replacement.done()
        assert service._exchange_closed is False

        first.allow_close.set()
        await service.stop()

    assert first.close_calls == 2
    assert second.close_calls == 1
    assert service._exchange_closed is True


async def test_failed_pending_close_does_not_starve_other_exchange_cleanup():
    class AlwaysFailingExchange:
        def __init__(self) -> None:
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1
            raise RuntimeError("first close failed")

    class ClosableExchange:
        def __init__(self) -> None:
            self.close_calls = 0

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            return []

        async def close(self) -> None:
            self.close_calls += 1

    with patch("src.market.service.create_okx_client") as ccxt:
        first = AlwaysFailingExchange()
        second = ClosableExchange()
        ccxt.side_effect = [first, second]
        service = MarketDataService("api-key", "secret", "passphrase")

        with pytest.raises(RuntimeError, match="first close failed"):
            await service.stop()
        await service._poll_once("BTC-USDT-SWAP", "1m")

        with pytest.raises(RuntimeError, match="first close failed"):
            await service.stop()

    assert first.close_calls == 2
    assert second.close_calls == 1
    assert service._exchange_closed is False


async def test_cancelled_close_remains_retryable_and_later_stop_closes_exchange():
    class BlockingCloseExchange:
        def __init__(self) -> None:
            self.close_calls = 0
            self.close_started = asyncio.Event()
            self.allow_close = asyncio.Event()

        async def close(self) -> None:
            self.close_calls += 1
            self.close_started.set()
            await self.allow_close.wait()

    with patch("src.market.service.create_okx_client") as ccxt:
        exchange = BlockingCloseExchange()
        ccxt.return_value = exchange
        service = MarketDataService("api-key", "secret", "passphrase")

        stopping = asyncio.create_task(service.stop())
        await exchange.close_started.wait()
        stopping.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stopping

        assert service._exchange_closed is False

        exchange.allow_close.set()
        await service.stop()

    assert exchange.close_calls == 2
    assert service._exchange_closed is True


async def test_failed_close_remains_retryable_and_later_stop_closes_exchange():
    class FailingCloseExchange:
        def __init__(self) -> None:
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1
            if self.close_calls == 1:
                raise RuntimeError("close failed")

    with patch("src.market.service.create_okx_client") as ccxt:
        exchange = FailingCloseExchange()
        ccxt.return_value = exchange
        service = MarketDataService("api-key", "secret", "passphrase")

        with pytest.raises(RuntimeError, match="close failed"):
            await service.stop()
        assert service._exchange_closed is False

        await service.stop()

    assert exchange.close_calls == 2
    assert service._exchange_closed is True


async def test_concurrent_direct_starts_create_one_owner_and_stop_drains_workers():
    class BlockingExchange:
        def __init__(self) -> None:
            self.fetch_calls = 0
            self.fetch_started = asyncio.Event()
            self.worker_finished = asyncio.Event()
            self.close_calls = 0

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            self.fetch_calls += 1
            self.fetch_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.worker_finished.set()

        async def close(self) -> None:
            assert self.worker_finished.is_set()
            self.close_calls += 1

    with patch("src.market.service.create_okx_client") as ccxt:
        exchange = BlockingExchange()
        ccxt.return_value = exchange
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", AsyncMock())

        first = asyncio.create_task(service.start())
        second = asyncio.create_task(service.start())
        await exchange.fetch_started.wait()
        await asyncio.sleep(0)

        assert exchange.fetch_calls == 1
        assert sum(not task.done() for task in (first, second)) == 1

        await service.stop()
        await asyncio.gather(first, second, return_exceptions=True)

    assert exchange.worker_finished.is_set()
    assert exchange.close_calls == 1
    assert service._service_task is None
    assert service._feed_tasks == {}


async def test_feed_callback_cannot_await_stop_and_external_stop_remains_coherent():
    callback_finished = asyncio.Event()
    callback_errors: list[str] = []
    worker_tasks: list[asyncio.Task[None]] = []

    class Exchange:
        def __init__(self) -> None:
            self.close_calls = 0

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            return [[1700000000000, 50000, 51000, 49000, 50500, 100.5]]

        async def close(self) -> None:
            assert worker_tasks
            assert all(task.done() for task in worker_tasks)
            self.close_calls += 1

    async def callback(bar: Bar) -> None:
        task = asyncio.current_task()
        assert task is not None
        worker_tasks.append(task)
        try:
            await service.stop()
        except RuntimeError as exc:
            callback_errors.append(str(exc))
        callback_finished.set()

    with patch("src.market.service.create_okx_client") as ccxt:
        exchange = Exchange()
        ccxt.return_value = exchange
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", callback)
        handle = service.ensure_started()

        await asyncio.wait_for(callback_finished.wait(), timeout=1)
        assert callback_errors == [
            "MarketDataService.stop() cannot be awaited from a feed callback or worker"
        ]
        assert not handle.done()
        assert service._running is True

        await service.stop()

    assert exchange.close_calls == 1
    assert service._feed_tasks == {}


async def test_cancelled_feed_worker_remains_registered_during_nested_stop():
    callback_started = asyncio.Event()
    nested_stop_started = asyncio.Event()
    nested_stop_finished = asyncio.Event()
    release_callback = asyncio.Event()

    class Exchange:
        def __init__(self) -> None:
            self.close_calls = 0

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            return [[1700000000000, 50000, 51000, 49000, 50500, 100.5]]

        async def close(self) -> None:
            self.close_calls += 1

    async def callback(bar: Bar) -> None:
        callback_started.set()
        try:
            await release_callback.wait()
        except asyncio.CancelledError:
            nested_stop_started.set()
            with pytest.raises(
                RuntimeError,
                match=(
                    "MarketDataService.stop\\(\\) cannot be awaited from a feed callback or worker"
                ),
            ):
                await service.stop()
            nested_stop_finished.set()
            raise

    with patch("src.market.service.create_okx_client") as ccxt:
        exchange = Exchange()
        ccxt.return_value = exchange
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", callback)
        service.ensure_started()
        await callback_started.wait()

        stopping = asyncio.create_task(service.stop())
        await nested_stop_started.wait()
        await asyncio.wait_for(nested_stop_finished.wait(), timeout=1)
        await asyncio.wait_for(stopping, timeout=1)

    assert exchange.close_calls == 1
    assert service._feed_tasks == {}


async def test_start_setup_failure_clears_owner_and_retries_with_fresh_exchange():
    class Exchange:
        def __init__(self) -> None:
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1

    with patch("src.market.service.create_okx_client") as ccxt:
        first = Exchange()
        replacement = Exchange()
        ccxt.side_effect = [first, RuntimeError("exchange recreation failed"), replacement]
        service = MarketDataService("api-key", "secret", "passphrase")

        await service.stop()
        with pytest.raises(RuntimeError, match="exchange recreation failed"):
            await service.start()

        assert service._service_task is None
        assert service._service_handle is None
        assert service._running is False
        assert service._stopping is False
        assert service._shutdown_task is None

        handle = service.ensure_started()
        await asyncio.sleep(0)
        assert service._exchange is replacement
        assert not handle.done()
        await service.stop()

    assert first.close_calls == 1
    assert replacement.close_calls == 1


async def test_poll_once_continues_dispatch_after_one_callback_fails():
    class Exchange:
        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            return [[1700000000000, 50000, 51000, 49000, 50500, 100.5]]

        async def close(self) -> None:
            pass

    received: list[Bar] = []

    async def failing_callback(bar: Bar) -> None:
        raise RuntimeError("first callback failed")

    async def succeeding_callback(bar: Bar) -> None:
        received.append(bar)

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = Exchange()
        service = MarketDataService("api-key", "secret", "passphrase")
        service._current_time = lambda: 1700000060
        service.subscribe("BTC-USDT-SWAP", "1m", failing_callback)
        service.subscribe("BTC-USDT-SWAP", "1m", succeeding_callback)

        with pytest.raises(RuntimeError, match="first callback failed"):
            await service._poll_once("BTC-USDT-SWAP", "1m")

    assert len(received) == 1
    health = service.get_feed_health("BTC-USDT-SWAP", "1m")
    assert health.error_code is None
    assert health.buffered_bars == 1


async def test_blocked_feed_does_not_prevent_other_feed_readiness_and_stop_drains_it():
    class Exchange:
        def __init__(self) -> None:
            self.blocked_started = asyncio.Event()
            self.blocked_cancelled = asyncio.Event()
            self.close_calls = 0

        async def fetch_ohlcv(self, symbol: str, timeframe: str):
            if symbol == "BLOCKED-USDT-SWAP":
                self.blocked_started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    self.blocked_cancelled.set()
            return [[1700000000000, 50000, 51000, 49000, 50500, 100.5]]

        async def close(self) -> None:
            assert self.blocked_cancelled.is_set()
            self.close_calls += 1

    with patch("src.market.service.create_okx_client") as ccxt:
        exchange = Exchange()
        ccxt.return_value = exchange
        service = MarketDataService("api-key", "secret", "passphrase")
        service._current_time = lambda: 1700000060
        service.subscribe("BLOCKED-USDT-SWAP", "1m", AsyncMock())
        service.subscribe("READY-USDT-SWAP", "1m", AsyncMock())

        task = service.ensure_started()
        await exchange.blocked_started.wait()
        ready = await service.wait_until_ready("READY-USDT-SWAP", "1m", timeout=1)

        assert ready.status == "ready"
        await service.stop()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert exchange.blocked_cancelled.is_set()
    assert exchange.close_calls == 1
    assert service._feed_tasks == {}


def test_subscribe_rejects_foreign_loop_after_waiter_binds_without_event_or_start():
    service = MarketDataService("api-key", "secret", "passphrase")
    original_callback = AsyncMock()
    rejected_callback = AsyncMock()
    service.subscribe("BTC-USDT-SWAP", "1m", original_callback)

    async def bind_first_loop() -> None:
        with pytest.raises(TimeoutError):
            await service.wait_until_ready("BTC-USDT-SWAP", "1m", timeout=0)

    asyncio.run(bind_first_loop())
    assert service._subscriptions_changed is None
    callbacks_before = list(service._subscriptions["BTC-USDT-SWAP:1m"])
    buffers_before = dict(service._buffers)
    health_before = dict(service._health)

    async def use_second_loop() -> None:
        with pytest.raises(
            RuntimeError,
            match="MarketDataService is bound to a different event loop",
        ):
            service.subscribe("ETH-USDT-SWAP", "5m", rejected_callback)

    asyncio.run(use_second_loop())

    assert service._subscriptions == {"BTC-USDT-SWAP:1m": callbacks_before}
    assert service._subscriptions["BTC-USDT-SWAP:1m"][0] is original_callback
    assert service._buffers == buffers_before
    assert all(service._buffers[key] is buffer for key, buffer in buffers_before.items())
    assert service._health == health_before
    assert service._subscriptions_changed is None


def test_unsubscribe_rejects_foreign_loop_after_waiter_binds_without_event_or_start():
    service = MarketDataService("api-key", "secret", "passphrase")
    callback = AsyncMock()
    service.subscribe("BTC-USDT-SWAP", "1m", callback)

    async def bind_first_loop() -> None:
        with pytest.raises(TimeoutError):
            await service.wait_until_ready("BTC-USDT-SWAP", "1m", timeout=0)

    asyncio.run(bind_first_loop())
    assert service._subscriptions_changed is None
    callbacks_before = list(service._subscriptions["BTC-USDT-SWAP:1m"])
    buffers_before = dict(service._buffers)
    health_before = dict(service._health)

    async def use_second_loop() -> None:
        with pytest.raises(
            RuntimeError,
            match="MarketDataService is bound to a different event loop",
        ):
            service.unsubscribe("BTC-USDT-SWAP", "1m", callback)

    asyncio.run(use_second_loop())

    assert service._subscriptions == {"BTC-USDT-SWAP:1m": callbacks_before}
    assert service._subscriptions["BTC-USDT-SWAP:1m"][0] is callback
    assert service._buffers == buffers_before
    assert all(service._buffers[key] is buffer for key, buffer in buffers_before.items())
    assert service._health == health_before
    assert service._subscriptions_changed is None


def test_cross_loop_subscribe_rejects_without_mutating_subscriptions():
    class Exchange:
        async def close(self) -> None:
            pass

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = Exchange()
        service = MarketDataService("api-key", "secret", "passphrase")
        original_callback = AsyncMock()
        rejected_callback = AsyncMock()
        service.subscribe("BTC-USDT-SWAP", "1m", original_callback)

        async def bind_first_loop() -> None:
            handle = service.ensure_started()
            await asyncio.sleep(0)
            await service.stop()
            with pytest.raises(asyncio.CancelledError):
                await handle

        asyncio.run(bind_first_loop())
        subscriptions_before = {
            key: list(callbacks) for key, callbacks in service._subscriptions.items()
        }
        health_keys_before = set(service._health)

        async def use_second_loop() -> None:
            with pytest.raises(
                RuntimeError,
                match="MarketDataService is bound to a different event loop",
            ):
                service.subscribe("ETH-USDT-SWAP", "5m", rejected_callback)

        asyncio.run(use_second_loop())

    assert service._subscriptions == subscriptions_before
    assert set(service._health) == health_keys_before


def test_cross_loop_unsubscribe_rejects_without_mutating_subscriptions():
    class Exchange:
        async def close(self) -> None:
            pass

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = Exchange()
        service = MarketDataService("api-key", "secret", "passphrase")
        callback = AsyncMock()
        service.subscribe("BTC-USDT-SWAP", "1m", callback)

        async def bind_first_loop() -> None:
            handle = service.ensure_started()
            await asyncio.sleep(0)
            await service.stop()
            with pytest.raises(asyncio.CancelledError):
                await handle

        asyncio.run(bind_first_loop())
        subscriptions_before = {
            key: list(callbacks) for key, callbacks in service._subscriptions.items()
        }

        async def use_second_loop() -> None:
            with pytest.raises(
                RuntimeError,
                match="MarketDataService is bound to a different event loop",
            ):
                service.unsubscribe("BTC-USDT-SWAP", "1m", callback)

        asyncio.run(use_second_loop())

    assert service._subscriptions == subscriptions_before


def test_async_operations_reject_a_second_event_loop_with_service_error():
    class Exchange:
        async def close(self) -> None:
            pass

    with patch("src.market.service.create_okx_client") as ccxt:
        ccxt.return_value = Exchange()
        service = MarketDataService("api-key", "secret", "passphrase")

        async def use_first_loop() -> None:
            with pytest.raises(TimeoutError):
                await service.wait_until_ready("BTC-USDT-SWAP", "1m", timeout=0.001)
            await service.stop()

        asyncio.run(use_first_loop())

        async def use_second_loop() -> None:
            with pytest.raises(
                RuntimeError,
                match="MarketDataService is bound to a different event loop",
            ):
                await service.wait_until_ready("BTC-USDT-SWAP", "1m", timeout=0.001)

        asyncio.run(use_second_loop())
