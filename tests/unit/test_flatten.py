import pytest

from src.core.types import OrderSide, OrderStatus, OrderType
from src.data.models import OrderRecord, PositionRecord
from src.ops.flatten import cancel_open_orders, flatten_exposure
from src.order.manager import UnifiedOrderManager
from src.order.router import OrderRouter
from tests.unit.test_order_router import FakeRepository, MockHandler


def _manager(repository, handler, *, mode="backtest", kill_switch=False):
    return UnifiedOrderManager(
        router=OrderRouter(backtest=handler, live=handler, mode=mode),
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        kill_switch_checker=lambda: kill_switch,
    )


def _pending_order():
    return OrderRecord(
        order_id="local-1",
        exchange_order_id="exchange-1",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=0.1,
        price=49000.0,
        status="pending",
        fill_price=0.0,
        timestamp=1700000000000,
        updated_at=1700000000000,
        filled_amount=0.0,
        remaining_amount=0.1,
    )


def _long_position(amount=0.2):
    return PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="long",
        amount=amount,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
    )


@pytest.mark.asyncio
async def test_cancel_removes_resting_orders_and_does_not_open_a_new_one():
    repository = FakeRepository()
    repository.orders.append(_pending_order())
    filled = _pending_order()
    filled.order_id = "done"
    filled.exchange_order_id = "done-exchange"
    filled.status = "filled"
    filled.filled_amount = 0.1
    filled.remaining_amount = 0.0
    repository.orders.append(filled)
    handler = MockHandler()
    manager = _manager(repository, handler, kill_switch=True)

    result = await cancel_open_orders(manager)

    assert result["cancelled"] == ["exchange-1"]
    assert result["failed"] == []
    assert handler.cancelled == [("exchange-1", "BTC-USDT")]
    assert handler.submitted == []


@pytest.mark.asyncio
async def test_flatten_cancels_then_closes_the_position_while_kill_switch_is_on():
    repository = FakeRepository()
    repository.orders.append(_pending_order())
    repository.upsert_position(_long_position())
    handler = MockHandler()
    manager = _manager(repository, handler, kill_switch=True)

    result = await flatten_exposure(manager)

    assert result["cancelled"] == ["exchange-1"]
    assert result["closed"] == [{
        "strategy": "ma_cross",
        "symbol": "BTC-USDT",
        "side": "sell",
        "amount": 0.2,
        "status": "filled",
    }]
    assert handler.submitted[0].side is OrderSide.SELL
    assert handler.submitted[0].type is OrderType.MARKET
    assert handler.submitted[0].amount == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_flatten_buys_back_a_short_without_opening_a_new_long():
    repository = FakeRepository()
    short = _long_position(0.3)
    short.side = "short"
    repository.upsert_position(short)
    handler = MockHandler()
    manager = _manager(repository, handler, kill_switch=True)

    result = await flatten_exposure(manager)

    assert result["closed"][0]["side"] == "buy"
    assert result["closed"][0]["amount"] == pytest.approx(0.3)
    assert handler.submitted[0].side is OrderSide.BUY
    assert len(handler.submitted) == 1


@pytest.mark.asyncio
async def test_protective_reduce_cannot_open_exposure():
    handler = MockHandler()
    manager = _manager(FakeRepository(), handler, kill_switch=True)

    rejected = await manager.submit(
        "BTC-USDT",
        OrderSide.BUY,
        OrderType.MARKET,
        0.1,
        strategy_name="ma_cross",
        protective_reduce=True,
    )

    assert rejected.status is OrderStatus.REJECTED
    assert handler.submitted == []


@pytest.mark.asyncio
async def test_unresolved_live_order_is_cancelled_but_not_replaced():
    class UnresolvedRepository(FakeRepository):
        def get_risk_events(self):
            return [{
                "reason_code": "live_order_submission_unknown",
                "client_order_id": "cid-1",
            }]

    repository = UnresolvedRepository()
    order = _pending_order()
    order.client_order_id = "cid-1"
    repository.orders.append(order)
    repository.upsert_position(_long_position())
    handler = MockHandler()
    manager = _manager(repository, handler, mode="live", kill_switch=True)

    result = await flatten_exposure(manager)

    assert result["cancelled"] == ["exchange-1"]
    assert result["closed"] == []
    assert result["close_failed"][0]["reason"] == "unresolved_live_order"
    assert handler.submitted == []
