import pytest

from src.analytics.strategy_performance import build_strategy_performance
from src.data.models import AccountRecord, OrderRecord, PositionRecord, TradeRecord


def test_build_strategy_performance_aggregates_union_exposure_and_account_fields():
    accounts = [
        AccountRecord(
            strategy="beta",
            initial_equity=1000.0,
            cash_balance=700.0,
            available_balance=650.0,
            equity=1100.0,
            realized_pnl=90.0,
            unrealized_pnl=10.0,
            daily_pnl=5.0,
            fees_paid=4.0,
            updated_at=200,
        ),
        AccountRecord(
            strategy="alpha",
            initial_equity=0.0,
            cash_balance=0.0,
            available_balance=0.0,
            equity=0.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            daily_pnl=0.0,
            fees_paid=0.0,
            updated_at=100,
        ),
        AccountRecord(
            strategy="__exchange__",
            initial_equity=1.0,
            cash_balance=1.0,
            available_balance=1.0,
            equity=1.0,
            realized_pnl=1.0,
            unrealized_pnl=1.0,
            daily_pnl=1.0,
            fees_paid=1.0,
            updated_at=300,
        ),
    ]
    positions = [
        PositionRecord(
            strategy="alpha",
            symbol="BTC-USDT-SWAP",
            side="long",
            amount=2.0,
            entry_price=100.0,
            leverage=1,
            timestamp=10,
            mark_price=120.0,
        ),
        PositionRecord(
            strategy="beta",
            symbol="ETH-USDT-SWAP",
            side="short",
            amount=0.0,
            entry_price=200.0,
            leverage=1,
            timestamp=20,
            mark_price=None,
        ),
        PositionRecord(
            strategy="",
            symbol="XRP-USDT-SWAP",
            side="long",
            amount=1.0,
            entry_price=1.0,
            leverage=1,
            timestamp=30,
            mark_price=1.5,
        ),
    ]
    orders = [
        OrderRecord(
            order_id="o-1",
            exchange_order_id="",
            client_order_id="",
            strategy="alpha",
            symbol="BTC-USDT-SWAP",
            side="buy",
            type="market",
            amount=1.0,
            price=100.0,
            status="filled",
            fill_price=101.0,
            timestamp=150,
            updated_at=160,
        ),
        OrderRecord(
            order_id="o-2",
            exchange_order_id="",
            client_order_id="",
            strategy="beta",
            symbol="ETH-USDT-SWAP",
            side="sell",
            type="market",
            amount=1.0,
            price=200.0,
            status="open",
            fill_price=0.0,
            timestamp=250,
            updated_at=260,
        ),
        OrderRecord(
            order_id="o-3",
            exchange_order_id="",
            client_order_id="",
            strategy="__exchange__",
            symbol="BTC-USDT-SWAP",
            side="buy",
            type="market",
            amount=1.0,
            price=100.0,
            status="open",
            fill_price=0.0,
            timestamp=50,
            updated_at=50,
        ),
    ]
    trades = [
        TradeRecord(
            exchange_trade_id="t-1",
            order_id="o-1",
            strategy="alpha",
            symbol="BTC-USDT-SWAP",
            side="buy",
            amount=2.0,
            price=100.0,
            fee=1.0,
            timestamp=101,
        ),
        TradeRecord(
            exchange_trade_id="t-2",
            order_id="o-2",
            strategy="beta",
            symbol="ETH-USDT-SWAP",
            side="sell",
            amount=1.0,
            price=200.0,
            fee=1.0,
            timestamp=201,
        ),
        TradeRecord(
            exchange_trade_id="t-3",
            order_id="o-3",
            strategy="",
            symbol="XRP-USDT-SWAP",
            side="buy",
            amount=1.0,
            price=1.0,
            fee=0.01,
            timestamp=301,
        ),
    ]

    performances = build_strategy_performance(accounts, positions, orders, trades)

    assert [performance.strategy for performance in performances] == ["alpha", "beta"]

    alpha = performances[0]
    assert alpha.initial_equity == 0.0
    assert alpha.equity == 0.0
    assert alpha.return_pct is None
    assert alpha.realized_pnl == 0.0
    assert alpha.unrealized_pnl == 0.0
    assert alpha.fees_paid == 0.0
    assert alpha.position_notional == 240.0
    assert alpha.open_positions == 1
    assert alpha.order_count == 1
    assert alpha.filled_order_count == 1
    assert alpha.trade_count == 1
    assert alpha.closed_trade_count == 0
    assert alpha.winning_trade_count == 0
    assert alpha.losing_trade_count == 0
    assert alpha.win_rate is None
    assert alpha.last_order_at == 150

    beta = performances[1]
    assert beta.initial_equity == 1000.0
    assert beta.equity == 1100.0
    assert beta.return_pct == pytest.approx(0.1)
    assert beta.realized_pnl == 90.0
    assert beta.unrealized_pnl == 10.0
    assert beta.fees_paid == 4.0
    assert beta.position_notional == 0.0
    assert beta.open_positions == 0
    assert beta.order_count == 1
    assert beta.filled_order_count == 0
    assert beta.trade_count == 1
    assert beta.closed_trade_count == 0
    assert beta.winning_trade_count == 0
    assert beta.losing_trade_count == 0
    assert beta.win_rate is None
    assert beta.last_order_at == 250


def test_build_strategy_performance_uses_fifo_matching_for_closed_trades():
    trades = [
        TradeRecord(
            exchange_trade_id="t-1",
            order_id="o-1",
            strategy="gamma",
            symbol="BTC-USDT-SWAP",
            side="buy",
            amount=2.0,
            price=100.0,
            fee=2.0,
            timestamp=10,
        ),
        TradeRecord(
            exchange_trade_id="t-2",
            order_id="o-2",
            strategy="gamma",
            symbol="BTC-USDT-SWAP",
            side="buy",
            amount=1.0,
            price=110.0,
            fee=1.0,
            timestamp=20,
        ),
        TradeRecord(
            exchange_trade_id="t-3",
            order_id="o-3",
            strategy="gamma",
            symbol="BTC-USDT-SWAP",
            side="sell",
            amount=2.5,
            price=130.0,
            fee=2.5,
            timestamp=30,
        ),
        TradeRecord(
            exchange_trade_id="t-4",
            order_id="o-4",
            strategy="gamma",
            symbol="BTC-USDT-SWAP",
            side="sell",
            amount=0.5,
            price=90.0,
            fee=0.5,
            timestamp=40,
        ),
        TradeRecord(
            exchange_trade_id="t-5",
            order_id="o-5",
            strategy="gamma",
            symbol="BTC-USDT-SWAP",
            side="buy",
            amount=1.0,
            price=80.0,
            fee=1.0,
            timestamp=50,
        ),
        TradeRecord(
            exchange_trade_id="t-6",
            order_id="o-6",
            strategy="gamma",
            symbol="BTC-USDT-SWAP",
            side="sell",
            amount=2.0,
            price=70.0,
            fee=2.0,
            timestamp=60,
        ),
    ]

    performance = build_strategy_performance([], [], [], trades)[0]

    assert performance.strategy == "gamma"
    assert performance.trade_count == 6
    assert performance.closed_trade_count == 3
    assert performance.winning_trade_count == 1
    assert performance.losing_trade_count == 2
    assert performance.win_rate == pytest.approx(1 / 3)


def test_build_strategy_performance_returns_none_win_rate_for_open_only_positions():
    trades = [
        TradeRecord(
            exchange_trade_id="t-1",
            order_id="o-1",
            strategy="delta",
            symbol="ETH-USDT-SWAP",
            side="buy",
            amount=1.0,
            price=100.0,
            fee=1.0,
            timestamp=10,
        ),
    ]

    performance = build_strategy_performance([], [], [], trades)[0]

    assert performance.strategy == "delta"
    assert performance.closed_trade_count == 0
    assert performance.winning_trade_count == 0
    assert performance.losing_trade_count == 0
    assert performance.win_rate is None
