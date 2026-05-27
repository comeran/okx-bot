import pytest

from src.core.types import Order, OrderSide, OrderStatus, OrderType
from src.order.manager import UnifiedOrderManager
from src.order.router import OrderHandler, OrderRouter


class MockHandler(OrderHandler):
    def __init__(self):
        self.submitted = []
        self.cancelled = []

    async def submit(self, order: Order) -> Order:
        self.submitted.append(order)
        order.status = OrderStatus.FILLED
        order.fill_price = 50000
        return order

    async def cancel(self, order_id: str) -> bool:
        self.cancelled.append(order_id)
        return True


@pytest.mark.asyncio
async def test_router_backtest():
    handler = MockHandler()
    router = OrderRouter(backtest=handler, mode="backtest")
    order = Order(
        id="1",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=0.1,
    )
    result = await router.submit(order)
    assert result.status == OrderStatus.FILLED
    assert len(handler.submitted) == 1


@pytest.mark.asyncio
async def test_router_mode_switch():
    bt_handler = MockHandler()
    live_handler = MockHandler()
    router = OrderRouter(backtest=bt_handler, live=live_handler, mode="backtest")
    await router.submit(
        Order(
            id="1",
            symbol="BTC-USDT",
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            amount=0.1,
        )
    )
    assert len(bt_handler.submitted) == 1
    assert len(live_handler.submitted) == 0
    router.mode = "live"
    await router.submit(
        Order(
            id="2",
            symbol="BTC-USDT",
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            amount=0.1,
        )
    )
    assert len(live_handler.submitted) == 1


@pytest.mark.asyncio
async def test_order_manager_submit():
    handler = MockHandler()
    router = OrderRouter(backtest=handler, mode="backtest")
    manager = UnifiedOrderManager(router=router)
    order = await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.1,
        strategy_name="test",
    )
    assert order.status == OrderStatus.FILLED
