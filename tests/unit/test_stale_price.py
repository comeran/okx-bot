import pytest

from src.core.types import OrderSide, OrderStatus, OrderType
from src.data.models import PositionRecord
from src.order.manager import UnifiedOrderManager
from src.order.router import OrderRouter
from tests.unit.test_order_router import FakeRepository, MockHandler


@pytest.mark.asyncio
async def test_stale_or_missing_price_blocks_opening_and_allows_a_full_reduce():
    repository = FakeRepository()
    repository.upsert_position(PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="long",
        amount=0.2,
        entry_price=50000.0,
        leverage=1,
        timestamp=1_700_000_000_000,
    ))
    handler = MockHandler()
    now = {"value": 1_700_000_020_000}
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=handler, mode="backtest"),
        repository=repository,
        timestamp_ms=lambda: now["value"],
        price_observed_at_provider=lambda _symbol: 1_700_000_000_000,
        max_price_age_ms=15_000,
    )

    stale = await manager.submit(
        "BTC-USDT", OrderSide.BUY, OrderType.MARKET, 0.1, strategy_name="ma_cross",
    )
    reduced = await manager.submit(
        "BTC-USDT", OrderSide.SELL, OrderType.MARKET, 0.2, strategy_name="ma_cross",
    )

    assert stale.status == OrderStatus.REJECTED
    assert reduced.status == OrderStatus.FILLED
    assert len(handler.submitted) == 1

    fresh = UnifiedOrderManager(
        router=OrderRouter(backtest=MockHandler(), mode="backtest"),
        repository=FakeRepository(),
        timestamp_ms=lambda: 1_700_000_010_000,
        price_observed_at_provider=lambda _symbol: None,
        max_price_age_ms=15_000,
    )
    missing = await fresh.submit(
        "BTC-USDT", OrderSide.BUY, OrderType.MARKET, 0.1, strategy_name="ma_cross",
    )
    assert missing.status == OrderStatus.REJECTED
