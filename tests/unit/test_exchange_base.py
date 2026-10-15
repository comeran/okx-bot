from decimal import Decimal
from unittest.mock import Mock

import pytest
from ccxt.base.errors import NetworkError, NotSupported

from src.core.types import (
    AssetBalance,
    Order,
    OrderSide,
    OrderType,
    PositionSide,
    execution_type_family,
)
from src.exchange import base as exchange_base
from src.exchange.base import OKXBaseAdapter
from src.exchange.errors import AcceptedUnknownOrderResultError, MalformedProviderData
from src.exchange.factory import create_okx_adapter


class FakeOKX:
    instances = []

    def __init__(self, config):
        self.config = config
        self.fetch_ohlcv_calls = []
        self.fetch_tickers_calls = []
        self.fetch_balance_calls = 0
        self.fetch_positions_calls = []
        self.public_tickers_calls = []
        self.public_candles_calls = []
        self.public_history_candles_calls = []
        self.ticker_rows = {}
        self.balance_response = {}
        self.positions_response = []
        self.raw_ticker_response = {"data": []}
        self.raw_candle_response = {"data": []}
        self.raw_history_candle_response = {"data": []}
        self.closed = False
        self.sandbox_enabled = None
        self.precisionMode = exchange_base.DECIMAL_PLACES
        self.markets = {}
        self.load_markets_calls = 0
        self.create_order_calls = []
        self.instances.append(self)

    def set_sandbox_mode(self, enabled):
        self.sandbox_enabled = enabled

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

    async def fetch_tickers(self, symbols=None):
        self.fetch_tickers_calls.append(symbols)
        return self.ticker_rows

    async def fetch_balance(self):
        self.fetch_balance_calls += 1
        return self.balance_response

    async def fetch_positions(self, symbols=None):
        self.fetch_positions_calls.append(symbols)
        return self.positions_response

    async def public_get_market_tickers(self, params):
        self.public_tickers_calls.append(params)
        return self.raw_ticker_response

    async def public_get_market_candles(self, params):
        self.public_candles_calls.append(params)
        return self.raw_candle_response

    async def public_get_market_history_candles(self, params):
        self.public_history_candles_calls.append(params)
        return self.raw_history_candle_response

    async def load_markets(self):
        self.load_markets_calls += 1
        return self.markets

    async def create_order(self, symbol, order_type, side, amount, price, params):
        self.create_order_calls.append(
            {
                "symbol": symbol,
                "type": order_type,
                "side": side,
                "amount": amount,
                "price": price,
                "params": params,
            }
        )
        return {"id": "okx-1", "status": "closed", "average": price, "timestamp": 1700000000000}

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def fake_okx(monkeypatch):
    FakeOKX.instances = []

    def create_client(**kwargs):
        config = {"options": {"defaultType": kwargs["default_type"]}}
        if kwargs["api_key"].strip():
            config["apiKey"] = kwargs["api_key"]
        if kwargs["secret"].strip():
            config["secret"] = kwargs["secret"]
        if kwargs["passphrase"].strip():
            config["password"] = kwargs["passphrase"]
        fake = FakeOKX(config)
        if kwargs["demo"]:
            fake.set_sandbox_mode(True)
        return fake

    create_client_mock = Mock(side_effect=create_client)
    monkeypatch.setattr(exchange_base, "create_okx_client", create_client_mock)
    return create_client_mock


@pytest.mark.asyncio
async def test_okx_adapter_factory_rejects_whitespace_only_live_credentials():
    class ExchangeConfig:
        api_key = " api-key "
        secret = "   "
        passphrase = "passphrase"
        market_type = "spot"
        demo = False

    with pytest.raises(ValueError, match="Live trading requires"):
        create_okx_adapter(
            ExchangeConfig(),
            adapter_classes={"spot": Mock(return_value=object())},
        )


def test_okx_adapter_factory_strips_only_for_credential_gate_not_forwarding():
    constructor = Mock(return_value=object())

    class ExchangeConfig:
        api_key = " api-key "
        secret = " secret "
        passphrase = " passphrase "
        market_type = " spot "
        demo = False

    create_okx_adapter(ExchangeConfig(), adapter_classes={"spot": constructor})

    constructor.assert_called_once_with(" api-key ", " secret ", " passphrase ", demo=False)


@pytest.mark.asyncio
async def test_okx_base_adapter_includes_non_empty_credentials():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "spot")
    await adapter.close()

    fake = FakeOKX.instances[0]
    assert fake.config["apiKey"] == "api-key"
    assert fake.config["secret"] == "secret"
    assert fake.config["password"] == "passphrase"
    assert fake.config["options"] == {"defaultType": "spot"}
    assert fake.sandbox_enabled is True
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
    assert fake.sandbox_enabled is True
    assert fake.closed is True


@pytest.mark.asyncio
async def test_okx_base_adapter_can_disable_sandbox():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "spot", demo=False)
    await adapter.close()

    fake = FakeOKX.instances[0]
    assert fake.sandbox_enabled is None


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
@pytest.mark.asyncio
@pytest.mark.parametrize("column_count", [7, 9])
async def test_okx_base_adapter_accepts_ohlcv_rows_with_extra_columns(column_count):
    adapter = OKXBaseAdapter("", "", "", "spot")
    fake = FakeOKX.instances[0]
    fake.raw_candle_response = {
        "data": [
            ["1700000000000", "100", "101", "99", "100.5", "12.5"]
            + ["provider-extra"] * (column_count - 6)
        ]
    }

    bars = await adapter.fetch_ohlcv("BTC-USDT", "1m")
    await adapter.close()

    assert len(bars) == 1
    assert bars[0].volume == 12.5


@pytest.mark.asyncio
async def test_okx_base_adapter_accepts_okx_spot_ohlcv_extra_columns():
    adapter = OKXBaseAdapter("", "", "", "spot")
    fake = FakeOKX.instances[0]
    fake.raw_candle_response = {
        "data": [["1700000000000", "100", "101", "99", "100.5", "12.5", "1250", "1255", "1"]]
    }

    bars = await adapter.fetch_ohlcv("BTC-USDT", "1m")
    await adapter.close()

    assert bars[0].timestamp == 1700000000000
    assert bars[0].open == 100.0
    assert bars[0].high == 101.0
    assert bars[0].low == 99.0
    assert bars[0].close == 100.5
    assert bars[0].volume == 12.5


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
async def test_okx_base_adapter_matches_spot_tickers_by_slash_and_hyphen_aliases():
    adapter = OKXBaseAdapter("", "", "", "spot")
    fake = FakeOKX.instances[0]
    fake.raw_ticker_response = {
        "data": [
            {
                "instId": "BTC-USDT",
                "last": "100",
                "bidPx": "99",
                "askPx": "101",
                "vol24h": "12.5",
            }
        ]
    }

    tickers = await adapter.fetch_tickers(["BTC/USDT"])
    await adapter.close()

    assert tickers == [
        {
            "symbol": "BTC/USDT",
            "last": 100.0,
            "bidPx": 99.0,
            "askPx": 101.0,
            "vol24h": 12.5,
        }
    ]


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("market_type", "requested_symbol", "ccxt_symbol", "market_id"),
    [
        ("swap", "BTC-USDT-SWAP", "BTC/USDT:USDT", "BTC-USDT-SWAP"),
        ("future", "BTC-USDT-260925", "BTC/USDT:USDT-260925", "BTC-USDT-260925"),
        (
            "option",
            "BTC-USD-260925-100000-C",
            "BTC/USD:USD-260925-100000-C",
            "BTC-USD-260925-100000-C",
        ),
    ],
)
async def test_okx_base_adapter_fetch_tickers_requests_unified_symbols_and_returns_canonical(
    market_type, requested_symbol, ccxt_symbol, market_id
):
    adapter = OKXBaseAdapter("", "", "", market_type)
    fake = FakeOKX.instances[0]
    fake.markets = {
        ccxt_symbol: {
            "id": market_id,
            "symbol": ccxt_symbol,
            "type": market_type,
        }
    }
    fake.ticker_rows = {
        ccxt_symbol: {
            "symbol": ccxt_symbol,
            "last": "100",
            "bid": "99",
            "ask": "101",
            "baseVolume": "12.5",
        }
    }

    tickers = await adapter.fetch_tickers([requested_symbol])
    await adapter.close()

    assert fake.fetch_tickers_calls == [[ccxt_symbol]]
    assert tickers == [
        {
            "symbol": requested_symbol,
            "last": 100.0,
            "bidPx": 99.0,
            "askPx": 101.0,
            "vol24h": 12.5,
        }
    ]


@pytest.mark.asyncio
async def test_okx_base_adapter_matches_option_ticker_aliases_without_market_metadata():
    adapter = OKXBaseAdapter("", "", "", "option")
    fake = FakeOKX.instances[0]
    fake.ticker_rows = {
        "BTC/USDT:USDT-260925-100000-C": {
            "symbol": "BTC/USDT:USDT-260925-100000-C",
            "last": "100",
            "bid": "99",
            "ask": "101",
            "baseVolume": "12.5",
        }
    }

    tickers = await adapter.fetch_tickers(["BTC-USDT-260925-100000-C"])
    await adapter.close()

    assert fake.fetch_tickers_calls == [["BTC/USDT:USDT-260925-100000-C"]]
    assert tickers == [{
        "symbol": "BTC-USDT-260925-100000-C",
        "last": 100.0,
        "bidPx": 99.0,
        "askPx": 101.0,
        "vol24h": 12.5,
    }]


@pytest.mark.asyncio
@pytest.mark.parametrize("requested_symbol", [
    "BTC-USDT-260925-100000-C",
    "BTC/USDT:USDT-260925-100000-C",
])
async def test_okx_base_adapter_does_not_match_different_option_contracts_without_metadata(
    requested_symbol,
):
    adapter = OKXBaseAdapter("", "", "", "option")
    fake = FakeOKX.instances[0]
    fake.ticker_rows = {
        "BTC/USDT:USDT-260925-110000-C": {
            "symbol": "BTC/USDT:USDT-260925-110000-C",
            "last": "100",
            "bid": "99",
            "ask": "101",
            "baseVolume": "12.5",
        }
    }

    assert await adapter.fetch_tickers([requested_symbol]) == []
    await adapter.close()


async def test_okx_base_adapter_fetch_tickers_canonicalizes_unified_request_in_response():
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC/USDT:USDT": {
            "id": "BTC-USDT-SWAP",
            "symbol": "BTC/USDT:USDT",
            "type": "swap",
        }
    }
    fake.ticker_rows = {
        "BTC/USDT:USDT": {
            "last": "100",
            "bid": "99",
            "ask": "101",
            "baseVolume": "12.5",
        }
    }

    tickers = await adapter.fetch_tickers(["BTC/USDT:USDT"])
    await adapter.close()

    assert tickers[0]["symbol"] == "BTC-USDT-SWAP"


@pytest.mark.asyncio
async def test_okx_base_adapter_fetch_tickers_none_requests_all_without_symbol_argument():
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]
    fake.ticker_rows = {}

    assert await adapter.fetch_tickers(None) == []
    await adapter.close()

    assert fake.fetch_tickers_calls == [None]


@pytest.mark.asyncio
async def test_okx_base_adapter_fetch_tickers_empty_does_not_call_provider():
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]

    assert await adapter.fetch_tickers([]) == []
    await adapter.close()

    assert fake.fetch_tickers_calls == []


@pytest.mark.asyncio
async def test_okx_base_adapter_matches_derivative_tickers_by_market_id_and_unified_symbol():
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC/USDT:USDT": {
            "id": "BTC-USDT-SWAP",
            "symbol": "BTC/USDT:USDT",
            "type": "swap",
        },
        "BTC/USDT:USDT-260925": {
            "id": "BTC-USDT-260925",
            "symbol": "BTC/USDT:USDT-260925",
            "type": "future",
        },
    }
    fake.ticker_rows = {
        "BTC/USDT:USDT": {
            "symbol": "BTC/USDT:USDT",
            "last": "100",
            "bid": "99",
            "ask": "101",
            "baseVolume": "12.5",
        },
        "BTC/USDT:USDT-260925": {
            "symbol": "BTC/USDT:USDT-260925",
            "last": "200",
            "bid": "199",
            "ask": "201",
            "baseVolume": "25",
        },
    }

    tickers = await adapter.fetch_tickers(["BTC-USDT-SWAP", "BTC-USDT-260925"])
    await adapter.close()

    assert tickers == [
        {
            "symbol": "BTC-USDT-SWAP",
            "last": 100.0,
            "bidPx": 99.0,
            "askPx": 101.0,
            "vol24h": 12.5,
        },
        {
            "symbol": "BTC-USDT-260925",
            "last": 200.0,
            "bidPx": 199.0,
            "askPx": 201.0,
            "vol24h": 25.0,
        },
    ]


@pytest.mark.asyncio
async def test_okx_base_adapter_does_not_fuzzy_match_derivative_ticker_aliases():
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC/USDT:USDT": {
            "id": "BTC-USDT-SWAP",
            "symbol": "BTC/USDT:USDT",
            "type": "swap",
        },
        "BTC/USDT:USDT-260925": {
            "id": "BTC-USDT-260925",
            "symbol": "BTC/USDT:USDT-260925",
            "type": "future",
        },
    }
    fake.ticker_rows = {
        "BTC/USDT:USDT": {
            "last": "100",
            "bid": "99",
            "ask": "101",
            "baseVolume": "12.5",
        }
    }

    assert await adapter.fetch_tickers(["BTC-USDT-261225"]) == []


@pytest.mark.asyncio
async def test_okx_base_adapter_skips_malformed_ticker_prices_and_defaults_invalid_volume():
    spot_adapter = OKXBaseAdapter("", "", "", "spot")
    spot_fake = FakeOKX.instances[0]
    spot_fake.raw_ticker_response = {
        "data": [
            {
                "instId": "BAD-LAST",
                "last": "not-a-number",
                "bidPx": "99",
                "askPx": "101",
                "vol24h": "1",
            },
            {
                "instId": "BAD-BID",
                "last": "100",
                "bidPx": "-1",
                "askPx": "101",
                "vol24h": "1",
            },
            {
                "instId": "BAD-ASK",
                "last": "100",
                "bidPx": "99",
                "askPx": "inf",
                "vol24h": "1",
            },
            {
                "instId": "OK-SPOT",
                "last": "100",
                "bidPx": "99",
                "askPx": "101",
                "vol24h": "bad",
            },
        ]
    }

    spot_tickers = await spot_adapter.fetch_tickers(
        ["BAD-LAST", "BAD-BID", "BAD-ASK", "OK-SPOT"]
    )

    swap_adapter = OKXBaseAdapter("", "", "", "swap")
    swap_fake = FakeOKX.instances[1]
    swap_fake.ticker_rows = {
        "BAD-SWAP": {"last": "nan", "bid": "99", "ask": "101", "baseVolume": "1"},
        "OK-SWAP": {"last": "200", "bid": "199", "ask": "201", "baseVolume": "-1"},
    }

    swap_tickers = await swap_adapter.fetch_tickers(["BAD-SWAP", "OK-SWAP"])

    assert spot_tickers == []
    assert swap_tickers == []


@pytest.mark.asyncio
async def test_okx_base_adapter_rejects_amount_below_market_min_before_create_order():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {"BTC-USDT-SWAP": {"limits": {"amount": {"min": 0.01}}}}

    with pytest.raises(ValueError, match="amount below exchange minimum"):
        await adapter.submit(
            Order(
                id="1",
                symbol="BTC-USDT-SWAP",
                side=OrderSide.BUY,
                type=OrderType.LIMIT,
                amount=0.001,
                price=50000.0,
            )
        )

    assert fake.create_order_calls == []


@pytest.mark.asyncio
async def test_okx_base_adapter_rejects_invalid_precision_before_create_order():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {"BTC-USDT-SWAP": {"precision": {"amount": 2, "price": 1}}}

    with pytest.raises(ValueError, match="amount precision exceeds exchange precision"):
        await adapter.submit(
            Order(
                id="1",
                symbol="BTC-USDT-SWAP",
                side=OrderSide.BUY,
                type=OrderType.LIMIT,
                amount=0.001,
                price=50000.0,
            )
        )

    assert fake.create_order_calls == []


@pytest.mark.asyncio
async def test_okx_base_adapter_accepts_tick_size_precision():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC-USDT-SWAP": {
            "precisionMode": exchange_base.TICK_SIZE,
            "precision": {"amount": 0.001, "price": 0.1},
        }
    }

    await adapter.submit(
        Order(
            id="1",
            symbol="BTC-USDT-SWAP",
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            amount=0.123,
            price=50000.1,
        )
    )

    assert len(fake.create_order_calls) == 1


@pytest.mark.asyncio
async def test_okx_base_adapter_rejects_amount_not_on_tick_size():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC-USDT-SWAP": {
            "precisionMode": exchange_base.TICK_SIZE,
            "precision": {"amount": 0.001, "price": 0.1},
        }
    }

    with pytest.raises(
        ValueError,
        match="amount precision exceeds exchange precision",
    ):
        await adapter.submit(
            Order(
                id="1",
                symbol="BTC-USDT-SWAP",
                side=OrderSide.BUY,
                type=OrderType.LIMIT,
                amount=0.1234,
                price=50000.1,
            )
        )

    assert fake.create_order_calls == []


@pytest.mark.asyncio
async def test_okx_base_adapter_rejects_price_not_on_tick_size():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC-USDT-SWAP": {
            "precisionMode": exchange_base.TICK_SIZE,
            "precision": {"amount": 0.001, "price": 0.1},
        }
    }

    with pytest.raises(
        ValueError,
        match="price precision exceeds exchange precision",
    ):
        await adapter.submit(
            Order(
                id="1",
                symbol="BTC-USDT-SWAP",
                side=OrderSide.BUY,
                type=OrderType.LIMIT,
                amount=0.123,
                price=50000.05,
            )
        )

    assert fake.create_order_calls == []


@pytest.mark.asyncio
async def test_okx_base_adapter_validates_significant_digit_precision():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC-USDT-SWAP": {
            "precisionMode": exchange_base.SIGNIFICANT_DIGITS,
            "precision": {"amount": 3, "price": 5},
        }
    }

    await adapter.submit(
        Order(
            id="1",
            symbol="BTC-USDT-SWAP",
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            amount=0.123,
            price=50000.0,
        )
    )
    with pytest.raises(
        ValueError,
        match="amount precision exceeds exchange precision",
    ):
        await adapter.submit(
            Order(
                id="2",
                symbol="BTC-USDT-SWAP",
                side=OrderSide.BUY,
                type=OrderType.LIMIT,
                amount=0.1234,
                price=50000.0,
            )
        )

    assert len(fake.create_order_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_status", "expected_filled", "expected_remaining"),
    [
        (
            {"status": "closed", "average": "50100"},
            exchange_base.OrderStatus.FILLED,
            1.0,
            0.0,
        ),
        (
            {
                "status": "open",
                "filled": "0.25",
                "remaining": "0.75",
                "average": "49900",
            },
            exchange_base.OrderStatus.PARTIALLY_FILLED,
            0.25,
            0.75,
        ),
        (
            {"status": "open"},
            exchange_base.OrderStatus.PENDING,
            0.0,
            1.0,
        ),
    ],
)
async def test_okx_base_adapter_submit_applies_response_fill_group_atomically(
    response, expected_status, expected_filled, expected_remaining
):
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {"BTC-USDT-SWAP": {"limits": {}, "precision": {}}}

    async def create_order(*args, **kwargs):
        return {"id": "submit-fill-group", "timestamp": 1700000000000, **response}

    fake.create_order = create_order
    order = Order(
        id="submit-fill-group",
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=1.0,
        price=50000.0,
    )

    result = await adapter.submit(order)

    assert result.status is expected_status
    assert result.filled_amount == pytest.approx(expected_filled)
    assert result.remaining_amount == pytest.approx(expected_remaining)
    if "average" in response:
        assert result.fill_price == pytest.approx(float(response["average"]))


@pytest.mark.asyncio
async def test_okx_base_adapter_submit_prefers_explicit_partial_quantity_over_terminal_status():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {"BTC-USDT-SWAP": {"limits": {}, "precision": {}}}

    async def create_order(*args, **kwargs):
        return {
            "id": "submit-partial-closed",
            "status": "filled",
            "filled": "0.4",
            "remaining": "0.6",
            "average": "50050",
        }

    fake.create_order = create_order
    order = Order(
        id="submit-partial-closed",
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=1.0,
    )

    result = await adapter.submit(order)

    assert result.status is exchange_base.OrderStatus.PARTIALLY_FILLED
    assert result.filled_amount == pytest.approx(0.4)
    assert result.remaining_amount == pytest.approx(0.6)
    assert result.fill_price == pytest.approx(50050.0)


@pytest.mark.asyncio
async def test_okx_base_adapter_submits_non_empty_client_order_id_without_losing_it():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {"BTC-USDT-SWAP": {"limits": {}, "precision": {}}}
    order = Order(
        id="client-id-order",
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=0.01,
        client_order_id="stable-client-id",
    )

    result = await adapter.submit(order)

    assert fake.create_order_calls[0]["params"]["clientOrderId"] == "stable-client-id"
    assert "clOrdId" not in fake.create_order_calls[0]["params"]
    assert result.client_order_id == "stable-client-id"


@pytest.mark.asyncio
async def test_okx_base_adapter_does_not_fabricate_client_order_id_when_empty():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {"BTC-USDT-SWAP": {"limits": {}, "precision": {}}}
    order = Order(
        id="empty-client-id-order",
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=0.01,
    )

    result = await adapter.submit(order)

    assert "clientOrderId" not in fake.create_order_calls[0]["params"]
    assert "clOrdId" not in fake.create_order_calls[0]["params"]
    assert result.client_order_id == ""


@pytest.mark.asyncio
async def test_okx_base_adapter_defaults_derivative_orders_to_cross_td_mode():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {"BTC-USDT-SWAP": {"limits": {}, "precision": {}}}
    order = Order(
        id="1",
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=0.01,
    )

    await adapter.submit(order)

    assert fake.create_order_calls == [
        {
            "symbol": "BTC-USDT-SWAP",
            "type": "market",
            "side": "buy",
            "amount": 0.01,
            "price": None,
            "params": {"tdMode": "cross"},
        }
    ]


@pytest.mark.asyncio
async def test_okx_base_adapter_passes_reduce_only_params_for_derivatives():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC-USDT-SWAP": {
            "limits": {"amount": {"min": 0.001}, "cost": {"min": 1}},
            "precision": {"amount": 3, "price": 1},
        }
    }
    order = Order(
        id="1",
        symbol="BTC-USDT-SWAP",
        side=OrderSide.SELL,
        type=OrderType.LIMIT,
        amount=0.01,
        price=50000.0,
        reduce_only=True,
        params={"reduceOnly": True},
    )

    result = await adapter.submit(order)

    assert result.id == "1"
    assert result.exchange_order_id == "okx-1"
    assert fake.create_order_calls == [
        {
            "symbol": "BTC-USDT-SWAP",
            "type": "limit",
            "side": "sell",
            "amount": 0.01,
            "price": 50000.0,
            "params": {"reduceOnly": True, "tdMode": "cross"},
        }
    ]


@pytest.mark.asyncio
async def test_okx_base_adapter_safely_maps_invalid_filled_response_timestamp():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {"BTC-USDT-SWAP": {"limits": {}, "precision": {}}}

    async def create_order(*args, **kwargs):
        return {
            "id": "okx-filled-invalid-time",
            "status": "closed",
            "average": 50000.0,
            "timestamp": float("nan"),
        }

    fake.create_order = create_order
    order = Order(
        id="1",
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=0.01,
    )

    result = await adapter.submit(order)

    assert result.fill_time == 0
    assert result.updated_at == 0


@pytest.mark.asyncio
async def test_okx_base_adapter_maps_stop_order_to_trigger_params():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {"BTC-USDT-SWAP": {"limits": {}, "precision": {}}}
    order = Order(
        id="1",
        symbol="BTC-USDT-SWAP",
        side=OrderSide.SELL,
        type=OrderType.STOP,
        amount=0.01,
        trigger_price=49000.0,
        params={"reduceOnly": True},
    )

    result = await adapter.submit(order)

    assert result.id == "1"
    assert result.exchange_order_id == "okx-1"
    assert fake.create_order_calls == [
        {
            "symbol": "BTC-USDT-SWAP",
            "type": "market",
            "side": "sell",
            "amount": 0.01,
            "price": None,
            "params": {
                "reduceOnly": True,
                "tdMode": "cross",
                "triggerPrice": 49000.0,
            },
        }
    ]


@pytest.mark.asyncio
async def test_okx_base_adapter_passes_stop_loss_take_profit_params():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {"BTC-USDT-SWAP": {"limits": {}, "precision": {}}}
    order = Order(
        id="1",
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=0.01,
        price=50000.0,
        stop_loss=49000.0,
        take_profit=52000.0,
        params={"tdMode": "cross"},
    )

    result = await adapter.submit(order)

    assert result.id == "1"
    assert result.exchange_order_id == "okx-1"
    assert fake.create_order_calls == [
        {
            "symbol": "BTC-USDT-SWAP",
            "type": "limit",
            "side": "buy",
            "amount": 0.01,
            "price": 50000.0,
            "params": {
                "tdMode": "cross",
                "stopLoss": {"triggerPrice": 49000.0},
                "takeProfit": {"triggerPrice": 52000.0},
            },
        }
    ]


@pytest.mark.asyncio
async def test_okx_base_adapter_rejects_stop_order_without_trigger_price():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {"BTC-USDT-SWAP": {"limits": {}, "precision": {}}}

    with pytest.raises(ValueError, match="Stop orders require trigger_price"):
        await adapter.submit(
            Order(
                id="1",
                symbol="BTC-USDT-SWAP",
                side=OrderSide.SELL,
                type=OrderType.STOP,
                amount=0.01,
            )
        )

    assert fake.create_order_calls == []


@pytest.mark.asyncio
async def test_okx_base_adapter_fetch_account_snapshot_parses_balance_totals():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.balance_response = {
        "USDT": {"total": "560", "free": "525", "used": "35"},
        "info": {
            "data": [
                {
                    "totalEq": "660.5",
                    "upl": "7.75",
                    "details": [
                        {
                            "ccy": "USDT",
                            "eq": "565.5",
                            "eqUtd": "560",
                            "cashBal": "550",
                            "availBal": "525",
                            "upl": "5.5",
                            "realizedPnl": "1.25",
                            "uTime": "1700000000000",
                        },
                        {
                            "ccy": "BTC",
                            "eq": "0.002",
                            "eqUsd": "100",
                            "cashBal": "0.002",
                            "availBal": "0.0015",
                            "upl": "2",
                            "realizedPnl": "0.75",
                            "uTime": "1700000000001",
                        },
                    ],
                }
            ]
        },
    }

    snapshot = await adapter.fetch_account_snapshot()

    assert fake.fetch_balance_calls == 1
    assert snapshot.currency == "USDT"
    assert snapshot.equity == 660.5
    assert snapshot.cash_balance == 550.0
    assert snapshot.available_balance == 525.0
    assert snapshot.unrealized_pnl == 7.75
    assert snapshot.realized_pnl == 2.0
    assert snapshot.updated_at == 1700000000000
    assert snapshot.assets == [
        AssetBalance(
            ccy="USDT",
            cash_bal=550.0,
            eq=565.5,
            eq_utd=560.0,
            avail_bal=525.0,
            upl=5.5,
        ),
        AssetBalance(
            ccy="BTC",
            cash_bal=0.002,
            eq=0.002,
            eq_utd=100.0,
            avail_bal=0.0015,
            upl=2.0,
        ),
    ]


@pytest.mark.parametrize("symbol", ["BTC/USDT", "BTC-USDT"])
def test_canonical_spot_symbol_uses_inst_id_shape(symbol):
    assert exchange_base.canonical_symbol(symbol, market_type="spot") == "BTC-USDT"


@pytest.mark.parametrize(
    ("market_type", "symbol"),
    [
        ("spot", "BTC-USDT-SWAP"),
        ("spot", "BTC-USDT-260925"),
        ("spot", "BTC-USD-260925-100000-C"),
        ("swap", "BTC-USDT-260925"),
        ("swap", "BTC-USD-260925-100000-C"),
        ("future", "BTC-USDT-SWAP"),
        ("future", "BTC-USD-260925-100000-C"),
        ("option", "BTC-USDT-SWAP"),
        ("option", "BTC-USDT-260925"),
    ],
)
def test_supported_canonical_symbol_rejects_other_market_shapes(market_type, symbol):
    assert not exchange_base.is_supported_canonical_symbol(
        symbol, market_type=market_type
    )


@pytest.mark.parametrize(
    ("market_type", "symbol"),
    [
        ("spot", "BTC-USDT"),
        ("swap", "BTC-USDT-SWAP"),
        ("future", "BTC-USDT-260925"),
        ("option", "BTC-USD-260925-100000-C"),
    ],
)
def test_supported_canonical_symbol_accepts_market_shape(market_type, symbol):
    assert exchange_base.is_supported_canonical_symbol(
        symbol, market_type=market_type
    )


@pytest.mark.parametrize(
    ("market_type", "symbol"),
    [
        ("spot", "BTC-USDT-SWAP"),
        ("swap", "BTC-USDT-260925"),
        ("swap", "BTC-USD-260925-100000-C"),
        ("future", "BTC-USDT-SWAP"),
        ("option", "BTC-USDT-260925"),
    ],
)
def test_canonical_symbol_support_rejects_wrong_market_identity(market_type, symbol):
    assert not exchange_base.is_supported_canonical_symbol(
        symbol, market_type=market_type
    )


def test_canonical_symbol_metadata_aliases_are_filtered_by_market_type():
    markets = {
        "BTC/USDT:USDT": {
            "id": "BTC-USDT-SWAP",
            "symbol": "BTC/USDT:USDT",
            "type": "swap",
        },
        "BTC/USDT:USDT-260925": {
            "id": "BTC-USDT-260925",
            "symbol": "BTC/USDT:USDT-260925",
            "type": "future",
        },
    }

    assert exchange_base.canonical_symbol(
        "BTC/USDT:USDT-260925", market_type="swap", markets=markets
    ) == "BTC-USDT-260925"
    assert exchange_base.canonical_symbol(
        "BTC/USDT:USDT", market_type="future", markets=markets
    ) == "BTC-USDT-SWAP"


def test_canonical_inverse_swap_aliases_preserve_base_settlement_identity():
    assert exchange_base.canonical_symbol(
        "BTC-USD-SWAP", market_type="swap"
    ) == "BTC-USD-SWAP"
    assert exchange_base.canonical_symbol(
        "BTC/USD:BTC", market_type="swap"
    ) == "BTC-USD-SWAP"
    assert exchange_base.canonical_symbol(
        "BTC/USD:USD", market_type="swap"
    ) == "BTC/USD:USD"
    assert exchange_base.canonical_symbol(
        "BTC/USD:USDT", market_type="swap"
    ) == "BTC/USD:USDT"


def test_canonical_swap_symbol_uses_exact_market_metadata_without_base_matching():
    markets = {
        "BTC/USDT:USDT": {
            "id": "BTC-USDT-SWAP",
            "symbol": "BTC/USDT:USDT",
        },
        "BTC/USDT:USDT-260925": {
            "id": "BTC-USDT-260925",
            "symbol": "BTC/USDT:USDT-260925",
        },
    }

    assert exchange_base.canonical_symbol(
        "BTC/USDT:USDT", market_type="swap", markets=markets
    ) == "BTC-USDT-SWAP"
    assert exchange_base.canonical_symbol(
        "BTC-USDT-SWAP", market_type="swap", markets=markets
    ) == "BTC-USDT-SWAP"
    assert exchange_base.canonical_symbol(
        "BTC-USDT-260925", market_type="swap", markets=markets
    ) == "BTC-USDT-260925"
    assert exchange_base.canonical_symbol(
        "BTC-USDT", market_type="swap", markets=markets
    ) == "BTC-USDT"


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("BTC/USDT:USDT-260925", "BTC-USDT-260925"),
        ("BTC-USDT-260925", "BTC/USDT:USDT-260925"),
    ],
)
def test_canonical_dated_future_aliases_match_without_market_metadata(left, right):
    assert exchange_base.canonical_symbol(left, market_type="swap") == (
        exchange_base.canonical_symbol(right, market_type="swap")
    )


def test_canonical_dated_future_aliases_do_not_match_different_expiry():
    assert exchange_base.canonical_symbol(
        "BTC/USDT:USDT-260925", market_type="swap"
    ) != exchange_base.canonical_symbol("BTC-USDT-261225", market_type="swap")


def test_canonical_inverse_future_preserves_non_quote_settlement_identity():
    assert exchange_base.canonical_symbol(
        "BTC/USD:BTC-260925", market_type="future"
    ) == "BTC-USD-260925"
    assert exchange_base.canonical_symbol(
        "BTC-USD-260925", market_type="future"
    ) == "BTC-USD-260925"
    assert exchange_base.canonical_symbol(
        "BTC/USD:USD-260925", market_type="future"
    ) != exchange_base.canonical_symbol("BTC/USD:BTC-260925", market_type="future")


def test_canonical_inverse_option_preserves_non_quote_settlement_identity():
    assert exchange_base.canonical_symbol(
        "BTC/USD:BTC-260919-66000-C", market_type="option"
    ) == "BTC-USD-260919-66000-C"
    assert exchange_base.canonical_symbol(
        "BTC-USD-260919-66000-C", market_type="option"
    ) == "BTC-USD-260919-66000-C"
    assert exchange_base.canonical_symbol(
        "BTC/USD:USD-260919-66000-C", market_type="option"
    ) != exchange_base.canonical_symbol(
        "BTC/USD:BTC-260919-66000-C", market_type="option"
    )


def test_canonical_inverse_derivatives_match_only_exact_contract_components():
    expected = exchange_base.canonical_symbol(
        "BTC/USD:BTC-260919-66000-C", market_type="option"
    )
    for different in (
        "ETH/USD:BTC-260919-66000-C",
        "BTC/USDT:BTC-260919-66000-C",
        "BTC/USD:BTC-260925-66000-C",
        "BTC/USD:BTC-260919-66001-C",
        "BTC/USD:BTC-260919-66000-P",
    ):
        assert exchange_base.canonical_symbol(different, market_type="option") != expected


def test_canonical_dated_future_aliases_prefer_exact_market_metadata():
    markets = {
        "BTC/USDT:USDT-260925": {
            "id": "BTC-USDT-260925",
            "symbol": "BTC/USDT:USDT-260925",
            "type": "future",
        },
        "BTC/USDT:USDT-261225": {
            "id": "BTC-USDT-261225",
            "symbol": "BTC/USDT:USDT-261225",
            "type": "future",
        },
    }

    assert exchange_base.canonical_symbol(
        "BTC/USDT:USDT-260925", market_type="swap", markets=markets
    ) == "BTC-USDT-260925"
    assert exchange_base.canonical_symbol(
        "BTC-USDT-261225", market_type="swap", markets=markets
    ) == "BTC-USDT-261225"


def test_canonical_option_symbol_preserves_decimal_strike_exactly_across_aliases():
    assert exchange_base.canonical_symbol(
        "BTC-USD-260925-100000.5-C", market_type="option"
    ) == "BTC-USD-260925-100000.5-C"
    assert exchange_base.canonical_symbol(
        "BTC/USD:USD-260925-100000.5-C", market_type="option"
    ) == "BTC/USD:USD-260925-100000.5-C"
    assert exchange_base.canonical_symbol(
        "BTC-USD-260925-100000.50-C", market_type="option"
    ) != exchange_base.canonical_symbol(
        "BTC-USD-260925-100000.5-C", market_type="option"
    )
    assert exchange_base.canonical_symbol(
        "BTC-USD-260925-100000.5-C", market_type="option"
    ) != exchange_base.canonical_symbol(
        "BTC-USD-260925-100001.5-C", market_type="option"
    )
    assert exchange_base.canonical_symbol(
        "BTC-USD-260925-100000.5-C", market_type="option"
    ) != exchange_base.canonical_symbol(
        "BTC-USD-260925-100000.5-P", market_type="option"
    )


def test_canonical_option_symbol_preserves_expiry_strike_and_call_put():
    markets = {
        "BTC/USD:USD-260925-100000-C": {
            "id": "BTC-USD-260925-100000-C",
            "symbol": "BTC/USD:USD-260925-100000-C",
            "type": "option",
        },
        "BTC/USD:USD-260925-110000-C": {
            "id": "BTC-USD-260925-110000-C",
            "symbol": "BTC/USD:USD-260925-110000-C",
            "type": "option",
        },
    }

    assert exchange_base.canonical_symbol(
        "BTC-USD-260925-100000-C", market_type="option", markets=markets
    ) == "BTC-USD-260925-100000-C"
    assert exchange_base.canonical_symbol(
        "BTC/USD:USD-260925-100000-C", market_type="option", markets=markets
    ) == "BTC-USD-260925-100000-C"
    assert exchange_base.canonical_symbol(
        "BTC-USD-260925-100000-P", market_type="option", markets=markets
    ) == "BTC-USD-260925-100000-P"
    assert exchange_base.canonical_symbol(
        "BTC-USD-261225-100000-C", market_type="option", markets=markets
    ) == "BTC-USD-261225-100000-C"
    assert exchange_base.canonical_symbol(
        "BTC-USD-260925-110000-C", market_type="option", markets=markets
    ) == "BTC-USD-260925-110000-C"
    assert exchange_base.canonical_symbol(
        "BTC/USD:USD-260925-100000-C", market_type="option"
    ) == "BTC/USD:USD-260925-100000-C"
    assert exchange_base.canonical_symbol(
        "BTC-USD-260925-100000-C", market_type="option"
    ) != exchange_base.canonical_symbol(
        "BTC-USD-260925-100000-P", market_type="option"
    )
    assert exchange_base.canonical_symbol(
        "BTC-USD-260925-100000-C", market_type="option"
    ) != exchange_base.canonical_symbol(
        "BTC-USD-261225-100000-C", market_type="option"
    )
    assert exchange_base.canonical_symbol(
        "BTC-USD-260925-100000-C", market_type="option"
    ) != exchange_base.canonical_symbol(
        "BTC-USD-260925-110000-C", market_type="option"
    )


@pytest.mark.asyncio
async def test_okx_base_adapter_requests_exact_unified_decimal_option_symbols_for_private_fetches():
    adapter = OKXBaseAdapter("", "", "", "option")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC/USD:USD-260925-100000.5-C": {
            "id": "BTC-USD-260925-100000.5-C",
            "symbol": "BTC/USD:USD-260925-100000.5-C",
            "type": "option",
        },
    }
    calls = []

    async def fetch_positions(symbols=None):
        calls.append(symbols)
        return []

    fake.fetch_positions = fetch_positions

    await adapter.fetch_position_snapshots(["BTC-USD-260925-100000.5-C"])

    assert calls == [["BTC/USD:USD-260925-100000.5-C"]]


@pytest.mark.asyncio
async def test_okx_base_adapter_generates_inverse_unified_symbols_without_market_metadata():
    future_adapter = OKXBaseAdapter("", "", "", "future")
    option_adapter = OKXBaseAdapter("", "", "", "option")

    assert future_adapter._to_ccxt_symbol("BTC-USD-260925") == "BTC/USD:BTC-260925"
    assert option_adapter._to_ccxt_symbol("BTC-USD-260919-66000-C") == (
        "BTC/USD:BTC-260919-66000-C"
    )


@pytest.mark.asyncio
async def test_okx_base_adapter_generates_inverse_swap_unified_symbol_without_metadata():
    adapter = OKXBaseAdapter("", "", "", "swap")

    assert adapter._to_ccxt_symbol("BTC-USD-SWAP") == "BTC/USD:BTC"
    assert adapter._to_ccxt_symbol("BTC/USD:BTC") == "BTC/USD:BTC"


@pytest.mark.asyncio
async def test_okx_base_adapter_returns_native_inverse_swap_ticker_identity():
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]
    fake.ticker_rows = {
        "BTC/USD:BTC": {
            "symbol": "BTC/USD:BTC",
            "last": 68000,
            "bid": 67999.5,
            "ask": 68000.5,
            "baseVolume": 123.45,
        }
    }

    tickers = await adapter.fetch_tickers(["BTC-USD-SWAP"])

    assert fake.fetch_tickers_calls == [["BTC/USD:BTC"]]
    assert tickers[0]["symbol"] == "BTC-USD-SWAP"


@pytest.mark.asyncio
async def test_okx_base_adapter_does_not_merge_inverse_swap_settlements():
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC/USD:BTC": {
            "id": "BTC-USD-SWAP",
            "symbol": "BTC/USD:BTC",
            "type": "swap",
        },
        "BTC/USD:USD": {
            "id": "BTC-USD-USD-SWAP",
            "symbol": "BTC/USD:USD",
            "type": "swap",
        },
    }

    assert adapter._to_ccxt_symbol("BTC-USD-SWAP") == "BTC/USD:BTC"
    assert adapter._to_ccxt_symbol("BTC/USD:USD") == "BTC/USD:USD"
    assert adapter.canonical_symbol("BTC/USD:USD") != "BTC-USD-SWAP"


@pytest.mark.asyncio
async def test_okx_base_adapter_requests_exact_unified_symbols_for_option_private_fetches():
    adapter = OKXBaseAdapter("", "", "", "option")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC/USD:USD-260925-100000-C": {
            "id": "BTC-USD-260925-100000-C",
            "symbol": "BTC/USD:USD-260925-100000-C",
            "type": "option",
        },
    }
    calls = []

    async def fetch_positions(symbols=None):
        calls.append(symbols)
        return []

    fake.fetch_positions = fetch_positions

    await adapter.fetch_position_snapshots(["BTC-USD-260925-100000-C"])

    assert calls == [["BTC/USD:USD-260925-100000-C"]]


@pytest.mark.asyncio
async def test_okx_base_adapter_requests_exact_unified_symbols_for_derivative_private_fetches():
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC/USDT:USDT": {
            "id": "BTC-USDT-SWAP",
            "symbol": "BTC/USDT:USDT",
            "type": "swap",
        },
        "BTC/USDT:USDT-260925": {
            "id": "BTC-USDT-260925",
            "symbol": "BTC/USDT:USDT-260925",
            "type": "future",
        },
        "BTC/USDT:USDT-261225": {
            "id": "BTC-USDT-261225",
            "symbol": "BTC/USDT:USDT-261225",
            "type": "future",
        },
    }
    open_order_calls = []
    trade_calls = []
    position_calls = []

    async def fetch_open_orders(symbol=None, **kwargs):
        open_order_calls.append(symbol)
        return []

    async def fetch_my_trades(symbol=None, since=None, limit=None):
        trade_calls.append((symbol, since, limit))
        return []

    async def fetch_positions(symbols=None):
        position_calls.append(symbols)
        return []

    fake.fetch_open_orders = fetch_open_orders
    fake.fetch_my_trades = fetch_my_trades
    fake.fetch_positions = fetch_positions

    await adapter.fetch_open_order_snapshots(
        ["BTC-USDT-SWAP", "BTC-USDT-260925", "BTC-USDT-261225"]
    )
    await adapter.fetch_recent_trade_snapshots(["BTC-USDT-260925"], since=10, limit=20)
    await adapter.fetch_position_snapshots(["BTC-USDT-SWAP", "BTC-USDT-260925"])

    assert open_order_calls == [
        "BTC/USDT:USDT",
        "BTC/USDT:USDT",
        "BTC/USDT:USDT-260925",
        "BTC/USDT:USDT-260925",
        "BTC/USDT:USDT-261225",
        "BTC/USDT:USDT-261225",
    ]
    assert trade_calls == [("BTC/USDT:USDT-260925", 10, 20)]
    assert position_calls == [["BTC/USDT:USDT", "BTC/USDT:USDT-260925"]]


@pytest.mark.asyncio
async def test_okx_base_adapter_keeps_spot_private_request_symbols_compatible():
    adapter = OKXBaseAdapter("", "", "", "spot")
    fake = FakeOKX.instances[0]
    open_order_calls = []
    trade_calls = []

    async def fetch_open_orders(symbol=None, **kwargs):
        open_order_calls.append(symbol)
        return []

    async def fetch_my_trades(symbol=None, since=None, limit=None):
        trade_calls.append(symbol)
        return []

    fake.fetch_open_orders = fetch_open_orders
    fake.fetch_my_trades = fetch_my_trades

    await adapter.fetch_open_order_snapshots(["BTC-USDT", "BTC/USDT"])
    await adapter.fetch_recent_trade_snapshots(["BTC-USDT", "BTC/USDT"])

    assert open_order_calls == [
        "BTC/USDT", "BTC/USDT", "BTC/USDT", "BTC/USDT"
    ]
    assert trade_calls == ["BTC/USDT", "BTC/USDT"]


@pytest.mark.asyncio
async def test_okx_base_adapter_does_not_map_unknown_derivative_symbol_by_base_quote():
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC/USDT:USDT-260925": {
            "id": "BTC-USDT-260925",
            "symbol": "BTC/USDT:USDT-260925",
            "type": "future",
        }
    }
    calls = []

    async def fetch_open_orders(symbol=None, **kwargs):
        calls.append(symbol)
        return []

    fake.fetch_open_orders = fetch_open_orders

    await adapter.fetch_open_order_snapshots(["BTC-USDT-261225"])

    assert calls == ["BTC/USDT:USDT-261225", "BTC/USDT:USDT-261225"]


@pytest.mark.asyncio
async def test_okx_base_adapter_rejects_malformed_account_numeric_field():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    FakeOKX.instances[0].balance_response = {
        "info": {"data": [{"totalEq": "not-a-number", "details": []}]}
    }

    with pytest.raises(MalformedProviderData, match="totalEq"):
        await adapter.fetch_account_snapshot()


@pytest.mark.asyncio
async def test_okx_base_adapter_rejects_nonfinite_asset_numeric_field():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    FakeOKX.instances[0].balance_response = {
        "info": {"data": [{"details": [{"ccy": "USDT", "eq": "nan"}]}]}
    }

    with pytest.raises(MalformedProviderData, match="eq"):
        await adapter.fetch_account_snapshot()


@pytest.mark.asyncio
async def test_okx_base_adapter_rejects_nonfinite_or_illegal_account_values():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    FakeOKX.instances[0].balance_response = {
        "info": {
            "data": [{
                "totalEq": "inf",
                "upl": "-3.5",
                "details": [{
                    "ccy": "USDT",
                    "eq": "-1",
                    "eqUtd": "nan",
                    "cashBal": "bad",
                    "availBal": "-2",
                    "upl": "-4.5",
                    "realizedPnl": "-1",
                }],
            }]
        }
    }

    with pytest.raises(MalformedProviderData, match="totalEq"):
        await adapter.fetch_account_snapshot()


@pytest.mark.asyncio
async def test_okx_base_adapter_fetch_account_snapshot_preserves_zero_available_balance():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.balance_response = {
        "info": {
            "data": [
                {
                    "totalEq": "660.5",
                    "details": [
                        {
                            "ccy": "USDT",
                            "eq": "565.5",
                            "eqUtd": "560",
                            "cashBal": "550",
                            "availBal": "0",
                            "uTime": "1700000000000",
                        }
                    ],
                }
            ]
        },
    }

    snapshot = await adapter.fetch_account_snapshot()

    assert snapshot.cash_balance == 550.0
    assert snapshot.available_balance == 0.0
    assert isinstance(snapshot.available_balance, float)


@pytest.mark.asyncio
async def test_okx_base_adapter_fetch_account_snapshot_parses_account_level_totals():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.balance_response = {
        "free": {"USDT": 980.5},
        "total": {"USDT": 1000.25},
        "info": {
            "data": [
                {
                    "totalEq": "1000.25",
                    "availEq": "980.5",
                    "upl": "12.75",
                    "uTime": "1700000000123",
                }
            ]
        },
    }

    snapshot = await adapter.fetch_account_snapshot()

    assert fake.fetch_balance_calls == 1
    assert snapshot.currency == "USDT"
    assert snapshot.equity == 1000.25
    assert snapshot.cash_balance == 980.5
    assert snapshot.available_balance == 980.5
    assert snapshot.unrealized_pnl == 12.75
    assert snapshot.realized_pnl == 0.0
    assert snapshot.updated_at == 1700000000123
    assert snapshot.assets == []


@pytest.mark.asyncio
@pytest.mark.parametrize("envelope", [None, "malformed", {"info": None}, {"info": {}}])
async def test_okx_base_adapter_rejects_malformed_balance_envelopes(envelope):
    adapter = OKXBaseAdapter("", "", "", "spot")
    FakeOKX.instances[0].balance_response = envelope

    with pytest.raises(ValueError, match="Malformed provider balance response"):
        await adapter.fetch_account_snapshot()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [[None], [{}], [{"details": []}], [{"unknown": "value"}]],
)
async def test_okx_base_adapter_rejects_malformed_nonempty_balance_data(data):
    adapter = OKXBaseAdapter("", "", "", "spot")
    FakeOKX.instances[0].balance_response = {"info": {"data": data}}

    with pytest.raises(ValueError, match="Malformed provider balance response"):
        await adapter.fetch_account_snapshot()


@pytest.mark.asyncio
async def test_okx_base_adapter_accepts_valid_empty_balance_envelope():
    adapter = OKXBaseAdapter("", "", "", "spot")
    FakeOKX.instances[0].balance_response = {"info": {"data": []}}

    snapshot = await adapter.fetch_account_snapshot()

    assert snapshot.equity == 0.0
    assert snapshot.assets == []


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [None, {"id": "not-a-list"}, "malformed"])
async def test_okx_base_adapter_rejects_malformed_open_order_responses(response):
    adapter = OKXBaseAdapter("", "", "", "swap")

    async def fetch_open_orders(*args, **kwargs):
        return response

    FakeOKX.instances[0].fetch_open_orders = fetch_open_orders

    with pytest.raises(ValueError, match="Malformed provider open-order response"):
        await adapter.fetch_open_order_snapshots()


@pytest.mark.asyncio
async def test_okx_base_adapter_accepts_valid_empty_open_order_response():
    adapter = OKXBaseAdapter("", "", "", "swap")

    async def fetch_open_orders(*args, **kwargs):
        return []

    FakeOKX.instances[0].fetch_open_orders = fetch_open_orders

    assert await adapter.fetch_open_order_snapshots() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows",
    [
        [None],
        [{}],
        [
            {
                "id": "valid-order",
                "symbol": "BTC-USDT-SWAP",
                "side": "buy",
                "amount": "1",
            },
            None,
        ],
    ],
)
async def test_okx_base_adapter_rejects_malformed_open_order_rows(rows):
    adapter = OKXBaseAdapter("", "", "", "swap")

    async def fetch_open_orders(*args, **kwargs):
        return rows

    FakeOKX.instances[0].fetch_open_orders = fetch_open_orders

    with pytest.raises(ValueError, match="Malformed provider open-order row"):
        await adapter.fetch_open_order_snapshots()


@pytest.mark.asyncio
@pytest.mark.parametrize("envelope", [None, [], "malformed", {"data": "malformed"}])
async def test_okx_base_adapter_rejects_malformed_ohlcv_envelopes(envelope):
    adapter = OKXBaseAdapter("", "", "", "spot")
    FakeOKX.instances[0].raw_candle_response = envelope

    with pytest.raises(MalformedProviderData, match="OHLCV"):
        await adapter.fetch_ohlcv("BTC-USDT", "1m")


@pytest.mark.asyncio
async def test_okx_base_adapter_keeps_malformed_ticker_envelopes_non_fatal(envelope=None):
    adapter = OKXBaseAdapter("", "", "", "spot")
    FakeOKX.instances[0].raw_ticker_response = envelope

    assert await adapter.fetch_tickers(["BTC-USDT"]) == []


@pytest.mark.asyncio
async def test_okx_base_adapter_skips_malformed_rows_and_unknown_execution_fields(caplog):
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]
    fake.positions_response = [None, 1, {"side": "mystery"}]
    fake.markets = {"BTC-USDT-SWAP": {"limits": {}, "precision": {}}}
    fake.ticker_rows = {"BTC-USDT-SWAP": {"last": 1, "bid": 1, "ask": 1, "baseVolume": 1}}
    fake.fetch_tickers = lambda symbols: None
    async def malformed_order(*args, **kwargs):
        return None

    fake.create_order = malformed_order

    with pytest.raises(MalformedProviderData):
        await adapter.fetch_position_snapshots()
    with pytest.raises(MalformedProviderData):
        adapter._map_order(None)
    assert adapter._map_trade("bad") is None
    assert any("skipping" in record.message.lower() for record in caplog.records)


@pytest.mark.asyncio
async def test_okx_base_adapter_malformed_submit_response_is_recoverable():
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {"BTC-USDT-SWAP": {"limits": {}, "precision": {}}}

    async def malformed_order(*args, **kwargs):
        return None

    fake.create_order = malformed_order

    order = Order(
        id="client-order",
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=1.0,
    )
    with pytest.raises(AcceptedUnknownOrderResultError, match="reconciliation required"):
        await adapter.submit(order)

    async def empty_order(*args, **kwargs):
        return {}

    fake.create_order = empty_order
    with pytest.raises(AcceptedUnknownOrderResultError, match="reconciliation required"):
        await adapter.submit(order)


def test_okx_base_adapter_position_mapper_infers_short_from_negative_amount_without_side():
    adapter = OKXBaseAdapter("", "", "", "swap")

    snapshot = adapter._parse_position_snapshot(
        {
            "symbol": "BTC-USDT-SWAP",
            "contracts": "-2",
            "entryPrice": "100",
        }
    )

    assert snapshot.side == PositionSide.SHORT
    assert snapshot.amount == 2.0


def test_okx_base_adapter_rejects_explicit_side_conflicting_with_signed_amount():
    adapter = OKXBaseAdapter("", "", "", "swap")

    with pytest.raises(MalformedProviderData, match="conflicting position direction"):
        adapter._parse_position_snapshot(
            {
                "symbol": "BTC-USDT-SWAP",
                "contracts": "-2",
                "side": "long",
                "entryPrice": "100",
            }
        )


def test_okx_base_adapter_accepts_explicit_side_consistent_with_signed_amount():
    adapter = OKXBaseAdapter("", "", "", "swap")

    snapshot = adapter._parse_position_snapshot(
        {
            "symbol": "BTC-USDT-SWAP",
            "contracts": "-2",
            "side": "short",
            "entryPrice": "100",
        }
    )

    assert snapshot.side == PositionSide.SHORT
    assert snapshot.amount == 2.0


@pytest.mark.parametrize(
    "field",
    ["entryPrice", "markPrice", "unrealizedPnl", "realizedPnl", "leverage", "timestamp"],
)
def test_okx_base_adapter_position_mapper_rejects_invalid_explicit_values(field):
    adapter = OKXBaseAdapter("", "", "", "swap")
    values = {
        "entryPrice": "inf",
        "markPrice": "nan",
        "unrealizedPnl": "nan",
        "realizedPnl": "inf",
        "leverage": "-3",
        "timestamp": "-1",
    }

    with pytest.raises(MalformedProviderData, match="position row"):
        adapter._parse_position_snapshot({
            "symbol": "BTC/USDT:USDT",
            "contracts": "-2",
            "side": "short",
            field: values[field],
        })


def test_okx_base_adapter_position_mapper_rejects_leverage_that_cannot_be_stored_in_sqlite():
    adapter = OKXBaseAdapter("", "", "", "swap")

    with pytest.raises(MalformedProviderData, match="position row"):
        adapter._parse_position_snapshot(
            {
                "symbol": "BTC-USDT-SWAP",
                "contracts": "1",
                "entryPrice": "100",
                "leverage": str(2**63),
                "timestamp": "1",
            }
        )


@pytest.mark.asyncio
async def test_okx_base_adapter_fetch_position_snapshots_uses_inst_id_and_consistent_side():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.positions_response = [
        {
            "symbol": "BTC/USDT:USDT",
            "side": "long",
            "contracts": "0.2",
            "entryPrice": "50000",
            "markPrice": "51000",
            "unrealizedPnl": "200",
            "realizedPnl": "10",
            "leverage": "5",
            "timestamp": "1700000000000",
            "info": {"instId": "BTC-USDT-SWAP", "posSide": "long"},
        }
    ]

    snapshots = await adapter.fetch_position_snapshots(["BTC-USDT-SWAP"])

    assert fake.fetch_positions_calls == [["BTC/USDT:USDT"]]
    assert len(snapshots) == 1
    assert snapshots[0].symbol == "BTC-USDT-SWAP"
    assert snapshots[0].side == PositionSide.LONG
    assert snapshots[0].amount == 0.2
    assert snapshots[0].entry_price == 50000.0
    assert snapshots[0].mark_price == 51000.0
    assert snapshots[0].unrealized_pnl == 200.0
    assert snapshots[0].realized_pnl == 10.0
    assert snapshots[0].leverage == 5
    assert snapshots[0].updated_at == 1700000000000


@pytest.mark.asyncio
async def test_okx_base_adapter_maps_net_positions_from_signed_amount_and_preserves_hedge_side():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.positions_response = [
        {
            "symbol": "SOL-USDT-SWAP",
            "contracts": "-2",
            "entryPrice": "150",
            "info": {"posSide": "net"},
        },
        {
            "symbol": "DOGE-USDT-SWAP",
            "contracts": "0",
            "entryPrice": "0.1",
            "info": {"posSide": "net"},
        },
        {
            "symbol": "ETH/USDT:USDT",
            "contracts": "1.5",
            "entryPrice": "3000",
            "info": {"posSide": "long"},
        },
    ]

    snapshots = await adapter.fetch_position_snapshots()

    assert fake.fetch_positions_calls == [None]
    assert [(snapshot.symbol, snapshot.side, snapshot.amount) for snapshot in snapshots] == [
        ("SOL-USDT-SWAP", PositionSide.SHORT, 2.0),
        ("DOGE-USDT-SWAP", PositionSide.LONG, 0.0),
        ("ETH/USDT:USDT", PositionSide.LONG, 1.5),
    ]


@pytest.mark.parametrize("side", ["hold", "", "future"])
def test_okx_base_adapter_rejects_explicit_invalid_position_side(side):
    adapter = OKXBaseAdapter("", "", "", "swap")

    with pytest.raises(MalformedProviderData, match="position row"):
        adapter._parse_position_snapshot(
            {
                "symbol": "BTC-USDT-SWAP",
                "contracts": "-2",
                "entryPrice": "100",
                "side": side,
            }
        )


@pytest.mark.parametrize(
    "row",
    [
        {"side": "long", "contracts": "1"},
        {"symbol": "BTC-USDT-SWAP", "side": "long"},
        {"symbol": "BTC-USDT-SWAP", "side": "long", "contracts": "nan"},
        {"symbol": "BTC-USDT-SWAP", "side": "long", "contracts": "inf"},
        {"symbol": "BTC-USDT-SWAP", "side": "long", "info": {}},
    ],
)
def test_okx_base_adapter_rejects_position_rows_without_complete_identity(row):
    adapter = OKXBaseAdapter("", "", "", "swap")

    with pytest.raises(MalformedProviderData, match="position row"):
        adapter._parse_position_snapshot(row)


def test_okx_base_adapter_accepts_explicit_zero_position_with_complete_fields():
    adapter = OKXBaseAdapter("", "", "", "swap")

    snapshot = adapter._parse_position_snapshot(
        {"symbol": "BTC-USDT-SWAP", "side": "long", "contracts": "0"}
    )

    assert snapshot.symbol == "BTC-USDT-SWAP"
    assert snapshot.side is PositionSide.LONG
    assert snapshot.amount == 0.0


def test_okx_base_adapter_rejects_contract_size_only_position_row():
    adapter = OKXBaseAdapter("", "", "", "swap")

    with pytest.raises(MalformedProviderData, match="missing amount"):
        adapter._parse_position_snapshot(
            {
                "symbol": "BTC-USDT-SWAP",
                "side": "long",
                "contractSize": "0.01",
            }
        )


def test_okx_base_adapter_position_mapper_uses_signed_amount_only_when_side_fields_are_missing():
    adapter = OKXBaseAdapter("", "", "", "swap")

    short_snapshot = adapter._parse_position_snapshot(
        {"symbol": "BTC-USDT-SWAP", "contracts": "-2", "entryPrice": "100"}
    )

    assert short_snapshot.side == PositionSide.SHORT
    assert short_snapshot.amount == 2.0


def test_derivative_contracts_are_converted_to_base_amount_from_exact_market_metadata():
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC/USDT:USDT": {
            "id": "BTC-USDT-SWAP",
            "symbol": "BTC/USDT:USDT",
            "type": "swap",
            "contractSize": 0.01,
        }
    }

    snapshot = adapter._parse_position_snapshot(
        {"symbol": "BTC-USDT-SWAP", "contracts": "100", "entryPrice": "100"}
    )

    assert snapshot.amount == pytest.approx(1.0)


def test_inverse_position_without_contract_size_fails_closed_even_without_market_metadata():
    adapter = OKXBaseAdapter("", "", "", "swap")

    with pytest.raises(MalformedProviderData, match="contract size"):
        adapter._parse_position_snapshot(
            {
                "symbol": "BTC-USD-SWAP",
                "contracts": "2",
                "entryPrice": "50000",
            }
        )


def test_position_snapshot_separates_inverse_contracts_and_quote_notional():
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC/USD:BTC": {
            "id": "BTC-USD-SWAP",
            "symbol": "BTC/USD:BTC",
            "type": "swap",
            "contractSize": 100,
        }
    }

    snapshot = adapter._parse_position_snapshot(
        {
            "symbol": "BTC-USD-SWAP",
            "contracts": "2",
            "entryPrice": "50000",
            "markPrice": "50000",
        }
    )

    assert snapshot.is_inverse is True
    assert snapshot.contracts == pytest.approx(2.0)
    assert snapshot.quantity == pytest.approx(2.0)
    assert snapshot.quantity_unit == "contracts"
    assert snapshot.notional_value == pytest.approx(200.0)
    assert snapshot.amount == pytest.approx(200.0)


def test_position_snapshot_keeps_linear_base_quantity_and_mark_notional():
    adapter = OKXBaseAdapter("", "", "", "future")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC/USDT:USDT-260925": {
            "id": "BTC-USDT-260925",
            "symbol": "BTC/USDT:USDT-260925",
            "type": "future",
            "contractSize": 0.01,
        }
    }

    snapshot = adapter._parse_position_snapshot(
        {
            "symbol": "BTC-USDT-260925",
            "contracts": "100",
            "entryPrice": "50000",
            "markPrice": "50000",
        }
    )

    assert snapshot.is_inverse is False
    assert snapshot.quantity == pytest.approx(1.0)
    assert snapshot.quantity_unit == "base"
    assert snapshot.notional_value == pytest.approx(50000.0)
    assert snapshot.amount == pytest.approx(1.0)


def test_conflicting_explicit_market_contract_sizes_fail_closed():
    adapter = OKXBaseAdapter("", "", "", "swap")
    FakeOKX.instances[0].markets = {
        "BTC/USDT:USDT": {
            "id": "BTC-USDT-SWAP",
            "symbol": "BTC/USDT:USDT",
            "type": "swap",
            "contractSize": 0.01,
            "info": {"ctVal": 0.02},
        }
    }

    with pytest.raises(MalformedProviderData, match="contract size"):
        adapter._parse_position_snapshot(
            {"symbol": "BTC-USDT-SWAP", "contracts": "100"}
        )


def test_market_type_aliases_match_ccxt_metadata_types():
    markets = {
        "BTC/USDT:USDT-260925": {
            "id": "BTC-USDT-260925",
            "symbol": "BTC/USDT:USDT-260925",
            "type": "future",
            "contractSize": 0.01,
        }
    }

    assert exchange_base.canonical_symbol(
        "BTC-USDT-260925", market_type="futures", markets=markets
    ) == "BTC-USDT-260925"
    option_markets = {
        "BTC/USD:USD-260925-100000-C": {
            "id": "BTC-USD-260925-100000-C",
            "symbol": "BTC/USD:USD-260925-100000-C",
            "type": "option",
        }
    }
    assert exchange_base.canonical_symbol(
        "BTC-USD-260925-100000-C", market_type="options", markets=option_markets
    ) == "BTC-USD-260925-100000-C"


def test_signed_derivative_contracts_drive_short_side_and_base_amount_is_nonnegative():
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC/USDT:USDT": {
            "id": "BTC-USDT-SWAP",
            "symbol": "BTC/USDT:USDT",
            "type": "swap",
            "contractSize": 0.01,
        }
    }

    snapshot = adapter._parse_position_snapshot(
        {"symbol": "BTC-USDT-SWAP", "contracts": "-100", "entryPrice": "100"}
    )

    assert snapshot.side == PositionSide.SHORT
    assert snapshot.amount == pytest.approx(1.0)


def test_signed_position_amt_takes_side_priority_but_contracts_determine_amount():
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC/USDT:USDT": {
            "id": "BTC-USDT-SWAP",
            "symbol": "BTC/USDT:USDT",
            "type": "swap",
            "contractSize": 0.01,
        }
    }

    snapshot = adapter._parse_position_snapshot(
        {
            "symbol": "BTC-USDT-SWAP",
            "contracts": "100",
            "positionAmt": "-100",
            "entryPrice": "100",
        }
    )

    assert snapshot.side == PositionSide.SHORT
    assert snapshot.amount == pytest.approx(1.0)


def test_conflicting_contracts_and_signed_position_amt_fail_closed_without_side():
    adapter = OKXBaseAdapter("", "", "", "swap")

    with pytest.raises(MalformedProviderData, match="position amount"):
        adapter._parse_position_snapshot(
            {
                "symbol": "BTC-USDT-SWAP",
                "contracts": "100",
                "positionAmt": "-90",
                "entryPrice": "100",
            }
        )


def test_derivative_amount_only_is_scaled_to_base_amount_from_contract_size():
    adapter = OKXBaseAdapter("", "", "", "swap")

    snapshot = adapter._parse_position_snapshot(
        {
            "symbol": "BTC-USDT-SWAP",
            "amount": "100",
            "contractSize": "0.01",
            "entryPrice": "100",
        }
    )

    assert snapshot.amount == pytest.approx(1.0)


def test_derivative_amount_only_must_match_signed_position_evidence():
    adapter = OKXBaseAdapter("", "", "", "swap")

    with pytest.raises(MalformedProviderData, match="position amount"):
        adapter._parse_position_snapshot(
            {
                "symbol": "BTC-USDT-SWAP",
                "amount": "100",
                "contractSize": "0.01",
                "positionAmt": "90",
                "entryPrice": "100",
            }
        )


def test_derivative_amount_only_accepts_consistent_signed_short_evidence():
    adapter = OKXBaseAdapter("", "", "", "swap")

    snapshot = adapter._parse_position_snapshot(
        {
            "symbol": "BTC-USDT-SWAP",
            "amount": "100",
            "contractSize": "0.01",
            "info": {"pos": "-100"},
            "entryPrice": "100",
        }
    )

    assert snapshot.side is PositionSide.SHORT
    assert snapshot.amount == pytest.approx(1.0)


def test_derivative_info_pos_drives_side_when_contracts_are_unsigned():
    adapter = OKXBaseAdapter("", "", "", "swap")

    snapshot = adapter._parse_position_snapshot(
        {
            "symbol": "BTC-USDT-SWAP",
            "contracts": "100",
            "entryPrice": "100",
            "info": {"pos": "-100"},
        }
    )

    assert snapshot.side == PositionSide.SHORT
    assert snapshot.amount == pytest.approx(100.0)


def test_derivative_info_pos_conflicting_with_contracts_fails_closed():
    adapter = OKXBaseAdapter("", "", "", "swap")

    with pytest.raises(MalformedProviderData, match="position amount"):
        adapter._parse_position_snapshot(
            {
                "symbol": "BTC-USDT-SWAP",
                "contracts": "100",
                "entryPrice": "100",
                "info": {"pos": "-90"},
            }
        )


def test_signed_derivative_evidence_must_agree_without_contracts_field():
    adapter = OKXBaseAdapter("", "", "", "swap")

    with pytest.raises(MalformedProviderData, match="position amount"):
        adapter._parse_position_snapshot(
            {
                "symbol": "BTC-USDT-SWAP",
                "amount": "100",
                "positionAmt": "100",
                "entryPrice": "100",
                "info": {"pos": "-100"},
            }
        )


def test_signed_derivative_evidence_agrees_without_contracts_field():
    adapter = OKXBaseAdapter("", "", "", "swap")

    snapshot = adapter._parse_position_snapshot(
        {
            "symbol": "BTC-USDT-SWAP",
            "positionAmt": "-100",
            "entryPrice": "100",
            "info": {"pos": "-100"},
        }
    )

    assert snapshot.side is PositionSide.SHORT
    assert snapshot.amount == pytest.approx(100.0)


def test_derivative_chosen_contract_count_conflict_fails_closed_without_contracts_field():
    adapter = OKXBaseAdapter("", "", "", "swap")

    with pytest.raises(MalformedProviderData, match="position amount"):
        adapter._parse_position_snapshot(
            {
                "symbol": "BTC-USDT-SWAP",
                "positionAmt": "100",
                "entryPrice": "100",
                "info": {"pos": "90"},
            }
        )


def test_spot_signed_evidence_does_not_apply_derivative_consistency_rules():
    adapter = OKXBaseAdapter("", "", "", "spot")

    snapshot = adapter._parse_position_snapshot(
        {
            "symbol": "BTC-USDT",
            "amount": "100",
            "positionAmt": "100",
            "entryPrice": "100",
            "info": {"pos": "-100"},
        }
    )

    assert snapshot.amount == pytest.approx(100.0)


def test_derivative_contract_conversion_rejects_nonfinite_product():
    adapter = OKXBaseAdapter("", "", "", "swap")

    with pytest.raises(MalformedProviderData, match="position amount"):
        adapter._parse_position_snapshot(
            {
                "symbol": "BTC-USDT-SWAP",
                "contracts": "1e308",
                "contractSize": "1e308",
                "entryPrice": "100",
            }
        )


def test_derivative_contract_conversion_accepts_large_finite_product():
    adapter = OKXBaseAdapter("", "", "", "swap")

    snapshot = adapter._parse_position_snapshot(
        {
            "symbol": "BTC-USDT-SWAP",
            "contracts": "1e308",
            "contractSize": "1",
            "entryPrice": "100",
        }
    )

    assert snapshot.amount == 1e308
    assert snapshot.amount != 0.0


def test_position_side_requires_all_explicit_direction_fields_to_agree():
    adapter = OKXBaseAdapter("", "", "", "swap")

    with pytest.raises(MalformedProviderData, match="position row"):
        adapter._parse_position_snapshot(
            {
                "symbol": "BTC-USDT-SWAP",
                "contracts": "2",
                "side": "long",
                "posSide": "short",
            }
        )


def test_position_side_rejects_invalid_explicit_field_even_after_valid_side():
    adapter = OKXBaseAdapter("", "", "", "swap")

    with pytest.raises(MalformedProviderData, match="position row"):
        adapter._parse_position_snapshot(
            {
                "symbol": "BTC-USDT-SWAP",
                "contracts": "2",
                "side": "long",
                "info": {"posSide": "hold"},
            }
        )


def test_position_side_accepts_consistent_explicit_fields():
    adapter = OKXBaseAdapter("", "", "", "swap")

    snapshot = adapter._parse_position_snapshot(
        {
            "symbol": "BTC-USDT-SWAP",
            "contracts": "2",
            "side": "long",
            "posSide": "buy",
            "info": {"posSide": "long"},
        }
    )

    assert snapshot.side == PositionSide.LONG


def test_position_side_net_uses_signed_amount_fallback():
    adapter = OKXBaseAdapter("", "", "", "swap")

    snapshot = adapter._parse_position_snapshot(
        {
            "symbol": "BTC-USDT-SWAP",
            "contracts": "-2",
            "info": {"posSide": "net"},
        }
    )

    assert snapshot.side == PositionSide.SHORT


@pytest.mark.parametrize("field", ["positionAmt"])
def test_derivative_position_amount_is_scaled_to_base_amount_from_metadata(field):
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC/USDT:USDT": {
            "id": "BTC-USDT-SWAP",
            "symbol": "BTC/USDT:USDT",
            "type": "swap",
            "contractSize": 0.01,
        }
    }

    snapshot = adapter._parse_position_snapshot(
        {"symbol": "BTC-USDT-SWAP", field: "100", "entryPrice": "100"}
    )

    assert snapshot.amount == pytest.approx(1.0)


def test_signed_derivative_position_amount_drives_short_side_and_scales_with_fallback_size():
    adapter = OKXBaseAdapter("", "", "", "swap")

    snapshot = adapter._parse_position_snapshot(
        {
            "symbol": "BTC-USDT-SWAP",
            "positionAmt": "-100",
            "contractSize": "0.01",
            "entryPrice": "100",
        }
    )

    assert snapshot.side == PositionSide.SHORT
    assert snapshot.amount == pytest.approx(1.0)


def test_spot_position_amount_is_not_scaled_by_contract_size():
    adapter = OKXBaseAdapter("", "", "", "spot")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC/USDT": {
            "id": "BTC-USDT",
            "symbol": "BTC/USDT",
            "type": "spot",
            "contractSize": 0.01,
        }
    }

    snapshot = adapter._parse_position_snapshot(
        {"symbol": "BTC-USDT", "amount": "100", "side": "long"}
    )

    assert snapshot.amount == pytest.approx(100.0)


def test_market_contract_size_has_priority_over_row_contract_size():
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC/USDT:USDT": {
            "id": "BTC-USDT-SWAP",
            "symbol": "BTC/USDT:USDT",
            "type": "swap",
            "contractSize": 0.01,
        }
    }

    snapshot = adapter._parse_position_snapshot(
        {
            "symbol": "BTC-USDT-SWAP",
            "contracts": "100",
            "contractSize": "0.02",
            "entryPrice": "100",
        }
    )

    assert snapshot.amount == pytest.approx(1.0)


def test_exact_market_none_contract_size_falls_back_to_market_info_ct_val():
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC/USDT:USDT": {
            "id": "BTC-USDT-SWAP",
            "symbol": "BTC/USDT:USDT",
            "type": "swap",
            "contractSize": None,
            "info": {"ctVal": "0.03"},
        }
    }

    snapshot = adapter._parse_position_snapshot(
        {"symbol": "BTC-USDT-SWAP", "contracts": "100"}
    )

    assert snapshot.amount == pytest.approx(3.0)


def test_exact_market_none_contract_size_falls_back_to_row_contract_size():
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC/USDT:USDT": {
            "id": "BTC-USDT-SWAP",
            "symbol": "BTC/USDT:USDT",
            "type": "swap",
            "contractSize": None,
        }
    }

    snapshot = adapter._parse_position_snapshot(
        {
            "symbol": "BTC-USDT-SWAP",
            "contracts": "100",
            "contractSize": "0.02",
        }
    )

    assert snapshot.amount == pytest.approx(2.0)


def test_derivative_contract_size_falls_back_to_explicit_row_value():
    adapter = OKXBaseAdapter("", "", "", "swap")

    snapshot = adapter._parse_position_snapshot(
        {
            "symbol": "BTC-USDT-SWAP",
            "contracts": "100",
            "contractSize": "0.02",
            "entryPrice": "100",
        }
    )

    assert snapshot.amount == pytest.approx(2.0)


@pytest.mark.parametrize("invalid_size", [0, -1, "nan", "inf", True, "invalid"])
def test_invalid_exact_market_metadata_does_not_fall_back_to_valid_row_contract_size(
    invalid_size,
):
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC/USDT:USDT": {
            "id": "BTC-USDT-SWAP",
            "symbol": "BTC/USDT:USDT",
            "type": "swap",
            "contractSize": invalid_size,
        }
    }

    with pytest.raises(MalformedProviderData, match="contract size"):
        adapter._parse_position_snapshot(
            {
                "symbol": "BTC-USDT-SWAP",
                "contracts": "100",
                "contractSize": "0.01",
            }
        )


@pytest.mark.parametrize("invalid_size", [0, -1, "nan", "inf", True, "invalid"])
def test_invalid_exact_market_info_metadata_does_not_fall_back_to_valid_row_contract_size(
    invalid_size,
):
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC/USDT:USDT": {
            "id": "BTC-USDT-SWAP",
            "symbol": "BTC/USDT:USDT",
            "type": "swap",
            "info": {"ctVal": invalid_size},
        }
    }

    with pytest.raises(MalformedProviderData, match="contract size"):
        adapter._parse_position_snapshot(
            {
                "symbol": "BTC-USDT-SWAP",
                "contracts": "100",
                "contractSize": "0.01",
            }
        )


def test_derivative_contract_size_falls_back_to_provider_info_value():
    adapter = OKXBaseAdapter("", "", "", "swap")

    snapshot = adapter._parse_position_snapshot(
        {
            "symbol": "BTC-USDT-SWAP",
            "contracts": "100",
            "entryPrice": "100",
            "info": {"ctVal": "0.03"},
        }
    )

    assert snapshot.amount == pytest.approx(3.0)


def test_derivative_contracts_use_explicitly_allowed_unit_contract_fallback_when_size_is_missing():
    adapter = OKXBaseAdapter("", "", "", "swap")

    snapshot = adapter._parse_position_snapshot(
        {"symbol": "BTC-USDT-SWAP", "contracts": "100", "entryPrice": "100"}
    )

    assert snapshot.amount == pytest.approx(100.0)


@pytest.mark.parametrize("contract_size", [0, -1, "nan", "inf", True, "invalid"])
def test_derivative_contracts_fail_closed_when_explicit_contract_size_is_invalid(contract_size):
    adapter = OKXBaseAdapter("", "", "", "swap")
    row = {
        "symbol": "BTC-USDT-SWAP",
        "contracts": "100",
        "entryPrice": "100",
        "contractSize": contract_size,
    }

    with pytest.raises(MalformedProviderData, match="contract size"):
        adapter._parse_position_snapshot(row)


def test_exact_market_metadata_does_not_cross_match_dated_or_option_contracts():
    adapter = OKXBaseAdapter("", "", "", "option")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC/USD:USD-260925-100000-C": {
            "id": "BTC-USD-260925-100000-C",
            "symbol": "BTC/USD:USD-260925-100000-C",
            "type": "option",
            "contractSize": 0.1,
        },
        "BTC/USD:USD-260925-110000-C": {
            "id": "BTC-USD-260925-110000-C",
            "symbol": "BTC/USD:USD-260925-110000-C",
            "type": "option",
            "contractSize": 0.01,
        },
    }

    snapshot = adapter._parse_position_snapshot(
        {
            "symbol": "BTC-USD-260925-110000-C",
            "contracts": "100",
            "entryPrice": "100",
        }
    )

    assert snapshot.amount == pytest.approx(1.0)


def test_okx_base_adapter_maps_linear_contract_trades_to_base_quantity():
    adapter = OKXBaseAdapter("", "", "", "swap")
    FakeOKX.instances[0].markets = {
        "BTC/USDT:USDT": {
            "id": "BTC-USDT-SWAP",
            "symbol": "BTC/USDT:USDT",
            "type": "swap",
            "contractSize": 0.01,
        }
    }

    snapshot = adapter._map_trade(
        {
            "id": "linear-trade",
            "order": "linear-order",
            "symbol": "BTC-USDT-SWAP",
            "side": "buy",
            "amount": "100",
            "price": "50000",
        }
    )

    assert snapshot.amount == pytest.approx(1.0)
    assert snapshot.quantity == pytest.approx(1.0)
    assert snapshot.quantity_unit == "base"
    assert snapshot.contracts == pytest.approx(100.0)
    assert snapshot.contract_size == pytest.approx(0.01)
    assert snapshot.is_inverse is False


def test_okx_base_adapter_keeps_inverse_trade_quantity_as_quote_notional_metadata():
    adapter = OKXBaseAdapter("", "", "", "swap")
    FakeOKX.instances[0].markets = {
        "BTC/USD:BTC": {
            "id": "BTC-USD-SWAP",
            "symbol": "BTC/USD:BTC",
            "type": "swap",
            "contractSize": 100.0,
            "inverse": True,
        }
    }

    snapshot = adapter._map_trade(
        {
            "id": "inverse-trade",
            "order": "inverse-order",
            "symbol": "BTC-USD-SWAP",
            "side": "buy",
            "amount": "2",
            "price": "50000",
        }
    )

    assert snapshot.amount == pytest.approx(200.0)
    assert snapshot.quantity == pytest.approx(2.0)
    assert snapshot.quantity_unit == "contracts"
    assert snapshot.contracts == pytest.approx(2.0)
    assert snapshot.contract_size == pytest.approx(100.0)
    assert snapshot.is_inverse is True


@pytest.mark.parametrize("timestamp", [float("nan"), float("inf"), "invalid"])
def test_okx_base_adapter_maps_invalid_trade_timestamp_to_safe_default(timestamp):
    adapter = OKXBaseAdapter("", "", "", "swap")

    snapshot = adapter._map_trade(
        {
            "id": "trade-invalid-timestamp",
            "order": "ex-1",
            "symbol": "BTC-USDT-SWAP",
            "side": "buy",
            "amount": "0.1",
            "price": "50000",
            "timestamp": timestamp,
        }
    )

    assert snapshot.timestamp == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("amount", None),
        ("amount", "nan"),
        ("amount", "-1"),
        ("amount", "0"),
        ("price", None),
        ("price", "inf"),
        ("price", "-1"),
        ("price", "0"),
        ("symbol", ""),
    ],
)
def test_okx_base_adapter_skips_trade_rows_with_invalid_critical_fields(field, value, caplog):
    adapter = OKXBaseAdapter("", "", "", "swap")
    row = {
        "id": "bad-trade",
        "order": "order-1",
        "symbol": "BTC-USDT-SWAP",
        "side": "buy",
        "amount": "1",
        "price": "100",
    }
    row[field] = value

    assert adapter._map_trade(row) is None
    assert "invalid critical fields" in caplog.text


@pytest.mark.asyncio
async def test_okx_base_adapter_rejects_malformed_trade_rows_in_batch(caplog):
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]

    async def fetch_my_trades(*args, **kwargs):
        return [
            {
                "id": "bad-amount", "symbol": "BTC-USDT-SWAP", "side": "buy",
                "amount": "0", "price": "100",
            },
            {
                "id": "bad-symbol", "symbol": "", "side": "sell",
                "amount": "1", "price": "100",
            },
            {
                "id": "valid", "symbol": "BTC-USDT-SWAP", "side": "buy",
                "amount": "1", "price": "100",
            },
        ]

    fake.fetch_my_trades = fetch_my_trades

    with pytest.raises(ValueError, match="Malformed provider recent-trade row"):
        await adapter.fetch_recent_trade_snapshots()

    assert "invalid critical fields" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [[None], [{}]])
async def test_okx_base_adapter_rejects_malformed_trade_rows(rows):
    adapter = OKXBaseAdapter("", "", "", "swap")

    async def fetch_my_trades(*args, **kwargs):
        return rows

    FakeOKX.instances[0].fetch_my_trades = fetch_my_trades

    with pytest.raises(ValueError, match="Malformed provider recent-trade row"):
        await adapter.fetch_recent_trade_snapshots()


@pytest.mark.asyncio
async def test_okx_base_adapter_accepts_valid_empty_recent_trade_response():
    adapter = OKXBaseAdapter("", "", "", "swap")

    async def fetch_my_trades(*args, **kwargs):
        return []

    FakeOKX.instances[0].fetch_my_trades = fetch_my_trades

    assert await adapter.fetch_recent_trade_snapshots() == []


@pytest.mark.parametrize("fee", [{"cost": "nan"}, {"cost": "inf"}, {"cost": "bad"}, {"cost": -1.0}])
def test_okx_base_adapter_rejects_invalid_trade_fee_costs(fee):
    adapter = OKXBaseAdapter("", "", "", "swap")

    with pytest.raises(MalformedProviderData, match="trade fee"):
        adapter._map_trade({
            "id": "trade-invalid-fee",
            "order": "order-1",
            "symbol": "BTC-USDT-SWAP",
            "side": "buy",
            "amount": "1",
            "price": "100",
            "fee": fee,
        })


@pytest.mark.parametrize("fee", ["invalid-fee", [], 1, True])
def test_okx_base_adapter_rejects_non_mapping_trade_fee(fee):
    adapter = OKXBaseAdapter("", "", "", "swap")

    with pytest.raises(MalformedProviderData, match="trade fee"):
        adapter._map_trade({
            "id": "trade-invalid-fee-shape",
            "order": "order-1",
            "symbol": "BTC-USDT-SWAP",
            "side": "buy",
            "amount": "1",
            "price": "100",
            "fee": fee,
        })


def test_okx_base_adapter_accepts_absent_or_null_trade_fee_as_zero():
    adapter = OKXBaseAdapter("", "", "", "swap")

    for fee_fields in ({}, {"fee": None}, {"fee": {}}, {"fee": {"cost": None}}):
        row = {
            "id": "trade-zero-fee",
            "order": "order-1",
            "symbol": "BTC-USDT-SWAP",
            "side": "buy",
            "amount": "1",
            "price": "100",
            **fee_fields,
        }
        snapshot = adapter._map_trade(row)
        assert snapshot.fee == 0.0


async def test_okx_base_adapter_rejects_trade_row_without_any_external_identity():
    adapter = OKXBaseAdapter("", "", "", "swap")

    async def fetch_my_trades(*args, **kwargs):
        return [{
            "id": "",
            "order": "",
            "clientOrderId": "",
            "symbol": "BTC-USDT-SWAP",
            "side": "buy",
            "amount": "1",
            "price": "100",
        }]

    FakeOKX.instances[0].fetch_my_trades = fetch_my_trades

    with pytest.raises(MalformedProviderData, match="missing external identity"):
        await adapter.fetch_recent_trade_snapshots()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1e308, 0),
        (2**63, 0),
        (-1, 0),
        (1700000000000, 1700000000000),
        (2**63 - 1, 2**63 - 1),
        (str(2**63 - 1), 2**63 - 1),
        (1.5, 0),
        ("1.5", 0),
    ],
)
def test_okx_base_adapter_safe_int_stays_within_sqlite_signed_range(value, expected):
    adapter = OKXBaseAdapter("", "", "", "swap")

    assert adapter._safe_int(value) == expected


@pytest.mark.parametrize("value", ["nan", "inf", "-1"])
def test_okx_base_adapter_skips_invalid_limit_order_prices(value):
    adapter = OKXBaseAdapter("", "", "", "swap")

    with pytest.raises(MalformedProviderData, match="order row"):
        adapter._map_order(
            {
                "id": "ex-invalid-price",
                "symbol": "BTC-USDT-SWAP",
                "side": "buy",
                "type": "limit",
                "amount": "1.0",
                "filled": "0",
                "price": value,
                "average": value,
                "status": "open",
                "timestamp": "1700000000000",
            }
        )


@pytest.mark.asyncio
async def test_okx_base_adapter_rejects_order_rows_with_blank_external_ids():
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]
    fake.ticker_rows = {}

    async def fetch_open_orders(*args, **kwargs):
        return [
            {
                "id": "",
                "clientOrderId": "",
                "symbol": "BTC-USDT-SWAP",
                "side": "buy",
                "amount": "1",
            },
            {
                "id": "",
                "clientOid": "",
                "symbol": "BTC-USDT-SWAP",
                "side": "sell",
                "amount": "2",
            },
            {
                "id": "invalid-amount-order",
                "clientOrderId": "invalid-amount-client",
                "symbol": "BTC-USDT-SWAP",
                "side": "buy",
                "amount": "nan",
            },
            {
                "id": "valid-order",
                "clientOrderId": "valid-client",
                "symbol": "BTC-USDT-SWAP",
                "side": "buy",
                "amount": "3",
            },
        ]

    fake.fetch_open_orders = fetch_open_orders

    with pytest.raises(ValueError, match="Malformed provider open-order row"):
        await adapter.fetch_open_order_snapshots()



@pytest.mark.parametrize("amount", [None, "", "bad", "nan", "inf", "-1", 0])
def test_okx_base_adapter_skips_orders_with_invalid_amounts(amount, caplog):
    adapter = OKXBaseAdapter("", "", "", "swap")

    row = {
        "id": "invalid-amount",
        "symbol": "BTC-USDT-SWAP",
        "side": "buy",
        "type": "limit",
        "amount": amount,
        "price": "50000",
        "status": "open",
    }

    with pytest.raises(MalformedProviderData, match="order row"):
        adapter._map_order(row)


def test_okx_base_adapter_maps_exchange_order_fill_amounts_and_partial_status():
    adapter = OKXBaseAdapter("", "", "", "swap")

    snapshot = adapter._map_order(
        {
            "id": "ex-partial",
            "clientOrderId": "client-partial",
            "symbol": "BTC-USDT-SWAP",
            "side": "buy",
            "type": "limit",
            "amount": "1.0",
            "filled": "0.25",
            "remaining": "0.75",
            "price": "50000",
            "status": "open",
            "average": "49900",
            "timestamp": "1700000000000",
        }
    )

    assert snapshot.amount == 1.0
    assert snapshot.filled_amount == 0.25
    assert snapshot.remaining_amount == 0.75
    assert snapshot.status == exchange_base.OrderStatus.PARTIALLY_FILLED


@pytest.mark.parametrize(
    ("fields", "expected_filled", "expected_remaining"),
    [
        ({"filled": "0.25"}, 0.25, 0.75),
        ({"remaining": "0.75"}, 0.25, 0.75),
        ({"filled": "0.25", "remaining": "0.75"}, 0.25, 0.75),
    ],
)
def test_okx_base_adapter_accepts_consistent_or_single_order_fill_fields(
    fields, expected_filled, expected_remaining
):
    adapter = OKXBaseAdapter("", "", "", "swap")

    snapshot = adapter._map_order({
        "id": "ex-fill-fields",
        "symbol": "BTC-USDT-SWAP",
        "side": "buy",
        "type": "market",
        "amount": "1.0",
        "status": "open",
        **fields,
    })

    assert snapshot.filled_amount == pytest.approx(expected_filled)
    assert snapshot.remaining_amount == pytest.approx(expected_remaining)


def test_okx_base_adapter_rejects_inconsistent_explicit_order_fill_fields():
    adapter = OKXBaseAdapter("", "", "", "swap")

    with pytest.raises(MalformedProviderData, match="order row"):
        adapter._map_order({
            "id": "ex-inconsistent-fill-fields",
            "symbol": "BTC-USDT-SWAP",
            "side": "buy",
            "type": "market",
            "amount": "1.0",
            "filled": "0.9",
            "remaining": "0.9",
            "status": "open",
        })


def test_okx_base_adapter_maps_open_fully_filled_exchange_order_as_filled():
    adapter = OKXBaseAdapter("", "", "", "swap")

    snapshot = adapter._map_order(
        {
            "id": "ex-filled",
            "symbol": "BTC-USDT-SWAP",
            "side": "buy",
            "type": "limit",
            "amount": "1.0",
            "filled": "1.0",
            "remaining": "0",
            "price": "50000",
            "status": "open",
            "timestamp": "1700000000000",
        }
    )

    assert snapshot.filled_amount == 1.0
    assert snapshot.remaining_amount == 0.0
    assert snapshot.status == exchange_base.OrderStatus.FILLED


@pytest.mark.parametrize("status", ["cancel", "canceled", "cancelled"])
def test_okx_base_adapter_maps_cancel_statuses_to_cancelled(status):
    adapter = OKXBaseAdapter("", "", "", "swap")

    snapshot = adapter._map_order(
        {
            "id": "ex-cancelled",
            "symbol": "BTC-USDT-SWAP",
            "side": "buy",
            "type": "limit",
            "amount": "1.0",
            "price": "50000",
            "status": status,
            "timestamp": "1700000000000",
        }
    )

    assert snapshot.status == exchange_base.OrderStatus.CANCELLED


@pytest.mark.parametrize("field", ["filled", "remaining"])
@pytest.mark.parametrize("value", [True, "bad", "nan", "inf", -1.0, 2.0])
def test_okx_base_adapter_rejects_explicit_malformed_open_order_fill_fields(field, value):
    adapter = OKXBaseAdapter("", "", "", "swap")
    row = {
        "id": "ex-invalid-fill-field",
        "symbol": "BTC-USDT-SWAP",
        "side": "buy",
        "type": "limit",
        "amount": "1.0",
        "price": "50000",
        "status": "open",
        "timestamp": "1700000000000",
        field: value,
    }

    with pytest.raises(MalformedProviderData, match="order row"):
        adapter._map_order(row)


def test_okx_base_adapter_treats_absent_or_null_open_order_fill_fields_as_missing():
    adapter = OKXBaseAdapter("", "", "", "swap")
    for field in ("filled", "remaining"):
        row = {
            "id": f"ex-missing-{field}",
            "symbol": "BTC-USDT-SWAP",
            "side": "buy",
            "type": "market",
            "amount": "1.0",
            "status": "open",
            field: None,
        }
        snapshot = adapter._map_order(row)
        assert snapshot.filled_amount == 0.0
        assert snapshot.remaining_amount == 1.0


@pytest.mark.asyncio
@pytest.mark.parametrize("volume", [None, "", "bad", "nan", "inf", "-1"])
async def test_okx_base_adapter_skips_ticker_when_volume_is_invalid(volume):
    adapter = OKXBaseAdapter("", "", "", "spot")
    fake = FakeOKX.instances[0]
    fake.raw_ticker_response = {"data": [{
        "instId": "BTC-USDT",
        "last": "100",
        "bidPx": "99",
        "askPx": "101",
        "vol24h": volume,
    }]}

    assert await adapter.fetch_tickers(["BTC-USDT"]) == []


@pytest.mark.asyncio
async def test_okx_base_adapter_keeps_valid_ticker_volume():
    adapter = OKXBaseAdapter("", "", "", "spot")
    fake = FakeOKX.instances[0]
    fake.raw_ticker_response = {"data": [{
        "instId": "BTC-USDT",
        "last": "100",
        "bidPx": "99",
        "askPx": "101",
        "vol24h": "12.5",
    }]}

    assert (await adapter.fetch_tickers(["BTC-USDT"]))[0]["vol24h"] == 12.5


def test_okx_base_adapter_skips_unknown_order_type_without_guessing(caplog):
    adapter = OKXBaseAdapter("", "", "", "swap")

    with pytest.raises(MalformedProviderData, match="order row"):
        adapter._map_order({
            "id": "mystery-order",
            "symbol": "BTC-USDT-SWAP",
            "side": "buy",
            "type": "mystery",
            "amount": "1",
            "price": "100",
            "status": "open",
        })


@pytest.mark.parametrize(
    "raw_type",
    ["stop-market", "stop-limit", "stop_market", "stop_limit", "trigger-market", "trigger-limit"],
)
def test_execution_type_family_normalizes_provider_stop_aliases(raw_type):
    assert execution_type_family("market", provider_type=raw_type) == "stop"


@pytest.mark.parametrize(
    ("raw_type", "expected"),
    [("market", "market"), ("limit", "limit"), ("unknown-triggerish", "unknown-triggerish")],
)
def test_execution_type_family_preserves_market_limit_and_unknown_values(raw_type, expected):
    assert execution_type_family("market", provider_type=raw_type) == expected


@pytest.mark.parametrize(
    "raw_type",
    ["stop-market", "stop-limit", "stop_market", "stop_limit", "trigger-market", "trigger-limit"],
)
def test_okx_base_adapter_maps_hyphenated_trigger_order_types(raw_type):
    adapter = OKXBaseAdapter("", "", "", "swap")

    snapshot = adapter._map_order({
        "id": f"{raw_type}-order",
        "symbol": "BTC-USDT-SWAP",
        "side": "buy",
        "type": raw_type,
        "amount": "1",
        "price": "100",
        "status": "open",
    })

    assert snapshot.execution_type_family == "stop"


def test_okx_base_adapter_maps_known_trigger_order_types(caplog):
    adapter = OKXBaseAdapter("", "", "", "swap")

    limit_snapshot = adapter._map_order({
        "id": "stop-limit",
        "symbol": "BTC-USDT-SWAP",
        "side": "buy",
        "type": "stop_limit",
        "amount": "1",
        "price": "100",
        "status": "open",
    })
    market_snapshot = adapter._map_order({
        "id": "trigger-market",
        "symbol": "BTC-USDT-SWAP",
        "side": "sell",
        "type": "trigger",
        "amount": "1",
        "status": "open",
    })

    assert limit_snapshot.type is OrderType.LIMIT
    assert market_snapshot.type is OrderType.MARKET
    assert limit_snapshot.execution_type_family == "stop"
    assert market_snapshot.execution_type_family == "stop"
    assert "Unknown exchange order type" not in caplog.text


def test_okx_base_adapter_maps_stop_market_to_market_with_stop_family():
    adapter = OKXBaseAdapter("", "", "", "swap")

    snapshot = adapter._map_order({
        "id": "stop-market",
        "symbol": "BTC-USDT-SWAP",
        "side": "sell",
        "type": "stop_market",
        "amount": "1",
        "status": "open",
    })

    assert snapshot.type is OrderType.MARKET
    assert snapshot.execution_type_family == "stop"


@pytest.mark.parametrize("side", ["hold", "future", None, "buy-now"])
def test_okx_base_adapter_skips_order_rows_with_invalid_sides(side, caplog):
    adapter = OKXBaseAdapter("", "", "", "swap")

    with pytest.raises(MalformedProviderData, match="order row"):
        adapter._map_order({
            "id": f"invalid-{side}",
            "symbol": "BTC-USDT-SWAP",
            "side": side,
            "type": "limit",
            "amount": "1",
            "price": "100",
            "status": "open",
        })


@pytest.mark.parametrize(
    ("side", "expected"),
    [
        ("BUY", OrderSide.BUY),
        ("SELL", OrderSide.SELL),
        ("long", OrderSide.BUY),
        ("short", OrderSide.SELL),
    ],
)
def test_okx_base_adapter_maps_order_side_aliases(side, expected):
    adapter = OKXBaseAdapter("", "", "", "swap")

    snapshot = adapter._map_order({
        "id": f"valid-{side}",
        "symbol": "BTC-USDT-SWAP",
        "side": side,
        "type": "limit",
        "amount": "1",
        "price": "100",
        "status": "open",
    })

    assert snapshot.side is expected


@pytest.mark.parametrize("side", ["hold", "future", None, "SELL_SHORT"])
def test_okx_base_adapter_skips_trade_rows_with_invalid_sides(side, caplog):
    adapter = OKXBaseAdapter("", "", "", "swap")

    snapshot = adapter._map_trade({
        "id": f"invalid-trade-{side}",
        "order": "order-1",
        "symbol": "BTC-USDT-SWAP",
        "side": side,
        "amount": "1",
        "price": "100",
        "timestamp": "1",
    })

    assert snapshot is None
    assert "Unknown exchange trade side" in caplog.text


def test_okx_base_adapter_maps_trade_side_aliases():
    adapter = OKXBaseAdapter("", "", "", "swap")

    snapshots = [
        adapter._map_trade({
            "id": f"trade-{side}",
            "order": "order-1",
            "symbol": "BTC-USDT-SWAP",
            "side": side,
            "amount": "1",
            "price": "100",
            "timestamp": "1",
        })
        for side in ("BUY", "SELL", "long", "short")
    ]

    assert [snapshot.side for snapshot in snapshots] == [
        OrderSide.BUY,
        OrderSide.SELL,
        OrderSide.BUY,
        OrderSide.SELL,
    ]


@pytest.mark.asyncio
async def test_okx_base_adapter_rejects_invalid_sides_from_order_and_trade_fetches():
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]
    async def fetch_open_orders(*args, **kwargs):
        return [
            {"id": "valid-order", "symbol": "BTC-USDT-SWAP", "side": "buy", "amount": "1"},
            {"id": "invalid-order", "symbol": "BTC-USDT-SWAP", "side": "hold", "amount": "1"},
        ]

    async def fetch_my_trades(*args, **kwargs):
        return [
            {
                "id": "valid-trade",
                "order": "valid-order",
                "symbol": "BTC-USDT-SWAP",
                "side": "short",
                "amount": "1",
                "price": "100",
            },
            {
                "id": "invalid-trade",
                "order": "invalid-order",
                "symbol": "BTC-USDT-SWAP",
                "side": "future",
                "amount": "1",
            },
        ]

    fake.fetch_open_orders = fetch_open_orders
    fake.fetch_my_trades = fetch_my_trades

    with pytest.raises(ValueError, match="Malformed provider open-order row"):
        await adapter.fetch_open_order_snapshots()
    with pytest.raises(ValueError, match="Malformed provider recent-trade row"):
        await adapter.fetch_recent_trade_snapshots()


@pytest.mark.parametrize("value", [10**1000, "1e1000000", float("nan"), float("inf"), "bad"])
def test_okx_base_adapter_optional_nonnegative_float_rejects_unrepresentable_values(value):
    adapter = OKXBaseAdapter("", "", "", "swap")

    assert adapter._optional_nonnegative_float(value) is None


def test_okx_base_adapter_rejects_bool_as_optional_numeric_provider_value():
    adapter = OKXBaseAdapter("", "", "", "swap")

    assert adapter._optional_nonnegative_float(True) is None
    assert adapter._optional_nonnegative_float(False) is None
    assert adapter._safe_float(True) == 0.0
    assert adapter._safe_float(False, 7.0) == 7.0


@pytest.mark.parametrize("field", ["amount", "price"])
def test_okx_base_adapter_rejects_bool_trade_numeric_fields(field):
    adapter = OKXBaseAdapter("", "", "", "swap")
    row = {
        "id": "bool-trade",
        "order": "order-1",
        "symbol": "BTC-USDT-SWAP",
        "side": "buy",
        "amount": "1",
        "price": "100",
        field: True,
    }

    assert adapter._map_trade(row) is None


@pytest.mark.parametrize("field", ["symbol", "id", "order", "clientOrderId"])
def test_okx_base_adapter_rejects_non_string_trade_identity_fields(field):
    adapter = OKXBaseAdapter("", "", "", "swap")
    row = {
        "id": "trade-1",
        "order": "order-1",
        "clientOrderId": "client-1",
        "symbol": "BTC-USDT-SWAP",
        "side": "buy",
        "amount": "1",
        "price": "100",
    }
    row[field] = {"forged": "identity"}

    with pytest.raises(MalformedProviderData, match="trade row"):
        adapter._map_trade(row)


@pytest.mark.parametrize("field", ["symbol", "id", "clientOrderId"])
def test_okx_base_adapter_rejects_non_string_order_identity_fields(field):
    adapter = OKXBaseAdapter("", "", "", "swap")
    row = {
        "id": "order-1",
        "clientOrderId": "client-1",
        "symbol": "BTC-USDT-SWAP",
        "side": "buy",
        "type": "market",
        "amount": "1",
    }
    row[field] = ["forged", "identity"]

    with pytest.raises(MalformedProviderData, match="order row"):
        adapter._map_order(row)


@pytest.mark.asyncio
async def test_okx_base_adapter_fails_closed_for_valid_and_bad_order_rows():
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]

    async def fetch_open_orders(*args, **kwargs):
        return [
            {
                "id": "valid-order",
                "symbol": "BTC-USDT-SWAP",
                "side": "buy",
                "type": "market",
                "amount": "1",
            },
            {
                "id": 42,
                "symbol": "BTC-USDT-SWAP",
                "side": "sell",
                "type": "market",
                "amount": "1",
            },
        ]

    fake.fetch_open_orders = fetch_open_orders

    with pytest.raises(MalformedProviderData, match="open-order row"):
        await adapter.fetch_open_order_snapshots()


@pytest.mark.asyncio
async def test_okx_base_adapter_fails_closed_for_valid_and_bad_trade_rows():
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]

    async def fetch_my_trades(*args, **kwargs):
        return [
            {
                "id": "valid-trade",
                "order": "order-1",
                "symbol": "BTC-USDT-SWAP",
                "side": "buy",
                "amount": "1",
                "price": "100",
            },
            {
                "id": "bad-trade",
                "order": "order-2",
                "symbol": {"forged": "symbol"},
                "side": "sell",
                "amount": "1",
                "price": "100",
            },
        ]

    fake.fetch_my_trades = fetch_my_trades

    with pytest.raises(MalformedProviderData, match="recent-trade row"):
        await adapter.fetch_recent_trade_snapshots()


@pytest.mark.parametrize("value", [10**1000, Decimal("1E999999"), "bad", "nan", "inf"])
def test_okx_base_adapter_rejects_position_with_unsafe_amount(value):
    adapter = OKXBaseAdapter("", "", "", "swap")

    with pytest.raises(MalformedProviderData, match="position row"):
        adapter._parse_position_snapshot({
            "symbol": "BTC-USDT-SWAP", "side": "long", "contracts": value,
            "entryPrice": value, "markPrice": value,
        })


@pytest.mark.asyncio
async def test_okx_base_adapter_skips_ticker_with_unrepresentable_numeric_field():
    adapter = OKXBaseAdapter("", "", "", "spot")
    fake = FakeOKX.instances[0]
    fake.raw_ticker_response = {"data": [{
        "instId": "BTC-USDT", "last": "100", "bidPx": "99", "askPx": "101",
        "vol24h": 10**1000,
    }]}

    assert await adapter.fetch_tickers(["BTC-USDT"]) == []


@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.parametrize("response", [None, "malformed", [] , {}])
async def test_okx_base_adapter_raises_accepted_unknown_for_unidentifiable_submit_response(
    response,
):
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {"BTC-USDT-SWAP": {"limits": {}, "precision": {}}}

    async def create_order(*args, **kwargs):
        return response

    fake.create_order = create_order

    with pytest.raises(AcceptedUnknownOrderResultError, match="reconciliation required"):
        await adapter.submit(Order(
            id="unknown-submit", symbol="BTC-USDT-SWAP", side=OrderSide.BUY,
            type=OrderType.MARKET, amount=1.0,
        ))


@pytest.mark.asyncio
async def test_okx_base_adapter_rejects_position_response_malformed_data():
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]

    for response in (None, "malformed", [None], [{"side": "mystery"}]):
        fake.positions_response = response
        with pytest.raises(MalformedProviderData, match="position"):
            await adapter.fetch_position_snapshots()

    fake.positions_response = []
    assert await adapter.fetch_position_snapshots() == []


@pytest.mark.parametrize(
    "row",
    [
        {"id": "missing-symbol", "side": "buy", "type": "market", "amount": "1"},
        {"id": "missing-type", "symbol": "BTC-USDT-SWAP", "side": "buy", "amount": "1"},
        {
            "id": "missing-price", "symbol": "BTC-USDT-SWAP", "side": "buy",
            "type": "limit", "amount": "1",
        },
        {
            "id": "bad-side", "symbol": "BTC-USDT-SWAP", "side": "hold",
            "type": "market", "amount": "1",
        },
        {
            "id": "bad-amount", "symbol": "BTC-USDT-SWAP", "side": "buy",
            "type": "market", "amount": "nan",
        },
        {
            "id": "bad-type", "symbol": "BTC-USDT-SWAP", "side": "buy",
            "type": "mystery", "amount": "1",
        },
        {"symbol": "BTC-USDT-SWAP", "side": "buy", "type": "market", "amount": "1"},
    ],
)
def test_okx_base_adapter_raises_for_each_malformed_order_row(row):
    adapter = OKXBaseAdapter("", "", "", "swap")

    with pytest.raises(MalformedProviderData, match="order row"):
        adapter._map_order(row)


def test_okx_base_adapter_allows_market_order_without_price():
    adapter = OKXBaseAdapter("", "", "", "swap")

    snapshot = adapter._map_order({
        "id": "market-without-price",
        "symbol": "BTC-USDT-SWAP",
        "side": "buy",
        "type": "market",
        "amount": "1",
    })

    assert snapshot.price == 0.0


@pytest.mark.asyncio
async def test_okx_base_adapter_keeps_accepted_order_recoverable_with_malformed_response_numbers():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {"BTC-USDT-SWAP": {"limits": {}, "precision": {}}}

    async def create_order(*args, **kwargs):
        return {
            "id": "accepted-but-malformed", "status": "open", "filled": 10**1000,
            "remaining": Decimal("1E999999"), "average": "not-a-price",
            "timestamp": 10**1000, "updated": "invalid",
        }

    fake.create_order = create_order

    with pytest.raises(
        AcceptedUnknownOrderResultError,
        match="malformed fill field; reconciliation required",
    ) as exc_info:
        await adapter.submit(Order(
            id="local-order", symbol="BTC-USDT-SWAP", side=OrderSide.BUY,
            type=OrderType.MARKET, amount=1.0,
        ))

    assert exc_info.value.id == "accepted-but-malformed"


@pytest.mark.asyncio
async def test_okx_base_adapter_submit_rejects_inconsistent_explicit_fill_group():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {"BTC-USDT-SWAP": {"limits": {}, "precision": {}}}

    async def create_order(*args, **kwargs):
        return {
            "id": "accepted-inconsistent-fill-group",
            "status": "open",
            "filled": "0.4",
            "remaining": "0.5",
        }

    fake.create_order = create_order

    with pytest.raises(
        AcceptedUnknownOrderResultError,
        match="malformed fill field; reconciliation required",
    ) as exc_info:
        await adapter.submit(Order(
            id="local-order", symbol="BTC-USDT-SWAP", side=OrderSide.BUY,
            type=OrderType.MARKET, amount=1.0,
        ))

    assert exc_info.value.id == "accepted-inconsistent-fill-group"


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["filled", "remaining"])
@pytest.mark.parametrize("value", [True, "bad", "nan", "inf", -1.0, 2.0])
async def test_okx_base_adapter_submit_rejects_explicit_malformed_fill_fields(field, value):
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {"BTC-USDT-SWAP": {"limits": {}, "precision": {}}}

    async def create_order(*args, **kwargs):
        return {"id": "accepted-malformed", "status": "open", field: value}

    fake.create_order = create_order

    with pytest.raises(
        AcceptedUnknownOrderResultError,
        match="malformed fill field; reconciliation required",
    ) as exc_info:
        await adapter.submit(Order(
            id="local-order", symbol="BTC-USDT-SWAP", side=OrderSide.BUY,
            type=OrderType.MARKET, amount=1.0,
        ))

    assert exc_info.value.id == "accepted-malformed"


@pytest.mark.asyncio
async def test_okx_base_adapter_ignores_unrepresentable_market_limits_when_submitting():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.markets = {
        "BTC-USDT-SWAP": {
            "limits": {
                "amount": {"min": 10**1000},
                "price": {"min": Decimal("1E999999")},
                "cost": {"min": "not-a-number"},
            },
            "precision": {},
        }
    }

    result = await adapter.submit(Order(
        id="safe-limit-order", symbol="BTC-USDT-SWAP", side=OrderSide.BUY,
        type=OrderType.LIMIT, amount=1.0, price=100.0,
    ))

    assert result.exchange_order_id == "okx-1"


@pytest.mark.asyncio
async def test_okx_base_adapter_rejects_invalid_ohlcv_rows_atomically():
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]

    async def fetch_ohlcv(*args, **kwargs):
        return [
            ["invalid", "100", "101", "99", "100.5", "12.5"],
            [1700000000001, "nan", "101", "99", "100.5", "12.5"],
            [1700000000002, "100", "101", "99", "inf", "12.5"],
            [1700000000003, "100", "101", "99", "100.5"],
            [1700000000000, "100", "101", "99", "100.5", "12.5"],
        ]

    fake.fetch_ohlcv = fetch_ohlcv

    with pytest.raises(MalformedProviderData, match="OHLCV row"):
        await adapter.fetch_ohlcv("BTC-USDT-SWAP", "1m")


def test_safe_ohlcv_bar_rejects_boolean_numeric_values():
    assert exchange_base.safe_ohlcv_bar([1, True, 2, 1, 1, 1]) is None
    assert exchange_base.safe_ohlcv_bar([1, 1, 2, 1, 1, False]) is None


def test_bar_rejects_boolean_numeric_values():
    from src.core.types import Bar

    with pytest.raises(ValueError, match="invalid OHLCV bar"):
        Bar(1, True, 2, 1, 1, 1)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("entryPrice", ""),
        ("entryPrice", "nan"),
        ("markPrice", "inf"),
        ("unrealizedPnl", True),
        ("leverage", "-2"),
        ("timestamp", "-1"),
    ],
)
def test_position_mapper_rejects_explicit_invalid_key_fields(field, value):
    adapter = OKXBaseAdapter("", "", "", "swap")
    row = {
        "symbol": "BTC-USDT-SWAP",
        "side": "long",
        "contracts": "1",
        "entryPrice": "100",
    }
    row[field] = value

    with pytest.raises(MalformedProviderData, match="position row"):
        adapter._parse_position_snapshot(row)


@pytest.mark.parametrize("field", ["average", "fill_price"])
def test_open_order_mapper_rejects_explicit_invalid_average_fields(field):
    adapter = OKXBaseAdapter("", "", "", "swap")

    with pytest.raises(MalformedProviderData, match="order row"):
        adapter._map_order({
            "id": "bad-average",
            "symbol": "BTC-USDT-SWAP",
            "side": "buy",
            "type": "market",
            "amount": "1",
            "status": "open",
            field: "nan",
        })


async def test_okx_base_adapter_rejects_impossible_ohlcv_rows_atomically():
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]

    async def fetch_ohlcv(*args, **kwargs):
        return [
            [1700000000000, 100, 101, 99, 100, 1],
            [1700000000001, 100, 98, 99, 99, 1],
            [1700000000002, 102, 101, 99, 100, 1],
            [1700000000003, 100, 101, 99, 102, 1],
            [1700000000004, 100, 101, 99, 100, -1],
        ]

    fake.fetch_ohlcv = fetch_ohlcv

    with pytest.raises(MalformedProviderData, match="OHLCV row"):
        await adapter.fetch_ohlcv("BTC-USDT-SWAP", "1m")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("symbols", "expected_symbol"),
    [(["BTC-USDT"], "BTC/USDT"), (None, None)],
)
async def test_okx_base_adapter_fetches_ordinary_and_trigger_open_orders(
    symbols, expected_symbol
):
    adapter = OKXBaseAdapter("", "", "", "spot")
    fake = FakeOKX.instances[0]
    calls = []

    async def fetch_open_orders(symbol=None, since=None, limit=None, params=None):
        calls.append((symbol, since, limit, params))
        if params == {"trigger": True}:
            return [{
                "id": "algo-1", "clientOrderId": "stop-client", "symbol": "BTC/USDT",
                "side": "sell", "type": "trigger", "amount": "0.1", "status": "open",
            }]
        return [{
            "id": "ordinary-1", "clientOrderId": "limit-client", "symbol": "BTC/USDT",
            "side": "buy", "type": "limit", "amount": "0.1", "price": "50000",
            "status": "open",
        }]

    fake.fetch_open_orders = fetch_open_orders

    snapshots = await adapter.fetch_open_order_snapshots(symbols)

    assert calls == [
        (expected_symbol, None, None, None),
        (expected_symbol, None, None, {"trigger": True}),
    ]
    assert [snapshot.exchange_order_id for snapshot in snapshots] == ["ordinary-1", "algo-1"]
    assert snapshots[1].execution_type_family == "stop"


@pytest.mark.asyncio
async def test_okx_base_adapter_returns_trigger_only_open_order():
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]

    async def fetch_open_orders(symbol=None, since=None, limit=None, params=None):
        if params == {"trigger": True}:
            return [{
                "id": "algo-only", "clientOrderId": "stop-client", "symbol": "BTC-USDT-SWAP",
                "side": "sell", "type": "trigger_limit", "amount": "1", "price": "49000",
                "status": "open",
            }]
        return []

    fake.fetch_open_orders = fetch_open_orders

    snapshots = await adapter.fetch_open_order_snapshots(["BTC-USDT-SWAP"])

    assert [snapshot.exchange_order_id for snapshot in snapshots] == ["algo-only"]
    assert snapshots[0].execution_type_family == "stop"


@pytest.mark.asyncio
async def test_okx_base_adapter_deduplicates_matching_ordinary_and_trigger_order():
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]
    duplicate = {
        "id": "shared-order", "clientOrderId": "shared-client", "symbol": "BTC-USDT-SWAP",
        "side": "buy", "type": "limit", "amount": "1", "price": "50000", "status": "open",
    }

    async def fetch_open_orders(symbol=None, since=None, limit=None, params=None):
        return [duplicate]

    fake.fetch_open_orders = fetch_open_orders

    snapshots = await adapter.fetch_open_order_snapshots(["BTC-USDT-SWAP"])

    assert [snapshot.exchange_order_id for snapshot in snapshots] == ["shared-order"]


@pytest.mark.asyncio
async def test_okx_base_adapter_fails_closed_for_conflicting_duplicate_open_order_facts(caplog):
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]

    async def fetch_open_orders(symbol=None, since=None, limit=None, params=None):
        return [{
            "id": "shared-order", "clientOrderId": "shared-client", "symbol": "BTC-USDT-SWAP",
            "side": "buy", "type": "limit", "amount": "1",
            "price": "49000" if params == {"trigger": True} else "50000", "status": "open",
        }]

    fake.fetch_open_orders = fetch_open_orders

    with pytest.raises(MalformedProviderData, match="conflicting duplicate"):
        await adapter.fetch_open_order_snapshots(["BTC-USDT-SWAP"])

    assert "Conflicting duplicate open-order facts" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger_response", [None, {"not": "a list"}])
async def test_okx_base_adapter_fails_closed_for_malformed_trigger_open_orders(
    trigger_response,
):
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]

    async def fetch_open_orders(symbol=None, since=None, limit=None, params=None):
        return trigger_response if params == {"trigger": True} else []

    fake.fetch_open_orders = fetch_open_orders

    with pytest.raises(MalformedProviderData, match="Malformed provider .*open-order response"):
        await adapter.fetch_open_order_snapshots(["BTC-USDT-SWAP"])


@pytest.mark.asyncio
async def test_okx_base_adapter_fails_closed_for_trigger_open_order_network_failure():
    adapter = OKXBaseAdapter("", "", "", "swap")
    fake = FakeOKX.instances[0]

    async def fetch_open_orders(symbol=None, since=None, limit=None, params=None):
        if params == {"trigger": True}:
            raise NetworkError("trigger endpoint unavailable")
        return []

    fake.fetch_open_orders = fetch_open_orders

    with pytest.raises(NetworkError, match="trigger endpoint unavailable"):
        await adapter.fetch_open_order_snapshots(["BTC-USDT-SWAP"])


@pytest.mark.asyncio
async def test_okx_base_adapter_surfaces_unsupported_trigger_endpoint_without_missing_order_result(
    caplog,
):
    adapter = OKXBaseAdapter("", "", "", "option")
    fake = FakeOKX.instances[0]

    async def fetch_open_orders(symbol=None, since=None, limit=None, params=None):
        if params == {"trigger": True}:
            raise NotSupported("algo endpoint is unsupported for options")
        return []

    fake.fetch_open_orders = fetch_open_orders

    with pytest.raises(NotSupported, match="algo endpoint is unsupported"):
        await adapter.fetch_open_order_snapshots(["BTC-USD-260925-100000-C"])

    assert "Trigger open-order endpoint is not supported" in caplog.text


@pytest.mark.asyncio
async def test_okx_base_adapter_empty_open_order_scope_does_not_query_provider():
    adapter = OKXBaseAdapter("", "", "", "spot")
    fake = FakeOKX.instances[0]
    calls = []

    async def fetch_open_orders(*args, **kwargs):
        calls.append((args, kwargs))
        return []

    fake.fetch_open_orders = fetch_open_orders

    assert await adapter.fetch_open_order_snapshots([]) == []
    assert calls == []


@pytest.mark.asyncio
async def test_okx_base_adapter_empty_trade_scope_does_not_query_provider():
    adapter = OKXBaseAdapter("", "", "", "spot")
    fake = FakeOKX.instances[0]
    calls = []

    async def fetch_my_trades(*args, **kwargs):
        calls.append((args, kwargs))
        return []

    fake.fetch_my_trades = fetch_my_trades

    assert await adapter.fetch_recent_trade_snapshots([]) == []
    assert calls == []


@pytest.mark.parametrize(
    ("contracts", "fallback_field", "fallback_value", "expected_side"),
    [
        (None, "amount", "2", PositionSide.LONG),
        ("", "positionAmt", "-2", PositionSide.SHORT),
    ],
)
def test_derivative_quantity_falls_back_when_primary_field_is_empty(
    contracts, fallback_field, fallback_value, expected_side
):
    adapter = OKXBaseAdapter("", "", "", "swap")
    row = {
        "symbol": "BTC-USDT-SWAP",
        "contracts": contracts,
        fallback_field: fallback_value,
        "entryPrice": "100",
    }

    snapshot = adapter._parse_position_snapshot(row)

    assert snapshot.side is expected_side
    assert snapshot.amount == pytest.approx(2.0)


def test_derivative_quantity_invalid_primary_field_does_not_fallback():
    adapter = OKXBaseAdapter("", "", "", "swap")

    with pytest.raises(MalformedProviderData, match="invalid amount"):
        adapter._parse_position_snapshot(
            {
                "symbol": "BTC-USDT-SWAP",
                "contracts": "not-a-number",
                "amount": "2",
                "entryPrice": "100",
            }
        )
