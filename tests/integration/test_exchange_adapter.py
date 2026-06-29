from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.types import (
    AccountSnapshot,
    ExchangeTradeSnapshot,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
)
from src.exchange.okx_futures import OKXFuturesAdapter
from src.exchange.okx_options import OKXOptionsAdapter
from src.exchange.okx_spot import OKXSpotAdapter
from src.exchange.okx_swap import OKXSwapAdapter


@pytest.mark.parametrize(
    ("adapter_cls", "default_type"),
    [
        (OKXSpotAdapter, "spot"),
        (OKXSwapAdapter, "swap"),
        (OKXFuturesAdapter, "future"),
        (OKXOptionsAdapter, "option"),
    ],
)
def test_okx_adapters_configure_market_type(adapter_cls, default_type):
    with patch("src.exchange.base.ccxt") as ccxt:
        exchange = MagicMock()
        ccxt.okx.return_value = exchange
        adapter_cls("api-key", "secret", "passphrase")

    ccxt.okx.assert_called_once_with(
        {
            "apiKey": "api-key",
            "secret": "secret",
            "password": "passphrase",
            "options": {"defaultType": default_type},
        }
    )
    exchange.set_sandbox_mode.assert_called_once_with(True)


def test_okx_adapter_can_disable_demo_mode_for_live_configuration():
    with patch("src.exchange.base.ccxt") as ccxt:
        exchange = MagicMock()
        ccxt.okx.return_value = exchange
        OKXSpotAdapter("api-key", "secret", "passphrase", demo=False)

    exchange.set_sandbox_mode.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_ohlcv_maps_rows_to_bars():
    with patch("src.exchange.base.ccxt") as ccxt:
        exchange = AsyncMock()
        exchange.fetch_ohlcv.return_value = [
            [1700000000000, 50000, 51000, 49000, 50500, 100.5],
            [1700000060000, 50500, 51500, 50000, 51200, 80.25],
        ]
        ccxt.okx.return_value = exchange
        adapter = OKXSpotAdapter("api-key", "secret", "passphrase")

    bars = await adapter.fetch_ohlcv("BTC-USDT", "1m", limit=2)

    exchange.fetch_ohlcv.assert_awaited_once_with("BTC-USDT", "1m", since=None, limit=2)
    assert [bar.timestamp for bar in bars] == [1700000000000, 1700000060000]
    assert bars[0].open == 50000.0
    assert bars[1].close == 51200.0


@pytest.mark.asyncio
async def test_fetch_ohlcv_forwards_since_to_exchange():
    with patch("src.exchange.base.ccxt") as ccxt:
        exchange = AsyncMock()
        exchange.fetch_ohlcv.return_value = [
            [1700000060000, 50500, 51500, 50000, 51200, 80.25],
        ]
        ccxt.okx.return_value = exchange
        adapter = OKXSpotAdapter("api-key", "secret", "passphrase")

    bars = await adapter.fetch_ohlcv("BTC-USDT", "1m", limit=1, since=1700000060000)

    exchange.fetch_ohlcv.assert_awaited_once_with(
        "BTC-USDT",
        "1m",
        since=1700000060000,
        limit=1,
    )
    assert [bar.timestamp for bar in bars] == [1700000060000]


@pytest.mark.asyncio
async def test_fetch_tickers_maps_public_ticker_fields():
    with patch("src.exchange.base.ccxt") as ccxt:
        exchange = AsyncMock()
        exchange.fetch_tickers.return_value = {
            "BTC-USDT": {
                "symbol": "BTC-USDT",
                "last": 68000,
                "bid": 67999.5,
                "ask": 68000.5,
                "baseVolume": 123.45,
            },
            "ETH-USDT": {
                "symbol": "ETH-USDT",
                "last": 3800,
                "bid": 3799.5,
                "ask": 3800.5,
                "baseVolume": 456.78,
            },
        }
        ccxt.okx.return_value = exchange
        adapter = OKXSpotAdapter("api-key", "secret", "passphrase")

    tickers = await adapter.fetch_tickers(["BTC-USDT", "ETH-USDT"])

    exchange.fetch_tickers.assert_awaited_once_with(["BTC-USDT", "ETH-USDT"])
    assert tickers == [
        {
            "symbol": "BTC-USDT",
            "last": 68000.0,
            "bidPx": 67999.5,
            "askPx": 68000.5,
            "vol24h": 123.45,
        },
        {
            "symbol": "ETH-USDT",
            "last": 3800.0,
            "bidPx": 3799.5,
            "askPx": 3800.5,
            "vol24h": 456.78,
        },
    ]


@pytest.mark.asyncio
async def test_fetch_tickers_preserves_requested_okx_symbols_when_ccxt_normalizes_keys():
    with patch("src.exchange.base.ccxt") as ccxt:
        exchange = AsyncMock()
        exchange.fetch_tickers.return_value = {
            "BTC/USDT": {
                "symbol": "BTC/USDT",
                "last": 68000,
                "bid": 67999.5,
                "ask": 68000.5,
                "baseVolume": 123.45,
            },
            "ETH/USDT": {
                "symbol": "ETH/USDT",
                "last": 3800,
                "bid": 3799.5,
                "ask": 3800.5,
                "baseVolume": 456.78,
            },
        }
        ccxt.okx.return_value = exchange
        adapter = OKXSpotAdapter("api-key", "secret", "passphrase")

    tickers = await adapter.fetch_tickers(["BTC-USDT", "ETH-USDT"])

    assert [ticker["symbol"] for ticker in tickers] == ["BTC-USDT", "ETH-USDT"]


@pytest.mark.asyncio
async def test_submit_creates_order_and_maps_exchange_response():
    with patch("src.exchange.base.ccxt") as ccxt:
        exchange = AsyncMock()
        exchange.create_order.return_value = {
            "id": "exchange-order-1",
            "status": "closed",
            "average": 50500,
            "timestamp": 1700000000000,
        }
        ccxt.okx.return_value = exchange
        adapter = OKXSwapAdapter("api-key", "secret", "passphrase")

    order = Order(
        id="local-order-1",
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=0.1,
        price=50000,
    )
    result = await adapter.submit(order)

    exchange.create_order.assert_awaited_once_with(
        "BTC-USDT-SWAP",
        "limit",
        "buy",
        0.1,
        50000,
        {},
    )
    assert result.id == "exchange-order-1"
    assert result.status == OrderStatus.FILLED
    assert result.fill_price == 50500.0
    assert result.fill_time == 1700000000000


@pytest.mark.asyncio
async def test_submit_leaves_fill_fields_empty_for_open_order():
    with patch("src.exchange.base.ccxt") as ccxt:
        exchange = AsyncMock()
        exchange.create_order.return_value = {
            "id": "exchange-order-1",
            "status": "open",
            "price": 50000,
            "timestamp": 1700000000000,
        }
        ccxt.okx.return_value = exchange
        adapter = OKXSwapAdapter("api-key", "secret", "passphrase")

    order = Order(
        id="local-order-1",
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=0.1,
        price=50000,
    )
    result = await adapter.submit(order)

    assert result.status == OrderStatus.PENDING
    assert result.fill_price is None
    assert result.fill_time is None


@pytest.mark.asyncio
async def test_submit_rejects_stop_orders_without_okx_trigger_params():
    with patch("src.exchange.base.ccxt") as ccxt:
        ccxt.okx.return_value = AsyncMock()
        adapter = OKXSwapAdapter("api-key", "secret", "passphrase")

    order = Order(
        id="local-order-1",
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        type=OrderType.STOP,
        amount=0.1,
        price=50000,
    )

    with pytest.raises(ValueError, match="Stop orders require OKX trigger parameters"):
        await adapter.submit(order)


@pytest.mark.asyncio
async def test_submit_rejects_stop_loss_or_take_profit_until_okx_params_are_supported():
    with patch("src.exchange.base.ccxt") as ccxt:
        ccxt.okx.return_value = AsyncMock()
        adapter = OKXSwapAdapter("api-key", "secret", "passphrase")

    order = Order(
        id="local-order-1",
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=0.1,
        price=50000,
        stop_loss=49000,
    )

    with pytest.raises(ValueError, match="OKX stop_loss and take_profit are not supported"):
        await adapter.submit(order)


@pytest.mark.asyncio
async def test_cancel_delegates_to_exchange_with_symbol():
    with patch("src.exchange.base.ccxt") as ccxt:
        exchange = AsyncMock()
        ccxt.okx.return_value = exchange
        adapter = OKXSpotAdapter("api-key", "secret", "passphrase")

    assert await adapter.cancel("exchange-order-1", symbol="BTC-USDT") is True

    exchange.cancel_order.assert_awaited_once_with("exchange-order-1", "BTC-USDT")


@pytest.mark.asyncio
async def test_cancel_requires_symbol_for_okx():
    with patch("src.exchange.base.ccxt") as ccxt:
        exchange = AsyncMock()
        ccxt.okx.return_value = exchange
        adapter = OKXSpotAdapter("api-key", "secret", "passphrase")

    with pytest.raises(ValueError, match="OKX cancel requires symbol"):
        await adapter.cancel("exchange-order-1")

    exchange.cancel_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_fetch_account_snapshot_maps_private_balance_response():
    with patch("src.exchange.base.ccxt") as ccxt:
        exchange = AsyncMock()
        exchange.fetch_balance.return_value = {
            "total": {"USDT": 1000, "BTC": 0.1},
            "free": {"USDT": 900, "BTC": 0.05},
            "info": {"uTime": "1700000000000"},
        }
        ccxt.okx.return_value = exchange
        adapter = OKXSpotAdapter("api-key", "secret", "passphrase")

    snapshot = await adapter.fetch_account_snapshot()

    assert snapshot == AccountSnapshot(
        initial_equity=1000.1,
        cash_balance=900.05,
        equity=1000.1,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        timestamp=1700000000000,
    )


@pytest.mark.asyncio
async def test_fetch_open_order_snapshots_maps_status_and_identifiers():
    with patch("src.exchange.base.ccxt") as ccxt:
        exchange = AsyncMock()
        exchange.fetch_open_orders.return_value = [
            {
                "id": "ex-1",
                "clientOrderId": "client-1",
                "symbol": "BTC-USDT",
                "side": "buy",
                "type": "limit",
                "amount": 0.1,
                "price": 50000,
                "status": "open",
                "average": None,
                "timestamp": 1700000000000,
                "lastTradeTimestamp": 1700000001000,
            }
        ]
        ccxt.okx.return_value = exchange
        adapter = OKXSpotAdapter("api-key", "secret", "passphrase")

    snapshots = await adapter.fetch_open_order_snapshots(["BTC-USDT"])

    exchange.fetch_open_orders.assert_awaited_once_with("BTC-USDT")
    assert snapshots[0].exchange_order_id == "ex-1"
    assert snapshots[0].client_order_id == "client-1"
    assert snapshots[0].status == OrderStatus.PENDING
    assert snapshots[0].updated_at == 1700000001000


@pytest.mark.asyncio
async def test_fetch_recent_trade_snapshots_maps_fee_cost_and_order_ids():
    with patch("src.exchange.base.ccxt") as ccxt:
        exchange = AsyncMock()
        exchange.fetch_my_trades.return_value = [
            {
                "id": "trade-1",
                "order": "ex-1",
                "clientOrderId": "client-1",
                "symbol": "BTC-USDT",
                "side": "buy",
                "amount": 0.1,
                "price": 50000,
                "fee": {"cost": 2.5, "currency": "USDT"},
                "timestamp": 1700000000000,
            },
            {
                "id": "trade-2",
                "order": "ex-2",
                "symbol": "BTC-USDT",
                "side": "sell",
                "amount": 0.2,
                "price": 51000,
                "fee": None,
                "timestamp": 1700000001000,
            },
        ]
        ccxt.okx.return_value = exchange
        adapter = OKXSpotAdapter("api-key", "secret", "passphrase")

    snapshots = await adapter.fetch_recent_trade_snapshots(
        symbols=["BTC-USDT"], since=1700000000000, limit=2
    )

    exchange.fetch_my_trades.assert_awaited_once_with(
        "BTC-USDT", since=1700000000000, limit=2
    )
    assert snapshots == [
        ExchangeTradeSnapshot(
            exchange_trade_id="trade-1",
            exchange_order_id="ex-1",
            client_order_id="client-1",
            symbol="BTC-USDT",
            side=OrderSide.BUY,
            amount=0.1,
            price=50000.0,
            fee=2.5,
            timestamp=1700000000000,
        ),
        ExchangeTradeSnapshot(
            exchange_trade_id="trade-2",
            exchange_order_id="ex-2",
            client_order_id="",
            symbol="BTC-USDT",
            side=OrderSide.SELL,
            amount=0.2,
            price=51000.0,
            fee=0.0,
            timestamp=1700000001000,
        ),
    ]


@pytest.mark.asyncio
async def test_close_delegates_to_exchange():
    with patch("src.exchange.base.ccxt") as ccxt:
        exchange = AsyncMock()
        ccxt.okx.return_value = exchange
        adapter = OKXOptionsAdapter("api-key", "secret", "passphrase")

    await adapter.close()

    exchange.close.assert_awaited_once_with()
