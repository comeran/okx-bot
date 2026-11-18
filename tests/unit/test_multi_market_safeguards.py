from types import SimpleNamespace

import pytest

from src.core.types import Order, OrderSide, OrderType
from src.order.manager import UnifiedOrderManager
from src.order.router import OrderRouter


def manager():
    return UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=object(), mode="live"),
        repository=None,
        timestamp_ms=lambda: 1,
        live_safeguards=True,
    )


def sell(symbol, amount=1):
    return Order(
        id="synthetic-close",
        symbol=symbol,
        side=OrderSide.SELL,
        type=OrderType.MARKET,
        amount=amount,
    )


def position():
    return SimpleNamespace(
        side="long", amount=2, contracts=None, quantity=None, contract_size=None, is_inverse=False
    )


def test_account_wide_spot_reduction_does_not_send_derivative_reduce_only():
    order = sell("BTC-USDT")
    account = SimpleNamespace(assets=[{"ccy": "BTC", "avail_bal": 2}])
    result = manager()._check_live_order_safety(order, account, [], 100)
    assert result.passed is True
    assert order.reduce_only is False
    assert "reduceOnly" not in order.params


def test_account_wide_spot_sell_cannot_exceed_current_spot_holding():
    account = SimpleNamespace(assets=[{"ccy": "BTC", "avail_bal": 2}])
    result = manager()._check_live_order_safety(sell("BTC-USDT", 3), account, [], 100)
    assert result.passed is False
    assert result.reason == "Live spot sell exceeds available position"


def test_account_wide_spot_notional_does_not_require_contract_metadata():
    assert manager()._order_value_for_risk(sell("BTC-USDT", 2), 100, []) == 200


@pytest.mark.parametrize("symbol", ["BTC-USDT-SWAP", "BTC-USDT-271231"])
def test_account_wide_derivative_reduction_keeps_reduce_only(symbol):
    order = sell(symbol)
    result = manager()._check_live_order_safety(order, object(), [position()], 100)
    assert result.passed is True
    assert order.reduce_only is True
    assert order.params["reduceOnly"] is True
