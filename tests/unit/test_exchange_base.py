import pytest

from src.exchange import base as exchange_base
from src.exchange.base import OKXBaseAdapter


@pytest.mark.asyncio
async def test_okx_base_adapter_fetch_ohlcv_passes_since_and_limit(monkeypatch):
    class FakeOKX:
        instances = []

        def __init__(self, config):
            self.config = config
            self.fetch_calls = []
            self.closed = False
            self.instances.append(self)

        async def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
            self.fetch_calls.append(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "since": since,
                    "limit": limit,
                }
            )
            return [[1700000000000, 100, 101, 99, 100.5, 12.5]]

        async def close(self):
            self.closed = True

    monkeypatch.setattr(exchange_base.ccxt, "okx", FakeOKX)

    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "spot")
    bars = await adapter.fetch_ohlcv(
        "BTC-USDT",
        "1h",
        limit=300,
        since=1700000000000,
    )
    await adapter.close()

    fake = FakeOKX.instances[0]
    assert fake.config["apiKey"] == "api-key"
    assert fake.config["secret"] == "secret"
    assert fake.config["password"] == "passphrase"
    assert fake.config["options"] == {"defaultType": "spot"}
    assert fake.fetch_calls == [
        {
            "symbol": "BTC-USDT",
            "timeframe": "1h",
            "since": 1700000000000,
            "limit": 300,
        }
    ]
    assert bars[0].timestamp == 1700000000000
    assert bars[0].open == 100.0
    assert bars[0].high == 101.0
    assert bars[0].low == 99.0
    assert bars[0].close == 100.5
    assert bars[0].volume == 12.5
    assert fake.closed is True
