import pytest

from src.core.types import Order, OrderSide, OrderStatus, OrderType
from src.order.accounting import PaperAccountingService


class FakeRepository:
    def __init__(self):
        self.accounts = {}
        self.positions = {}
        self.trades = []
        self.ledger = []
        self.deleted_positions = []

    def get_account(self, strategy):
        return self.accounts.get(strategy)

    def upsert_account(self, account):
        self.accounts[account.strategy] = account
        return account

    def get_position(self, strategy, symbol):
        return self.positions.get((strategy, symbol))

    def upsert_position(self, position):
        self.positions[(position.strategy, position.symbol)] = position
        return position

    def delete_position(self, strategy, symbol):
        self.deleted_positions.append((strategy, symbol))
        self.positions.pop((strategy, symbol), None)

    def get_open_positions(self, strategy=None):
        return [
            position
            for position in self.positions.values()
            if position.amount != 0 and (strategy is None or position.strategy == strategy)
        ]

    def save_trade(self, trade):
        self.trades.append(trade)
        return trade

    def save_cash_ledger(self, entry):
        self.ledger.append(entry)
        return entry


def filled_order(side, amount, price, order_id="order-1"):
    return Order(
        id=order_id,
        symbol="BTC-USDT",
        side=side,
        type=OrderType.MARKET,
        amount=amount,
        price=price,
        status=OrderStatus.FILLED,
        fill_price=price,
        fill_time=1700000000000,
    )


def service(repo=None, fee_rate=0.0):
    return PaperAccountingService(
        repository=repo or FakeRepository(),
        initial_equity=100000.0,
        fee_rate=fee_rate,
    )


def process(repo, order):
    service(repo).process_filled_order(order, "ma_cross", 1700000000000)


def test_buy_opens_long_and_updates_cash_account_trade_and_ledger():
    repo = FakeRepository()

    process(repo, filled_order(OrderSide.BUY, 0.1, 50000.0))

    position = repo.get_position("ma_cross", "BTC-USDT")
    account = repo.get_account("ma_cross")
    assert position.side == "long"
    assert position.amount == 0.1
    assert position.entry_price == 50000.0
    assert account.cash_balance == 95000.0
    assert account.equity == 100000.0
    assert len(repo.trades) == 1
    assert repo.ledger[0].amount == -5000.0


def test_buy_adds_to_long_with_weighted_average_entry():
    repo = FakeRepository()

    process(repo, filled_order(OrderSide.BUY, 1.0, 100.0, "order-1"))
    process(repo, filled_order(OrderSide.BUY, 1.0, 200.0, "order-2"))

    position = repo.get_position("ma_cross", "BTC-USDT")
    assert position.side == "long"
    assert position.amount == 2.0
    assert position.entry_price == 150.0


def test_sell_partially_closes_long_and_realizes_price_difference_pnl():
    repo = FakeRepository()

    process(repo, filled_order(OrderSide.BUY, 1.0, 100.0, "order-1"))
    process(repo, filled_order(OrderSide.SELL, 0.4, 150.0, "order-2"))

    position = repo.get_position("ma_cross", "BTC-USDT")
    account = repo.get_account("ma_cross")
    assert position.side == "long"
    assert position.amount == pytest.approx(0.6)
    assert position.entry_price == 100.0
    assert account.realized_pnl == pytest.approx(20.0)
    assert account.daily_pnl == pytest.approx(20.0)


def test_sell_fully_closes_long_and_removes_open_position():
    repo = FakeRepository()

    process(repo, filled_order(OrderSide.BUY, 1.0, 100.0, "order-1"))
    process(repo, filled_order(OrderSide.SELL, 1.0, 120.0, "order-2"))

    account = repo.get_account("ma_cross")
    assert repo.get_position("ma_cross", "BTC-USDT") is None
    assert repo.deleted_positions == [("ma_cross", "BTC-USDT")]
    assert account.realized_pnl == pytest.approx(20.0)


def test_sell_flips_long_to_short():
    repo = FakeRepository()

    process(repo, filled_order(OrderSide.BUY, 1.0, 100.0, "order-1"))
    process(repo, filled_order(OrderSide.SELL, 1.5, 120.0, "order-2"))

    position = repo.get_position("ma_cross", "BTC-USDT")
    account = repo.get_account("ma_cross")
    assert position.side == "short"
    assert position.amount == pytest.approx(0.5)
    assert position.entry_price == 120.0
    assert account.realized_pnl == pytest.approx(20.0)
    assert account.equity == pytest.approx(100020.0)


def test_sell_opens_and_adds_to_short_with_weighted_average_entry():
    repo = FakeRepository()

    process(repo, filled_order(OrderSide.SELL, 1.0, 100.0, "order-1"))
    process(repo, filled_order(OrderSide.SELL, 1.0, 80.0, "order-2"))

    position = repo.get_position("ma_cross", "BTC-USDT")
    account = repo.get_account("ma_cross")
    assert position.side == "short"
    assert position.amount == 2.0
    assert position.entry_price == 90.0
    assert account.equity == pytest.approx(100000.0)


def test_buy_partially_closes_short_and_realizes_pnl():
    repo = FakeRepository()

    process(repo, filled_order(OrderSide.SELL, 1.0, 100.0, "order-1"))
    process(repo, filled_order(OrderSide.BUY, 0.4, 80.0, "order-2"))

    position = repo.get_position("ma_cross", "BTC-USDT")
    account = repo.get_account("ma_cross")
    assert position.side == "short"
    assert position.amount == pytest.approx(0.6)
    assert account.realized_pnl == pytest.approx(8.0)


def test_buy_fully_closes_short():
    repo = FakeRepository()

    process(repo, filled_order(OrderSide.SELL, 1.0, 100.0, "order-1"))
    process(repo, filled_order(OrderSide.BUY, 1.0, 80.0, "order-2"))

    account = repo.get_account("ma_cross")
    assert repo.get_position("ma_cross", "BTC-USDT") is None
    assert account.realized_pnl == pytest.approx(20.0)


def test_buy_flips_short_to_long():
    repo = FakeRepository()

    process(repo, filled_order(OrderSide.SELL, 1.0, 100.0, "order-1"))
    process(repo, filled_order(OrderSide.BUY, 1.4, 80.0, "order-2"))

    position = repo.get_position("ma_cross", "BTC-USDT")
    account = repo.get_account("ma_cross")
    assert position.side == "long"
    assert position.amount == pytest.approx(0.4)
    assert position.entry_price == 80.0
    assert account.realized_pnl == pytest.approx(20.0)


def test_fees_reduce_cash_and_accumulate_separately_from_realized_pnl():
    repo = FakeRepository()
    accounting = service(repo, fee_rate=0.01)

    accounting.process_filled_order(
        filled_order(OrderSide.BUY, 1.0, 100.0), "ma_cross", 1700000000000
    )
    accounting.process_filled_order(
        filled_order(OrderSide.SELL, 1.0, 110.0), "ma_cross", 1700000000000
    )

    account = repo.get_account("ma_cross")
    assert account.realized_pnl == 10.0
    assert account.fees_paid == pytest.approx(2.1)
    assert account.cash_balance == pytest.approx(100007.9)


def test_non_filled_order_does_not_mutate_account_position_or_trade():
    repo = FakeRepository()
    order = filled_order(OrderSide.BUY, 1.0, 100.0)
    order.status = OrderStatus.REJECTED
    order.fill_price = None

    service(repo).process_filled_order(order, "ma_cross", 1700000000000)

    assert repo.accounts == {}
    assert repo.positions == {}
    assert repo.trades == []
