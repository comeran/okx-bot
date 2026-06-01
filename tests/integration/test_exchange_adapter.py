from unittest.mock import AsyncMock, patch

import pytest

from src.core.types import Order, OrderSide, OrderStatus, OrderType
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
        adapter_cls("api-key", "secret", "passphrase")

    ccxt.okx.assert_called_once_with(
        {
            "apiKey": "api-key",
            "secret": "secret",
            "password": "passphrase",
            "options": {"defaultType": default_type},
        }
    )


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

    exchange.fetch_ohlcv.assert_awaited_once_with("BTC-USDT", "1m", limit=2)
    assert [bar.timestamp for bar in bars] == [1700000000000, 1700000060000]
    assert bars[0].open == 50000.0
    assert bars[1].close == 51200.0


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
async def test_close_delegates_to_exchange():
    with patch("src.exchange.base.ccxt") as ccxt:
        exchange = AsyncMock()
        ccxt.okx.return_value = exchange
        adapter = OKXOptionsAdapter("api-key", "secret", "passphrase")

    await adapter.close()

    exchange.close.assert_awaited_once_with()
