from types import SimpleNamespace

import pytest

from src.analytics.strategy_performance import (
    _build_closed_trade_results,
    build_strategy_performance,
)
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
            filled_amount=1.0,
            remaining_amount=0.0,
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


def test_build_strategy_performance_does_not_fabricate_account_metrics_without_account():
    performance = build_strategy_performance(
        [],
        [PositionRecord(
            strategy="orphan",
            symbol="BTC-USDT-SWAP",
            side="long",
            amount=1.0,
            entry_price=100.0,
            leverage=1,
            timestamp=1,
        )],
        [],
        [],
    )[0]

    assert performance.initial_equity is None
    assert performance.equity is None
    assert performance.realized_pnl is None
    assert performance.unrealized_pnl is None
    assert performance.fees_paid is None
    assert performance.position_notional == 100.0
    assert performance.open_positions == 1


def test_build_strategy_performance_rejects_mixed_runtime_modes():
    with pytest.raises(ValueError, match="single runtime_mode"):
        build_strategy_performance([], [], [], [], runtime_modes={"paper", "live"})


def test_build_strategy_performance_filters_by_runtime_without_mixing_same_named_strategy():
    records = [
        AccountRecord(
            strategy="alpha", source="paper", runtime_mode="paper",
            initial_equity=100.0, cash_balance=100.0, equity=110.0,
            realized_pnl=10.0, unrealized_pnl=0.0, daily_pnl=0.0,
            fees_paid=1.0, updated_at=10,
        ),
        AccountRecord(
            strategy="alpha", source="live_exchange", runtime_mode="live",
            initial_equity=200.0, cash_balance=200.0, equity=180.0,
            realized_pnl=-20.0, unrealized_pnl=0.0, daily_pnl=0.0,
            fees_paid=2.0, updated_at=20,
        ),
        AccountRecord(
            strategy="alpha", source="backtest", runtime_mode="backtest",
            initial_equity=300.0, cash_balance=300.0, equity=390.0,
            realized_pnl=90.0, unrealized_pnl=0.0, daily_pnl=0.0,
            fees_paid=3.0, updated_at=30,
        ),
    ]
    orders = [
        OrderRecord(
            order_id="paper", strategy="alpha", source="paper", runtime_mode="paper",
            symbol="BTC-USDT", side="buy", type="market", amount=1.0, price=100.0,
            status="filled", fill_price=100.0, timestamp=10,
        ),
        OrderRecord(
            order_id="live", strategy="alpha", source="live_exchange", runtime_mode="live",
            symbol="BTC-USDT", side="buy", type="market", amount=2.0, price=100.0,
            status="filled", fill_price=100.0, timestamp=20,
        ),
        OrderRecord(
            order_id="legacy", strategy="alpha", source="unknown", runtime_mode="unknown",
            symbol="BTC-USDT", side="buy", type="market", amount=3.0, price=100.0,
            status="filled", fill_price=100.0, timestamp=30,
        ),
    ]

    paper = build_strategy_performance(records, [], orders, [], runtime_modes={"paper"})[0]
    live = build_strategy_performance(records, [], orders, [], runtime_modes={"live"})[0]

    assert (paper.equity, paper.order_count, paper.last_order_at) == (110.0, 1, 10)
    assert (live.equity, live.order_count, live.last_order_at) == (180.0, 1, 20)


def test_build_strategy_performance_requires_matching_source_and_runtime_provenance():
    accounts = [
        AccountRecord(
            strategy="alpha", source="paper", runtime_mode="paper",
            initial_equity=100.0, cash_balance=100.0, equity=110.0,
            realized_pnl=10.0, unrealized_pnl=0.0, daily_pnl=0.0,
            fees_paid=1.0, updated_at=10,
        ),
        AccountRecord(
            strategy="alpha", source="live_exchange", runtime_mode="paper",
            initial_equity=200.0, cash_balance=200.0, equity=220.0,
            realized_pnl=20.0, unrealized_pnl=0.0, daily_pnl=0.0,
            fees_paid=2.0, updated_at=20,
        ),
    ]

    paper = build_strategy_performance(accounts, [], [], [], runtime_modes={"paper"})

    assert len(paper) == 1
    assert paper[0].equity == 110.0


def test_strategy_performance_fifo_closes_legacy_alias_against_canonical_contract():
    trades = [
        TradeRecord(
            exchange_trade_id="open", order_id="open", strategy="alpha",
            symbol="BTC/USDT:USDT", side="buy", amount=1.0, price=100.0,
            fee=0.0, timestamp=1,
        ),
        TradeRecord(
            exchange_trade_id="close", order_id="close", strategy="alpha",
            symbol="BTC-USDT-SWAP", side="sell", amount=1.0, price=120.0,
            fee=0.0, timestamp=2,
        ),
    ]

    performance = build_strategy_performance([], [], [], trades)[0]

    assert performance.closed_trade_count == 1
    assert performance.winning_trade_count == 1
    assert performance.win_rate == 1.0


def test_strategy_performance_ignores_inactive_and_mismatched_scope_accounts():
    accounts = [
        AccountRecord(
            strategy="alpha", source="live_exchange", runtime_mode="live",
            initial_equity=100.0, cash_balance=100.0, equity=110.0,
            realized_pnl=10.0, unrealized_pnl=0.0, daily_pnl=0.0, fees_paid=1.0,
            updated_at=30, active=False, stale_at=40,
        ),
        AccountRecord(
            strategy="alpha", source="live_exchange", runtime_mode="paper",
            initial_equity=100.0, cash_balance=100.0, equity=999.0,
            realized_pnl=99.0, unrealized_pnl=0.0, daily_pnl=0.0, fees_paid=9.0,
            updated_at=20,
        ),
        AccountRecord(
            strategy="alpha", source="live_exchange", runtime_mode="live",
            initial_equity=100.0, cash_balance=100.0, equity=120.0,
            realized_pnl=20.0, unrealized_pnl=0.0, daily_pnl=0.0, fees_paid=2.0,
            updated_at=10,
        ),
        AccountRecord(
            strategy="alpha", source="live_exchange", runtime_mode=None,
            initial_equity=100.0, cash_balance=100.0, equity=777.0,
            realized_pnl=77.0, unrealized_pnl=0.0, daily_pnl=0.0, fees_paid=7.0,
            updated_at=100,
        ),
    ]

    performance = build_strategy_performance(
        accounts, [], [], [], runtime_modes={"live"}
    )[0]

    assert performance.equity == 120.0
    assert performance.realized_pnl == 20.0


def test_strategy_performance_excludes_inactive_positions_orders_and_trades_from_detail():
    accounts = [AccountRecord(
        strategy="alpha", source="paper", runtime_mode="paper",
        initial_equity=100.0, cash_balance=100.0, equity=100.0,
        realized_pnl=0.0, unrealized_pnl=0.0, daily_pnl=0.0, fees_paid=0.0,
        updated_at=1,
    )]
    positions = [
        PositionRecord(
            strategy="alpha", symbol="BTC-USDT", side="long", amount=2.0,
            entry_price=100.0, leverage=1, timestamp=1,
            source="paper", runtime_mode="paper", active=False,
        )
    ]
    orders = [
        OrderRecord(
            order_id="inactive", strategy="alpha", symbol="BTC-USDT", side="buy",
            type="market", amount=2.0, price=100.0, status="filled", fill_price=100.0,
            timestamp=1, source="paper", runtime_mode="paper", active=False,
        )
    ]
    trades = [
        TradeRecord(
            exchange_trade_id="inactive", order_id="inactive", strategy="alpha",
            symbol="BTC-USDT", side="buy", amount=2.0, price=100.0, fee=0.0,
            timestamp=1, source="paper", runtime_mode="paper", active=False,
        )
    ]

    performance = build_strategy_performance(
        accounts, positions, orders, trades, runtime_modes={"paper"}
    )[0]

    assert performance.position_notional == 0.0
    assert performance.open_positions == 0
    assert performance.order_count == 0
    assert performance.trade_count == 0
    assert performance.positions == []
    assert performance.recent_orders == []


def test_strategy_performance_fifo_keeps_different_contracts_separate():
    trades = [
        TradeRecord(
            exchange_trade_id="open", order_id="open", strategy="alpha",
            symbol="BTC/USDT:USDT-260925", side="buy", amount=1.0, price=100.0,
            fee=0.0, timestamp=1,
        ),
        TradeRecord(
            exchange_trade_id="close", order_id="close", strategy="alpha",
            symbol="BTC-USDT-261225", side="sell", amount=1.0, price=120.0,
            fee=0.0, timestamp=2,
        ),
    ]

    performance = build_strategy_performance([], [], [], trades)[0]

    assert performance.closed_trade_count == 0
    assert performance.win_rate is None


def test_strategy_performance_uses_explicit_inverse_notional_without_mark_price_multiplier():
    performance = build_strategy_performance(
        [],
        [PositionRecord(
            strategy="inverse",
            symbol="BTC-USD-SWAP",
            side="long",
            amount=200.0,
            entry_price=50000.0,
            mark_price=50000.0,
            leverage=1,
            timestamp=1,
            quantity=2.0,
            contracts=2.0,
            quantity_unit="contracts",
            notional_value=200.0,
            is_inverse=True,
            contract_size=100.0,
        )],
        [],
        [],
    )[0]

    assert performance.position_notional == pytest.approx(200.0)
    assert performance.positions[0]["notional_value"] == pytest.approx(200.0)


def test_strategy_performance_recovers_legacy_inverse_notional_without_mark_price_multiplier():
    performance = build_strategy_performance(
        [],
        [PositionRecord(
            strategy="inverse",
            symbol="BTC-USD-SWAP",
            side="long",
            amount=200.0,
            entry_price=50000.0,
            mark_price=60000.0,
            leverage=1,
            timestamp=1,
            quantity=2.0,
            contracts=2.0,
            quantity_unit="contracts",
            notional_value=None,
            is_inverse=True,
            contract_size=100.0,
        )],
        [],
        [],
    )[0]

    assert performance.position_notional == pytest.approx(200.0)


def test_strategy_performance_fifo_uses_inverse_contract_quote_pnl_and_fees():
    trades = [
        SimpleNamespace(
            exchange_trade_id="open", order_id="open", strategy="inverse",
            symbol="BTC-USD-SWAP", side="buy", amount=2.0, price=50000.0,
            fee=2.0, timestamp=1, is_inverse=True, contracts=2.0,
            contract_size=100.0, quantity_unit="contracts",
        ),
        SimpleNamespace(
            exchange_trade_id="close", order_id="close", strategy="inverse",
            symbol="BTC-USD-SWAP", side="sell", amount=1.0, price=40000.0,
            fee=1.0, timestamp=2, is_inverse=True, contracts=1.0,
            contract_size=100.0, quantity_unit="contracts",
        ),
    ]

    closed_results = _build_closed_trade_results(trades)
    assert [result.pnl for result in closed_results] == [pytest.approx(-22.0)]

    performance = build_strategy_performance([], [], [], trades)[0]

    assert performance.closed_trade_count == 1
    assert performance.realized_pnl is None
    assert performance.winning_trade_count == 0
    assert performance.losing_trade_count == 1
    assert performance.win_rate == 0.0


@pytest.mark.parametrize(
    ("opening_side", "closing_side", "close_prices", "expected_pnl"),
    [
        ("buy", "sell", (60000.0, 40000.0), (pytest.approx(72.0), pytest.approx(-132.0))),
        ("sell", "buy", (60000.0, 40000.0), (pytest.approx(-88.0), pytest.approx(108.0))),
    ],
)
def test_strategy_performance_inverse_partial_close_scales_original_lot_before_reducing(
    opening_side, closing_side, close_prices, expected_pnl
):
    trades = [
        SimpleNamespace(
            exchange_trade_id="open", order_id="open", strategy="inverse-partial",
            symbol="BTC-USD-SWAP", side=opening_side, amount=10.0, price=50000.0,
            fee=10.0, timestamp=1, is_inverse=True, contracts=10.0,
            contract_size=100.0, quantity_unit="contracts",
        ),
        *[
            SimpleNamespace(
                exchange_trade_id=f"close-{index}", order_id=f"close-{index}",
                strategy="inverse-partial", symbol="BTC-USD-SWAP", side=closing_side,
                amount=amount, price=price, fee=fee, timestamp=index + 2,
                is_inverse=True, contracts=amount, contract_size=100.0,
                quantity_unit="contracts",
            )
            for index, (amount, price, fee) in enumerate(
                zip((4.0, 6.0), close_prices, (4.0, 6.0), strict=True)
            )
        ],
    ]

    closed_results = _build_closed_trade_results(trades)

    assert [result.pnl for result in closed_results] == list(expected_pnl)


def test_strategy_performance_ignores_legacy_inverse_trade_without_contract_metadata():
    trades = [
        SimpleNamespace(
            exchange_trade_id="open", order_id="open", strategy="inverse",
            symbol="BTC-USD-SWAP", side="buy", amount=1.0, price=50000.0,
            fee=0.0, timestamp=1, is_inverse=True, contracts=1.0,
            contract_size=None, quantity_unit="contracts",
        ),
        SimpleNamespace(
            exchange_trade_id="close", order_id="close", strategy="inverse",
            symbol="BTC-USD-SWAP", side="sell", amount=1.0, price=40000.0,
            fee=0.0, timestamp=2, is_inverse=True, contracts=1.0,
            contract_size=None, quantity_unit="contracts",
        ),
    ]

    performance = build_strategy_performance([], [], [], trades)[0]

    assert performance.closed_trade_count == 0
    assert performance.win_rate is None


def test_strategy_performance_accepts_backtest_and_demo_paper_aliases():
    account = AccountRecord(
        strategy="alpha", source="backtest", runtime_mode="backtest",
        initial_equity=100.0, cash_balance=100.0, equity=110.0,
        realized_pnl=10.0, unrealized_pnl=0.0, daily_pnl=0.0, fees_paid=0.0,
        updated_at=1,
    )
    backtest = build_strategy_performance(
        [account], [], [], [], runtime_modes={"backtest"}
    )
    demo = build_strategy_performance(
        [AccountRecord(
            strategy="alpha", source="paper", runtime_mode="paper",
            initial_equity=100.0, cash_balance=100.0, equity=105.0,
            realized_pnl=5.0, unrealized_pnl=0.0, daily_pnl=0.0, fees_paid=0.0,
            updated_at=1,
        )],
        [], [], [], runtime_modes={"demo"}
    )

    assert backtest[0].equity == 110.0
    assert demo[0].equity == 105.0
