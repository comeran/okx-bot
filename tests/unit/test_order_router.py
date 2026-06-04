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

    async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
        self.cancelled.append((order_id, symbol))
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


@pytest.mark.asyncio
async def test_order_manager_generates_unique_order_ids_for_repeated_submits():
    handler = MockHandler()
    router = OrderRouter(backtest=handler, mode="backtest")
    manager = UnifiedOrderManager(router=router)

    order1 = await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.1,
        strategy_name="test",
    )
    order2 = await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=0.1,
        strategy_name="test",
    )

    assert order1.id != order2.id


@pytest.mark.asyncio
async def test_order_manager_persists_filled_order_trade_and_position():
    class FakeRepository:
        def __init__(self):
            self.orders = []
            self.trades = []
            self.positions = []

        def save_order(self, order):
            self.orders.append(order)
            return order

        def save_trade(self, trade):
            self.trades.append(trade)
            return trade

        def save_position(self, position):
            self.positions.append(position)
            return position

    handler = MockHandler()
    repository = FakeRepository()
    router = OrderRouter(backtest=handler, mode="backtest")
    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
    )

    order = await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.1,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.FILLED
    assert [saved.model_dump() for saved in repository.orders] == [
        {
            "id": None,
            "order_id": order.id,
            "strategy": "ma_cross",
            "symbol": "BTC-USDT",
            "side": "buy",
            "type": "market",
            "amount": 0.1,
            "price": 0.0,
            "status": "filled",
            "fill_price": 50000.0,
            "timestamp": 1700000000000,
        }
    ]
    assert [saved.model_dump() for saved in repository.trades] == [
        {
            "id": None,
            "strategy": "ma_cross",
            "symbol": "BTC-USDT",
            "side": "buy",
            "amount": 0.1,
            "price": 50000.0,
            "fee": 0.0,
            "timestamp": 1700000000000,
        }
    ]
    assert [saved.model_dump() for saved in repository.positions] == [
        {
            "id": None,
            "strategy": "ma_cross",
            "symbol": "BTC-USDT",
            "side": "long",
            "amount": 0.1,
            "entry_price": 50000.0,
            "leverage": 1,
            "timestamp": 1700000000000,
        }
    ]


@pytest.mark.asyncio
async def test_order_manager_persists_pending_order_without_trade_or_position():
    class PendingHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            return order

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    class FakeRepository:
        def __init__(self):
            self.orders = []
            self.trades = []
            self.positions = []

        def save_order(self, order):
            self.orders.append(order)
            return order

        def save_trade(self, trade):
            self.trades.append(trade)
            return trade

        def save_position(self, position):
            self.positions.append(position)
            return position

    repository = FakeRepository()
    router = OrderRouter(backtest=PendingHandler(), mode="backtest")
    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
    )

    await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        amount=0.1,
        price=49000.0,
        strategy_name="ma_cross",
    )

    assert len(repository.orders) == 1
    assert repository.orders[0].status == "pending"
    assert repository.trades == []
    assert repository.positions == []


@pytest.mark.asyncio
async def test_order_manager_cancel_forwards_symbol():
    handler = MockHandler()
    router = OrderRouter(backtest=handler, mode="backtest")
    manager = UnifiedOrderManager(router=router)

    assert await manager.cancel("exchange-order-1", symbol="BTC-USDT") is True

    assert handler.cancelled == [("exchange-order-1", "BTC-USDT")]
