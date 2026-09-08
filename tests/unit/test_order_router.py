import pytest

from src.core.types import Order, OrderSide, OrderStatus, OrderType
from src.data.models import AccountRecord, PositionRecord
from src.order.manager import UnifiedOrderManager, risk_reason_code
from src.order.router import OrderHandler, OrderRouter
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
        self.accounts = {}
        self.ledger = []

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
        self.positions.pop((strategy, symbol), None)

    def get_open_positions(self, strategy=None):
        return [
            position
            for position in self.positions.values()
            if position.amount != 0 and (strategy is None or position.strategy == strategy)
        ]

    def save_cash_ledger(self, entry):
        self.ledger.append(entry)
        return entry


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
            "order_id": "",
            "strategy": "ma_cross",
            "symbol": "BTC-USDT",
            "side": "buy",
            "amount": 0.1,
            "price": 50000.0,
            "fee": 0.0,
            "timestamp": 1700000000000,
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
    assert repository.trades == []
    assert repository.positions == {}
    assert repository.accounts == {}


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
    )
    repository.positions[("ma_cross", "BTC-USDT")] = PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="long",
        amount=1.4,
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
async def test_live_order_manager_notifies_when_private_sync_fails():
    class PendingLiveHandler(OrderHandler):
        async def submit(self, order: Order) -> Order:
            order.exchange_order_id = "exchange-order-1"
            return order

        async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
            return True

    repository = FakeRepository()
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

    with pytest.raises(RuntimeError, match="private sync failed"):
        await manager.submit(
            symbol="BTC-USDT-SWAP",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            amount=0.25,
            strategy_name="ma_cross",
        )

    assert repository.orders[0].exchange_order_id == "exchange-order-1"
    assert calls == ["private-sync", "order-update"]


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
        )
        repository.positions[(strategy_name, symbol)] = PositionRecord(
            strategy=strategy_name,
            symbol=symbol,
            side="long",
            amount=1.0,
            entry_price=50000.0,
            leverage=1,
            timestamp=1700000000000,
        )

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
    assert handler.submitted[0].params == {"reduceOnly": True}


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
async def test_live_order_manager_marks_derivative_reducer_reduce_only():
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
    repository.positions[("ma_cross", "BTC-USDT-SWAP")] = PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
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
    )

    order = await manager.submit(
        symbol="BTC-USDT-SWAP",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=0.25,
        strategy_name="ma_cross",
    )

    assert order.status == OrderStatus.FILLED
    assert handler.submitted[0].reduce_only is True
    assert handler.submitted[0].params == {"reduceOnly": True}


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
    assert risk_events[0]["reason"] == "Live opening orders are disabled"
    assert risk_events[0]["reason_code"] == "live_opening_disabled"
    assert risk_events[0]["order_value"] == pytest.approx(5000.0)


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

    assert order.status == OrderStatus.FILLED
    assert len(handler.submitted) == 1
    assert handler.submitted[0].reduce_only is False
    assert handler.submitted[0].params == {}


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
    assert risk_events[0]["reason"] == "Live order exceeds configured notional cap"
    assert risk_events[0]["reason_code"] == "live_order_notional_exceeded"
    assert risk_events[0]["order_value"] == pytest.approx(15000.0)
