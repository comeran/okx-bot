import pytest

from src.core.types import Order, OrderSide, OrderType, PositionSide
from src.exchange import base as exchange_base
from src.exchange.base import OKXBaseAdapter


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

    async def fetch_tickers(self, symbols):
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

    assert result.id == "okx-1"
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

    assert result.id == "okx-1"
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

    assert result.id == "okx-1"
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
        "USDT": {"total": "125.5", "free": "100.25", "used": "25.25"},
        "info": {
            "data": [
                {
                    "details": [
                        {
                            "ccy": "USDT",
                            "eq": "125.5",
                            "cashBal": "120",
                            "availBal": "100.25",
                            "upl": "5.5",
                            "realizedPnl": "1.25",
                            "uTime": "1700000000000",
                        }
                    ]
                }
            ]
        },
    }

    snapshot = await adapter.fetch_account_snapshot()

    assert fake.fetch_balance_calls == 1
    assert snapshot.currency == "USDT"
    assert snapshot.equity == 125.5
    assert snapshot.cash_balance == 120.0
    assert snapshot.available_balance == 100.25
    assert snapshot.unrealized_pnl == 5.5
    assert snapshot.realized_pnl == 1.25
    assert snapshot.updated_at == 1700000000000


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


@pytest.mark.asyncio
async def test_okx_base_adapter_fetch_position_snapshots_prefers_inst_id_and_explicit_side():
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
            "info": {"instId": "BTC-USDT-SWAP", "posSide": "short"},
        }
    ]

    snapshots = await adapter.fetch_position_snapshots(["BTC-USDT-SWAP"])

    assert fake.fetch_positions_calls == [["BTC-USDT-SWAP"]]
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
async def test_okx_base_adapter_fetch_position_snapshots_prefers_pos_side_before_amount_sign():
    adapter = OKXBaseAdapter("api-key", "secret", "passphrase", "swap")
    fake = FakeOKX.instances[0]
    fake.positions_response = [
        {
            "symbol": "ETH/USDT:USDT",
            "contracts": "-1.5",
            "entryPrice": "3000",
            "info": {"posSide": "long"},
        },
        {
            "symbol": "SOL-USDT-SWAP",
            "contracts": "-2",
            "entryPrice": "150",
            "info": {"posSide": "net"},
        },
    ]

    snapshots = await adapter.fetch_position_snapshots()

    assert fake.fetch_positions_calls == [None]
    assert [snapshot.side for snapshot in snapshots] == [PositionSide.LONG, PositionSide.SHORT]
    assert [snapshot.symbol for snapshot in snapshots] == ["ETH/USDT:USDT", "SOL-USDT-SWAP"]
    assert [snapshot.amount for snapshot in snapshots] == [1.5, 2.0]
