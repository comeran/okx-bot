import threading

import pytest

import src.data.repository as repository_module
from src.core.types import Order, OrderSide, OrderStatus, OrderType
from src.data.models import AccountRecord, PositionRecord
from src.data.repository import Repository
from src.order.accounting import PaperAccountingService
from src.order.mark_to_market import PaperMarkToMarketService


class HedgedFakeRepository:
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

    def get_positions(self, strategy=None, symbol=None):
        return [
            position
            for position in self.positions.values()
            if (strategy is None or position.strategy == strategy)
            and (symbol is None or position.symbol == symbol)
        ]

    def get_position(self, strategy, symbol, side=None):
        positions = self.get_positions(strategy, symbol)
        if side is not None:
            positions = [position for position in positions if position.side == side]
        return positions[0] if positions else None

    def upsert_position(self, position):
        self.positions[(position.strategy, position.symbol, position.side)] = position
        return position

    def delete_position(self, strategy, symbol, side=None):
        keys = [
            key for key, position in self.positions.items()
            if position.strategy == strategy and position.symbol == symbol
            and (side is None or position.side == side)
        ]
        for key in keys:
            self.deleted_positions.append(key)
            del self.positions[key]

    def get_open_positions(self, strategy=None):
        return [
            position
            for position in self.positions.values()
            if position.amount != 0 and (strategy is None or position.strategy == strategy)
        ]

    def get_trades(self, strategy=None, order_id=None):
        trades = self.trades
        if strategy is not None:
            trades = [trade for trade in trades if trade.strategy == strategy]
        if order_id is not None:
            trades = [trade for trade in trades if trade.order_id == order_id]
        return list(trades)

    def save_trade(self, trade):
        self.trades.append(trade)
        return trade

    def save_cash_ledger(self, entry):
        self.ledger.append(entry)
        return entry


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

    def get_trades(self, strategy=None, order_id=None):
        trades = self.trades
        if strategy is not None:
            trades = [trade for trade in trades if trade.strategy == strategy]
        if order_id is not None:
            trades = [trade for trade in trades if trade.order_id == order_id]
        return list(trades)

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


def hedged_position(side, amount, entry_price):
    return PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT",
        side=side,
        amount=amount,
        entry_price=entry_price,
        leverage=1,
        timestamp=1700000000000,
        source="paper",
        runtime_mode="paper",
    )


def test_legacy_fallback_position_is_not_used_for_another_runtime():
    repo = FakeRepository()
    legacy = PositionRecord(
        strategy="alpha", symbol="BTC-USDT", side="long", amount=2.0,
        entry_price=100.0, leverage=1, timestamp=1,
    )
    repo.positions[("alpha", "BTC-USDT")] = legacy

    accounting = PaperAccountingService(repo, runtime_mode="backtest")
    accounting.process_filled_order(filled_order(OrderSide.SELL, 1.0, 90.0), "alpha", 2)

    assert legacy.amount == 2.0
    assert repo.get_account("alpha").realized_pnl == 0.0


def test_fallback_position_creation_preserves_runtime_provenance():
    repo = FakeRepository()
    accounting = PaperAccountingService(
        repository=repo,
        initial_equity=100000.0,
        runtime_mode="backtest",
    )

    accounting.process_filled_order(filled_order(OrderSide.BUY, 1.0, 100.0), "alpha", 10)

    position = repo.positions[("alpha", "BTC-USDT")]
    assert position.source == "backtest"
    assert position.runtime_mode == "backtest"


def test_buy_rejects_when_cash_cannot_cover_notional_and_fee_without_mutation():
    repo = FakeRepository()
    repo.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        source="paper",
        runtime_mode="paper",
        initial_equity=50.0,
        cash_balance=50.0,
        equity=50.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1,
    )

    with pytest.raises(ValueError, match="insufficient cash"):
        service(repo, fee_rate=0.01).process_filled_order(
            filled_order(OrderSide.BUY, 1.0, 100.0), "ma_cross", 2
        )

    account = repo.get_account("ma_cross")
    assert account.cash_balance == 50.0
    assert account.equity == 50.0
    assert repo.positions == {}
    assert repo.trades == []
    assert repo.ledger == []


def test_buy_reduces_short_before_opening_long_in_hedged_account():
    repo = HedgedFakeRepository()
    repo.positions[("ma_cross", "BTC-USDT", "short")] = hedged_position("short", 2.0, 100.0)

    process(repo, filled_order(OrderSide.BUY, 1.0, 80.0))

    assert repo.get_position("ma_cross", "BTC-USDT", side="short").amount == pytest.approx(1.0)
    assert repo.get_position("ma_cross", "BTC-USDT", side="long") is None
    assert repo.get_account("ma_cross").realized_pnl == pytest.approx(20.0)


def test_sell_reduces_long_before_opening_short_in_hedged_account():
    repo = HedgedFakeRepository()
    repo.positions[("ma_cross", "BTC-USDT", "long")] = hedged_position("long", 2.0, 100.0)

    process(repo, filled_order(OrderSide.SELL, 1.0, 120.0))

    assert repo.get_position("ma_cross", "BTC-USDT", side="long").amount == pytest.approx(1.0)
    assert repo.get_position("ma_cross", "BTC-USDT", side="short") is None
    assert repo.get_account("ma_cross").realized_pnl == pytest.approx(20.0)


def test_hedged_buy_flip_keeps_short_zero_and_opens_long_residual():
    repo = HedgedFakeRepository()
    repo.positions[("ma_cross", "BTC-USDT", "short")] = hedged_position("short", 1.0, 100.0)

    process(repo, filled_order(OrderSide.BUY, 1.5, 80.0))

    assert repo.get_position("ma_cross", "BTC-USDT", side="short") is None
    long = repo.get_position("ma_cross", "BTC-USDT", side="long")
    assert long.amount == pytest.approx(0.5)
    assert long.entry_price == 80.0
    assert repo.get_account("ma_cross").realized_pnl == pytest.approx(20.0)


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


def test_sell_partially_closes_marked_long_and_preserves_remaining_unrealized_pnl():
    repo = FakeRepository()

    process(repo, filled_order(OrderSide.BUY, 1.0, 100.0, "order-1"))
    PaperMarkToMarketService(repo).mark(
        strategy_name="ma_cross",
        symbol="BTC-USDT",
        mark_price=120.0,
        timestamp=1700003600000,
    )
    process(repo, filled_order(OrderSide.SELL, 0.4, 110.0, "order-2"))

    position = repo.get_position("ma_cross", "BTC-USDT")
    account = repo.get_account("ma_cross")
    assert position.side == "long"
    assert position.amount == pytest.approx(0.6)
    assert position.mark_price == 120.0
    assert position.unrealized_pnl == pytest.approx(12.0)
    assert account.realized_pnl == pytest.approx(4.0)
    assert account.unrealized_pnl == pytest.approx(12.0)
    assert account.equity == pytest.approx(100016.0)


def test_paper_close_does_not_delete_same_key_live_position(tmp_path):
    repo = Repository(db_path=str(tmp_path / "domain-close.db"))
    repo.upsert_position(PositionRecord(
        strategy="same", symbol="BTC-USDT", side="long", amount=1,
        entry_price=100, leverage=1, timestamp=1,
        source="live_exchange", runtime_mode="live",
    ))
    paper = PaperAccountingService(repo, initial_equity=1000.0, runtime_mode="paper")
    paper.process_filled_order(filled_order(OrderSide.BUY, 1.0, 100.0), "same", 2)
    paper.process_filled_order(filled_order(OrderSide.SELL, 1.0, 120.0, "close"), "same", 3)

    assert repo.get_position(
        "same", "BTC-USDT", side="long", source="live_exchange", runtime_mode="live"
    ) is not None


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
        filled_order(OrderSide.SELL, 1.0, 110.0, "order-2"), "ma_cross", 1700000000000
    )

    account = repo.get_account("ma_cross")
    assert account.realized_pnl == 10.0
    assert account.fees_paid == pytest.approx(2.1)
    assert account.cash_balance == pytest.approx(100007.9)


def test_fill_preserves_marked_unrealized_pnl_from_other_open_positions():
    repo = FakeRepository()
    repo.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        source="paper",
        runtime_mode="paper",
        initial_equity=100000.0,
        cash_balance=96000.0,
        equity=100050.0,
        realized_pnl=0.0,
        unrealized_pnl=50.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
    )
    repo.positions[("ma_cross", "ETH-USDT")] = PositionRecord(
        strategy="ma_cross",
        symbol="ETH-USDT",
        side="long",
        amount=2.0,
        entry_price=2000.0,
        leverage=1,
        timestamp=1700000000000,
        mark_price=2025.0,
        unrealized_pnl=50.0,
        source="paper",
        runtime_mode="paper",
    )

    process(repo, filled_order(OrderSide.BUY, 1.0, 100.0, "btc-order"))

    account = repo.get_account("ma_cross")
    assert account.unrealized_pnl == pytest.approx(50.0)
    assert account.equity == pytest.approx(100050.0)


def test_non_filled_order_does_not_mutate_account_position_or_trade():
    repo = FakeRepository()
    order = filled_order(OrderSide.BUY, 1.0, 100.0)
    order.status = OrderStatus.REJECTED
    order.fill_price = None

    service(repo).process_filled_order(order, "ma_cross", 1700000000000)

    assert repo.accounts == {}
    assert repo.positions == {}
    assert repo.trades == []


def test_partially_filled_order_with_zero_or_missing_fill_amount_is_fail_closed():
    repo = FakeRepository()
    order = filled_order(OrderSide.BUY, 1.0, 100.0)
    order.status = OrderStatus.PARTIALLY_FILLED
    order.filled_amount = 0.0
    order.remaining_amount = 1.0

    process(repo, order)
    order.filled_amount = None
    process(repo, order)

    assert repo.accounts == {}
    assert repo.positions == {}
    assert repo.trades == []
    assert repo.ledger == []


def test_concurrent_duplicate_fill_is_accounted_once(tmp_path):
    db_path = tmp_path / "accounting.db"
    first_repo = Repository(db_path=str(db_path))
    second_repo = Repository(db_path=str(db_path))
    order = filled_order(OrderSide.BUY, 1.0, 100.0, "concurrent-order")
    barrier = threading.Barrier(2)
    errors = []

    def account(repo):
        try:
            barrier.wait()
            service(repo).process_filled_order(order, "ma_cross", 1700000000000)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=account, args=(repo,)) for repo in (first_repo, second_repo)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    repo = Repository(db_path=str(db_path))
    assert len(repo.get_trades(order_id="concurrent-order")) == 1
    assert repo.get_positions("ma_cross", "BTC-USDT")[0].amount == pytest.approx(1.0)
    assert repo.get_account("ma_cross").cash_balance == pytest.approx(99900.0)
    assert len(repo.get_cash_ledger("ma_cross")) == 1


def test_partial_fill_callbacks_only_account_new_filled_increment():
    repo = FakeRepository()
    order = filled_order(OrderSide.BUY, 1.0, 100.0, "partial-order")
    order.status = OrderStatus.PARTIALLY_FILLED
    order.filled_amount = 0.4
    order.remaining_amount = 0.6

    process(repo, order)
    process(repo, order)
    order.filled_amount = 0.7
    order.remaining_amount = 0.3
    process(repo, order)

    assert repo.get_position("ma_cross", "BTC-USDT").amount == pytest.approx(0.7)
    assert repo.get_account("ma_cross").cash_balance == pytest.approx(99930.0)
    assert [trade.amount for trade in repo.trades] == pytest.approx([0.4, 0.3])
    assert [entry.amount for entry in repo.ledger] == pytest.approx([-40.0, -30.0])


def test_partial_fill_uses_incremental_price_derived_from_cumulative_average():
    repo = FakeRepository()
    order = filled_order(OrderSide.BUY, 2.0, 100.0, "partial-average-order")
    order.status = OrderStatus.PARTIALLY_FILLED
    order.filled_amount = 1.0
    order.remaining_amount = 1.0

    process(repo, order)
    order.filled_amount = 2.0
    order.remaining_amount = 0.0
    order.fill_price = 110.0
    process(repo, order)
    process(repo, order)

    assert repo.get_position("ma_cross", "BTC-USDT").entry_price == pytest.approx(110.0)
    assert repo.get_account("ma_cross").cash_balance == pytest.approx(99780.0)
    assert [(trade.amount, trade.price) for trade in repo.trades] == [(1.0, 100.0), (1.0, 120.0)]


@pytest.mark.parametrize("field, value", [
    ("amount", float("nan")),
    ("filled_amount", float("inf")),
    ("fill_price", float("nan")),
])
def test_accounting_rejects_nonfinite_order_values_without_mutation(field, value):
    repo = FakeRepository()
    order = filled_order(OrderSide.BUY, 1.0, 100.0)
    setattr(order, field, value)

    with pytest.raises(ValueError):
        service(repo).process_filled_order(order, "ma_cross", 1700000000000)

    assert repo.accounts == {}
    assert repo.positions == {}
    assert repo.trades == []


def test_accounting_rejects_filled_amount_above_order_amount_without_mutation():
    repo = FakeRepository()
    order = filled_order(OrderSide.BUY, 1.0, 100.0)
    order.filled_amount = 2.0

    with pytest.raises(ValueError):
        service(repo).process_filled_order(order, "ma_cross", 1700000000000)

    assert repo.accounts == {}
    assert repo.positions == {}
    assert repo.trades == []


@pytest.mark.parametrize("fee", [float("nan"), float("inf"), -0.01])
def test_accounting_rejects_invalid_fee_without_mutation(fee):
    repo = FakeRepository()

    with pytest.raises(ValueError):
        service(repo).process_filled_order(
            filled_order(OrderSide.BUY, 1.0, 100.0), "ma_cross", 1700000000000, fee=fee
        )

    assert repo.accounts == {}
    assert repo.positions == {}
    assert repo.trades == []


@pytest.mark.parametrize("mark_price", [0.0, -1.0, float("nan"), float("inf")])
def test_mark_to_market_rejects_non_positive_or_non_finite_price(mark_price):
    repo = FakeRepository()
    repo.positions[("ma_cross", "BTC-USDT")] = PositionRecord(
        strategy="ma_cross", symbol="BTC-USDT", side="long", amount=1.0,
        entry_price=100.0, mark_price=110.0, unrealized_pnl=10.0, leverage=1, timestamp=1,
    )
    repo.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross", initial_equity=100000.0, cash_balance=99900.0,
        equity=100010.0, realized_pnl=0.0, unrealized_pnl=10.0, daily_pnl=0.0,
        fees_paid=0.0, updated_at=1,
    )

    assert PaperMarkToMarketService(repo).mark("ma_cross", "BTC-USDT", mark_price, 2) is False
    position = repo.get_position("ma_cross", "BTC-USDT")
    account = repo.get_account("ma_cross")
    assert position.mark_price == 110.0
    assert position.unrealized_pnl == 10.0
    assert account.unrealized_pnl == 10.0
    assert account.equity == 100010.0


def test_mark_to_market_updates_long_position_and_account_equity():
    repo = FakeRepository()
    repo.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        source="paper",
        runtime_mode="paper",
        initial_equity=100000.0,
        cash_balance=95000.0,
        equity=100000.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
    )
    repo.positions[("ma_cross", "BTC-USDT")] = PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="long",
        amount=0.1,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
        source="paper",
        runtime_mode="paper",
    )

    updated = PaperMarkToMarketService(repo).mark(
        strategy_name="ma_cross",
        symbol="BTC-USDT",
        mark_price=51000.0,
        timestamp=1700003600000,
    )

    position = repo.get_position("ma_cross", "BTC-USDT")
    account = repo.get_account("ma_cross")
    assert updated is True
    assert position.mark_price == 51000.0
    assert position.unrealized_pnl == pytest.approx(100.0)
    assert position.timestamp == 1700003600000
    assert account.unrealized_pnl == pytest.approx(100.0)
    assert account.equity == pytest.approx(100100.0)
    assert account.updated_at == 1700003600000


def test_mark_to_market_updates_short_position_and_account_equity():
    repo = FakeRepository()
    repo.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        source="paper",
        runtime_mode="paper",
        initial_equity=100000.0,
        cash_balance=110000.0,
        equity=100000.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
    )
    repo.positions[("ma_cross", "BTC-USDT")] = PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="short",
        amount=0.2,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
        source="paper",
        runtime_mode="paper",
    )

    updated = PaperMarkToMarketService(repo).mark(
        strategy_name="ma_cross",
        symbol="BTC-USDT",
        mark_price=49000.0,
        timestamp=1700003600000,
    )

    position = repo.get_position("ma_cross", "BTC-USDT")
    account = repo.get_account("ma_cross")
    assert updated is True
    assert position.mark_price == 49000.0
    assert position.unrealized_pnl == pytest.approx(200.0)
    assert account.unrealized_pnl == pytest.approx(200.0)
    assert account.equity == pytest.approx(100200.0)


def test_mark_to_market_sums_existing_unrealized_pnl_for_all_strategy_positions():
    repo = FakeRepository()
    repo.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        source="paper",
        runtime_mode="paper",
        initial_equity=100000.0,
        cash_balance=99000.0,
        equity=100000.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
    )
    repo.positions[("ma_cross", "BTC-USDT")] = PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="long",
        amount=0.1,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
        source="paper",
        runtime_mode="paper",
    )
    repo.positions[("ma_cross", "ETH-USDT")] = PositionRecord(
        strategy="ma_cross",
        symbol="ETH-USDT",
        side="short",
        amount=2.0,
        entry_price=2000.0,
        leverage=1,
        timestamp=1700000000000,
        mark_price=1975.0,
        unrealized_pnl=50.0,
        source="paper",
        runtime_mode="paper",
    )
    repo.positions[("other", "BTC-USDT")] = PositionRecord(
        strategy="other",
        symbol="BTC-USDT",
        side="long",
        amount=10.0,
        entry_price=1.0,
        leverage=1,
        timestamp=1700000000000,
        unrealized_pnl=999.0,
    )

    PaperMarkToMarketService(repo).mark(
        strategy_name="ma_cross",
        symbol="BTC-USDT",
        mark_price=51000.0,
        timestamp=1700003600000,
    )

    account = repo.get_account("ma_cross")
    assert account.unrealized_pnl == pytest.approx(150.0)
    assert account.equity == pytest.approx(100150.0)


def test_mark_to_market_updates_both_hedged_positions_and_aggregates_equity():
    repo = HedgedFakeRepository()
    repo.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        source="paper",
        runtime_mode="paper",
        initial_equity=100000.0,
        cash_balance=100000.0,
        equity=100000.0,
        realized_pnl=12.0,
        unrealized_pnl=0.0,
        daily_pnl=12.0,
        fees_paid=3.0,
        updated_at=1700000000000,
    )
    long = hedged_position("long", 2.0, 100.0)
    short = hedged_position("short", 3.0, 120.0)
    long.realized_pnl = 7.0
    short.realized_pnl = -4.0
    repo.upsert_position(long)
    repo.upsert_position(short)

    updated = PaperMarkToMarketService(repo).mark_update(
        strategy_name="ma_cross",
        symbol="BTC-USDT",
        mark_price=110.0,
        timestamp=1700003600000,
    )

    saved_long = repo.get_position("ma_cross", "BTC-USDT", side="long")
    saved_short = repo.get_position("ma_cross", "BTC-USDT", side="short")
    account = repo.get_account("ma_cross")
    assert updated is not None
    assert {position.side for position in updated.positions} == {"long", "short"}
    assert saved_long.mark_price == 110.0
    assert saved_long.unrealized_pnl == pytest.approx(20.0)
    assert saved_long.realized_pnl == pytest.approx(7.0)
    assert saved_short.mark_price == 110.0
    assert saved_short.unrealized_pnl == pytest.approx(30.0)
    assert saved_short.realized_pnl == pytest.approx(-4.0)
    assert account.unrealized_pnl == pytest.approx(50.0)
    assert account.equity == pytest.approx(99890.0)


def test_mark_to_market_rolls_back_all_hedged_positions_and_account_when_persist_fails(
    tmp_path, monkeypatch
):
    repo = Repository(db_path=str(tmp_path / "mark-to-market.db"))
    repo.upsert_account(AccountRecord(
        strategy="ma_cross", initial_equity=100000.0, cash_balance=100000.0,
        equity=100000.0, realized_pnl=0.0, unrealized_pnl=0.0, daily_pnl=0.0,
        fees_paid=0.0, updated_at=1,
    ))
    repo.upsert_position(hedged_position("long", 2.0, 100.0))
    repo.upsert_position(hedged_position("short", 3.0, 120.0))
    original_upsert_position = repository_module._AccountingTransaction.upsert_position
    calls = 0

    def fail_on_second_position(transaction, position):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated persistence failure")
        return original_upsert_position(transaction, position)

    monkeypatch.setattr(
        repository_module._AccountingTransaction,
        "upsert_position",
        fail_on_second_position,
    )

    with pytest.raises(RuntimeError, match="simulated persistence failure"):
        PaperMarkToMarketService(repo).mark_update(
            "ma_cross", "BTC-USDT", 110.0, 1700000000001
        )

    positions = {position.side: position for position in repo.get_positions("ma_cross", "BTC-USDT")}
    account = repo.get_account("ma_cross")
    assert positions["long"].mark_price is None
    assert positions["long"].unrealized_pnl == 0.0
    assert positions["short"].mark_price is None
    assert positions["short"].unrealized_pnl == 0.0
    assert account is not None
    assert account.unrealized_pnl == 0.0
    assert account.equity == 100000.0
    assert account.updated_at == 1


def test_mark_to_market_creates_account_when_position_exists_without_account():
    repo = FakeRepository()
    repo.positions[("ma_cross", "BTC-USDT")] = PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="long",
        amount=1.0,
        entry_price=100.0,
        leverage=1,
        timestamp=1700000000000,
        source="paper",
        runtime_mode="paper",
    )

    updated = PaperMarkToMarketService(repo, initial_equity=100000.0).mark(
        strategy_name="ma_cross",
        symbol="BTC-USDT",
        mark_price=110.0,
        timestamp=1700003600000,
    )

    account = repo.get_account("ma_cross")
    assert updated is True
    assert account.cash_balance == 100000.0
    assert account.unrealized_pnl == pytest.approx(10.0)
    assert account.equity == pytest.approx(100110.0)
    assert account.updated_at == 1700003600000


def test_mark_to_market_noops_without_open_position_and_does_not_create_account():
    repo = FakeRepository()

    updated = PaperMarkToMarketService(repo).mark(
        strategy_name="ma_cross",
        symbol="BTC-USDT",
        mark_price=51000.0,
        timestamp=1700003600000,
    )

    assert updated is False
    assert repo.accounts == {}
    assert repo.positions == {}


def test_linear_contract_fill_uses_base_amount_and_contract_reduction():
    repo = FakeRepository()
    for side, amount, price, order_id in [
        (OrderSide.BUY, 100.0, 50000.0, "linear-open"),
        (OrderSide.SELL, 50.0, 60000.0, "linear-close"),
    ]:
        service(repo).process_filled_order(
            Order(
                id=order_id, symbol="BTC-USDT-SWAP", side=side,
                type=OrderType.MARKET, amount=amount, price=price,
                status=OrderStatus.FILLED, fill_price=price,
                params={"market_type": "swap", "contract_size": 0.01},
            ),
            "linear", 1 if side == OrderSide.BUY else 2,
        )

    position = repo.get_position("linear", "BTC-USDT-SWAP")
    account = repo.get_account("linear")
    assert position.amount == pytest.approx(0.5)
    assert position.contracts == pytest.approx(50.0)
    assert account.realized_pnl == pytest.approx(5000.0)
    assert account.equity == pytest.approx(105000.0)


def test_inverse_reduce_uses_contracts_not_quote_notional():
    repo = FakeRepository()
    service(repo).process_filled_order(
        Order(
            id="inverse-open",
            symbol="BTC-USD-SWAP",
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            amount=2.0,
            price=50000.0,
            status=OrderStatus.FILLED,
            fill_price=50000.0,
            params={"is_inverse": True, "contract_size": 100.0},
        ),
        "inverse",
        1,
    )
    service(repo).process_filled_order(
        Order(
            id="inverse-close",
            symbol="BTC-USD-SWAP",
            side=OrderSide.SELL,
            type=OrderType.MARKET,
            amount=1.0,
            price=40000.0,
            status=OrderStatus.FILLED,
            fill_price=40000.0,
            params={"is_inverse": True, "contract_size": 100.0},
        ),
        "inverse",
        2,
    )

    position = repo.get_position("inverse", "BTC-USD-SWAP")
    account = repo.get_account("inverse")
    assert position.contracts == pytest.approx(1.0)
    assert position.amount == pytest.approx(100.0)
    assert account.realized_pnl == pytest.approx(-20.0)


def test_inverse_accounting_uses_quote_contract_notional_and_inverse_mtm_pnl():
    repo = FakeRepository()
    order = Order(
        id="inverse-order",
        symbol="BTC-USD-SWAP",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=2.0,
        price=50000.0,
        status=OrderStatus.FILLED,
        fill_price=50000.0,
        params={"is_inverse": True, "contract_size": 100.0},
    )

    service(repo).process_filled_order(order, "inverse", 1)

    account = repo.get_account("inverse")
    position = repo.get_position("inverse", "BTC-USD-SWAP")
    assert position.contracts == pytest.approx(2.0)
    assert position.contract_size == pytest.approx(100.0)
    assert position.notional_value == pytest.approx(200.0)
    assert account.cash_balance == pytest.approx(99800.0)
    assert account.equity == pytest.approx(100000.0)

    assert PaperMarkToMarketService(repo).mark(
        "inverse", "BTC-USD-SWAP", 60000.0, 2
    )
    position = repo.get_position("inverse", "BTC-USD-SWAP")
    account = repo.get_account("inverse")
    assert position.unrealized_pnl == pytest.approx(40.0)
    assert account.unrealized_pnl == pytest.approx(40.0)
    assert account.equity == pytest.approx(100040.0)
