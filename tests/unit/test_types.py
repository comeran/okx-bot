from src.core.types import (
    AccountSnapshot,
    AssetBalance,
    Bar,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionSide,
    PositionSnapshot,
)


def test_account_snapshot_creation():
    snapshot = AccountSnapshot(
        initial_equity=1000.0,
        cash_balance=900.0,
        equity=1010.0,
        realized_pnl=10.0,
        unrealized_pnl=0.0,
        daily_pnl=10.0,
        fees_paid=1.5,
        timestamp=1700000000000,
    )

    assert snapshot.cash_balance == 900.0
    assert snapshot.equity == 1010.0
    assert snapshot.timestamp == 1700000000000


def test_account_snapshot_post_init_preserves_assets_and_derives_defaults():
    snapshot = AccountSnapshot(
        cash_balance=900.0,
        equity=1000.0,
        timestamp=1700000000000,
        assets=[AssetBalance(ccy="USDT", cash_bal=900.0, eq=1000.0, avail_bal=850.0)],
    )

    assert snapshot.initial_equity == 1000.0
    assert snapshot.available_balance == 900.0
    assert isinstance(snapshot.available_balance, float)
    assert snapshot.updated_at == 1700000000000
    assert snapshot.assets == [AssetBalance(ccy="USDT", cash_bal=900.0, eq=1000.0, avail_bal=850.0)]


def test_account_snapshot_post_init_preserves_explicit_zero_available_balance():
    snapshot = AccountSnapshot(cash_balance=900.0, available_balance=0.0)

    assert snapshot.available_balance == 0.0
    assert isinstance(snapshot.available_balance, float)


def test_position_snapshot_creation():
    snapshot = PositionSnapshot(
        symbol="BTC/USDT:USDT",
        side=PositionSide.LONG,
        amount=0.5,
        entry_price=50000.0,
        mark_price=50100.0,
        realized_pnl=5.0,
        unrealized_pnl=50.0,
        leverage=2,
        timestamp=1700000000000,
    )

    assert snapshot.symbol == "BTC/USDT:USDT"
    assert snapshot.side == PositionSide.LONG
    assert snapshot.unrealized_pnl == 50.0


def test_bar_creation():
    bar = Bar(
        timestamp=1700000000000,
        open=50000.0,
        high=51000.0,
        low=49000.0,
        close=50500.0,
        volume=100.5,
    )
    assert bar.timestamp == 1700000000000
    assert bar.open == 50000.0
    assert bar.close == 50500.0


def test_order_creation():
    order = Order(
        id="test-001",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=0.1,
        price=50000.0,
        stop_loss=49000.0,
        take_profit=52000.0,
    )
    assert order.id == "test-001"
    assert order.status == OrderStatus.PENDING
    assert order.fill_price is None


def test_position_creation():
    pos = Position(
        symbol="BTC-USDT-SWAP",
        side=PositionSide.LONG,
        amount=0.5,
        entry_price=50000.0,
        unrealized_pnl=250.0,
        leverage=10,
    )
    assert pos.symbol == "BTC-USDT-SWAP"
    assert pos.leverage == 10
