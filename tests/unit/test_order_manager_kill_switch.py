from unittest.mock import AsyncMock

import pytest

from src.core.types import Order, OrderSide, OrderStatus, OrderType
from src.order.manager import UnifiedOrderManager, risk_reason_code


class RecordingRepository:
    def __init__(self):
        self.orders = []

    def upsert_order(self, order):
        self.orders.append(order)
        return order


@pytest.mark.asyncio
async def test_kill_switch_rejects_order_before_router_submit():
    router = AsyncMock()
    repository = RecordingRepository()
    risk_events = []

    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        on_risk_event=lambda payload: risk_events.append(payload),
        kill_switch_checker=lambda: True,
    )

    order = await manager.submit(
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=1.0,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.REJECTED
    router.submit.assert_not_awaited()
    assert repository.orders[0].status == "rejected"
    assert risk_events[0]["reason_code"] == "kill_switch_engaged"
    assert risk_events[0]["reason"] == "Kill switch engaged"


@pytest.mark.asyncio
async def test_disengaged_kill_switch_allows_router_submit():
    submitted = Order(
        id="submitted-1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=1.0,
        status=OrderStatus.PENDING,
    )
    router = AsyncMock()
    router.submit.return_value = submitted
    repository = RecordingRepository()

    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        kill_switch_checker=lambda: False,
    )

    order = await manager.submit(
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=1.0,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.PENDING
    router.submit.assert_awaited_once()


def test_kill_switch_reason_code_is_specific():
    assert risk_reason_code("Kill switch engaged") == "kill_switch_engaged"
