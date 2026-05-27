import pytest

from src.backtest.matcher import OrderMatcher
from src.core.types import Bar, Order, OrderSide, OrderStatus, OrderType


@pytest.fixture
def bar():
    return Bar(timestamp=1000, open=50000, high=52000, low=48000, close=51000, volume=100)


def test_market_order_fills_at_open(bar):
    matcher = OrderMatcher(slippage=0.0, fee_rate=0.001)
    order = Order(id="1", symbol="BTC-USDT", side=OrderSide.BUY, type=OrderType.MARKET, amount=0.1)
    result = matcher.match(order, bar)
    assert result.status == OrderStatus.FILLED
    assert result.fill_price == 50000


def test_limit_order_fills_within_range(bar):
    matcher = OrderMatcher(slippage=0.0, fee_rate=0.0)
    order = Order(
        id="2", symbol="BTC-USDT", side=OrderSide.BUY, type=OrderType.LIMIT, amount=0.1, price=50000
    )
    result = matcher.match(order, bar)
    assert result.status == OrderStatus.FILLED
    assert result.fill_price == 50000


def test_limit_order_not_fills_outside_range(bar):
    matcher = OrderMatcher(slippage=0.0, fee_rate=0.0)
    order = Order(
        id="3", symbol="BTC-USDT", side=OrderSide.BUY, type=OrderType.LIMIT, amount=0.1, price=47000
    )
    result = matcher.match(order, bar)
    assert result.status == OrderStatus.PENDING


def test_slippage_applied_buy(bar):
    matcher = OrderMatcher(slippage=0.001, fee_rate=0.0)
    order = Order(id="4", symbol="BTC-USDT", side=OrderSide.BUY, type=OrderType.MARKET, amount=0.1)
    result = matcher.match(order, bar)
    assert result.fill_price == pytest.approx(50050.0)


def test_slippage_applied_sell(bar):
    matcher = OrderMatcher(slippage=0.001, fee_rate=0.0)
    order = Order(id="5", symbol="BTC-USDT", side=OrderSide.SELL, type=OrderType.MARKET, amount=0.1)
    result = matcher.match(order, bar)
    assert result.fill_price == pytest.approx(49950.0)


def test_fee_deducted(bar):
    matcher = OrderMatcher(slippage=0.0, fee_rate=0.001)
    order = Order(id="6", symbol="BTC-USDT", side=OrderSide.BUY, type=OrderType.MARKET, amount=1.0)
    result = matcher.match(order, bar)
    assert result.fee == pytest.approx(50.0)


def test_stop_order_triggers_when_price_crosses(bar):
    matcher = OrderMatcher(slippage=0.0, fee_rate=0.0)
    order = Order(
        id="7", symbol="BTC-USDT", side=OrderSide.SELL, type=OrderType.STOP, amount=0.1, price=49000
    )
    result = matcher.match(order, bar)
    assert result.status == OrderStatus.FILLED
    assert result.fill_price == 49000


def test_stop_order_not_triggered(bar):
    matcher = OrderMatcher(slippage=0.0, fee_rate=0.0)
    order = Order(
        id="8", symbol="BTC-USDT", side=OrderSide.SELL, type=OrderType.STOP, amount=0.1, price=47000
    )
    result = matcher.match(order, bar)
    assert result.status == OrderStatus.PENDING
