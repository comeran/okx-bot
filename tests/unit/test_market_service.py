from collections import deque
from unittest.mock import AsyncMock, patch

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
