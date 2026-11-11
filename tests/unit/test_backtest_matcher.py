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


def test_stop_order_gapped_fill_uses_worse_open_price():
    matcher = OrderMatcher(slippage=0.0, fee_rate=0.0)
    gapped = Bar(timestamp=1000, open=48000, high=48200, low=47500, close=47600, volume=100)
    order = Order(
        id="9", symbol="BTC-USDT", side=OrderSide.SELL, type=OrderType.STOP, amount=0.1, price=49000
    )

    result = matcher.match(order, gapped)

    assert result.status == OrderStatus.FILLED
    assert result.fill_price == 48000


@pytest.mark.parametrize(
    ("side", "opening", "high", "low", "expected_price"),
    [
        (OrderSide.BUY, 90, 95, 85, 90),
        (OrderSide.SELL, 110, 115, 105, 110),
        (OrderSide.BUY, 95, 110, 90, 95),
        (OrderSide.SELL, 105, 110, 90, 105),
        (OrderSide.BUY, 110, 115, 95, 100),
        (OrderSide.SELL, 90, 105, 85, 100),
        (OrderSide.BUY, 110, 115, 105, None),
        (OrderSide.SELL, 90, 95, 85, None),
        (OrderSide.BUY, 100, 105, 95, 100),
        (OrderSide.SELL, 100, 105, 95, 100),
    ],
)
def test_limit_order_uses_better_open_or_intrabar_limit(
    side, opening, high, low, expected_price,
):
    matcher = OrderMatcher(slippage=0.01, fee_rate=0.001)
    order = Order(
        id="limit", symbol="BTC-USDT", side=side,
        type=OrderType.LIMIT, amount=2, price=100,
    )
    candle = Bar(timestamp=1, open=opening, high=high, low=low, close=opening, volume=10)

    result = matcher.match(order, candle)

    if expected_price is None:
        assert result.status == OrderStatus.PENDING
        assert result.fill_price is None
        assert result.fee == 0
    else:
        assert result.status == OrderStatus.FILLED
        assert result.fill_price == expected_price
        assert result.fee == pytest.approx(expected_price * 0.002)
