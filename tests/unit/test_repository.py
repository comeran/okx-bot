import pytest
from sqlmodel import SQLModel, create_engine

from src.data.models import (
    AccountRecord,
    BacktestResultRecord,
    CashLedgerRecord,
    KlineCache,
    OrderRecord,
    PositionRecord,
    TradeRecord,
)
from src.data.repository import Repository


@pytest.fixture
def repo():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    return Repository(engine)


def test_save_and_get_trade(repo: Repository):
    trade = TradeRecord(
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        amount=0.1,
        price=50000.0,
        fee=2.5,
        timestamp=1700000000000,
    )
    repo.save_trade(trade)
    trades = repo.get_trades(strategy="ma_cross")
    assert len(trades) == 1
    assert trades[0].symbol == "BTC-USDT"


def test_save_and_get_order(repo: Repository):
    order = OrderRecord(
        order_id="ord-001",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=0.1,
        price=50000.0,
        status="filled",
        fill_price=50000.0,
        timestamp=1700000000000,
    )
    repo.save_order(order)
    orders = repo.get_orders(order_id="ord-001")
    assert len(orders) == 1
    assert orders[0].status == "filled"


def test_save_and_get_position(repo: Repository):
    pos = PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=0.5,
        entry_price=50000.0,
        leverage=10,
        timestamp=1700000000000,
    )
    repo.save_position(pos)
    positions = repo.get_positions(strategy="ma_cross")
    assert len(positions) == 1
    assert positions[0].leverage == 10


def test_kline_cache(repo: Repository):
    kline = KlineCache(
        symbol="BTC-USDT",
        timeframe="1h",
        timestamp=1700000000000,
        open=50000.0,
        high=51000.0,
        low=49000.0,
        close=50500.0,
        volume=100.0,
    )
    repo.save_kline(kline)
    klines = repo.get_klines("BTC-USDT", "1h", 1700000000000, 1700003600000)
    assert len(klines) == 1


def test_save_and_list_backtest_results_newest_first(repo: Repository):
    repo.save_backtest_result(
        BacktestResultRecord(
            id="bt-old",
            strategy="ma_cross",
            symbol="BTC-USDT",
            timeframe="1h",
            start_time=1700000000000,
            end_time=1700003600000,
            initial_capital=100000.0,
            total_return=0.01,
            sharpe_ratio=1.2,
            max_drawdown=0.03,
            win_rate=0.5,
            total_trades=2,
            created_at=1700003600000,
        )
    )
    repo.save_backtest_result(
        BacktestResultRecord(
            id="bt-new",
            strategy="ma_cross",
            symbol="ETH-USDT",
            timeframe="1h",
            start_time=1700007200000,
            end_time=1700010800000,
            initial_capital=200000.0,
            total_return=-0.02,
            sharpe_ratio=-0.5,
            max_drawdown=0.04,
            win_rate=0.25,
            total_trades=4,
            created_at=1700010800000,
        )
    )

    results = repo.get_backtest_results()

    assert [result.id for result in results] == ["bt-new", "bt-old"]
    assert results[0].symbol == "ETH-USDT"
    assert results[0].total_trades == 4


def test_list_backtest_results_honors_limit(repo: Repository):
    for index in range(3):
        repo.save_backtest_result(
            BacktestResultRecord(
                id=f"bt-{index}",
                strategy="ma_cross",
                symbol="BTC-USDT",
                timeframe="1h",
                start_time=1700000000000 + index,
                end_time=1700003600000 + index,
                initial_capital=100000.0,
                total_return=0.01,
                sharpe_ratio=1.0,
                max_drawdown=0.02,
                win_rate=0.5,
                total_trades=1,
                created_at=1700000000000 + index,
            )
        )

    assert [result.id for result in repo.get_backtest_results(limit=2)] == ["bt-2", "bt-1"]


def test_get_trades_filters(repo: Repository):
    for i in range(5):
        repo.save_trade(
            TradeRecord(
                strategy="strat_a" if i < 3 else "strat_b",
                symbol="BTC-USDT",
                side="buy",
                amount=0.1,
                price=50000.0 + i * 100,
                fee=2.5,
                timestamp=1700000000000 + i * 1000,
            )
        )
    assert len(repo.get_trades(strategy="strat_a")) == 3
    assert len(repo.get_trades(strategy="strat_b")) == 2
    assert len(repo.get_trades()) == 5


def test_upsert_and_get_account(repo: Repository):
    repo.upsert_account(
        AccountRecord(
            strategy="ma_cross",
            initial_equity=100000.0,
            cash_balance=95000.0,
            equity=100500.0,
            realized_pnl=500.0,
            unrealized_pnl=0.0,
            daily_pnl=500.0,
            fees_paid=2.0,
            updated_at=1700000000000,
        )
    )
    repo.upsert_account(
        AccountRecord(
            strategy="ma_cross",
            initial_equity=100000.0,
            cash_balance=96000.0,
            equity=101000.0,
            realized_pnl=1000.0,
            unrealized_pnl=0.0,
            daily_pnl=1000.0,
            fees_paid=3.0,
            updated_at=1700000001000,
        )
    )

    account = repo.get_account("ma_cross")

    assert account is not None
    assert account.cash_balance == 96000.0
    assert account.realized_pnl == 1000.0


def test_get_account_aggregates_all_strategies(repo: Repository):
    repo.upsert_account(
        AccountRecord(
            strategy="a",
            initial_equity=100.0,
            cash_balance=90.0,
            equity=110.0,
            realized_pnl=10.0,
            unrealized_pnl=1.0,
            daily_pnl=5.0,
            fees_paid=0.5,
            updated_at=1,
        )
    )
    repo.upsert_account(
        AccountRecord(
            strategy="b",
            initial_equity=200.0,
            cash_balance=180.0,
            equity=210.0,
            realized_pnl=20.0,
            unrealized_pnl=2.0,
            daily_pnl=6.0,
            fees_paid=0.7,
            updated_at=2,
        )
    )

    account = repo.get_account()

    assert account is not None
    assert account.initial_equity == 300.0
    assert account.cash_balance == 270.0
    assert account.updated_at == 2


def test_cash_ledger_filters_by_strategy(repo: Repository):
    repo.save_cash_ledger(
        CashLedgerRecord(
            strategy="a",
            symbol="BTC-USDT",
            order_id="1",
            event_type="fill",
            amount=-100.0,
            balance_after=99900.0,
            timestamp=2,
        )
    )
    repo.save_cash_ledger(
        CashLedgerRecord(
            strategy="b",
            symbol="ETH-USDT",
            order_id="2",
            event_type="fill",
            amount=-50.0,
            balance_after=99950.0,
            timestamp=1,
        )
    )

    assert [entry.order_id for entry in repo.get_cash_ledger()] == ["2", "1"]
    assert [entry.order_id for entry in repo.get_cash_ledger("a")] == ["1"]


def test_upsert_position_and_open_positions_filter_flat_rows(repo: Repository):
    repo.upsert_position(
        PositionRecord(
            strategy="ma_cross",
            symbol="BTC-USDT",
            side="long",
            amount=0.1,
            entry_price=50000.0,
            leverage=1,
            timestamp=1700000000000,
        )
    )
    repo.upsert_position(
        PositionRecord(
            strategy="ma_cross",
            symbol="ETH-USDT",
            side="long",
            amount=0.0,
            entry_price=0.0,
            leverage=1,
            timestamp=1700000001000,
        )
    )
    repo.upsert_position(
        PositionRecord(
            strategy="ma_cross",
            symbol="BTC-USDT",
            side="long",
            amount=0.2,
            entry_price=51000.0,
            leverage=1,
            timestamp=1700000002000,
            mark_price=52000.0,
            realized_pnl=10.0,
            unrealized_pnl=20.0,
        )
    )

    position = repo.get_position("ma_cross", "BTC-USDT")
    open_positions = repo.get_open_positions("ma_cross")

    assert position is not None
    assert position.amount == 0.2
    assert position.mark_price == 52000.0
    assert [(pos.symbol, pos.amount) for pos in open_positions] == [("BTC-USDT", 0.2)]
