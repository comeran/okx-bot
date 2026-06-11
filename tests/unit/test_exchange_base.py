import pytest

from src.exchange import base as exchange_base
from src.exchange.base import OKXBaseAdapter


class FakeOKX:
    instances = []

    def __init__(self, config):
        self.config = config
        self.fetch_ohlcv_calls = []
        self.fetch_tickers_calls = []
        self.public_tickers_calls = []
        self.public_candles_calls = []
        self.public_history_candles_calls = []
        self.ticker_rows = {}
        self.raw_ticker_response = {"data": []}
        self.raw_candle_response = {"data": []}
        self.raw_history_candle_response = {"data": []}
        self.closed = False
        self.instances.append(self)

    async def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
        self.fetch_ohlcv_calls.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "since": since,
                "limit": limit,
            }
        )
        return [[1700000000000, 100, 101, 99, 100.5, 12.5]]

    async def fetch_tickers(self, symbols):
        self.fetch_tickers_calls.append(symbols)
        return self.ticker_rows

    async def public_get_market_tickers(self, params):
        self.public_tickers_calls.append(params)
        return self.raw_ticker_response

    async def public_get_market_candles(self, params):
        self.public_candles_calls.append(params)
        return self.raw_candle_response

    async def public_get_market_history_candles(self, params):
        self.public_history_candles_calls.append(params)
        return self.raw_history_candle_response

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def fake_okx(monkeypatch):
    FakeOKX.instances = []
    monkeypatch.setattr(exchange_base.ccxt, "okx", FakeOKX)
    return FakeOKX


@pytest.mark.asyncio
async def test_okx_base_adapter_includes_non_empty_credentials():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "spot")
    await adapter.close()

    fake = FakeOKX.instances[0]
    assert fake.config["apiKey"] == "api-key"
    assert fake.config["secret"] == "secret"
    assert fake.config["password"] == "passphrase"
    assert fake.config["options"] == {"defaultType": "spot"}
    assert fake.closed is True


@pytest.mark.asyncio
async def test_okx_base_adapter_omits_empty_credentials():
    adapter = OKXBaseAdapter("", "", "", "spot")
    await adapter.close()

    fake = FakeOKX.instances[0]
    assert "apiKey" not in fake.config
    assert "secret" not in fake.config
    assert "password" not in fake.config
    assert fake.config["options"] == {"defaultType": "spot"}
    assert fake.closed is True


@pytest.mark.asyncio
async def test_okx_base_adapter_fetch_spot_ohlcv_with_since_uses_raw_history_candles():
    adapter = OKXBaseAdapter("", "", "", "spot")
    fake = FakeOKX.instances[0]
    fake.raw_history_candle_response = {
        "data": [
            ["1700003600000", "101", "102", "100", "101.5", "13.5"],
            ["1700000000000", "100", "101", "99", "100.5", "12.5"],
        ]
    }

    bars = await adapter.fetch_ohlcv(
        "BTC-USDT",
        "1h",
        limit=300,
        since=1700000000000,
    )
    await adapter.close()

    assert fake.fetch_ohlcv_calls == []
    assert fake.public_candles_calls == []
    assert fake.public_history_candles_calls == [
        {
            "instId": "BTC-USDT",
            "bar": "1H",
            "limit": 300,
            "before": 1699999999999,
            "after": 1701080000000,
        }
    ]
    assert [bar.timestamp for bar in bars] == [1700000000000, 1700003600000]
    assert bars[0].open == 100.0
    assert bars[0].high == 101.0
    assert bars[0].low == 99.0
    assert bars[0].close == 100.5
    assert bars[0].volume == 12.5
    assert fake.closed is True


@pytest.mark.asyncio
async def test_okx_base_adapter_fetch_ohlcv_preserves_non_spot_derivative_symbol():
    adapter = OKXBaseAdapter("", "", "", "swap")
    await adapter.fetch_ohlcv(
        "BTC-USDT-SWAP",
        "1h",
        limit=300,
        since=1700000000000,
    )
    await adapter.close()

    fake = FakeOKX.instances[0]
    assert fake.fetch_ohlcv_calls == [
        {
            "symbol": "BTC-USDT-SWAP",
            "timeframe": "1h",
            "since": 1700000000000,
            "limit": 300,
        }
    ]
    assert fake.public_candles_calls == []
    assert fake.closed is True


@pytest.mark.asyncio
async def test_okx_base_adapter_fetch_spot_tickers_uses_raw_public_method():
    adapter = OKXBaseAdapter("", "", "", "spot")
    fake = FakeOKX.instances[0]
    fake.raw_ticker_response = {
        "data": [
            {
                "instId": "ETH-USDT",
                "last": "200",
                "bidPx": "199",
                "askPx": "201",
                "vol24h": "25",
            },
            {
                "instId": "BTC-USDT",
                "last": "100",
                "bidPx": "99",
                "askPx": "101",
                "vol24h": "12.5",
            },
        ]
    }

    tickers = await adapter.fetch_tickers(["BTC-USDT", "MISSING-USDT", "ETH-USDT"])
    await adapter.close()

    assert fake.fetch_tickers_calls == []
    assert fake.public_tickers_calls == [{"instType": "SPOT"}]
    assert tickers == [
        {
            "symbol": "BTC-USDT",
            "last": 100.0,
            "bidPx": 99.0,
            "askPx": 101.0,
            "vol24h": 12.5,
        },
        {
            "symbol": "ETH-USDT",
            "last": 200.0,
            "bidPx": 199.0,
            "askPx": 201.0,
            "vol24h": 25.0,
        },
    ]
    assert fake.closed is True


@pytest.mark.asyncio
async def test_okx_base_adapter_fetch_non_spot_tickers_preserves_symbols():
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]
    fake.ticker_rows = {
        "BTC/USDT": {"last": 100, "bid": 99, "ask": 101, "baseVolume": 12.5},
        "ETH-USDT": {"last": 200, "bid": 199, "ask": 201, "baseVolume": 25},
    }

    tickers = await adapter.fetch_tickers(["BTC-USDT", "ETH-USDT"])
    await adapter.close()

    assert fake.fetch_tickers_calls == [["BTC-USDT", "ETH-USDT"]]
    assert fake.public_tickers_calls == []
    assert tickers == [
        {
            "symbol": "BTC-USDT",
            "last": 100.0,
            "bidPx": 99.0,
            "askPx": 101.0,
            "vol24h": 12.5,
        },
        {
            "symbol": "ETH-USDT",
            "last": 200.0,
            "bidPx": 199.0,
            "askPx": 201.0,
            "vol24h": 25.0,
        },
    ]
    assert fake.closed is True
