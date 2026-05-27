from src.core.types import Bar, Order, OrderSide, OrderStatus, OrderType, Position, PositionSide


def test_bar_creation():
    bar = Bar(
        timestamp=1700000000000,
        open=50000.0,
        high=51000.0,
        low=49000.0,
        close=50500.0,
        volume=100.5,
    )
    assert bar.timestamp == 1700000000000
    assert bar.open == 50000.0
    assert bar.close == 50500.0


def test_order_creation():
    order = Order(
        id="test-001",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=0.1,
        price=50000.0,
        stop_loss=49000.0,
        take_profit=52000.0,
    )
    assert order.id == "test-001"
    assert order.status == OrderStatus.PENDING
    assert order.fill_price is None


def test_position_creation():
    pos = Position(
        symbol="BTC-USDT-SWAP",
        side=PositionSide.LONG,
        amount=0.5,
        entry_price=50000.0,
        unrealized_pnl=250.0,
        leverage=10,
    )
    assert pos.symbol == "BTC-USDT-SWAP"
    assert pos.leverage == 10
