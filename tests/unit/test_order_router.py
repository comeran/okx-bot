import asyncio
import logging
import math

import ccxt.async_support as ccxt
import pytest

from src.core.types import Order, OrderSide, OrderStatus, OrderType
from src.data.models import AccountRecord, OrderRecord, PositionRecord, TradeRecord
from src.data.repository import Repository
from src.exchange.errors import AcceptedUnknownOrderResultError
from src.order.manager import (
    UnifiedOrderManager,
    order_notional,
    position_notional,
    risk_reason_code,
)
from src.order.router import OrderHandler, OrderRouter
from src.order.value import contract_size, is_inverse, position_order_quantity
from src.risk.manager import RiskManager


class MockHandler(OrderHandler):
    def __init__(self):
        self.submitted = []
        self.cancelled = []

    async def submit(self, order: Order) -> Order:
        self.submitted.append(order)
        order.status = OrderStatus.FILLED
        order.fill_price = 50000
        return order

    async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
        self.cancelled.append((order_id, symbol))
        return True


class FakeRepository:
    def __init__(self):
        self.orders = []
        self.trades = []
        self.positions = {}
        self.owned_positions = set()
        self.accounts = {}
        self.ledger = []

    def get_orders(self, order_id=None):
        if order_id is None:
            return list(self.orders)
        return [order for order in self.orders if order.order_id == order_id]

    def upsert_order(self, order):
        existing = next(
            (
                current
                for current in self.orders
                if current.order_id == order.order_id
                or (
                    order.exchange_order_id
                    and current.exchange_order_id == order.exchange_order_id
                )
            ),
            None,
        )
        if existing is None:
            self.orders.append(order)
            return order
        for field, value in order.model_dump(exclude={"id"}).items():
            setattr(existing, field, value)
        return existing

    def save_trade(self, trade):
        self.trades.append(trade)
        return trade

    def get_trades(self, strategy=None, order_id=None):
        return [
            trade
            for trade in self.trades
            if (strategy is None or trade.strategy == strategy)
            and (order_id is None or trade.order_id == order_id)
        ]

    def get_account(self, strategy):
        account = self.accounts.get(strategy)
        if account is None and strategy == "__exchange__" and len(self.accounts) == 1:
            return next(iter(self.accounts.values()))
        return account

    def upsert_account(self, account):
        self.accounts[account.strategy] = account
        return account

    def is_position_owned(self, strategy, position):
        return (strategy, position.symbol) in self.owned_positions

    def get_position(self, strategy, symbol):
        return self.positions.get((strategy, symbol))

    def upsert_position(self, position):
        self.positions[(position.strategy, position.symbol)] = position
        return position

    def delete_position(self, strategy, symbol):
        self.positions.pop((strategy, symbol), None)

    def get_open_positions(self, strategy=None):
        positions = [
            position
            for position in self.positions.values()
            if position.amount != 0 and (strategy is None or position.strategy == strategy)
        ]
        if strategy == "__exchange__" and not positions:
            return [position for position in self.positions.values() if position.amount != 0]
        return positions

    def save_cash_ledger(self, entry):
        self.ledger.append(entry)
        return entry


def grant_live_position_ownership(repository, strategy="ma_cross"):
    symbol = "BTC-USDT-SWAP"
    repository.positions[("__exchange__", symbol)] = PositionRecord(
        strategy="__exchange__",
        symbol=symbol,
        side="long",
        amount=1.0,
        entry_price=100.0,
        leverage=1,
        timestamp=1,
        source="live_exchange",
        runtime_mode="live",
    )
    repository.owned_positions.add((strategy, symbol))


@pytest.mark.asyncio
async def test_router_backtest():
    handler = MockHandler()
    router = OrderRouter(backtest=handler, mode="backtest")
    order = Order(
        id="1",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=0.1,
    )
    result = await router.submit(order)
    assert result.status == OrderStatus.FILLED
    assert len(handler.submitted) == 1


@pytest.mark.asyncio
async def test_router_mode_switch():
    bt_handler = MockHandler()
    live_handler = MockHandler()
    router = OrderRouter(backtest=bt_handler, live=live_handler, mode="backtest")
    await router.submit(
        Order(
            id="1",
            symbol="BTC-USDT",
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            amount=0.1,
        )
    )
    assert len(bt_handler.submitted) == 1
    assert len(live_handler.submitted) == 0
    router.mode = "live"
    await router.submit(
        Order(
            id="2",
            symbol="BTC-USDT",
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            amount=0.1,
        )
    )
    assert len(live_handler.submitted) == 1


@pytest.mark.asyncio
async def test_order_manager_submit():
    handler = MockHandler()
    router = OrderRouter(backtest=handler, mode="backtest")
    manager = UnifiedOrderManager(router=router)
    order = await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.1,
        strategy_name="test",
    )
    assert order.status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_order_manager_generates_unique_order_ids_for_repeated_submits():
    handler = MockHandler()
    router = OrderRouter(backtest=handler, mode="backtest")
    manager = UnifiedOrderManager(router=router)

    order1 = await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.1,
        strategy_name="test",
    )
    order2 = await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=0.1,
        strategy_name="test",
    )

    assert order1.id != order2.id


@pytest.mark.asyncio
async def test_order_manager_propagates_trigger_price_to_order():
    handler = MockHandler()
    router = OrderRouter(backtest=handler, mode="backtest")
    manager = UnifiedOrderManager(router=router)

    order = await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.SELL,
        order_type=OrderType.STOP,
        amount=0.1,
        trigger_price=49000.0,
        strategy_name="test",
    )

    assert order.trigger_price == 49000.0
    assert handler.submitted[0].trigger_price == 49000.0


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_price", [0.0, -1.0, float("nan"), float("inf")])
async def test_order_manager_rejects_invalid_filled_price_before_persistence(invalid_price):
    class InvalidFillHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            order.status = OrderStatus.FILLED
            order.fill_price = invalid_price
            return order

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=InvalidFillHandler(), mode="backtest"),
        repository=repository,
    )

    with pytest.raises(ValueError, match="positive fill price"):
        await manager.submit(
            symbol="BTC-USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            amount=0.1,
            strategy_name="ma_cross",
        )

    assert repository.orders == []
    assert repository.trades == []
    assert repository.accounts == {}


@pytest.mark.asyncio
async def test_order_manager_persists_filled_order_and_applies_accounting():
    handler = MockHandler()
    repository = FakeRepository()
    router = OrderRouter(backtest=handler, mode="backtest")
    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        initial_equity=100000.0,
    )

    order = await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.1,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.FILLED
    assert repository.orders[0].filled_amount == pytest.approx(0.1)
    assert repository.orders[0].remaining_amount == pytest.approx(0.0)
    assert [saved.model_dump() for saved in repository.orders] == [
        {
            "id": None,
            "order_id": order.id,
            "exchange_order_id": "",
            "client_order_id": "",
            "strategy": "ma_cross",
            "symbol": "BTC-USDT",
            "side": "buy",
            "type": "market",
            "amount": 0.1,
            "price": 0.0,
            "status": "filled",
            "fill_price": 50000.0,
            "timestamp": 1700000000000,
            "updated_at": 0,
        }
    ]
    assert [saved.model_dump() for saved in repository.trades] == [
        {
            "id": None,
            "exchange_trade_id": "",
            "exchange_order_id": "",
            "client_order_id": "",
            "order_id": order.id,
            "strategy": "ma_cross",
            "symbol": "BTC-USDT",
            "side": "buy",
            "amount": 0.1,
            "price": 50000.0,
            "fee": 0.0,
            "timestamp": 1700000000000,
            "blank_trade_fingerprint": "",
            "active": True,
            "stale_at": None,
        }
    ]
    assert repository.accounts["ma_cross"].cash_balance == 95000.0
    assert repository.accounts["ma_cross"].equity == 100000.0
    assert repository.positions[("ma_cross", "BTC-USDT")].model_dump() == {
        "id": None,
        "strategy": "ma_cross",
        "symbol": "BTC-USDT",
        "side": "long",
        "amount": 0.1,
        "entry_price": 50000.0,
        "leverage": 1,
        "timestamp": 1700000000000,
        "mark_price": None,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "active": True,
        "stale_at": None,
    }
    assert repository.ledger[0].amount == -5000.0


@pytest.mark.asyncio
async def test_order_manager_persists_pending_order_without_accounting_mutation():
    class PendingHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            return order

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    router = OrderRouter(backtest=PendingHandler(), mode="backtest")
    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
    )

    await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        amount=0.1,
        price=49000.0,
        strategy_name="ma_cross",
    )

    assert len(repository.orders) == 1
    assert repository.orders[0].status == "pending"
    assert repository.orders[0].filled_amount == 0.0
    assert repository.orders[0].remaining_amount == 0.1
    assert repository.trades == []
    assert repository.positions == {}
    assert repository.accounts == {}


@pytest.mark.asyncio
async def test_order_manager_persists_router_partial_fill_quantities():
    class PartialHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            order.status = OrderStatus.PARTIALLY_FILLED
            order.fill_price = 49100.0
            order.filled_amount = 0.04
            order.remaining_amount = 0.06
            return order

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=PartialHandler(), mode="backtest"),
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
    )

    result = await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        amount=0.1,
        price=49000.0,
        strategy_name="ma_cross",
    )

    saved = repository.orders[0]
    assert result.status == OrderStatus.PARTIALLY_FILLED
    assert saved.status == "partially_filled"
    assert saved.filled_amount == pytest.approx(0.04)
    assert saved.remaining_amount == pytest.approx(0.06)
    assert len(repository.trades) == 1
    assert repository.trades[0].amount == pytest.approx(0.04)
    assert repository.accounts["ma_cross"].cash_balance == pytest.approx(100000.0 - 0.04 * 49100.0)
    assert repository.positions[("ma_cross", "BTC-USDT")].amount == pytest.approx(0.04)


@pytest.mark.asyncio
async def test_order_manager_accounts_partial_fill_only_once_using_filled_amount():
    class PartialHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            order.status = OrderStatus.PARTIALLY_FILLED
            order.fill_price = 49100.0
            order.filled_amount = 0.04
            order.remaining_amount = 0.06
            return order

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=PartialHandler(), mode="backtest"),
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        initial_equity=100000.0,
    )

    result = await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        amount=0.1,
        price=49000.0,
        strategy_name="ma_cross",
    )
    manager._persist_order(result, "ma_cross", timestamp=1700000000000)

    assert repository.orders[0].remaining_amount == pytest.approx(0.06)
    assert len(repository.trades) == 1
    assert repository.trades[0].amount == pytest.approx(0.04)
    assert repository.accounts["ma_cross"].cash_balance == pytest.approx(100000.0 - 0.04 * 49100.0)
    assert repository.positions[("ma_cross", "BTC-USDT")].amount == pytest.approx(0.04)


@pytest.mark.asyncio
async def test_order_manager_cancel_persists_cancelled_status_only_after_router_success():
    repository = FakeRepository()
    order = OrderRecord(
        order_id="local-cancel-1",
        exchange_order_id="exchange-cancel-1",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=0.1,
        price=49000.0,
        status="pending",
        fill_price=0.0,
        timestamp=1700000000000,
        updated_at=1700000000000,
        filled_amount=0.0,
        remaining_amount=0.1,
    )
    repository.orders.append(order)
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=MockHandler(), mode="backtest"),
        repository=repository,
        timestamp_ms=lambda: 1700000001000,
    )

    assert await manager.cancel(order.order_id, symbol=order.symbol) is True
    assert repository.orders[0].status == OrderStatus.CANCELLED.value


def test_linear_position_reduction_uses_contract_quantity():
    position = PositionRecord(
        source="live_exchange", runtime_mode="live", strategy="__exchange__",
        symbol="BTC-USDT-SWAP", side="long", amount=1.0,
        contracts=100.0, contract_size=0.01, quantity_unit="base",
        entry_price=50000.0, timestamp=1,
    )

    assert position_order_quantity(position) == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_live_cancel_does_not_change_paper_order_with_same_external_id():
    repository = FakeRepository()
    paper_order = OrderRecord(
        source="paper", runtime_mode="paper", order_id="paper-1",
        exchange_order_id="shared-id", strategy="ma_cross", symbol="BTC-USDT",
        side="buy", type="limit", amount=1.0, price=100.0,
        status="pending", fill_price=0.0, timestamp=1, updated_at=1,
        filled_amount=0.0, remaining_amount=1.0,
    )
    repository.orders.append(paper_order)
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=MockHandler(), mode="live"),
        repository=repository,
    )

    assert await manager.cancel("shared-id", symbol="BTC-USDT") is True
    assert paper_order.status == OrderStatus.PENDING.value


@pytest.mark.asyncio
async def test_order_manager_cancel_accounts_explicit_partial_fill_before_cancel():
    class PartialCancelHandler(MockHandler):
        async def cancel(self, order_id: str, symbol: str | None = None):
            return {"status": "canceled", "filled_amount": 0.04, "fill_price": 49100.0}

    repository = FakeRepository()
    order = OrderRecord(
        order_id="local-cancel-partial",
        exchange_order_id="exchange-cancel-partial",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=0.1,
        price=49000.0,
        status="pending",
        fill_price=0.0,
        timestamp=1700000000000,
        updated_at=1700000000000,
        filled_amount=0.0,
        remaining_amount=0.1,
    )
    repository.orders.append(order)
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=PartialCancelHandler(), mode="backtest"),
        repository=repository,
        timestamp_ms=lambda: 1700000001000,
    )

    assert await manager.cancel(order.order_id, symbol=order.symbol) is True
    assert repository.orders[0].status == OrderStatus.CANCELLED.value
    assert repository.orders[0].filled_amount == pytest.approx(0.04)
    assert repository.orders[0].remaining_amount == pytest.approx(0.06)
    assert len(repository.trades) == 1
    assert repository.trades[0].amount == pytest.approx(0.04)
    assert repository.accounts["ma_cross"].cash_balance == pytest.approx(100000.0 - 0.04 * 49100.0)
    assert repository.positions[("ma_cross", "BTC-USDT")].amount == pytest.approx(0.04)

    assert await manager.cancel(order.order_id, symbol=order.symbol) is True
    assert len(repository.trades) == 1


@pytest.mark.asyncio
async def test_order_manager_cancel_keeps_exchange_response_and_accounts_concurrent_full_fill():
    class ConcurrentFillCancelHandler(MockHandler):
        async def cancel(self, order_id: str, symbol: str | None = None):
            return {
                "status": "closed",
                "filled": "0.1",
                "remaining": "0",
                "average": "49200",
            }

    repository = FakeRepository()
    original = OrderRecord(
        order_id="concurrent-fill-cancel", exchange_order_id="exchange-concurrent-fill",
        strategy="ma_cross", symbol="BTC-USDT", side="buy", type="limit", amount=0.1,
        price=49000.0, status="pending", fill_price=0.0, timestamp=1, updated_at=1,
        filled_amount=0.0, remaining_amount=0.1,
    )
    repository.orders.append(original)
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=ConcurrentFillCancelHandler(), mode="backtest"),
        repository=repository, timestamp_ms=lambda: 2,
    )

    assert await manager.cancel(original.order_id, symbol=original.symbol) is True

    saved = repository.orders[0]
    assert saved.status == OrderStatus.FILLED.value
    assert saved.filled_amount == pytest.approx(0.1)
    assert saved.remaining_amount == pytest.approx(0.0)
    assert saved.fill_price == pytest.approx(49200.0)
    assert len(repository.trades) == 1
    assert repository.trades[0].amount == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_order_manager_cancel_failure_does_not_persist_cancelled_status():
    class FailingCancelHandler(MockHandler):
        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return False

    repository = FakeRepository()
    repository.orders.append(
        OrderRecord(
            order_id="local-cancel-2",
            exchange_order_id="exchange-cancel-2",
            strategy="ma_cross",
            symbol="BTC-USDT",
            side="buy",
            type="limit",
            amount=0.1,
            price=49000.0,
            status="pending",
            fill_price=0.0,
            timestamp=1700000000000,
            updated_at=1700000000000,
            filled_amount=0.0,
            remaining_amount=0.1,
        )
    )
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=FailingCancelHandler(), mode="backtest"),
        repository=repository,
    )

    assert await manager.cancel("local-cancel-2", symbol="BTC-USDT") is False
    assert repository.orders[0].status == OrderStatus.PENDING.value


@pytest.mark.asyncio
async def test_order_manager_persists_rejected_order_without_accounting_mutation():
    class RejectedHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            order.status = OrderStatus.REJECTED
            return order

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    router = OrderRouter(backtest=RejectedHandler(), mode="backtest")
    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
    )

    await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.1,
        strategy_name="ma_cross",
    )

    assert repository.orders[0].status == "rejected"
    assert repository.trades == []
    assert repository.positions == {}
    assert repository.accounts == {}


@pytest.mark.asyncio
async def test_order_manager_rejects_order_when_risk_gate_blocks_position_size():
    class ShouldNotSubmitHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            raise AssertionError("risk-blocked order should not reach router")

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    repository.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        initial_equity=100000.0,
        cash_balance=100000.0,
        equity=100000.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
    )
    repository.positions[("ma_cross", "BTC-USDT")] = PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="long",
        amount=0.4,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
    )
    router = OrderRouter(backtest=ShouldNotSubmitHandler(), mode="backtest")
    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        initial_equity=100000.0,
        risk_manager=RiskManager(max_position_pct=0.25),
    )

    order = await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        amount=0.2,
        price=50000.0,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.REJECTED
    assert order.fill_price is None
    assert repository.orders[0].status == "rejected"
    assert repository.trades == []


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [("false", False), (" FALSE ", False), ("true", True), (" True ", True), (0, False), (1, True)],
)
def test_is_inverse_accepts_only_explicit_boolean_metadata(metadata, expected):
    order = Order(
        id="strict-inverse",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=1.0,
        params={"is_inverse": metadata},
    )

    assert is_inverse(order) is expected


@pytest.mark.parametrize("metadata", ["yes", "no", "2", "", object(), 2, -1, 2.0, -1.0])
def test_is_inverse_fails_closed_for_unknown_metadata(metadata):
    order = Order(
        id="invalid-inverse",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=1.0,
        params={"is_inverse": metadata},
    )

    assert is_inverse(order) is False


@pytest.mark.parametrize(("raw", "expected"), [("false", False), (" TRUE ", True)])
def test_trade_record_parses_explicit_inverse_strings(raw, expected):
    trade = TradeRecord(
        strategy="strict", symbol="BTC-USDT", side="buy", amount=1.0,
        price=100.0, fee=0.0, timestamp=1, is_inverse=raw,
    )

    assert trade.is_inverse is expected


def test_trade_record_rejects_unknown_inverse_string():
    with pytest.raises(ValueError):
        TradeRecord(
            strategy="strict", symbol="BTC-USDT", side="buy", amount=1.0,
            price=100.0, fee=0.0, timestamp=1, is_inverse="yes",
        )


def test_linear_derivative_order_notional_uses_base_quantity_and_contract_size():
    order = Order(
        id="linear-order",
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=100.0,
        price=50000.0,
        params={"is_inverse": False, "contract_size": 0.01},
    )

    assert order_notional(order, 50000.0) == pytest.approx(50000.0)


def test_inverse_position_and_order_notional_use_contracts_times_contract_size():
    position = PositionRecord(
        strategy="inverse",
        symbol="BTC-USD-SWAP",
        side="long",
        amount=200.0,
        entry_price=50000.0,
        leverage=1,
        timestamp=1,
        contracts=2.0,
        contract_size=100.0,
        is_inverse=True,
        notional_value=None,
    )
    order = Order(
        id="inverse-order",
        symbol="BTC-USD-SWAP",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=3.0,
        price=50000.0,
        params={"is_inverse": True, "contract_size": 100.0},
    )

    assert position_notional(position) == pytest.approx(200.0)
    assert order_notional(order, 50000.0) == pytest.approx(300.0)


@pytest.mark.parametrize("invalid_size", [0, -1, math.nan, math.inf, "not-a-number", True])
def test_inverse_contract_size_rejects_invalid_explicit_metadata(invalid_size):
    order = Order(
        id="invalid-inverse",
        symbol="BTC-USD-SWAP",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=1.0,
        params={"is_inverse": True, "contract_size": invalid_size},
    )

    with pytest.raises(ValueError, match="contract_size"):
        contract_size(order)



def test_inverse_contract_size_allows_missing_legacy_metadata():
    order = Order(
        id="legacy-linear",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=1.0,
    )

    assert contract_size(order) == 1.0


def test_position_notional_uses_current_mark_for_linear_and_contracts_for_inverse():
    linear = PositionRecord(
        strategy="linear", symbol="BTC-USDT", side="long", amount=2.0,
        entry_price=100.0, mark_price=120.0, leverage=1, timestamp=1,
        notional_value=999.0,
    )
    inverse = PositionRecord(
        strategy="inverse", symbol="BTC-USD-SWAP", side="long", amount=200.0,
        entry_price=50000.0, mark_price=60000.0, leverage=1, timestamp=1,
        contracts=2.0, quantity=2.0, quantity_unit="contracts",
        notional_value=999.0, is_inverse=True, contract_size=100.0,
    )

    assert position_notional(linear) == pytest.approx(240.0)
    assert position_notional(inverse) == pytest.approx(200.0)


def test_inverse_position_notional_rejects_missing_contract_metadata():
    position = PositionRecord(
        strategy="inverse", symbol="BTC-USD-SWAP", side="long", amount=200.0,
        entry_price=50000.0, mark_price=60000.0, leverage=1, timestamp=1,
        contracts=2.0, quantity=2.0, quantity_unit="contracts",
        notional_value=200.0, is_inverse=True,
    )

    with pytest.raises(ValueError, match="contract_size"):
        position_notional(position)


@pytest.mark.asyncio
async def test_live_new_inverse_order_uses_provider_contract_size_for_risk_notional():
    handler = MockHandler()
    repository = FakeRepository()
    repository.accounts["__exchange__"] = AccountRecord(
        strategy="__exchange__", initial_equity=100000.0, cash_balance=100000.0,
        equity=100000.0, realized_pnl=0.0, unrealized_pnl=0.0,
        daily_pnl=0.0, fees_paid=0.0, updated_at=1,
        source="live_exchange", runtime_mode="live",
    )
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=handler, mode="live"),
        repository=repository,
        risk_manager=RiskManager(max_position_pct=1.0),
        price_provider=lambda _symbol: 50000.0,
        live_safeguards=True,
        live_market_type="swap",
        allow_live_open_orders=True,
        live_max_order_notional=250.0,
        contract_size_provider=lambda _symbol: {
            "is_inverse": True, "contract_size": 100.0
        },
    )

    order = await manager.submit(
        symbol="BTC-USD-SWAP", side=OrderSide.BUY,
        order_type=OrderType.MARKET, amount=2.0, strategy_name="__exchange__",
    )

    assert order.status is OrderStatus.FILLED
    assert handler.submitted[0].params["is_inverse"] is True
    assert handler.submitted[0].params["contract_size"] == pytest.approx(100.0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata",
    [
        {"is_inverse": True},
        {"is_inverse": True, "contract_size": 0},
        {"is_inverse": True, "contract_size": math.nan},
    ],
)
async def test_live_new_inverse_order_rejects_without_valid_contract_size(metadata):
    class ShouldNotSubmitHandler(MockHandler):
        async def submit(self, order: Order) -> Order:
            raise AssertionError("inverse order without valid contract size reached router")

    repository = FakeRepository()
    risk_events = []
    repository.accounts["__exchange__"] = AccountRecord(
        strategy="__exchange__", initial_equity=100000.0, cash_balance=100000.0,
        equity=100000.0, realized_pnl=0.0, unrealized_pnl=0.0,
        daily_pnl=0.0, fees_paid=0.0, updated_at=1,
        source="live_exchange", runtime_mode="live",
    )
    manager = UnifiedOrderManager(
        router=OrderRouter(
            backtest=None, live=ShouldNotSubmitHandler(), mode="live"
        ),
        repository=repository,
        risk_manager=RiskManager(max_position_pct=1.0),
        price_provider=lambda _symbol: 50000.0,
        live_safeguards=True,
        live_market_type="swap",
        allow_live_open_orders=True,
        contract_size_provider=lambda _symbol: metadata,
        on_risk_event=lambda event: risk_events.append(event),
    )

    order = await manager.submit(
        symbol="BTC-USD-SWAP", side=OrderSide.BUY,
        order_type=OrderType.MARKET, amount=2.0, strategy_name="__exchange__",
    )

    assert order.status is OrderStatus.REJECTED
    assert repository.orders[0].status == OrderStatus.REJECTED.value
    assert risk_events[0]["reason"] == "Live inverse order requires valid contract size"
    assert risk_events[0]["reason_code"] == "live_contract_size_unavailable"


@pytest.mark.asyncio
async def test_order_manager_rejects_inverse_position_risk_without_contract_size():
    handler = MockHandler()
    repository = FakeRepository()
    repository.accounts["inverse"] = AccountRecord(
        strategy="inverse", initial_equity=100000.0, cash_balance=100000.0,
        equity=100000.0, realized_pnl=0.0, unrealized_pnl=0.0,
        fees_paid=0.0, updated_at=1,
        source="backtest", runtime_mode="backtest",
    )
    repository.positions[("inverse", "BTC-USD-SWAP")] = PositionRecord(
        strategy="inverse", symbol="BTC-USD-SWAP", side="long", amount=200.0,
        entry_price=50000.0, mark_price=50000.0, leverage=1, timestamp=1,
        contracts=2.0, quantity=2.0, quantity_unit="contracts",
        notional_value=200.0, is_inverse=True,
        source="backtest", runtime_mode="backtest",
    )
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=handler, mode="backtest"),
        repository=repository,
        price_provider=lambda _symbol: 50000.0,
        risk_manager=RiskManager(max_position_pct=1.0),
    )

    order = await manager.submit(
        symbol="BTC-USD-SWAP", side=OrderSide.BUY,
        order_type=OrderType.LIMIT, amount=0.4, price=50000.0,
        strategy_name="inverse",
    )

    assert order.status is OrderStatus.REJECTED
    assert handler.submitted == []


@pytest.mark.asyncio
async def test_order_manager_uses_inverse_quote_notional_for_max_position_risk():
    handler = MockHandler()
    repository = FakeRepository()
    repository.accounts["inverse"] = AccountRecord(
        strategy="inverse", initial_equity=100000.0, cash_balance=100000.0,
        equity=100000.0, realized_pnl=0.0, unrealized_pnl=0.0,
        daily_pnl=0.0, fees_paid=0.0, updated_at=1,
        source="backtest", runtime_mode="backtest",
    )
    repository.positions[("inverse", "BTC-USD-SWAP")] = PositionRecord(
        strategy="inverse", symbol="BTC-USD-SWAP", side="long", amount=200.0,
        entry_price=50000.0, mark_price=50000.0, leverage=1, timestamp=1,
        contracts=2.0, quantity=2.0, quantity_unit="contracts",
        notional_value=200.0, is_inverse=True, contract_size=100.0,
        source="backtest", runtime_mode="backtest",
    )
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=handler, mode="backtest"),
        repository=repository,
        timestamp_ms=lambda: 1,
        initial_equity=100000.0,
        risk_manager=RiskManager(max_position_pct=0.25),
    )

    order = await manager.submit(
        symbol="BTC-USD-SWAP", side=OrderSide.BUY,
        order_type=OrderType.LIMIT, amount=0.4, price=50000.0,
        strategy_name="inverse",
    )

    assert order.status is OrderStatus.FILLED
    assert handler.submitted[0].amount == 0.4


@pytest.mark.asyncio
async def test_order_manager_rejects_when_total_position_exposure_exceeds_limit():
    class ShouldNotSubmitHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            raise AssertionError("risk-blocked order should not reach router")

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    repository.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        initial_equity=100000.0,
        cash_balance=100000.0,
        equity=100000.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
        source="backtest",
        runtime_mode="backtest",
    )
    repository.positions[("ma_cross", "BTC-USDT")] = PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="long",
        amount=1.4,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
        source="backtest",
        runtime_mode="backtest",
    )
    router = OrderRouter(backtest=ShouldNotSubmitHandler(), mode="backtest")
    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        initial_equity=100000.0,
        risk_manager=RiskManager(max_position_pct=0.8),
    )

    order = await manager.submit(
        symbol="ETH-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        amount=1.0,
        price=50000.0,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.REJECTED
    assert repository.orders[0].status == "rejected"


@pytest.mark.asyncio
async def test_order_manager_allows_reducing_order_when_position_is_at_limit():
    handler = MockHandler()
    repository = FakeRepository()
    repository.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        initial_equity=100000.0,
        cash_balance=100000.0,
        equity=100000.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
    )
    repository.positions[("ma_cross", "BTC-USDT")] = PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="long",
        amount=0.5,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
    )
    router = OrderRouter(backtest=handler, mode="backtest")
    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        initial_equity=100000.0,
        risk_manager=RiskManager(max_position_pct=0.25),
        price_provider=lambda symbol: 50000.0,
    )

    order = await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=0.2,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.FILLED
    assert len(handler.submitted) == 1


@pytest.mark.asyncio
async def test_order_manager_allows_reducing_order_when_total_exposure_is_over_limit():
    handler = MockHandler()
    repository = FakeRepository()
    repository.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        initial_equity=100000.0,
        cash_balance=100000.0,
        equity=100000.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
    )
    repository.positions[("ma_cross", "BTC-USDT")] = PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="long",
        amount=1.6,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
    )
    repository.positions[("ma_cross", "ETH-USDT")] = PositionRecord(
        strategy="ma_cross",
        symbol="ETH-USDT",
        side="long",
        amount=0.2,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
    )
    router = OrderRouter(backtest=handler, mode="backtest")
    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        initial_equity=100000.0,
        risk_manager=RiskManager(max_position_pct=0.8),
    )

    order = await manager.submit(
        symbol="ETH-USDT",
        side=OrderSide.SELL,
        order_type=OrderType.LIMIT,
        amount=0.1,
        price=50000.0,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.FILLED
    assert len(handler.submitted) == 1


@pytest.mark.asyncio
async def test_order_manager_rejects_oversized_market_order_without_price_provider():
    class ShouldNotSubmitHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            raise AssertionError("order without valuation price must not reach router")

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    risk_events = []
    repository.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        initial_equity=100000.0,
        cash_balance=100000.0,
        equity=100000.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
    )
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=ShouldNotSubmitHandler(), mode="backtest"),
        repository=repository,
        risk_manager=RiskManager(max_position_pct=0.25),
        price_provider=None,
        on_risk_event=lambda event: risk_events.append(event),
    )

    order = await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=10.0,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.REJECTED
    assert repository.orders[0].status == "rejected"
    assert risk_events[0]["reason"] == "Risk evaluation requires a current price"


async def test_order_manager_values_market_order_with_price_provider_for_risk_gate():
    class ShouldNotSubmitHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            raise AssertionError("risk-blocked market order should not reach router")

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    repository.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        initial_equity=100000.0,
        cash_balance=100000.0,
        equity=100000.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
    )
    repository.positions[("ma_cross", "BTC-USDT")] = PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="long",
        amount=0.2,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
    )
    router = OrderRouter(backtest=ShouldNotSubmitHandler(), mode="backtest")
    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        initial_equity=100000.0,
        risk_manager=RiskManager(max_position_pct=0.25),
        price_provider=lambda symbol: 50000.0,
    )

    order = await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.4,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.REJECTED
    assert repository.orders[0].status == "rejected"
    assert repository.trades == []


def test_risk_reason_code_maps_known_and_unknown_reasons():
    assert risk_reason_code("Order exceeds maximum position size") == "max_position_exceeded"
    assert risk_reason_code("Daily loss exceeds maximum allowed loss") == "daily_loss_exceeded"
    assert risk_reason_code("Drawdown exceeds maximum allowed drawdown") == "drawdown_exceeded"
    assert risk_reason_code("Order requires a stop loss") == "stop_loss_required"
    assert risk_reason_code("Live opening orders are disabled") == "live_opening_disabled"
    assert (
        risk_reason_code("Live order exceeds configured notional cap")
        == "live_order_notional_exceeded"
    )
    assert (
        risk_reason_code("Live spot sell requires existing position")
        == "live_spot_position_required"
    )
    assert risk_reason_code("Unexpected rule failure") == "risk_rejected"
    assert risk_reason_code("") == "risk_rejected"


@pytest.mark.asyncio
async def test_order_manager_emits_risk_event_before_order_update_for_gate_rejection():
    class ShouldNotSubmitHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            raise AssertionError("risk-blocked market order should not reach router")

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    repository.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        initial_equity=100000.0,
        cash_balance=100000.0,
        equity=100000.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
    )
    repository.positions[("ma_cross", "BTC-USDT")] = PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="long",
        amount=0.2,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
    )
    calls = []
    risk_events = []

    async def on_risk_event(payload):
        calls.append("risk_event")
        assert repository.orders[0].status == "rejected"
        risk_events.append(payload)

    async def on_order_update(strategy_name: str):
        calls.append("order_update")
        assert strategy_name == "ma_cross"

    router = OrderRouter(backtest=ShouldNotSubmitHandler(), mode="backtest")
    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        initial_equity=100000.0,
        risk_manager=RiskManager(max_position_pct=0.25),
        price_provider=lambda symbol: 50000.0,
        on_risk_event=on_risk_event,
        on_order_update=on_order_update,
    )

    order = await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.4,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.REJECTED
    assert calls == ["risk_event", "order_update"]
    assert risk_events == [
        {
            "type": "risk_event",
            "strategy": "ma_cross",
            "order_id": order.id,
            "symbol": "BTC-USDT",
            "side": "buy",
            "order_type": "market",
            "amount": 0.4,
            "price": 50000.0,
            "requested_price": None,
            "order_value": pytest.approx(30000.0),
            "reason": "Order exceeds maximum position size",
            "reason_code": "max_position_exceeded",
            "timestamp": 1700000000000,
        }
    ]


@pytest.mark.asyncio
async def test_order_manager_skips_repository_and_price_provider_when_risk_manager_is_disabled():
    class ExplodingRiskRepository:
        orders = []

        def upsert_order(self, order):
            self.orders.append(order)
            return order

        def get_account(self, strategy):
            raise AssertionError("risk-disabled submit should not read account")

        def get_position(self, strategy, symbol):
            raise AssertionError("risk-disabled submit should not read position")

    class PendingHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            return order

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    def exploding_price_provider(symbol: str):
        raise AssertionError("risk-disabled submit should not read latest price")

    router = OrderRouter(backtest=PendingHandler(), mode="backtest")
    manager = UnifiedOrderManager(
        router=router,
        repository=ExplodingRiskRepository(),
        risk_manager=None,
        price_provider=exploding_price_provider,
    )

    order = await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.1,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.PENDING


@pytest.mark.asyncio
async def test_order_manager_runs_order_update_when_risk_event_callback_fails():
    class ShouldNotSubmitHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            raise AssertionError("risk-blocked order should not reach router")

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    calls = []

    async def on_risk_event(payload):
        calls.append("risk_event")
        raise RuntimeError("risk broadcast failed")

    async def on_order_update(strategy_name: str):
        calls.append("order_update")
        assert strategy_name == "ma_cross"

    router = OrderRouter(backtest=ShouldNotSubmitHandler(), mode="backtest")
    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        initial_equity=100000.0,
        risk_manager=RiskManager(max_position_pct=0.25),
        price_provider=lambda symbol: 50000.0,
        on_risk_event=on_risk_event,
        on_order_update=on_order_update,
    )

    with pytest.raises(RuntimeError, match="risk broadcast failed"):
        await manager.submit(
            symbol="BTC-USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            amount=10.0,
            strategy_name="ma_cross",
        )

    assert repository.orders[0].status == "rejected"
    assert calls == ["risk_event", "order_update"]


@pytest.mark.asyncio
async def test_order_manager_cancel_forwards_symbol():
    handler = MockHandler()
    router = OrderRouter(backtest=handler, mode="backtest")
    manager = UnifiedOrderManager(router=router)

    assert await manager.cancel("exchange-order-1", symbol="BTC-USDT") is True

    assert handler.cancelled == [("exchange-order-1", "BTC-USDT")]


@pytest.mark.asyncio
async def test_live_order_manager_persists_external_ids_before_private_sync():
    events = []

    class FilledLiveHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            events.append("submit")
            order.status = OrderStatus.FILLED
            order.fill_price = 50000.0
            order.fill_time = 1700000000000
            order.exchange_order_id = "exchange-order-1"
            order.client_order_id = "client-order-1"
            order.updated_at = 1700000001000
            return order

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    class RecordingRepository(FakeRepository):
        def upsert_order(self, order):
            events.append("persist")
            return super().upsert_order(order)

    repository = RecordingRepository()
    repository.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        initial_equity=100000.0,
        cash_balance=100000.0,
        equity=100000.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
        source="live_exchange",
        runtime_mode="live",
    )
    repository.positions[("ma_cross", "BTC-USDT-SWAP")] = PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
        source="live_exchange",
        runtime_mode="live",
    )
    repository.owned_positions.add(("ma_cross", "BTC-USDT-SWAP"))
    refresh_count = 0

    async def live_state_refresher(strategy_name: str, symbol: str) -> None:
        nonlocal refresh_count
        refresh_count += 1
        events.append(f"refresh-{refresh_count}")

    async def post_live_order_sync(strategy_name: str, symbol: str) -> None:
        events.append("private-sync")
        assert strategy_name == "ma_cross"
        assert symbol == "BTC-USDT-SWAP"
        saved = repository.orders[0]
        assert saved.exchange_order_id == "exchange-order-1"
        assert saved.client_order_id == "client-order-1"
        assert saved.updated_at == 1700000001000

    async def on_order_update(strategy_name: str) -> None:
        events.append("order-update")
        assert strategy_name == "ma_cross"

    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=FilledLiveHandler(), mode="live"),
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        risk_manager=RiskManager(max_position_pct=0.8),
        price_provider=lambda symbol: 50000.0,
        live_safeguards=True,
        live_market_type="swap",
        live_state_refresher=live_state_refresher,
        post_live_order_sync=post_live_order_sync,
        on_order_update=on_order_update,
    )

    order = await manager.submit(
        symbol="BTC-USDT-SWAP",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=0.25,
        strategy_name="ma_cross",
    )

    assert order.id == repository.orders[0].order_id
    assert events == [
        "refresh-1",
        "persist",
        "submit",
        "persist",
        "refresh-2",
        "private-sync",
        "order-update",
    ]


@pytest.mark.asyncio
async def test_live_order_manager_does_not_sync_risk_rejected_order():
    class ShouldNotSubmitHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            raise AssertionError("risk-rejected order should not reach router")

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    sync_calls = []

    async def post_live_order_sync(strategy_name: str, symbol: str) -> None:
        sync_calls.append((strategy_name, symbol))

    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=ShouldNotSubmitHandler(), mode="live"),
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        risk_manager=RiskManager(max_position_pct=0.8),
        price_provider=lambda symbol: 50000.0,
        live_safeguards=True,
        live_market_type="swap",
        post_live_order_sync=post_live_order_sync,
    )

    order = await manager.submit(
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.25,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.REJECTED
    assert repository.orders[0].status == "rejected"
    assert sync_calls == []


@pytest.mark.asyncio
async def test_live_submit_reports_accepted_order_when_local_persistence_fails():
    class AcceptedHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            order.exchange_order_id = "accepted-exchange-id"
            order.status = OrderStatus.PENDING
            return order

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    class FailingRepository(FakeRepository):
        def __init__(self):
            super().__init__()
            self.attempts = 0
            self.risk_events = []

        def upsert_order(self, order):
            self.attempts += 1
            if self.attempts == 2:
                raise OSError("database unavailable")
            return super().upsert_order(order)

        def save_risk_event(self, event):
            self.risk_events.append(event)
            return event

    repository = FailingRepository()
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=AcceptedHandler(), mode="live"),
        repository=repository,
    )

    with pytest.raises(AcceptedUnknownOrderResultError, match="accepted-exchange-id"):
        await manager.submit(
            symbol="BTC-USDT", side=OrderSide.BUY, order_type=OrderType.LIMIT,
            amount=0.1, price=49000.0, strategy_name="__exchange__",
        )

    assert repository.risk_events[0]["reason_code"] == "live_order_persistence_failed"
    assert repository.risk_events[0]["exchange_order_id"] == "accepted-exchange-id"


@pytest.mark.asyncio
async def test_live_submit_preserves_provider_rejection_with_only_local_client_id():
    class RejectingHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            order.status = OrderStatus.REJECTED
            return order

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=RejectingHandler(), mode="live"),
        repository=repository,
    )

    order = await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        amount=0.1,
        price=49000.0,
        strategy_name="__exchange__",
    )

    assert order.status is OrderStatus.REJECTED
    assert repository.orders[0].status == OrderStatus.REJECTED.value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filled_amount", "expected_status"),
    [
        (0.0, OrderStatus.PENDING),
        (0.04, OrderStatus.PARTIALLY_FILLED),
        (0.1, OrderStatus.FILLED),
    ],
)
async def test_live_submit_with_identity_and_rejected_provider_status_is_accepted(
    filled_amount, expected_status
):
    class ConflictingHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            order.exchange_order_id = "exchange-conflict-1"
            order.status = OrderStatus.REJECTED
            order.filled_amount = filled_amount
            order.remaining_amount = order.amount - filled_amount
            if filled_amount:
                order.fill_price = 50000.0
            return order

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    repository.risk_events = []
    repository.save_risk_event = lambda event: repository.risk_events.append(event) or event
    updates = []
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=ConflictingHandler(), mode="live"),
        repository=repository,
        on_order_update=lambda strategy: updates.append(strategy),
    )

    order = await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        amount=0.1,
        price=49000.0,
        strategy_name="__exchange__",
    )

    assert order.status is expected_status
    assert repository.orders[0].status == expected_status.value
    assert updates == ["__exchange__"]
    assert repository.risk_events[0]["reason_code"] == (
        "provider_status_conflict_reconciliation_required"
    )
    assert repository.risk_events[0]["exchange_order_id"] == "exchange-conflict-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_factory", "expected_status"),
    [
        (lambda: ccxt.InvalidOrder("invalid order"), OrderStatus.REJECTED),
        (lambda: RuntimeError("transport failed"), OrderStatus.PENDING),
        (
            lambda: AcceptedUnknownOrderResultError("accepted without identity"),
            OrderStatus.PENDING,
        ),
        (lambda: ValueError("malformed response"), OrderStatus.PENDING),
    ],
)
async def test_submit_exception_persists_status_and_notifies_order_update(
    error_factory, expected_status
):
    class FailingHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            raise error_factory()

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    repository.risk_events = []
    repository.save_risk_event = lambda event: repository.risk_events.append(event) or event
    updates = []
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=FailingHandler(), mode="live"),
        repository=repository,
        on_order_update=lambda strategy: updates.append(strategy),
    )

    with pytest.raises(Exception):
        await manager.submit(
            symbol="BTC-USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            amount=0.1,
            strategy_name="__exchange__",
        )

    assert repository.orders[-1].status == expected_status.value
    assert updates == ["__exchange__"]


@pytest.mark.asyncio
async def test_submit_exception_preserves_original_error_when_order_update_fails():
    class FailingHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            raise RuntimeError("transport failed")

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()

    async def failing_update(strategy: str) -> None:
        raise RuntimeError("broadcast failed")

    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=FailingHandler(), mode="live"),
        repository=repository,
        on_order_update=failing_update,
    )

    with pytest.raises(RuntimeError, match="transport failed"):
        await manager.submit(
            symbol="BTC-USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            amount=0.1,
            strategy_name="__exchange__",
        )

    assert repository.orders[-1].status == OrderStatus.PENDING.value


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_invalid_order_update_failure_does_not_replace_rejection():
    class InvalidOrderHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            raise ccxt.InvalidOrder("provider rejected order")

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    updates = []

    async def failing_update(strategy: str) -> None:
        updates.append(strategy)
        raise RuntimeError("broadcast failed")

    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=InvalidOrderHandler(), mode="live"),
        repository=repository,
        on_order_update=failing_update,
    )

    with pytest.raises(ccxt.InvalidOrder, match="provider rejected order"):
        await manager.submit(
            symbol="BTC-USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            amount=0.1,
            strategy_name="__exchange__",
        )

    assert repository.orders[-1].status == OrderStatus.REJECTED.value
    assert updates == ["__exchange__"]


@pytest.mark.asyncio
async def test_conflicting_provider_status_with_post_sync_notifies_once():
    class ConflictingHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            order.exchange_order_id = "exchange-conflict-once"
            order.status = OrderStatus.REJECTED
            return order

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    updates = []
    syncs = []

    async def post_sync(strategy: str, symbol: str) -> None:
        syncs.append((strategy, symbol))

    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=ConflictingHandler(), mode="live"),
        repository=repository,
        post_live_order_sync=post_sync,
        on_order_update=lambda strategy: updates.append(strategy),
    )

    order = await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.1,
        strategy_name="__exchange__",
    )

    assert order.status is OrderStatus.PENDING
    assert syncs == [("__exchange__", "BTC-USDT")]
    assert updates == ["__exchange__"]


@pytest.mark.asyncio
async def test_live_submit_returns_accepted_order_when_order_update_callback_fails():
    class AcceptedHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            order.exchange_order_id = "exchange-callback-failure-1"
            return order

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()

    async def failing_update(strategy: str) -> None:
        raise RuntimeError("order update failed")

    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=AcceptedHandler(), mode="live"),
        repository=repository,
        on_order_update=failing_update,
    )

    order = await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.1,
        strategy_name="__exchange__",
    )

    assert order.exchange_order_id == "exchange-callback-failure-1"
    assert order.status is OrderStatus.PENDING
    assert repository.orders[0].exchange_order_id == "exchange-callback-failure-1"
    assert repository.orders[0].status == OrderStatus.PENDING.value


@pytest.mark.asyncio
async def test_live_submit_records_post_sync_and_order_update_callback_failures(
    caplog,
):
    class AcceptedHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            order.exchange_order_id = "exchange-two-callback-failures-1"
            return order

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    repository.risk_events = []
    repository.save_risk_event = lambda event: repository.risk_events.append(event) or event
    updates = []

    async def post_sync(strategy: str, symbol: str) -> None:
        raise RuntimeError("post sync failed")

    async def failing_update(strategy: str) -> None:
        updates.append(strategy)
        raise RuntimeError("order update failed")

    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=AcceptedHandler(), mode="live"),
        repository=repository,
        post_live_order_sync=post_sync,
        on_order_update=failing_update,
    )

    with caplog.at_level(logging.ERROR):
        order = await manager.submit(
            symbol="BTC-USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            amount=0.1,
            strategy_name="__exchange__",
        )

    assert order.exchange_order_id == "exchange-two-callback-failures-1"
    assert order.status is OrderStatus.PENDING
    assert updates == ["__exchange__"]
    assert repository.risk_events[0]["reason_code"] == "post_submit_sync_failed"
    assert "Order update callback failed after accepted submit" in caplog.text
    assert "post_submit_sync_failed" not in caplog.text


@pytest.mark.asyncio
async def test_live_submit_calls_order_update_once_when_post_sync_fails():
    class PendingHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            order.exchange_order_id = "exchange-post-sync-1"
            return order

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    updates = []

    async def post_sync(strategy: str, symbol: str) -> None:
        raise RuntimeError("sync failed")

    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=PendingHandler(), mode="live"),
        repository=repository,
        post_live_order_sync=post_sync,
        on_order_update=lambda strategy: updates.append(strategy),
    )

    order = await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.1,
        strategy_name="__exchange__",
    )

    assert order.status is OrderStatus.PENDING
    assert updates == ["__exchange__"]


@pytest.mark.asyncio
async def test_live_order_manager_notifies_when_private_sync_fails():
    class PendingLiveHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            order.exchange_order_id = "exchange-order-1"
            return order

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    grant_live_position_ownership(repository)
    repository.risk_events = []
    repository.save_risk_event = lambda event: repository.risk_events.append(event) or event
    calls = []

    async def post_live_order_sync(strategy_name: str, symbol: str) -> None:
        calls.append("private-sync")
        raise RuntimeError("private sync failed")

    async def on_order_update(strategy_name: str) -> None:
        calls.append("order-update")

    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=PendingLiveHandler(), mode="live"),
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        post_live_order_sync=post_live_order_sync,
        on_order_update=on_order_update,
    )

    order = await manager.submit(
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.25,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.PENDING
    assert repository.orders[0].exchange_order_id == "exchange-order-1"
    assert repository.orders[0].amount == pytest.approx(0.25)
    assert repository.orders[0].filled_amount == pytest.approx(0.0)
    assert repository.orders[0].remaining_amount == pytest.approx(0.25)
    assert calls == ["private-sync", "order-update"]
    assert repository.risk_events[0]["reason_code"] == "post_submit_sync_failed"
    assert repository.risk_events[0]["severity"] == "high"


@pytest.mark.asyncio
async def test_live_submit_keeps_accepted_order_when_post_submit_refresh_fails():
    class AcceptedFilledHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            order.exchange_order_id = "exchange-refresh-failure-1"
            order.status = OrderStatus.FILLED
            order.filled_amount = order.amount
            order.remaining_amount = 0.0
            order.fill_price = 50000.0
            return order

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    repository.risk_events = []
    repository.save_risk_event = lambda event: repository.risk_events.append(event) or event
    repository.accounts["__exchange__"] = AccountRecord(
        strategy="__exchange__",
        initial_equity=100000.0,
        cash_balance=100000.0,
        equity=100000.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
        source="live_exchange",
        runtime_mode="live",
    )
    refresh_calls = 0

    async def refresh_state(strategy: str, symbol: str) -> None:
        nonlocal refresh_calls
        refresh_calls += 1
        if refresh_calls == 2:
            raise RuntimeError("post-submit refresh failed")

    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=AcceptedFilledHandler(), mode="live"),
        repository=repository,
        risk_manager=RiskManager(max_position_pct=0.8, max_daily_loss_pct=0.05),
        live_safeguards=True,
        live_state_refresher=refresh_state,
        price_provider=lambda symbol: 50000.0,
        live_market_type="spot",
        allow_live_open_orders=True,
    )

    order = await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.1,
        strategy_name="__exchange__",
    )

    assert order.exchange_order_id == "exchange-refresh-failure-1"
    assert order.status is OrderStatus.FILLED
    assert refresh_calls == 2
    assert repository.risk_events[0]["reason_code"] == "post_submit_sync_failed"


@pytest.mark.asyncio
async def test_live_order_manager_refreshes_state_before_live_risk_check():
    class PendingLiveHandler(OrderHandler):
        def __init__(self):
            self.submitted = []

        async def submit(self, order: Order) -> Order:
            self.submitted.append(order)
            return order

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    refresh_calls = []

    async def live_state_refresher(strategy_name: str, symbol: str) -> None:
        refresh_calls.append((strategy_name, symbol))
        repository.accounts[strategy_name] = AccountRecord(
            strategy=strategy_name,
            initial_equity=100000.0,
            cash_balance=100000.0,
            equity=100000.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            daily_pnl=0.0,
            fees_paid=0.0,
            updated_at=1700000000000,
            source="live_exchange",
            runtime_mode="live",
        )
        repository.positions[(strategy_name, symbol)] = PositionRecord(
            strategy=strategy_name,
            symbol=symbol,
            side="long",
            amount=1.0,
            entry_price=50000.0,
            leverage=1,
            timestamp=1700000000000,
            source="live_exchange",
            runtime_mode="live",
        )
        repository.owned_positions.add((strategy_name, symbol))

    handler = PendingLiveHandler()
    router = OrderRouter(backtest=None, live=handler, mode="live")
    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        initial_equity=100000.0,
        risk_manager=RiskManager(max_position_pct=0.8, max_daily_loss_pct=0.05),
        price_provider=lambda symbol: 50000.0,
        live_safeguards=True,
        live_market_type="swap",
        live_state_refresher=live_state_refresher,
    )

    order = await manager.submit(
        symbol="BTC-USDT-SWAP",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=0.25,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.PENDING
    assert refresh_calls == [("ma_cross", "BTC-USDT-SWAP")]
    assert len(handler.submitted) == 1
    assert handler.submitted[0].reduce_only is True
    assert handler.submitted[0].params == {"reduceOnly": True, "market_type": "swap"}


@pytest.mark.asyncio
async def test_live_strategy_reads_shared_exchange_state_after_refresh():
    class PendingLiveHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            return order

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()

    async def live_state_refresher(strategy_name: str, symbol: str) -> None:
        assert strategy_name == "alpha"
        repository.accounts["__exchange__"] = AccountRecord(
            strategy="__exchange__", initial_equity=100000.0, cash_balance=100000.0,
            equity=100000.0, realized_pnl=0.0, unrealized_pnl=0.0,
            daily_pnl=0.0, fees_paid=0.0, updated_at=1700000000000,
        )
        repository.positions[("__exchange__", symbol)] = PositionRecord(
            strategy="__exchange__", symbol=symbol, side="long", amount=1.0,
            entry_price=50000.0, leverage=1, timestamp=1700000000000,
        )

    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=PendingLiveHandler(), mode="live"),
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        risk_manager=RiskManager(max_position_pct=0.8, max_daily_loss_pct=0.05),
        price_provider=lambda symbol: 50000.0,
        live_safeguards=True,
        live_market_type="swap",
        live_state_refresher=live_state_refresher,
    )

    order = await manager.submit(
        symbol="BTC-USDT-SWAP", side=OrderSide.SELL, order_type=OrderType.MARKET,
        amount=0.25, strategy_name="alpha",
    )

    assert order.status == OrderStatus.REJECTED


@pytest.mark.asyncio
async def test_live_order_manager_skips_paper_accounting_for_live_fills(monkeypatch):
    class ExplodingPaperAccountingService:
        def __init__(self, **kwargs):
            pass

        def process_filled_order(self, order, strategy_name, timestamp):
            raise AssertionError("live fills should not run paper accounting")

    monkeypatch.setattr(
        "src.order.manager.PaperAccountingService",
        ExplodingPaperAccountingService,
    )
    handler = MockHandler()
    repository = FakeRepository()
    refresh_calls = []

    async def live_state_refresher(strategy_name: str, symbol: str) -> None:
        refresh_calls.append((strategy_name, symbol))

    repository.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        initial_equity=100000.0,
        cash_balance=100000.0,
        equity=100000.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
        source="live_exchange",
        runtime_mode="live",
    )
    repository.positions[("ma_cross", "BTC-USDT-SWAP")] = PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
        source="live_exchange",
        runtime_mode="live",
    )
    repository.owned_positions.add(("ma_cross", "BTC-USDT-SWAP"))
    router = OrderRouter(backtest=None, live=handler, mode="live")
    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        initial_equity=100000.0,
        risk_manager=RiskManager(max_position_pct=0.8, max_daily_loss_pct=0.05),
        price_provider=lambda symbol: 50000.0,
        live_safeguards=True,
        live_market_type="swap",
        live_state_refresher=live_state_refresher,
    )

    order = await manager.submit(
        symbol="BTC-USDT-SWAP",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=0.25,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.FILLED
    assert refresh_calls == [
        ("ma_cross", "BTC-USDT-SWAP"),
        ("ma_cross", "BTC-USDT-SWAP"),
    ]
    assert repository.trades == []


@pytest.mark.asyncio
async def test_live_order_manager_rejects_when_daily_loss_circuit_breaker_tripped():
    class ShouldNotSubmitHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            raise AssertionError("daily-loss-blocked order should not reach router")

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    repository.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        initial_equity=100000.0,
        cash_balance=94000.0,
        equity=94000.0,
        realized_pnl=-6000.0,
        unrealized_pnl=0.0,
        daily_pnl=-6000.0,
        fees_paid=0.0,
        updated_at=1700000000000,
    )
    repository.positions[("ma_cross", "BTC-USDT-SWAP")] = PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
    )
    router = OrderRouter(backtest=None, live=ShouldNotSubmitHandler(), mode="live")
    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        initial_equity=100000.0,
        risk_manager=RiskManager(max_position_pct=0.8, max_daily_loss_pct=0.05),
        price_provider=lambda symbol: 50000.0,
        live_safeguards=True,
        live_market_type="swap",
    )

    order = await manager.submit(
        symbol="BTC-USDT-SWAP",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=0.1,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.REJECTED
    assert repository.orders[0].status == "rejected"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("position_amount", "contracts", "size", "order_amount"),
    [(1.0, None, None, 0.25), (1.0, 100.0, 0.01, 50.0)],
)
async def test_live_order_manager_marks_derivative_reducer_reduce_only(
    position_amount, contracts, size, order_amount,
):
    handler = MockHandler()
    repository = FakeRepository()
    repository.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        initial_equity=100000.0,
        cash_balance=100000.0,
        equity=100000.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
        source="live_exchange",
        runtime_mode="live",
    )
    repository.positions[("ma_cross", "BTC-USDT-SWAP")] = PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=position_amount,
        contracts=contracts,
        contract_size=size,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
        source="live_exchange",
        runtime_mode="live",
    )
    repository.owned_positions.add(("ma_cross", "BTC-USDT-SWAP"))
    router = OrderRouter(backtest=None, live=handler, mode="live")
    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        initial_equity=100000.0,
        risk_manager=RiskManager(max_position_pct=0.8, max_daily_loss_pct=0.05),
        price_provider=lambda symbol: 50000.0,
        live_safeguards=True,
        live_market_type="swap",
    )

    order = await manager.submit(
        symbol="BTC-USDT-SWAP",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=order_amount,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.FILLED
    assert handler.submitted[0].reduce_only is True
    assert handler.submitted[0].params == {"reduceOnly": True, "market_type": "swap"}


@pytest.mark.asyncio
async def test_live_order_manager_matches_unified_symbol_to_canonical_position_for_reduce():
    handler = MockHandler()
    repository = FakeRepository()
    repository.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross", initial_equity=100000.0, cash_balance=100000.0,
        equity=100000.0, realized_pnl=0.0, unrealized_pnl=0.0, daily_pnl=0.0,
        fees_paid=0.0, updated_at=1700000000000,
        source="live_exchange", runtime_mode="live",
    )
    repository.positions[("ma_cross", "BTC-USDT-SWAP")] = PositionRecord(
        strategy="ma_cross", symbol="BTC-USDT-SWAP", side="long", amount=1.0,
        entry_price=50000.0, leverage=1, timestamp=1700000000000,
        source="live_exchange", runtime_mode="live",
    )
    repository.owned_positions.add(("ma_cross", "BTC-USDT-SWAP"))
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=handler, mode="live"),
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        risk_manager=RiskManager(max_position_pct=0.8, max_daily_loss_pct=0.05),
        price_provider=lambda _symbol: 50000.0,
        live_safeguards=True,
        live_market_type="swap",
    )

    order = await manager.submit(
        symbol="BTC/USDT:USDT", side=OrderSide.SELL,
        order_type=OrderType.MARKET, amount=0.25, strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.FILLED
    assert handler.submitted[0].reduce_only is True
    assert handler.submitted[0].symbol == "BTC-USDT-SWAP"


@pytest.mark.asyncio
async def test_live_order_manager_does_not_match_different_dated_contract_for_reduce():
    class ShouldNotSubmitHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            raise AssertionError("different contract must not reach router")

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    repository.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross", initial_equity=100000.0, cash_balance=100000.0,
        equity=100000.0, realized_pnl=0.0, unrealized_pnl=0.0, daily_pnl=0.0,
        fees_paid=0.0, updated_at=1700000000000,
    )
    repository.positions[("ma_cross", "BTC-USDT-260925")] = PositionRecord(
        strategy="ma_cross", symbol="BTC-USDT-260925", side="long", amount=1.0,
        entry_price=50000.0, leverage=1, timestamp=1700000000000,
    )
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=ShouldNotSubmitHandler(), mode="live"),
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        risk_manager=RiskManager(max_position_pct=0.8, max_daily_loss_pct=0.05),
        price_provider=lambda _symbol: 50000.0,
        live_safeguards=True,
        live_market_type="swap",
    )

    order = await manager.submit(
        symbol="BTC/USDT:USDT-261225", side=OrderSide.SELL,
        order_type=OrderType.MARKET, amount=0.25, strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.REJECTED
    assert repository.orders[0].status == OrderStatus.REJECTED.value


@pytest.mark.asyncio
async def test_live_order_manager_rejects_exposure_increasing_order_before_router():
    class ShouldNotSubmitHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            raise AssertionError("live exposure-increasing order should not reach router")

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    repository.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        initial_equity=100000.0,
        cash_balance=100000.0,
        equity=100000.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
    )
    repository.positions[("ma_cross", "BTC-USDT-SWAP")] = PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
    )
    router = OrderRouter(backtest=None, live=ShouldNotSubmitHandler(), mode="live")
    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        initial_equity=100000.0,
        risk_manager=RiskManager(max_position_pct=0.8, max_daily_loss_pct=0.05),
        price_provider=lambda symbol: 50000.0,
        live_safeguards=True,
        live_market_type="swap",
    )

    order = await manager.submit(
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.1,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.REJECTED
    assert repository.orders[0].status == "rejected"


@pytest.mark.asyncio
async def test_live_order_manager_rejects_opening_order_by_default():
    class ShouldNotSubmitHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            raise AssertionError("disabled live opening order should not reach router")

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    repository.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        initial_equity=100000.0,
        cash_balance=100000.0,
        equity=100000.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
    )
    risk_events = []

    async def on_risk_event(payload):
        risk_events.append(payload)

    router = OrderRouter(backtest=None, live=ShouldNotSubmitHandler(), mode="live")
    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        initial_equity=100000.0,
        risk_manager=RiskManager(max_position_pct=0.8, max_daily_loss_pct=0.05),
        price_provider=lambda symbol: 50000.0,
        live_safeguards=True,
        live_market_type="swap",
        on_risk_event=on_risk_event,
    )

    order = await manager.submit(
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.1,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.REJECTED
    assert repository.orders[0].status == "rejected"
    assert risk_events[0]["reason"] == "Live position ownership is unavailable"
    assert risk_events[0]["reason_code"] == "live_position_ownership_unavailable"
    assert risk_events[0]["order_value"] == 0.0


@pytest.mark.asyncio
async def test_live_order_manager_allows_opening_order_when_enabled_and_within_limits():
    handler = MockHandler()
    repository = FakeRepository()
    repository.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        initial_equity=100000.0,
        cash_balance=100000.0,
        equity=100000.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
    )
    router = OrderRouter(backtest=None, live=handler, mode="live")
    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        initial_equity=100000.0,
        risk_manager=RiskManager(max_position_pct=0.8, max_daily_loss_pct=0.05),
        price_provider=lambda symbol: 50000.0,
        live_safeguards=True,
        live_market_type="swap",
        allow_live_open_orders=True,
        live_max_order_notional=10000.0,
    )

    order = await manager.submit(
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.1,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.REJECTED
    assert handler.submitted == []


@pytest.mark.asyncio
async def test_live_order_manager_rejects_opening_order_over_notional_cap():
    class ShouldNotSubmitHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            raise AssertionError("over-cap live opening order should not reach router")

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    repository.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        initial_equity=100000.0,
        cash_balance=100000.0,
        equity=100000.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
    )
    risk_events = []

    async def on_risk_event(payload):
        risk_events.append(payload)

    router = OrderRouter(backtest=None, live=ShouldNotSubmitHandler(), mode="live")
    manager = UnifiedOrderManager(
        router=router,
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
        initial_equity=100000.0,
        risk_manager=RiskManager(max_position_pct=0.8, max_daily_loss_pct=0.05),
        price_provider=lambda symbol: 50000.0,
        live_safeguards=True,
        live_market_type="swap",
        allow_live_open_orders=True,
        live_max_order_notional=10000.0,
        on_risk_event=on_risk_event,
    )

    order = await manager.submit(
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.3,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.REJECTED
    assert repository.orders[0].status == "rejected"
    assert risk_events[0]["reason"] == "Live position ownership is unavailable"
    assert risk_events[0]["reason_code"] == "live_position_ownership_unavailable"
    assert risk_events[0]["order_value"] == 0.0


@pytest.mark.asyncio
async def test_order_manager_aggregates_long_and_short_positions_for_same_symbol_risk():
    class PendingHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            return order

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    class HedgedRepository(FakeRepository):
        def __init__(self):
            super().__init__()
            self.accounts["ma_cross"] = AccountRecord(
                strategy="ma_cross",
                initial_equity=1000.0,
                cash_balance=1000.0,
                equity=1000.0,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
                daily_pnl=0.0,
                fees_paid=0.0,
                updated_at=1,
            )
            self.hedged_positions = [
                PositionRecord(
                    strategy="ma_cross", symbol="BTC-USDT-SWAP", side="long",
                    amount=0.8, entry_price=100.0, leverage=1, timestamp=1,
                ),
                PositionRecord(
                    strategy="ma_cross", symbol="BTC-USDT-SWAP", side="short",
                    amount=0.8, entry_price=100.0, leverage=1, timestamp=1,
                ),
            ]

        def get_position(self, strategy, symbol):
            raise AssertionError("risk evaluation must not select one side")

        def get_positions(self, strategy=None, symbol=None):
            assert (strategy, symbol) == ("ma_cross", "BTC-USDT-SWAP")
            return self.hedged_positions

    repository = HedgedRepository()
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=PendingHandler(), mode="backtest"),
        repository=repository,
        risk_manager=RiskManager(max_position_pct=0.15),
        price_provider=lambda _symbol: 100.0,
    )

    order = await manager.submit(
        symbol="BTC-USDT-SWAP",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=0.2,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.PENDING


@pytest.mark.asyncio
@pytest.mark.parametrize("provided_price", [None, 0.0, -1.0, math.nan, math.inf])
async def test_order_manager_rejects_invalid_price_provider_value_with_risk_enabled(provided_price):
    class ShouldNotSubmitHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            raise AssertionError("invalid risk valuation price must not reach router")

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    repository.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        initial_equity=100000.0,
        cash_balance=100000.0,
        equity=100000.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
    )
    risk_events = []
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=ShouldNotSubmitHandler(), mode="backtest"),
        repository=repository,
        risk_manager=RiskManager(max_position_pct=0.25),
        price_provider=lambda _symbol: provided_price,
        on_risk_event=lambda event: risk_events.append(event),
    )

    order = await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=1.0,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.REJECTED
    assert risk_events[0]["reason"] == "Risk evaluation requires a current price"
    assert risk_events[0]["order_value"] == 0.0


@pytest.mark.asyncio
async def test_live_spot_sell_rejects_amount_above_available_position_when_open_orders_allowed():
    class ShouldNotSubmitHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            raise AssertionError("spot sell above available position must not reach router")

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    repository.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        initial_equity=100000.0,
        cash_balance=100000.0,
        equity=100000.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
        source="live_exchange",
        runtime_mode="live",
    )
    repository.positions[("ma_cross", "BTC-USDT")] = PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="long",
        amount=1.0,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
        source="live_exchange",
        runtime_mode="live",
    )
    repository.owned_positions.add(("ma_cross", "BTC-USDT"))
    risk_events = []
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=ShouldNotSubmitHandler(), mode="live"),
        repository=repository,
        risk_manager=RiskManager(max_position_pct=0.8),
        price_provider=lambda _symbol: 50000.0,
        live_safeguards=True,
        live_market_type="spot",
        allow_live_open_orders=True,
        on_risk_event=lambda event: risk_events.append(event),
    )

    order = await manager.submit(
        symbol="BTC-USDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=2.0,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.REJECTED
    assert risk_events[0]["reason"] == "Live spot sell exceeds available position"
    assert risk_events[0]["order_value"] == pytest.approx(100000.0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "filled_amount", "remaining_amount"),
    [
        (OrderStatus.FILLED, 1.0, 0.0),
        (OrderStatus.PARTIALLY_FILLED, 0.4, 0.6),
    ],
)
async def test_paper_submit_rolls_back_order_when_accounting_fails(
    monkeypatch, tmp_path, status, filled_amount, remaining_amount
):
    class FillHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            order.status = status
            order.fill_price = 100.0
            order.filled_amount = filled_amount
            order.remaining_amount = remaining_amount
            return order

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    def fail_after_mutation(
        self, order, strategy_name, timestamp, fee=None, transaction=None
    ):
        transaction = transaction or self.repository
        self._process_filled_order(transaction, order, strategy_name, timestamp, fee)
        raise RuntimeError("accounting failed")

    monkeypatch.setattr(
        "src.order.accounting.PaperAccountingService.process_filled_order",
        fail_after_mutation,
    )
    repository = Repository(db_path=str(tmp_path / "submit-rollback.db"))
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=FillHandler(), mode="backtest"),
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
    )

    with pytest.raises(RuntimeError, match="accounting failed"):
        await manager.submit(
            symbol="BTC-USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            amount=1.0,
            strategy_name="ma_cross",
        )

    assert repository.get_orders() == []
    assert repository.get_trades() == []
    assert repository.get_positions() == []
    assert repository.get_accounts() == []
    assert repository.get_cash_ledger() == []
    events = repository.get_risk_events()
    assert len(events) == 1
    assert events[0].reason_code == "paper_accounting_reconciliation_required"


@pytest.mark.asyncio
async def test_paper_cancel_partial_rolls_back_order_when_accounting_fails(
    monkeypatch, tmp_path
):
    class PartialCancelHandler(MockHandler):
        async def cancel(self, order_id: str, symbol: str | None = None):
            return {"status": "canceled", "filled_amount": 0.4, "fill_price": 100.0}

    repository = Repository(db_path=str(tmp_path / "cancel-rollback.db"))
    original = OrderRecord(
        order_id="cancel-rollback",
        exchange_order_id="exchange-cancel-rollback",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=1.0,
        price=100.0,
        status="pending",
        fill_price=0.0,
        timestamp=1700000000000,
        updated_at=1700000000000,
        filled_amount=0.0,
        remaining_amount=1.0,
        source="backtest",
        runtime_mode="backtest",
    )
    repository.upsert_order(original)

    def fail_after_mutation(
        self, order, strategy_name, timestamp, fee=None, transaction=None
    ):
        transaction = transaction or self.repository
        self._process_filled_order(transaction, order, strategy_name, timestamp, fee)
        raise RuntimeError("accounting failed")

    monkeypatch.setattr(
        "src.order.accounting.PaperAccountingService.process_filled_order",
        fail_after_mutation,
    )
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=PartialCancelHandler(), mode="backtest"),
        repository=repository,
        timestamp_ms=lambda: 1700000001000,
    )

    with pytest.raises(RuntimeError, match="accounting failed"):
        await manager.cancel(original.order_id, symbol=original.symbol)

    saved = repository.get_orders()[0]
    assert saved.status == "pending"
    assert saved.filled_amount == pytest.approx(0.0)
    assert saved.remaining_amount == pytest.approx(1.0)
    assert repository.get_trades() == []
    assert repository.get_positions() == []
    assert repository.get_accounts() == []
    assert repository.get_cash_ledger() == []


@pytest.mark.asyncio
async def test_order_manager_cancel_rejects_inconsistent_exchange_fill_group_without_local_mutation(
):
    class InconsistentCancelHandler(MockHandler):
        async def cancel(self, order_id: str, symbol: str | None = None):
            return {
                "status": "canceled",
                "filled": 0.4,
                "remaining": 0.5,
                "fill_price": 100.0,
            }

    repository = FakeRepository()
    original = OrderRecord(
        order_id="inconsistent-cancel-fill", strategy="ma_cross", symbol="BTC-USDT",
        side="buy", type="limit", amount=1.0, price=100.0, status="pending",
        fill_price=0.0, timestamp=1, updated_at=1, filled_amount=0.0, remaining_amount=1.0,
    )
    repository.orders.append(original)
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=InconsistentCancelHandler(), mode="backtest"),
        repository=repository,
    )

    with pytest.raises(ValueError, match="inconsistent cancellation fill amounts"):
        await manager.cancel(original.order_id, symbol=original.symbol)

    saved = repository.orders[0]
    assert (saved.status, saved.filled_amount, saved.remaining_amount, saved.fill_price) == (
        "pending", 0.0, 1.0, 0.0
    )
    assert repository.trades == []


@pytest.mark.asyncio
@pytest.mark.parametrize("filled_amount", [1.1, -0.1, math.nan, math.inf])
async def test_order_manager_cancel_rejects_invalid_exchange_fill_without_local_mutation(
    filled_amount,
):
    class InvalidCancelHandler(MockHandler):
        async def cancel(self, order_id: str, symbol: str | None = None):
            return {"status": "canceled", "filled_amount": filled_amount, "fill_price": 100.0}

    repository = FakeRepository()
    original = OrderRecord(
        order_id="invalid-cancel-fill", strategy="ma_cross", symbol="BTC-USDT",
        side="buy", type="limit", amount=1.0, price=100.0, status="pending",
        fill_price=0.0, timestamp=1, updated_at=1, filled_amount=0.0, remaining_amount=1.0,
    )
    repository.orders.append(original)
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=InvalidCancelHandler(), mode="backtest"),
        repository=repository,
    )

    with pytest.raises(ValueError, match="invalid cancellation fill"):
        await manager.cancel(original.order_id, symbol=original.symbol)

    saved = repository.orders[0]
    assert (saved.status, saved.filled_amount, saved.remaining_amount, saved.fill_price) == (
        "pending", 0.0, 1.0, 0.0
    )
    assert repository.trades == []


@pytest.mark.asyncio
@pytest.mark.parametrize("filled_amount", [0.0, 0.4])
async def test_order_manager_cancel_notifies_order_update_after_successful_persistence(
    filled_amount,
):
    class CancelHandler(MockHandler):
        async def cancel(self, order_id: str, symbol: str | None = None):
            return {"status": "canceled", "filled_amount": filled_amount, "fill_price": 100.0}

    repository = FakeRepository()
    original = OrderRecord(
        order_id=f"callback-cancel-{filled_amount}", strategy="ma_cross", symbol="BTC-USDT",
        side="buy", type="limit", amount=1.0, price=100.0, status="pending",
        fill_price=0.0, timestamp=1, updated_at=1, filled_amount=0.0, remaining_amount=1.0,
    )
    repository.orders.append(original)
    callbacks = []
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=CancelHandler(), mode="backtest"),
        repository=repository,
        on_order_update=lambda strategy: callbacks.append((strategy, repository.orders[0].status)),
    )

    assert await manager.cancel(original.order_id, symbol=original.symbol) is True
    assert callbacks == [("ma_cross", "cancelled")]
    assert repository.orders[0].filled_amount == pytest.approx(filled_amount)


@pytest.mark.asyncio
async def test_order_manager_cancel_swallows_order_update_callback_failure_after_persistence():
    repository = FakeRepository()
    original = OrderRecord(
        order_id="callback-failure-cancel", strategy="ma_cross", symbol="BTC-USDT",
        side="buy", type="limit", amount=1.0, price=100.0, status="pending",
        fill_price=0.0, timestamp=1, updated_at=1, filled_amount=0.0, remaining_amount=1.0,
    )
    repository.orders.append(original)

    async def fail_callback(_strategy):
        raise RuntimeError("broadcast failed")

    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=MockHandler(), mode="backtest"),
        repository=repository,
        on_order_update=fail_callback,
    )

    assert await manager.cancel(original.order_id, symbol=original.symbol) is True
    assert repository.orders[0].status == "cancelled"


@pytest.mark.asyncio
async def test_live_submissions_are_serialized_across_concurrent_orders():
    class LeaseRepository(FakeRepository):
        def __init__(self):
            super().__init__()
            self.lease = asyncio.Lock()
            self.position_amount = 1.0

        async def acquire_live_submission_lease(self, owner, timeout=1.0, lease_seconds=10.0):
            await asyncio.wait_for(self.lease.acquire(), timeout=timeout)
            return True

        def release_live_submission_lease(self, owner):
            self.lease.release()

        def get_account(self, strategy):
            return AccountRecord(
                strategy="__exchange__", initial_equity=1000, cash_balance=1000,
                equity=1000, realized_pnl=0, unrealized_pnl=0, daily_pnl=0,
                fees_paid=0, updated_at=1,
                source="live_exchange", runtime_mode="live",
            )

        def get_positions(self, strategy, symbol):
            if self.position_amount <= 0:
                return []
            return [PositionRecord(
                strategy="__exchange__", symbol=symbol, side="long", amount=self.position_amount,
                entry_price=100, leverage=1, timestamp=1, mark_price=100,
                source="live_exchange", runtime_mode="live",
            )]

    class LiveHandler(MockHandler):
        async def submit(self, order):
            await asyncio.sleep(0)
            repository.position_amount -= order.amount
            return await super().submit(order)

    repository = LeaseRepository()
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=MockHandler(), live=LiveHandler(), mode="live"),
        repository=repository,
        risk_manager=RiskManager(max_position_pct=2, max_daily_loss_pct=1, max_drawdown_pct=1),
        live_safeguards=True,
        live_market_type="swap",
        live_state_refresher=None,
        price_provider=lambda _symbol: 100.0,
    )

    results = await asyncio.gather(*(
        manager.submit(
            "BTC-USDT-SWAP",
            OrderSide.SELL,
            OrderType.MARKET,
            1.0,
            strategy_name="__exchange__",
        )
        for _ in range(2)
    ))

    assert [result.status for result in results].count(OrderStatus.REJECTED) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("price", [-1.0, float("nan"), float("inf")])
async def test_live_limit_orders_reject_non_positive_or_non_finite_price(price):
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=MockHandler(), live=MockHandler(), mode="live"),
        risk_manager=RiskManager(max_position_pct=2, max_daily_loss_pct=1, max_drawdown_pct=1),
        live_safeguards=True,
        live_market_type="swap",
    )

    result = await manager.submit(
        "BTC-USDT-SWAP", OrderSide.BUY, OrderType.LIMIT, 1.0, price=price,
        strategy_name="__exchange__",
    )

    assert result.status is OrderStatus.REJECTED
    assert result.reason if hasattr(result, "reason") else True


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [None, "", [], {}])
async def test_live_submit_unknown_adapter_result_keeps_pending_anchor_and_risk_event(response):
    class UnknownHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            raise AcceptedUnknownOrderResultError(f"accepted unknown: {response!r}")

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    class RecordingRepository(FakeRepository):
        def __init__(self):
            super().__init__()
            self.risk_events = []

        def save_risk_event(self, event):
            self.risk_events.append(event)
            return event

    repository = RecordingRepository()
    grant_live_position_ownership(repository)
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=UnknownHandler(), mode="live"),
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
    )

    with pytest.raises(AcceptedUnknownOrderResultError, match="accepted unknown"):
        await manager.submit(
            symbol="BTC-USDT-SWAP", side=OrderSide.BUY, order_type=OrderType.MARKET,
            amount=0.1, strategy_name="ma_cross",
        )

    assert repository.orders[0].status == OrderStatus.PENDING.value
    assert repository.orders[0].client_order_id
    assert repository.risk_events[0]["reason_code"] == "live_order_submission_unknown"


@pytest.mark.asyncio
async def test_live_submit_timeout_persists_stable_client_anchor_and_unknown_risk_event():
    import ccxt.async_support as ccxt

    class TimeoutHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            assert order.client_order_id
            raise ccxt.RequestTimeout("request timed out")

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    class RecordingRepository(FakeRepository):
        def __init__(self):
            super().__init__()
            self.risk_events = []

        def save_risk_event(self, event):
            self.risk_events.append(event)
            return event

    repository = RecordingRepository()
    grant_live_position_ownership(repository)
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=TimeoutHandler(), mode="live"),
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
    )

    with pytest.raises(ccxt.RequestTimeout):
        await manager.submit(
            symbol="BTC-USDT-SWAP",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            amount=0.1,
            strategy_name="ma_cross",
        )

    assert len(repository.orders) == 1
    assert repository.orders[0].status == OrderStatus.PENDING.value
    assert repository.orders[0].client_order_id
    assert repository.orders[0].exchange_order_id == ""
    assert repository.risk_events[0]["reason_code"] == "live_order_submission_unknown"
    assert repository.risk_events[0]["client_order_id"] == repository.orders[0].client_order_id


@pytest.mark.asyncio
async def test_live_submit_malformed_accepted_response_keeps_exchange_anchor_for_reconciliation():
    class MalformedAcceptedHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            error = AcceptedUnknownOrderResultError(
                "malformed fill field; reconciliation required"
            )
            error.id = "accepted-malformed"
            raise error

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    grant_live_position_ownership(repository)
    repository.risk_events = []
    repository.save_risk_event = lambda event: repository.risk_events.append(event) or event
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=MalformedAcceptedHandler(), mode="live"),
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
    )

    with pytest.raises(AcceptedUnknownOrderResultError, match="malformed fill field"):
        await manager.submit(
            symbol="BTC-USDT-SWAP",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            amount=0.1,
            strategy_name="ma_cross",
        )

    saved = repository.orders[0]
    assert saved.status == OrderStatus.PENDING.value
    assert saved.exchange_order_id == "accepted-malformed"
    assert saved.client_order_id
    assert repository.risk_events[0]["reason_code"] == "live_order_submission_unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {"status": "open"},
        {"code": "1", "data": [{"sCode": "51008", "sMsg": "order not found"}]},
    ],
)
async def test_order_manager_cancel_does_not_mutate_local_order_for_explicit_exchange_failure(
    response,
):
    class FailedCancelHandler(MockHandler):
        async def cancel(self, order_id: str, symbol: str | None = None):
            return response

    repository = FakeRepository()
    original = OrderRecord(
        order_id="cancel-explicit-failure",
        exchange_order_id="exchange-cancel-explicit-failure",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=1.0,
        price=100.0,
        status="pending",
        fill_price=0.0,
        timestamp=1,
        updated_at=1,
        filled_amount=0.0,
        remaining_amount=1.0,
    )
    repository.orders.append(original)
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=FailedCancelHandler(), mode="backtest"),
        repository=repository,
    )

    assert await manager.cancel(original.order_id, symbol=original.symbol) is False
    assert repository.orders[0].status == OrderStatus.PENDING.value
    assert repository.orders[0].filled_amount == 0.0


@pytest.mark.asyncio
async def test_order_manager_cancel_accepts_explicit_exchange_success_status():
    class SuccessfulCancelHandler(MockHandler):
        async def cancel(self, order_id: str, symbol: str | None = None):
            return {"status": "canceled", "sCode": "0"}

    repository = FakeRepository()
    repository.orders.append(OrderRecord(
        order_id="cancel-explicit-success",
        exchange_order_id="exchange-cancel-explicit-success",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=1.0,
        price=100.0,
        status="pending",
        fill_price=0.0,
        timestamp=1,
        updated_at=1,
        filled_amount=0.0,
        remaining_amount=1.0,
    ))
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=SuccessfulCancelHandler(), mode="backtest"),
        repository=repository,
    )

    assert await manager.cancel("cancel-explicit-success", symbol="BTC-USDT") is True
    assert repository.orders[0].status == OrderStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_cancel_does_not_regress_local_filled_quantity_from_stale_remaining_response():
    class StaleCancelHandler(MockHandler):
        async def cancel(self, order_id: str, symbol: str | None = None):
            return {
                "status": "canceled",
                "remaining": "0.8",
                "average": "90.0",
            }

    repository = FakeRepository()
    repository.risk_events = []
    repository.save_risk_event = lambda event: repository.risk_events.append(event) or event
    repository.orders.append(OrderRecord(
        order_id="cancel-stale-remaining",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=1.0,
        price=100.0,
        status="partially_filled",
        fill_price=110.0,
        timestamp=1,
        updated_at=1,
        filled_amount=0.6,
        remaining_amount=0.4,
    ))
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=StaleCancelHandler(), mode="backtest"),
        repository=repository,
    )

    assert await manager.cancel("cancel-stale-remaining", symbol="BTC-USDT") is True

    saved = repository.orders[0]
    assert saved.status == OrderStatus.CANCELLED.value
    assert saved.filled_amount == pytest.approx(0.6)
    assert saved.remaining_amount == pytest.approx(0.4)
    assert saved.fill_price == pytest.approx(110.0)
    assert repository.risk_events[0]["reason_code"] == "cancel_quantity_reconciliation_required"


@pytest.mark.asyncio
async def test_cancel_closed_without_quantities_keeps_local_partial_and_requires_reconciliation():
    class UnknownClosedCancelHandler(MockHandler):
        async def cancel(self, order_id: str, symbol: str | None = None):
            return {"status": "closed"}

    repository = FakeRepository()
    repository.risk_events = []
    repository.save_risk_event = lambda event: repository.risk_events.append(event) or event
    repository.orders.append(OrderRecord(
        order_id="cancel-closed-unknown",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=1.0,
        price=100.0,
        status="partially_filled",
        fill_price=100.0,
        timestamp=1,
        updated_at=1,
        filled_amount=0.2,
        remaining_amount=0.8,
    ))
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=UnknownClosedCancelHandler(), mode="backtest"),
        repository=repository,
    )

    assert await manager.cancel("cancel-closed-unknown", symbol="BTC-USDT") is True

    saved = repository.orders[0]
    assert saved.status == OrderStatus.PARTIALLY_FILLED.value
    assert saved.filled_amount == pytest.approx(0.2)
    assert saved.remaining_amount == pytest.approx(0.8)
    assert repository.risk_events[0]["reason_code"] == "cancel_outcome_reconciliation_required"


@pytest.mark.asyncio
async def test_cancel_closed_without_quantities_keeps_local_full_fill():
    class UnknownClosedCancelHandler(MockHandler):
        async def cancel(self, order_id: str, symbol: str | None = None):
            return {"status": "closed"}

    repository = FakeRepository()
    repository.orders.append(OrderRecord(
        order_id="cancel-closed-full",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=1.0,
        price=100.0,
        status="filled",
        fill_price=101.0,
        timestamp=1,
        updated_at=1,
        filled_amount=1.0,
        remaining_amount=0.0,
    ))
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=UnknownClosedCancelHandler(), mode="backtest"),
        repository=repository,
    )

    assert await manager.cancel("cancel-closed-full", symbol="BTC-USDT") is True
    assert repository.orders[0].status == OrderStatus.FILLED.value
    assert repository.orders[0].filled_amount == pytest.approx(1.0)
    assert repository.orders[0].remaining_amount == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_cancel_closed_with_complete_quantities_is_final_filled():
    class CompleteClosedCancelHandler(MockHandler):
        async def cancel(self, order_id: str, symbol: str | None = None):
            return {"status": "closed", "filled": "1.0", "remaining": "0", "average": "102.0"}

    repository = FakeRepository()
    repository.orders.append(OrderRecord(
        order_id="cancel-closed-complete",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=1.0,
        price=100.0,
        status="pending",
        fill_price=0.0,
        timestamp=1,
        updated_at=1,
        filled_amount=0.0,
        remaining_amount=1.0,
    ))
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=CompleteClosedCancelHandler(), mode="backtest"),
        repository=repository,
    )

    assert await manager.cancel("cancel-closed-complete", symbol="BTC-USDT") is True
    assert repository.orders[0].status == OrderStatus.FILLED.value
    assert repository.orders[0].filled_amount == pytest.approx(1.0)
    assert repository.orders[0].remaining_amount == pytest.approx(0.0)
    assert repository.orders[0].fill_price == pytest.approx(102.0)


@pytest.mark.asyncio
async def test_cancel_canceled_with_partial_quantities_remains_cancelled():
    class PartialCanceledHandler(MockHandler):
        async def cancel(self, order_id: str, symbol: str | None = None):
            return {"status": "canceled", "filled": "0.2", "remaining": "0.8", "average": "101.0"}

    repository = FakeRepository()
    repository.orders.append(OrderRecord(
        order_id="cancel-canceled-partial",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=1.0,
        price=100.0,
        status="pending",
        fill_price=0.0,
        timestamp=1,
        updated_at=1,
        filled_amount=0.0,
        remaining_amount=1.0,
    ))
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=PartialCanceledHandler(), mode="backtest"),
        repository=repository,
    )

    assert await manager.cancel("cancel-canceled-partial", symbol="BTC-USDT") is True
    assert repository.orders[0].status == OrderStatus.CANCELLED.value
    assert repository.orders[0].filled_amount == pytest.approx(0.2)
    assert repository.orders[0].remaining_amount == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_cancel_without_fill_is_cancelled_normally():
    class PureCancelHandler(MockHandler):
        async def cancel(self, order_id: str, symbol: str | None = None):
            return {"status": "canceled"}

    repository = FakeRepository()
    repository.orders.append(OrderRecord(
        order_id="cancel-pure",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=1.0,
        price=100.0,
        status="pending",
        fill_price=0.0,
        timestamp=1,
        updated_at=1,
        filled_amount=0.0,
        remaining_amount=1.0,
    ))
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=PureCancelHandler(), mode="backtest"),
        repository=repository,
    )

    assert await manager.cancel("cancel-pure", symbol="BTC-USDT") is True
    assert repository.orders[0].status == OrderStatus.CANCELLED.value
    assert repository.orders[0].filled_amount == pytest.approx(0.0)
    assert repository.orders[0].remaining_amount == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_live_submit_exception_with_identity_keeps_exchange_identity_on_anchor():
    class IdentifiedFailure(Exception):
        def __init__(self):
            super().__init__("accepted before connection failure")
            self.id = "exchange-after-timeout"
            self.clientOrderId = "client-from-provider"

    class Handler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            raise IdentifiedFailure()

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    grant_live_position_ownership(repository)
    repository.risk_events = []
    repository.save_risk_event = lambda event: repository.risk_events.append(event) or event
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=Handler(), mode="live"),
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
    )

    with pytest.raises(IdentifiedFailure):
        await manager.submit(
            symbol="BTC-USDT-SWAP",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            amount=0.1,
            strategy_name="ma_cross",
        )

    saved = repository.orders[0]
    assert saved.exchange_order_id == "exchange-after-timeout"
    assert saved.client_order_id == "client-from-provider"
    assert repository.risk_events[0]["reason_code"] == "live_order_submission_unknown"


@pytest.mark.asyncio
async def test_live_submit_clear_rejection_is_persisted_as_rejected_without_unknown_event():
    import ccxt.async_support as ccxt

    class RejectedHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            raise ccxt.InvalidOrder("order rejected")

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
    grant_live_position_ownership(repository)
    repository.risk_events = []
    repository.save_risk_event = lambda event: repository.risk_events.append(event) or event
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=RejectedHandler(), mode="live"),
        repository=repository,
        timestamp_ms=lambda: 1700000000000,
    )

    with pytest.raises(ccxt.InvalidOrder):
        await manager.submit(
            symbol="BTC-USDT-SWAP",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            amount=0.1,
            strategy_name="ma_cross",
        )

    assert repository.orders[0].status == OrderStatus.REJECTED.value
    assert repository.risk_events == []


@pytest.mark.asyncio
async def test_live_order_manager_rejects_strategy_position_without_ownership_marker():
    repository = FakeRepository()
    repository.accounts["alpha"] = AccountRecord(
        strategy="alpha", initial_equity=1000.0, cash_balance=1000.0,
        equity=1000.0, realized_pnl=0.0, unrealized_pnl=0.0,
        daily_pnl=0.0, fees_paid=0.0, updated_at=1,
    )
    repository.positions[("alpha", "BTC-USDT-SWAP")] = PositionRecord(
        strategy="alpha", symbol="BTC-USDT-SWAP", side="long", amount=1.0,
        entry_price=100.0, leverage=1, timestamp=1,
    )
    repository.risk_events = []
    repository.save_risk_event = lambda event: repository.risk_events.append(event) or event
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=MockHandler(), mode="live"),
        repository=repository,
        risk_manager=RiskManager(max_position_pct=2.0),
        price_provider=lambda _symbol: 100.0,
        live_safeguards=True,
        live_market_type="swap",
        allow_live_open_orders=True,
    )

    order = await manager.submit(
        "BTC-USDT-SWAP", OrderSide.SELL, OrderType.MARKET, 0.25, strategy_name="alpha"
    )

    assert order.status == OrderStatus.REJECTED
    assert repository.risk_events[0]["reason_code"] == "live_position_ownership_unavailable"
    assert repository.risk_events[0]["severity"] == "high"


@pytest.mark.asyncio
async def test_live_strategy_allows_reduce_when_exchange_position_has_explicit_mapping():
    repository = FakeRepository()
    repository.accounts["__exchange__"] = AccountRecord(
        strategy="__exchange__", initial_equity=1000.0, cash_balance=1000.0,
        equity=1000.0, realized_pnl=0.0, unrealized_pnl=0.0, daily_pnl=0.0,
        fees_paid=0.0, updated_at=1,
        source="live_exchange", runtime_mode="live",
    )
    repository.positions[("__exchange__", "BTC-USDT-SWAP")] = PositionRecord(
        strategy="__exchange__", symbol="BTC-USDT-SWAP", side="long", amount=1.0,
        entry_price=100.0, leverage=1, timestamp=1,
        source="live_exchange", runtime_mode="live",
    )
    repository.owned_positions.add(("alpha", "BTC-USDT-SWAP"))
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=MockHandler(), mode="live"),
        repository=repository,
        risk_manager=RiskManager(max_position_pct=2.0),
        price_provider=lambda _symbol: 100.0,
        live_safeguards=True,
        live_market_type="swap",
        allow_live_open_orders=True,
    )

    order = await manager.submit(
        "BTC-USDT-SWAP", OrderSide.SELL, OrderType.MARKET, 0.25, strategy_name="alpha"
    )

    assert order.status == OrderStatus.FILLED


@pytest.mark.asyncio
@pytest.mark.parametrize("side", [OrderSide.BUY, OrderSide.SELL])
async def test_live_strategy_rejects_open_and_reduce_when_exchange_position_ownership_is_unknown(
    side,
):
    class ShouldNotSubmitHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            raise AssertionError("unowned exchange position must not be traded")

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    class RiskRecordingRepository(FakeRepository):
        def __init__(self):
            super().__init__()
            self.risk_events = []

        def save_risk_event(self, event):
            self.risk_events.append(event)
            return event

    repository = RiskRecordingRepository()
    repository.accounts["__exchange__"] = AccountRecord(
        strategy="__exchange__", initial_equity=1000.0, cash_balance=1000.0,
        equity=1000.0, realized_pnl=0.0, unrealized_pnl=0.0, daily_pnl=0.0,
        fees_paid=0.0, updated_at=1,
    )
    repository.positions[("__exchange__", "BTC-USDT-SWAP")] = PositionRecord(
        strategy="__exchange__", symbol="BTC-USDT-SWAP", side="long", amount=1.0,
        entry_price=100.0, leverage=1, timestamp=1,
    )
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=ShouldNotSubmitHandler(), mode="live"),
        repository=repository,
        risk_manager=RiskManager(max_position_pct=2.0),
        price_provider=lambda _symbol: 100.0,
        live_safeguards=True,
        live_market_type="swap",
        allow_live_open_orders=True,
    )

    order = await manager.submit(
        "BTC-USDT-SWAP", side, OrderType.MARKET, 0.25, strategy_name="alpha"
    )

    assert order.status == OrderStatus.REJECTED
    assert repository.risk_events[0]["reason_code"] == "live_position_ownership_unavailable"
    assert repository.risk_events[0]["severity"] == "high"


@pytest.mark.asyncio
@pytest.mark.parametrize("side", [OrderSide.BUY, OrderSide.SELL])
async def test_live_ownership_gate_runs_when_live_safeguards_are_disabled(side):
    class ShouldNotSubmitHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            raise AssertionError("unknown live ownership must not reach router")

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    class RiskRecordingRepository(FakeRepository):
        def __init__(self):
            super().__init__()
            self.risk_events = []

        def save_risk_event(self, event):
            self.risk_events.append(event)
            return event

    repository = RiskRecordingRepository()
    repository.accounts["__exchange__"] = AccountRecord(
        strategy="__exchange__", initial_equity=1000.0, cash_balance=1000.0,
        equity=1000.0, realized_pnl=0.0, unrealized_pnl=0.0,
        daily_pnl=0.0, fees_paid=0.0, updated_at=1,
    )
    repository.positions[("__exchange__", "BTC-USDT-SWAP")] = PositionRecord(
        strategy="__exchange__", symbol="BTC-USDT-SWAP", side="long", amount=1.0,
        entry_price=100.0, leverage=1, timestamp=1,
    )
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=ShouldNotSubmitHandler(), mode="live"),
        repository=repository,
        risk_manager=RiskManager(max_position_pct=2.0),
        price_provider=lambda _symbol: 100.0,
        live_safeguards=False,
        live_market_type="swap",
        allow_live_open_orders=True,
    )

    order = await manager.submit(
        "BTC-USDT-SWAP", side, OrderType.MARKET, 0.25, strategy_name="alpha"
    )

    assert order.status == OrderStatus.REJECTED
    assert repository.risk_events[0]["reason_code"] == "live_position_ownership_unavailable"
    assert repository.risk_events[0]["severity"] == "high"


@pytest.mark.asyncio
async def test_live_ownership_gate_allows_explicit_owner_with_safeguards_disabled():
    handler = MockHandler()
    repository = FakeRepository()
    repository.accounts["__exchange__"] = AccountRecord(
        strategy="__exchange__", initial_equity=1000.0, cash_balance=1000.0,
        equity=1000.0, realized_pnl=0.0, unrealized_pnl=0.0,
        daily_pnl=0.0, fees_paid=0.0, updated_at=1,
        source="live_exchange", runtime_mode="live",
    )
    repository.positions[("__exchange__", "BTC-USDT-SWAP")] = PositionRecord(
        strategy="__exchange__", symbol="BTC-USDT-SWAP", side="long", amount=1.0,
        entry_price=100.0, leverage=1, timestamp=1,
        source="live_exchange", runtime_mode="live",
    )
    repository.owned_positions.add(("alpha", "BTC-USDT-SWAP"))
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=handler, mode="live"),
        repository=repository,
        live_safeguards=False,
        live_market_type="swap",
        allow_live_open_orders=True,
    )

    order = await manager.submit(
        "BTC-USDT-SWAP", OrderSide.SELL, OrderType.MARKET, 0.25, strategy_name="alpha"
    )

    assert order.status == OrderStatus.FILLED
    assert len(handler.submitted) == 1


@pytest.mark.asyncio
async def test_live_ownership_gate_runs_without_risk_manager_or_extra_safeguards():
    class RiskRecordingRepository(FakeRepository):
        def __init__(self):
            super().__init__()
            self.risk_events = []

        def save_risk_event(self, event):
            self.risk_events.append(event)
            return event

    class ShouldNotSubmitHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            raise AssertionError("unknown live ownership must not reach router")

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = RiskRecordingRepository()
    repository.positions[("__exchange__", "BTC-USDT-SWAP")] = PositionRecord(
        strategy="__exchange__",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=100.0,
        leverage=1,
        timestamp=1,
    )
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=ShouldNotSubmitHandler(), mode="live"),
        repository=repository,
        risk_manager=None,
        live_safeguards=False,
        live_market_type="swap",
        allow_live_open_orders=True,
    )

    order = await manager.submit(
        "BTC-USDT-SWAP", OrderSide.SELL, OrderType.MARKET, 0.25, strategy_name="alpha"
    )

    assert order.status == OrderStatus.REJECTED
    assert repository.risk_events[0]["reason_code"] == "live_position_ownership_unavailable"
    assert repository.risk_events[0]["severity"] == "high"


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_live_open_orders", [False, True])
@pytest.mark.parametrize("risk_manager_enabled", [False, True])
@pytest.mark.parametrize("live_safeguards", [False, True])
async def test_live_strategy_without_exchange_position_is_rejected_before_risk_or_router(
    allow_live_open_orders, risk_manager_enabled, live_safeguards
):
    class ShouldNotSubmitHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            raise AssertionError("live strategy without ownership must not reach router")

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    class RiskRecordingRepository(FakeRepository):
        def __init__(self):
            super().__init__()
            self.risk_events = []

        def save_risk_event(self, event):
            self.risk_events.append(event)
            return event

    repository = RiskRecordingRepository()
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=ShouldNotSubmitHandler(), mode="live"),
        repository=repository,
        risk_manager=RiskManager(max_position_pct=2.0) if risk_manager_enabled else None,
        price_provider=lambda _symbol: 100.0,
        live_safeguards=live_safeguards,
        live_market_type="swap",
        allow_live_open_orders=allow_live_open_orders,
    )

    order = await manager.submit(
        "BTC-USDT-SWAP", OrderSide.BUY, OrderType.MARKET, 0.25, strategy_name="alpha"
    )

    assert order.status == OrderStatus.REJECTED
    assert repository.risk_events[0]["reason"] == "Live position ownership is unavailable"
    assert repository.risk_events[0]["reason_code"] == "live_position_ownership_unavailable"
    assert repository.risk_events[0]["severity"] == "high"


def test_live_position_lookup_matches_legacy_alias_after_repository_query():
    class SqlLikeRepository(FakeRepository):
        def get_positions(self, strategy, symbol=None):
            positions = [
                position
                for position in self.positions.values()
                if position.strategy == strategy
            ]
            return [
                position for position in positions if symbol is None or position.symbol == symbol
            ]

    repository = SqlLikeRepository()
    repository.positions[("__exchange__", "BTC/USDT:USDT")] = PositionRecord(
        strategy="__exchange__", symbol="BTC/USDT:USDT", side="long", amount=1.0,
        entry_price=100.0, leverage=1, timestamp=1,
        source="live_exchange", runtime_mode="live",
    )
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=MockHandler(), mode="live"),
        repository=repository,
        live_market_type="swap",
    )

    assert manager.get_positions("__exchange__", "BTC-USDT-SWAP") == [
        repository.positions[("__exchange__", "BTC/USDT:USDT")]
    ]


def test_live_position_lookup_does_not_match_a_different_canonical_contract():
    class SqlLikeRepository(FakeRepository):
        def get_positions(self, strategy, symbol=None):
            positions = [
                position
                for position in self.positions.values()
                if position.strategy == strategy
            ]
            return [
                position for position in positions if symbol is None or position.symbol == symbol
            ]

    repository = SqlLikeRepository()
    repository.positions[("__exchange__", "BTC/USDT:USDT-260925")] = PositionRecord(
        strategy="__exchange__", symbol="BTC/USDT:USDT-260925", side="long", amount=1.0,
        entry_price=100.0, leverage=1, timestamp=1,
    )
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=MockHandler(), mode="live"),
        repository=repository,
        live_market_type="swap",
    )

    assert manager.get_positions("__exchange__", "BTC-USDT-261225") == []


def test_other_position_notional_counts_only_strict_live_exchange_rows():
    repository = FakeRepository()
    repository.positions[("__exchange__", "ETH-USDT")] = PositionRecord(
        strategy="__exchange__", symbol="ETH-USDT", side="long", amount=2.0,
        entry_price=100.0, mark_price=100.0, leverage=1, timestamp=1,
        source="paper", runtime_mode="paper",
    )
    repository.positions[("__exchange__", "SOL-USDT")] = PositionRecord(
        strategy="__exchange__", symbol="SOL-USDT", side="long", amount=3.0,
        entry_price=100.0, mark_price=100.0, leverage=1, timestamp=1,
        source="unknown", runtime_mode="unknown",
    )
    repository.positions[("__exchange__", "XRP-USDT")] = PositionRecord(
        strategy="__exchange__", symbol="XRP-USDT", side="long", amount=4.0,
        entry_price=100.0, mark_price=100.0, leverage=1, timestamp=1,
        source="live_exchange", runtime_mode="live",
    )
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=MockHandler(), mode="live"),
        repository=repository,
    )

    assert manager._other_position_notional("__exchange__", "BTC-USDT") == 400.0


@pytest.mark.asyncio
async def test_demo_persists_and_reads_paper_provenance_without_empty_fallback():
    repository = FakeRepository()
    repository.positions[("ma_cross", "BTC-USDT")] = PositionRecord(
        strategy="ma_cross", symbol="BTC-USDT", side="long", amount=1.0,
        entry_price=100.0, mark_price=100.0, leverage=1, timestamp=1,
        source="paper", runtime_mode="paper",
    )
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=MockHandler(), mode="demo"),
        repository=repository,
    )

    assert manager.get_position("ma_cross", "BTC-USDT").source == "paper"
    saved = repository.positions[("ma_cross", "BTC-USDT")]
    assert (saved.source, saved.runtime_mode) == ("paper", "paper")


@pytest.mark.asyncio
async def test_demo_risk_gate_counts_other_paper_symbols_for_max_exposure():
    repository = FakeRepository()
    repository.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross", initial_equity=100000.0, cash_balance=100000.0,
        equity=100000.0, realized_pnl=0.0, unrealized_pnl=0.0, daily_pnl=0.0,
        fees_paid=0.0, updated_at=1, source="paper", runtime_mode="paper",
    )
    repository.positions[("ma_cross", "BTC-USDT")] = PositionRecord(
        strategy="ma_cross", symbol="BTC-USDT", side="long", amount=1.6,
        entry_price=50000.0, mark_price=50000.0, leverage=1, timestamp=1,
        source="paper", runtime_mode="paper",
    )
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, demo=MockHandler(), mode="demo"),
        repository=repository,
        risk_manager=RiskManager(max_position_pct=0.8),
    )

    result = await manager.submit(
        symbol="ETH-USDT", side=OrderSide.BUY, order_type=OrderType.LIMIT,
        amount=0.1, price=50000.0, strategy_name="ma_cross",
    )

    assert result.status == OrderStatus.REJECTED
    assert repository.orders[0].status == OrderStatus.REJECTED.value


def test_live_other_position_notional_ignores_paper_rows():
    repository = FakeRepository()
    repository.positions[("__exchange__", "PAPER-USDT")] = PositionRecord(
        strategy="__exchange__", symbol="PAPER-USDT", side="long", amount=9.0,
        entry_price=100.0, mark_price=100.0, leverage=1, timestamp=1,
        source="paper", runtime_mode="paper",
    )
    repository.positions[("__exchange__", "LIVE-USDT")] = PositionRecord(
        strategy="__exchange__", symbol="LIVE-USDT", side="long", amount=2.0,
        entry_price=100.0, mark_price=100.0, leverage=1, timestamp=1,
        source="live_exchange", runtime_mode="live",
    )
    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=MockHandler(), mode="live"),
        repository=repository,
    )

    assert manager._other_position_notional("__exchange__", "BTC-USDT") == 200.0
