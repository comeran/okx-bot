import pytest

from src.core.types import OrderSide, OrderStatus, OrderType
from src.data.allocations import AllocationScope, AllocationStore
from src.data.paper_book import PaperBook
from src.data.repository import Repository
from src.order.manager import UnifiedOrderManager
from src.order.router import OrderHandler, OrderRouter


class PriceFill(OrderHandler):
    def __init__(self):
        self.submitted = []

    async def submit(self, order):
        self.submitted.append(order)
        order.status = OrderStatus.FILLED
        order.fill_price = order.price
        return order

    async def cancel(self, order_id, symbol=None):
        return True


def prepare(path):
    repository = Repository(db_path=str(path))
    store = AllocationStore(repository.engine)
    scope = AllocationScope(account_fingerprint="a" * 64, environment="sandbox")
    store.save_draft(
        scope, strategy="alpha", symbol="BTC-USDT", budget=600,
        expected_revision=0, timestamp=1,
    )
    store.save_draft(
        scope, strategy="beta", symbol="ETH-USDT", budget=400,
        expected_revision=0, timestamp=1,
    )
    book = PaperBook(repository.engine)
    book.activate(
        scope.scope_id, "alpha", symbol="BTC-USDT",
        expected_revision=1, book_equity=1000, timestamp=2,
    )
    book.activate(
        scope.scope_id, "beta", symbol="ETH-USDT",
        expected_revision=1, book_equity=1000, timestamp=3,
    )
    return repository, book, scope


def manager(repository, book, scope_id, handler):
    events = []
    order_manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, demo=handler, mode="demo"),
        repository=repository,
        initial_equity=100000,
        fee_rate=0.0,
        timestamp_ms=lambda: 10,
        paper_book=book,
        paper_scope_id=lambda: scope_id,
        on_risk_event=lambda payload: events.append(payload),
    )
    return order_manager, events


@pytest.mark.asyncio
async def test_reserved_strategies_spend_only_their_own_budget(tmp_path):
    repository, book, scope = prepare(tmp_path / "orders.db")
    alpha_handler = PriceFill()
    beta_handler = PriceFill()
    alpha, alpha_events = manager(repository, book, scope.scope_id, alpha_handler)
    beta, beta_events = manager(repository, book, scope.scope_id, beta_handler)

    opened = await alpha.submit(
        symbol="BTC-USDT", side=OrderSide.BUY, order_type=OrderType.LIMIT,
        amount=0.01, price=50000, strategy_name="alpha",
    )
    assert opened.status == OrderStatus.FILLED
    assert alpha.get_balance("alpha") == pytest.approx(100)
    blocked = await alpha.submit(
        symbol="BTC-USDT", side=OrderSide.BUY, order_type=OrderType.LIMIT,
        amount=0.01, price=50000, strategy_name="alpha",
    )
    assert blocked.status == OrderStatus.REJECTED
    assert alpha_events[-1]["reason"] == "Paper reservation cash is insufficient"
    assert alpha.get_balance("alpha") == pytest.approx(100)

    stolen = await beta.submit(
        symbol="BTC-USDT", side=OrderSide.BUY, order_type=OrderType.LIMIT,
        amount=0.01, price=50000, strategy_name="beta",
    )
    assert stolen.status == OrderStatus.REJECTED
    assert beta_events[-1]["reason"] == "Symbol is reserved by another strategy"
    assert beta_handler.submitted == []

    other = await beta.submit(
        symbol="ETH-USDT", side=OrderSide.BUY, order_type=OrderType.LIMIT,
        amount=0.1, price=1000, strategy_name="beta",
    )
    assert other.status == OrderStatus.FILLED
    assert beta.get_balance("beta") == pytest.approx(300)

    oversized_sell = await alpha.submit(
        symbol="BTC-USDT", side=OrderSide.SELL, order_type=OrderType.LIMIT,
        amount=1, price=50000, strategy_name="alpha",
    )
    assert oversized_sell.status == OrderStatus.REJECTED
    assert alpha_events[-1]["reason"] == "Paper spot sell exceeds reserved holdings"
    closed = await alpha.submit(
        symbol="BTC-USDT", side=OrderSide.SELL, order_type=OrderType.LIMIT,
        amount=0.01, price=50000, strategy_name="alpha",
    )
    assert closed.status == OrderStatus.FILLED
    assert alpha.get_balance("alpha") == pytest.approx(600)


@pytest.mark.asyncio
async def test_unreserved_strategy_cannot_spend_while_a_budget_is_active(tmp_path):
    repository, book, scope = prepare(tmp_path / "orders.db")
    handler = PriceFill()
    gamma, events = manager(repository, book, scope.scope_id, handler)
    order = await gamma.submit(
        symbol="SOL-USDT", side=OrderSide.BUY, order_type=OrderType.LIMIT,
        amount=1, price=10, strategy_name="gamma",
    )
    assert order.status == OrderStatus.REJECTED
    assert events[-1]["reason"] == "Activate a local paper budget before trading this symbol"
    assert handler.submitted == []


@pytest.mark.asyncio
async def test_reservation_does_not_follow_a_different_account_scope(tmp_path):
    repository, book, _scope = prepare(tmp_path / "orders.db")
    handler = PriceFill()
    alpha, events = manager(repository, book, "other-scope", handler)
    order = await alpha.submit(
        symbol="BTC-USDT", side=OrderSide.BUY, order_type=OrderType.LIMIT,
        amount=0.01, price=50000, strategy_name="alpha",
    )
    assert order.status == OrderStatus.REJECTED
    assert events[-1]["reason"] == "Paper reservation does not match the configured account"
    assert handler.submitted == []


@pytest.mark.asyncio
async def test_demo_orders_without_reservations_keep_the_shared_book(tmp_path):
    repository = Repository(db_path=str(tmp_path / "plain.db"))
    book = PaperBook(repository.engine)
    handler = PriceFill()
    plain, events = manager(repository, book, "unused-scope", handler)
    order = await plain.submit(
        symbol="BTC-USDT", side=OrderSide.BUY, order_type=OrderType.LIMIT,
        amount=1, price=100, strategy_name="plain",
    )
    assert order.status == OrderStatus.FILLED
    assert events == []
    assert plain.get_balance("plain") == pytest.approx(99900)
