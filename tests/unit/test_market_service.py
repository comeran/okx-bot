from collections import deque
from unittest.mock import AsyncMock, patch

import pytest
from ccxt.base.errors import NotSupported

from src.core.types import Bar
from src.market.service import MarketDataService


async def test_poll_once_builds_bars_and_notifies_subscriber():
    with patch("src.market.service.ccxt") as ccxt:
        exchange = AsyncMock()
        exchange.watch_ohlcv.return_value = [
            [1700000000000, 50000, 51000, 49000, 50500, 100.5],
            [1700000060000, 50500, 51500, 50000, 51200, 80.25],
        ]
        ccxt.okx.return_value = exchange
        callback = AsyncMock()

        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", callback)

        await service._poll_once("BTC-USDT-SWAP", "1m")

        bars = service.get_recent_bars("BTC-USDT-SWAP", "1m")
        assert len(bars) == 2
        assert bars[0].open == 50000.0
        assert callback.await_count == 2


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

    with patch("src.market.service.ccxt") as ccxt:
        exchange = FetchOnlyExchange()
        ccxt.okx.return_value = exchange
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

    with patch("src.market.service.ccxt") as ccxt:
        exchange = WatchUnsupportedExchange()
        ccxt.okx.return_value = exchange
        callback = AsyncMock()

        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", callback)

        await service._poll_once("BTC-USDT-SWAP", "1m")

        assert exchange.fetch_ohlcv_called is True
        assert len(service.get_recent_bars("BTC-USDT-SWAP", "1m")) == 1
        callback.assert_awaited_once()


async def test_poll_once_raises_when_exchange_has_no_ohlcv_method():
    with patch("src.market.service.ccxt") as ccxt:
        ccxt.okx.return_value = object()
        service = MarketDataService("api-key", "secret", "passphrase")

        with pytest.raises(RuntimeError, match="does not support OHLCV"):
            await service._poll_once("BTC-USDT-SWAP", "1m")


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

    with patch("src.market.service.ccxt") as ccxt:
        ccxt.okx.return_value = FetchExchange()
        callback = AsyncMock()
        service = MarketDataService("api-key", "secret", "passphrase")
        service.subscribe("BTC-USDT-SWAP", "1m", callback)

        await service._poll_once("BTC-USDT-SWAP", "1m")
        await service._poll_once("BTC-USDT-SWAP", "1m")

    bars = service.get_recent_bars("BTC-USDT-SWAP", "1m")
    assert [bar.timestamp for bar in bars] == [1700000000000, 1700000060000]
    assert callback.await_count == 2


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

    with patch("src.market.service.ccxt") as ccxt:
        exchange = FetchExchange()
        ccxt.okx.return_value = exchange
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
