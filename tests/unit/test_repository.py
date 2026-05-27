import pytest
from sqlmodel import SQLModel, create_engine

from src.data.models import KlineCache, OrderRecord, PositionRecord, TradeRecord
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
