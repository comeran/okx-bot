import pytest
from ccxt.base.errors import AuthenticationError, NetworkError, PermissionDenied
from sqlmodel import SQLModel, create_engine

import src.data.repository as repository_module
from src.core.types import (
    AccountSnapshot,
    AssetBalance,
    ExchangeOrderSnapshot,
    ExchangeTradeSnapshot,
    OrderSide,
    OrderStatus,
    OrderType,
)
from src.data.models import AccountRecord, OrderRecord, TradeRecord
from src.data.repository import Repository
from src.exchange.base import RecentTradePage
from src.exchange.errors import MalformedProviderData
from src.ops import private_sync
from src.ops.private_sync import _order_record, sync_private_state


class FakeRepository:
    def __init__(self, orders=None):
        self.orders = list(orders or [])
        self.trades = []
        self.accounts = []
        self.upserted_orders = []
        self.upserted_trades = []
        self.risk_events = []
        self.kill_switch = False
        self.calls = []

    def get_orders(self, order_id=None):
        if order_id is not None:
            return [order for order in self.orders if order.order_id == order_id]
        return list(self.orders)

    def get_trades(self, strategy=None):
        if strategy is not None:
            return [trade for trade in self.trades if trade.strategy == strategy]
        return list(self.trades)

    def upsert_account(self, account):
        self.calls.append("upsert_account")
        self.accounts.append(account)
        return account

    def upsert_order(self, order):
        self.calls.append(f"upsert_order:{order.order_id}")
        self.upserted_orders.append(order)
        existing = next(
            (
                current
                for current in self.orders
                if (
                    getattr(current, "source", "unknown") == getattr(order, "source", "unknown")
                    and getattr(current, "runtime_mode", "unknown")
                    == getattr(order, "runtime_mode", "unknown")
                    and (
                        current.order_id == order.order_id
                        or (
                            order.exchange_order_id
                            and current.exchange_order_id == order.exchange_order_id
                        )
                    )
                )
            ),
            None,
        )
        if existing is None:
            self.orders.append(order)
            return order
        for field, value in order.model_dump(exclude={"id"}).items():
            setattr(existing, field, value)
        for field in (
            "filled_amount",
            "remaining_amount",
            "snapshot_filled_amount",
            "snapshot_updated_at",
        ):
            setattr(existing, field, getattr(order, field))
        return existing

    def upsert_trade(self, trade):
        self.calls.append(f"upsert_trade:{trade.exchange_trade_id}")
        self.upserted_trades.append(trade)
        incoming_fingerprint = private_sync._trade_record_fingerprint(trade)
        existing = next(
            (
                current
                for current in self.trades
                if (
                    getattr(current, "source", "unknown") == getattr(trade, "source", "unknown")
                    and getattr(current, "runtime_mode", "unknown")
                    == getattr(trade, "runtime_mode", "unknown")
                    and (
                        trade.exchange_trade_id
                        and current.exchange_trade_id == trade.exchange_trade_id
                        or (
                            incoming_fingerprint is not None
                            and (trade.exchange_order_id or trade.client_order_id)
                            and private_sync._trade_record_fingerprint(current)
                            == incoming_fingerprint
                            and (not trade.exchange_trade_id or not current.exchange_trade_id)
                        )
                    )
                )
            ),
            None,
        )
        if existing is None:
            self.trades.append(trade)
            return trade
        for field, value in trade.model_dump(exclude={"id"}).items():
            setattr(existing, field, value)
        return existing

    def set_kill_switch(self, engaged, reason, updated_at):
        self.calls.append("set_kill_switch")
        self.kill_switch = engaged
        return {"engaged": engaged, "reason": reason, "updated_at": updated_at}

    def save_risk_event(self, event):
        self.calls.append(f"save_risk_event:{event['severity']}:{event['event_key']}")
        self.risk_events.append(event)
        return event


class FakeAdapter:
    def __init__(self, account=None, orders=None, trades=None):
        self.order_calls = []
        self.account_calls = 0
        self.account = account or AccountSnapshot(
            initial_equity=1000.0,
            cash_balance=900.0,
            equity=1010.0,
            realized_pnl=10.0,
            unrealized_pnl=20.0,
            daily_pnl=5.0,
            fees_paid=1.5,
            timestamp=1700000000000,
        )
        self.orders = list(orders or [])
        self.trades = list(trades or [])
        self.trade_calls = []

    async def fetch_account_snapshot(self):
        self.account_calls += 1
        return self.account

    async def fetch_open_order_snapshots(self, symbols=None):
        self.order_calls.append(symbols)
        return self.orders

    async def fetch_recent_trade_snapshots(self, symbols=None, since=None, limit=100):
        self.trade_calls.append({"symbols": symbols, "since": since, "limit": limit})
        return RecentTradePage(
            self.trades,
            since=since,
            complete=True,
            mode="since" if since is not None else "cumulative",
        )


def local_order(**overrides):
    values = {
        "order_id": "local-1",
        "source": "live_exchange",
        "runtime_mode": "live",
        "exchange_order_id": "ex-local-1",
        "client_order_id": "client-1",
        "strategy": "ma_cross",
        "symbol": "BTC-USDT",
        "side": "buy",
        "type": "limit",
        "amount": 0.1,
        "price": 50000.0,
        "status": "pending",
        "fill_price": 0.0,
        "timestamp": 1699999999000,
        "updated_at": 1699999999000,
    }
    values.update(overrides)
    return OrderRecord(**values)


def exchange_order(**overrides):
    values = {
        "exchange_order_id": "ex-local-1",
        "client_order_id": "client-1",
        "symbol": "BTC-USDT",
        "side": OrderSide.BUY,
        "type": OrderType.LIMIT,
        "amount": 0.1,
        "price": 50000.0,
        "status": OrderStatus.PENDING,
        "fill_price": 0.0,
        "timestamp": 1700000000000,
        "updated_at": 1700000001000,
    }
    values.update(overrides)
    return ExchangeOrderSnapshot(**values)


def exchange_trade(**overrides):
    values = {
        "exchange_trade_id": "trade-1",
        "exchange_order_id": "ex-local-1",
        "client_order_id": "client-1",
        "symbol": "BTC-USDT",
        "side": OrderSide.BUY,
        "amount": 0.1,
        "price": 50000.0,
        "fee": 2.5,
        "timestamp": 1700000003000,
    }
    values.update(overrides)
    return ExchangeTradeSnapshot(**values)


@pytest.mark.asyncio
async def test_private_sync_account_upsert_does_not_reclassify_non_live_exchange_rows():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    repo = Repository(engine)
    repo.upsert_account(AccountRecord(
        strategy="__exchange__", source="paper", runtime_mode="paper",
        initial_equity=100.0, cash_balance=100.0, equity=100.0,
        realized_pnl=0.0, unrealized_pnl=0.0, daily_pnl=0.0, fees_paid=0.0,
        updated_at=1,
    ))
    repo.upsert_account(AccountRecord(
        strategy="__exchange__", source="unknown", runtime_mode="unknown",
        initial_equity=200.0, cash_balance=200.0, equity=200.0,
        realized_pnl=0.0, unrealized_pnl=0.0, daily_pnl=0.0, fees_paid=0.0,
        updated_at=2,
    ))

    result = await sync_private_state(
        repo,
        FakeAdapter(),
        timestamp_ms=lambda: 3,
    )

    assert result.account_upserted == 1
    assert repo.get_account(
        "__exchange__", source="paper", runtime_mode="paper"
    ).equity == 100.0
    assert repo.get_account(
        "__exchange__", source="unknown", runtime_mode="unknown"
    ).equity == 200.0
    assert repo.get_account(
        "__exchange__", source="live_exchange", runtime_mode="live"
    ).equity == 1010.0


def test_order_record_uses_client_order_id_for_stable_placeholder_identity():
    first = _order_record(
        exchange_order(exchange_order_id="", client_order_id="client-a"), None
    )
    second = _order_record(
        exchange_order(exchange_order_id="", client_order_id="client-b"), None
    )

    assert first.order_id == "okx-client:client-a"
    assert second.order_id == "okx-client:client-b"
    assert first.order_id != second.order_id


@pytest.mark.asyncio
async def test_private_sync_keeps_client_only_orders_separate_and_upgrades_exchange_id():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    repo = Repository(engine)

    await sync_private_state(
        repo,
        FakeAdapter(orders=[
            exchange_order(exchange_order_id="", client_order_id="client-a"),
            exchange_order(exchange_order_id="", client_order_id="client-b"),
        ]),
        timestamp_ms=lambda: 1,
    )

    orders = sorted(repo.get_orders(), key=lambda order: order.client_order_id)
    assert [order.order_id for order in orders] == [
        "okx-client:client-a",
        "okx-client:client-b",
    ]

    await sync_private_state(
        repo,
        FakeAdapter(orders=[
            exchange_order(exchange_order_id="exchange-a", client_order_id="client-a"),
            exchange_order(exchange_order_id="", client_order_id="client-b"),
        ]),
        timestamp_ms=lambda: 2,
    )

    orders = sorted(repo.get_orders(), key=lambda order: order.client_order_id)
    assert len(orders) == 2
    assert orders[0].exchange_order_id == "exchange-a"
    assert orders[0].order_id == "okx-client:client-a"


@pytest.mark.asyncio
async def test_atomic_private_sync_upgrades_client_only_trade_without_duplicate():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    repo = Repository(engine)

    client_only = exchange_trade(
        exchange_trade_id="",
        exchange_order_id="",
        client_order_id="client-only",
    )
    await sync_private_state(repo, FakeAdapter(trades=[client_only]), timestamp_ms=lambda: 1)

    upgraded = exchange_trade(
        exchange_trade_id="exchange-trade-1",
        exchange_order_id="exchange-order-1",
        client_order_id="client-only",
    )
    await sync_private_state(repo, FakeAdapter(trades=[upgraded]), timestamp_ms=lambda: 2)

    trades = repo.get_trades()
    assert len(trades) == 1
    assert trades[0].exchange_trade_id == "exchange-trade-1"
    assert trades[0].exchange_order_id == "exchange-order-1"
    assert trades[0].client_order_id == "client-only"


def test_reconcile_order_upgrades_client_placeholder_when_exchange_id_arrives():
    local = _order_record(
        exchange_order(exchange_order_id="", client_order_id="client-a"), None
    )
    snapshot = exchange_order(
        exchange_order_id="exchange-a", client_order_id="client-a"
    )

    reconciled = private_sync.reconcile_order(local, snapshot, [])

    assert reconciled.order_id == "okx-client:client-a"
    assert reconciled.exchange_order_id == "exchange-a"
    assert reconciled.client_order_id == "client-a"

    upgraded = private_sync._order_record(snapshot, local)
    assert upgraded.order_id == "okx-client:client-a"
    assert upgraded.exchange_order_id == "exchange-a"


def test_order_record_preserves_exchange_partial_fill_state():
    record = _order_record(
        exchange_order(
            status=OrderStatus.PARTIALLY_FILLED,
            filled_amount=0.04,
            remaining_amount=0.06,
        ),
        local_order(),
    )

    assert record.status == "partially_filled"
    assert record.filled_amount == 0.04
    assert record.remaining_amount == pytest.approx(0.06)


def test_order_record_keeps_authoritative_high_precision_remaining_amount():
    remaining = 0.12345678901234567
    record = _order_record(
        exchange_order(
            amount=0.9876543210987654,
            filled_amount=0.8641975320864197,
            remaining_amount=remaining,
        ),
        local_order(amount=0.9876543210987654),
    )

    assert record.remaining_amount == remaining


@pytest.mark.asyncio
async def test_private_sync_same_round_order_identity_upgrade_binds_trade_before_reconcile():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    repository = Repository(engine)
    repository.upsert_order(
        local_order(
            exchange_order_id="",
            client_order_id="client-1",
            amount=1.0,
        )
    )

    result = await sync_private_state(
        repository,
        FakeAdapter(
            orders=[exchange_order(
                exchange_order_id="ex-discovered",
                client_order_id="client-1",
                amount=1.0,
                status=OrderStatus.FILLED,
                remaining_amount=None,
                fill_price=None,
            )],
            trades=[exchange_trade(
                exchange_trade_id="same-round-trade",
                exchange_order_id="ex-discovered",
                client_order_id="",
                amount=0.4,
                price=51000.0,
            )],
        ),
        timestamp_ms=lambda: 1,
    )

    order = repository.get_orders(order_id="local-1")[0]
    trade = repository.get_trades(order_id="local-1")[0]
    assert result.divergences == []
    assert result.kill_switch_engaged is False
    assert trade.exchange_trade_id == "same-round-trade"
    assert (trade.order_id, trade.strategy) == ("local-1", "ma_cross")
    assert order.exchange_order_id == "ex-discovered"
    assert order.filled_amount == pytest.approx(0.4)
    assert order.remaining_amount == pytest.approx(0.6)
    assert order.fill_price == pytest.approx(51000.0)


@pytest.mark.asyncio
async def test_private_sync_creates_open_snapshot_before_rebinding_same_round_client_only_trade():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    repository = Repository(engine)

    result = await sync_private_state(
        repository,
        FakeAdapter(
            orders=[exchange_order(
                exchange_order_id="ex-discovered",
                client_order_id="client-discovered",
                amount=1.0,
                status=OrderStatus.PENDING,
            )],
            trades=[exchange_trade(
                exchange_trade_id="trade-discovered",
                exchange_order_id="",
                client_order_id="client-discovered",
                amount=0.4,
                price=51000.0,
            )],
        ),
        timestamp_ms=lambda: 1,
    )

    assert [divergence["divergence_type"] for divergence in result.divergences] == [
        "unmatched_exchange_order"
    ]
    assert result.kill_switch_engaged is True
    orders = repository.get_orders()
    trades = repository.get_trades()
    assert len(orders) == 1
    assert len(trades) == 1
    assert orders[0].exchange_order_id == "ex-discovered"
    assert trades[0].order_id == orders[0].order_id
    assert trades[0].order_id != "okx-client:client-discovered"
    assert orders[0].filled_amount == pytest.approx(0.4)
    assert orders[0].remaining_amount == pytest.approx(0.6)


@pytest.mark.asyncio
async def test_private_sync_reconciles_new_pending_snapshot_with_same_sync_trade():
    repo = FakeRepository()

    result = await sync_private_state(
        repo,
        FakeAdapter(
            orders=[exchange_order(amount=1.0, updated_at=1700000001000)],
            trades=[exchange_trade(amount=0.4, price=51000.0)],
        ),
        timestamp_ms=lambda: 1,
    )

    assert result.orders_upserted == 1
    assert result.trades_upserted == 1
    persisted = repo.orders[0]
    assert persisted.status == OrderStatus.PARTIALLY_FILLED.value
    assert persisted.filled_amount == pytest.approx(0.4)
    assert persisted.remaining_amount == pytest.approx(0.6)
    assert persisted.fill_price == pytest.approx(51000.0)
    assert persisted.snapshot_updated_at == 1700000001000


@pytest.mark.asyncio
async def test_private_sync_completes_open_snapshot_from_same_round_exchange_id_trade():
    repo = FakeRepository(orders=[local_order(amount=1.0)])

    await sync_private_state(
        repo,
        FakeAdapter(
            orders=[exchange_order(
                amount=1.0,
                filled_amount=None,
                remaining_amount=None,
                fill_price=None,
            )],
            trades=[exchange_trade(
                exchange_trade_id="same-round-exchange-only",
                exchange_order_id="ex-local-1",
                client_order_id="",
                amount=0.4,
                price=51000.0,
            )],
        ),
        timestamp_ms=lambda: 1,
    )

    persisted = repo.orders[0]
    assert persisted.filled_amount == pytest.approx(0.4)
    assert persisted.remaining_amount == pytest.approx(0.6)
    assert persisted.fill_price == pytest.approx(51000.0)
    assert persisted.status == OrderStatus.PARTIALLY_FILLED.value


@pytest.mark.asyncio
async def test_private_sync_completes_missing_open_snapshot_average_from_same_round_trade():
    repo = FakeRepository(orders=[local_order(amount=1.0)])

    await sync_private_state(
        repo,
        FakeAdapter(
            orders=[exchange_order(
                amount=1.0,
                filled_amount=0.4,
                remaining_amount=0.6,
                fill_price=None,
            )],
            trades=[exchange_trade(
                exchange_trade_id="same-round-average-missing",
                exchange_order_id="ex-local-1",
                client_order_id="",
                amount=0.4,
                price=51000.0,
            )],
        ),
        timestamp_ms=lambda: 1,
    )

    persisted = repo.orders[0]
    assert persisted.filled_amount == pytest.approx(0.4)
    assert persisted.remaining_amount == pytest.approx(0.6)
    assert persisted.fill_price == pytest.approx(51000.0)


@pytest.mark.asyncio
async def test_private_sync_completes_explicit_zero_open_snapshot_from_same_round_trade():
    repo = FakeRepository(orders=[local_order(amount=1.0)])

    await sync_private_state(
        repo,
        FakeAdapter(
            orders=[exchange_order(
                amount=1.0,
                filled_amount=0.0,
                remaining_amount=1.0,
                fill_price=0.0,
            )],
            trades=[exchange_trade(
                exchange_trade_id="same-round-explicit-zero",
                exchange_order_id="ex-local-1",
                client_order_id="",
                amount=0.4,
                price=51000.0,
            )],
        ),
        timestamp_ms=lambda: 1,
    )

    persisted = repo.orders[0]
    assert persisted.filled_amount == pytest.approx(0.4)
    assert persisted.remaining_amount == pytest.approx(0.6)
    assert persisted.fill_price == pytest.approx(51000.0)
    assert persisted.status == OrderStatus.PARTIALLY_FILLED.value


@pytest.mark.asyncio
async def test_private_sync_persists_snapshot_baseline_before_historical_trade_sync():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    repo = Repository(engine)
    repo.upsert_order(local_order(amount=1.0))

    await sync_private_state(
        repo,
        FakeAdapter(
            orders=[exchange_order(amount=1.0, filled_amount=0.5, remaining_amount=0.5)],
            trades=[],
        ),
        timestamp_ms=lambda: 1,
    )
    await sync_private_state(
        repo,
        FakeAdapter(
            orders=[],
            trades=[exchange_trade(amount=0.5, exchange_trade_id="historical-half")],
        ),
        timestamp_ms=lambda: 2,
    )

    persisted = repo.get_orders()[0]

    assert persisted.snapshot_filled_amount == pytest.approx(0.5)
    assert persisted.filled_amount == pytest.approx(0.5)
    assert persisted.status == OrderStatus.PARTIALLY_FILLED.value


@pytest.mark.asyncio
async def test_private_sync_does_not_double_count_trade_after_snapshot_fill():
    repo = FakeRepository(orders=[local_order(amount=1.0)])
    first_adapter = FakeAdapter(
        orders=[exchange_order(amount=1.0, filled_amount=0.5, remaining_amount=0.5)],
        trades=[],
    )
    await sync_private_state(repo, first_adapter, timestamp_ms=lambda: 1)

    second_adapter = FakeAdapter(
        orders=[],
        trades=[exchange_trade(amount=0.5, exchange_trade_id="historical-half")],
    )
    await sync_private_state(repo, second_adapter, timestamp_ms=lambda: 2)

    assert repo.orders[0].filled_amount == pytest.approx(0.5)
    assert repo.orders[0].status == OrderStatus.PARTIALLY_FILLED.value


@pytest.mark.asyncio
async def test_private_sync_covers_snapshot_with_sorted_historical_trade_prefix():
    repo = FakeRepository(orders=[local_order(amount=1.0)])

    await sync_private_state(
        repo,
        FakeAdapter(
            orders=[exchange_order(amount=1.0, filled_amount=0.5, remaining_amount=0.5)],
            trades=[
                exchange_trade(
                    amount=0.2, exchange_trade_id="history-2", timestamp=1700000002000
                ),
                exchange_trade(
                    amount=0.3, exchange_trade_id="history-1", timestamp=1700000001000
                ),
            ],
        ),
        timestamp_ms=lambda: 1,
    )

    assert repo.orders[0].filled_amount == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_private_sync_adds_new_trade_after_historical_snapshot_prefix():
    repo = FakeRepository(orders=[local_order(amount=1.0)])

    await sync_private_state(
        repo,
        FakeAdapter(
            orders=[exchange_order(amount=1.0, filled_amount=0.5, remaining_amount=0.5)],
            trades=[
                exchange_trade(
                    amount=0.3, exchange_trade_id="history-1", timestamp=1700000001000
                ),
                exchange_trade(
                    amount=0.2, exchange_trade_id="history-2", timestamp=1700000002000
                ),
                exchange_trade(
                    amount=0.1, exchange_trade_id="new-1", timestamp=1700000003000
                ),
            ],
        ),
        timestamp_ms=lambda: 1,
    )

    assert repo.orders[0].filled_amount == pytest.approx(0.5)
    assert len(repo.trades) == 3


@pytest.mark.asyncio
async def test_private_sync_weights_incremental_fill_price_with_existing_fill():
    repo = FakeRepository(
        orders=[
            local_order(
                amount=1.0,
                status=OrderStatus.PARTIALLY_FILLED.value,
                filled_amount=0.5,
                remaining_amount=0.5,
                fill_price=100.0,
            )
        ]
    )

    await sync_private_state(
        repo,
        FakeAdapter(
            orders=[],
            trades=[exchange_trade(amount=0.1, price=200.0, exchange_trade_id="new-fill")],
        ),
        timestamp_ms=lambda: 2,
    )

    assert repo.orders[0].filled_amount == pytest.approx(0.5)
    assert repo.orders[0].fill_price == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_private_sync_prefers_valid_authoritative_snapshot_average():
    repo = FakeRepository(
        orders=[
            local_order(
                amount=1.0,
                status=OrderStatus.PARTIALLY_FILLED.value,
                filled_amount=0.5,
                remaining_amount=0.5,
                fill_price=100.0,
                snapshot_filled_amount=0.5,
            )
        ]
    )

    await sync_private_state(
        repo,
        FakeAdapter(
            orders=[
                exchange_order(
                    amount=1.0,
                    filled_amount=0.6,
                    remaining_amount=0.4,
                    fill_price=123.0,
                )
            ],
            trades=[exchange_trade(amount=0.1, price=200.0, exchange_trade_id="new-fill")],
        ),
        timestamp_ms=lambda: 2,
    )

    assert repo.orders[0].filled_amount == pytest.approx(0.6)
    assert repo.orders[0].fill_price == pytest.approx(123.0)


@pytest.mark.asyncio
async def test_private_sync_counts_incremental_trade_when_snapshot_is_unchanged():
    repo = FakeRepository(orders=[local_order(amount=1.0)])
    adapter = FakeAdapter(
        orders=[exchange_order(amount=1.0, filled_amount=0.4, remaining_amount=0.6)],
        trades=[],
    )
    await sync_private_state(repo, adapter, timestamp_ms=lambda: 1)

    await sync_private_state(
        repo,
        FakeAdapter(
            orders=[exchange_order(amount=1.0, filled_amount=0.4, remaining_amount=0.6)],
            trades=[exchange_trade(amount=0.1, exchange_trade_id="new-fill")],
        ),
        timestamp_ms=lambda: 2,
    )

    assert repo.orders[0].filled_amount == pytest.approx(0.4)
    assert repo.orders[0].status == OrderStatus.PARTIALLY_FILLED.value


@pytest.mark.asyncio
async def test_private_sync_counts_trade_after_order_disappears_without_snapshot_delta():
    repo = FakeRepository(orders=[local_order(amount=1.0)])
    await sync_private_state(
        repo,
        FakeAdapter(
            orders=[exchange_order(amount=1.0, filled_amount=0.5, remaining_amount=0.5)],
            trades=[],
        ),
        timestamp_ms=lambda: 1,
    )

    await sync_private_state(
        repo,
        FakeAdapter(
            orders=[],
            trades=[exchange_trade(amount=0.1, exchange_trade_id="new-fill")],
        ),
        timestamp_ms=lambda: 2,
    )

    assert repo.orders[0].filled_amount == pytest.approx(0.5)
    assert repo.orders[0].status == OrderStatus.PARTIALLY_FILLED.value


@pytest.mark.asyncio
async def test_private_sync_counts_complete_since_page_after_snapshot_disappears():
    class SincePage(list):
        mode = "since"
        since = 1700000001000
        complete = True

    class SinceAdapter(FakeAdapter):
        async def fetch_recent_trade_snapshots(self, symbols=None, since=None, limit=100):
            return SincePage(self.trades)

    repo = FakeRepository(orders=[local_order(amount=1.0)])
    await sync_private_state(
        repo,
        FakeAdapter(
            orders=[exchange_order(
                amount=1.0, filled_amount=0.5, remaining_amount=0.5,
                fill_price=100.0,
            )],
            trades=[],
        ),
        timestamp_ms=lambda: 1,
    )
    await sync_private_state(
        repo,
        SinceAdapter(
            orders=[],
            trades=[exchange_trade(
                amount=0.1, price=200.0, exchange_trade_id="new-fill",
            )],
        ),
        since=1700000001000,
        timestamp_ms=lambda: 2,
    )

    assert repo.orders[0].filled_amount == pytest.approx(0.6)
    assert repo.orders[0].remaining_amount == pytest.approx(0.4)
    assert repo.orders[0].fill_price == pytest.approx(116.6666666667)

    next_page = SinceAdapter(
        orders=[],
        trades=[
            exchange_trade(amount=0.1, price=200.0, exchange_trade_id="new-fill"),
            exchange_trade(
                amount=0.1, price=300.0,
                exchange_trade_id="next-fill", timestamp=1700000004000,
            ),
        ],
    )
    await sync_private_state(
        repo, next_page, since=1700000001000, timestamp_ms=lambda: 3,
    )
    await sync_private_state(
        repo, next_page, since=1700000001000, timestamp_ms=lambda: 4,
    )

    assert repo.orders[0].filled_amount == pytest.approx(0.7)
    assert repo.orders[0].fill_price == pytest.approx(142.857142857)


@pytest.mark.asyncio
async def test_private_sync_does_not_complete_fill_from_unmarked_trade_page():
    class UnmarkedAdapter(FakeAdapter):
        async def fetch_recent_trade_snapshots(self, symbols=None, since=None, limit=100):
            return self.trades

    repo = FakeRepository(orders=[local_order(amount=1.0)])
    await sync_private_state(
        repo,
        UnmarkedAdapter(
            orders=[], trades=[exchange_trade(
                amount=1.0, price=200.0, exchange_trade_id="unmarked-fill",
            )],
        ),
        timestamp_ms=lambda: 2,
    )

    assert repo.orders[0].filled_amount == pytest.approx(0.0)
    assert repo.orders[0].status == OrderStatus.PENDING.value


@pytest.mark.asyncio
async def test_private_sync_does_not_complete_fill_from_truncated_since_page():
    class TruncatedPage(list):
        mode = "since"
        since = 1700000001000
        complete = False

    class TruncatedAdapter(FakeAdapter):
        async def fetch_recent_trade_snapshots(self, symbols=None, since=None, limit=100):
            return TruncatedPage(self.trades)

    repo = FakeRepository(orders=[local_order(
        amount=1.0, status=OrderStatus.PARTIALLY_FILLED.value,
        filled_amount=0.5, remaining_amount=0.5, fill_price=100.0,
        snapshot_filled_amount=0.5, snapshot_updated_at=1700000001000,
    )])
    await sync_private_state(
        repo,
        TruncatedAdapter(
            orders=[], trades=[exchange_trade(
                amount=0.1, price=200.0, exchange_trade_id="recent-fill",
            )],
        ),
        since=1700000001000,
        timestamp_ms=lambda: 2,
    )

    assert repo.orders[0].filled_amount == pytest.approx(0.5)
    assert repo.orders[0].fill_price == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_private_sync_keeps_snapshot_fill_and_remaining_when_order_disappears():
    repo = FakeRepository(orders=[local_order(amount=1.0)])
    await sync_private_state(
        repo,
        FakeAdapter(
            orders=[exchange_order(amount=1.0, filled_amount=0.5, remaining_amount=0.5)],
            trades=[],
        ),
        timestamp_ms=lambda: 1,
    )

    await sync_private_state(
        repo,
        FakeAdapter(
            orders=[exchange_order(amount=1.0, filled_amount=0.6, remaining_amount=0.4)],
            trades=[exchange_trade(amount=0.1, exchange_trade_id="covered-by-snapshot")],
        ),
        timestamp_ms=lambda: 2,
    )

    assert repo.orders[0].filled_amount == pytest.approx(0.6)
    assert repo.orders[0].remaining_amount == pytest.approx(0.4)
    assert repo.orders[0].status == OrderStatus.PARTIALLY_FILLED.value


@pytest.mark.asyncio
async def test_private_sync_does_not_mark_disappeared_order_filled_from_partial_trade():
    repo = FakeRepository(orders=[local_order(amount=1.0)])
    await sync_private_state(
        repo,
        FakeAdapter(
            orders=[exchange_order(amount=1.0, filled_amount=0.6, remaining_amount=0.4)],
            trades=[],
        ),
        timestamp_ms=lambda: 1,
    )

    await sync_private_state(
        repo,
        FakeAdapter(
            orders=[],
            trades=[exchange_trade(amount=0.1, exchange_trade_id="partial-after-snapshot")],
        ),
        timestamp_ms=lambda: 2,
    )

    assert repo.orders[0].filled_amount >= 0.6
    assert repo.orders[0].status != OrderStatus.FILLED.value
    assert repo.orders[0].remaining_amount is not None


@pytest.mark.asyncio
async def test_private_sync_adds_new_trade_beyond_snapshot_fill():
    repo = FakeRepository(orders=[local_order(amount=1.0)])
    await sync_private_state(
        repo,
        FakeAdapter(
            orders=[exchange_order(amount=1.0, filled_amount=0.5, remaining_amount=0.5)]
        ),
        timestamp_ms=lambda: 1,
    )

    await sync_private_state(
        repo,
        FakeAdapter(
            orders=[],
            trades=[
                exchange_trade(amount=0.5, exchange_trade_id="historical-half"),
                exchange_trade(amount=0.5, exchange_trade_id="new-half"),
            ],
        ),
        timestamp_ms=lambda: 2,
    )

    assert repo.orders[0].filled_amount == pytest.approx(1.0)
    assert repo.orders[0].status == OrderStatus.FILLED.value


@pytest.mark.asyncio
async def test_private_sync_upserts_snapshots_and_attributes_orders_and_trades():
    repo = FakeRepository(orders=[local_order()])
    adapter = FakeAdapter(
        orders=[
            exchange_order(),
            exchange_order(
                exchange_order_id="ex-unmatched",
                client_order_id="",
                symbol="ETH-USDT",
                side=OrderSide.SELL,
                amount=1.0,
                price=3000.0,
            ),
        ],
        trades=[
            exchange_trade(),
            exchange_trade(
                exchange_trade_id="trade-2",
                exchange_order_id="ex-unmatched",
                client_order_id="",
                symbol="ETH-USDT",
                side=OrderSide.SELL,
                amount=1.0,
                price=3000.0,
                fee=0.0,
            ),
        ],
    )

    result = await sync_private_state(
        repo,
        adapter,
        symbols=["BTC-USDT"],
        since=1700000000000,
        timestamp_ms=lambda: 1700000005000,
    )

    assert result.as_response() == {
        "account_upserted": 1,
        "orders_upserted": 1,
        "trades_upserted": 1,
        "trades_status": "available",
        "risk_events_saved": 0,
        "kill_switch_engaged": False,
        "divergences": [],
    }
    assert adapter.trade_calls == [{"symbols": ["BTC-USDT"], "since": 1700000000000, "limit": 100}]
    assert repo.accounts[0].strategy == "__exchange__"
    assert repo.accounts[0].equity == 1010.0
    assert [order.strategy for order in repo.upserted_orders] == ["ma_cross"]
    assert [order.order_id for order in repo.upserted_orders] == ["local-1"]
    assert [trade.strategy for trade in repo.upserted_trades] == ["ma_cross"]
    assert [trade.order_id for trade in repo.upserted_trades] == ["local-1"]


@pytest.mark.asyncio
async def test_recent_trades_fill_missing_pending_order_without_engaging_kill_switch():
    repo = FakeRepository(orders=[local_order()])
    adapter = FakeAdapter(
        trades=[
            exchange_trade(
                exchange_trade_id="trade-1",
                amount=0.04,
                price=49000.0,
                timestamp=1700000003000,
            ),
            exchange_trade(
                exchange_trade_id="trade-2",
                amount=0.06,
                price=51000.0,
                timestamp=1700000004000,
            ),
        ]
    )

    result = await sync_private_state(
        repo,
        adapter,
        timestamp_ms=lambda: 1700000005000,
    )

    assert result.orders_upserted == 1
    assert result.trades_upserted == 2
    assert result.risk_events_saved == 0
    assert result.kill_switch_engaged is False
    assert result.divergences == []
    assert repo.kill_switch is False
    assert repo.risk_events == []
    assert len(repo.orders) == 1
    assert repo.orders[0].order_id == "local-1"
    assert repo.orders[0].strategy == "ma_cross"
    assert repo.orders[0].status == "filled"
    assert repo.orders[0].fill_price == pytest.approx(50200.0)
    assert repo.orders[0].updated_at == 1700000004000
    assert [trade.order_id for trade in repo.trades] == ["local-1", "local-1"]
    assert [trade.strategy for trade in repo.trades] == ["ma_cross", "ma_cross"]

    repeated = await sync_private_state(
        repo,
        adapter,
        timestamp_ms=lambda: 1700000006000,
    )

    assert repeated.orders_upserted == 0
    assert repeated.risk_events_saved == 0
    assert len(repo.orders) == 1
    assert len(repo.trades) == 2
    assert [order.order_id for order in repo.orders] == ["local-1"]


@pytest.mark.asyncio
async def test_recent_trade_matches_pending_order_by_client_id():
    repo = FakeRepository(
        orders=[local_order(exchange_order_id="", client_order_id="client-1")]
    )
    adapter = FakeAdapter(
        trades=[exchange_trade(exchange_order_id="ex-discovered")]
    )

    result = await sync_private_state(
        repo,
        adapter,
        timestamp_ms=lambda: 1700000005000,
    )

    assert result.orders_upserted == 1
    assert result.risk_events_saved == 0
    assert repo.orders[0].order_id == "local-1"
    assert repo.orders[0].exchange_order_id == "ex-discovered"
    assert repo.orders[0].client_order_id == "client-1"
    assert repo.orders[0].strategy == "ma_cross"
    assert repo.orders[0].status == "filled"
    assert repo.trades[0].order_id == "local-1"


@pytest.mark.asyncio
async def test_recent_trade_updates_existing_synthetic_order_without_duplicate():
    repo = FakeRepository(
        orders=[
            local_order(
                order_id="okx:ex-local-1",
                client_order_id="",
                strategy="__exchange__",
            )
        ]
    )
    adapter = FakeAdapter(trades=[exchange_trade(client_order_id="")])

    result = await sync_private_state(
        repo,
        adapter,
        timestamp_ms=lambda: 1700000005000,
    )

    assert result.orders_upserted == 1
    assert result.risk_events_saved == 0
    assert len(repo.orders) == 1
    assert repo.orders[0].order_id == "okx:ex-local-1"
    assert repo.orders[0].status == "filled"
    assert repo.trades[0].order_id == "okx:ex-local-1"


@pytest.mark.asyncio
async def test_high_risk_divergence_engages_kill_switch_before_events_and_upserts():
    repo = FakeRepository(orders=[local_order(exchange_order_id="ex-missing")])
    adapter = FakeAdapter()

    await sync_private_state(repo, adapter, timestamp_ms=lambda: 1700000005000)

    event_key = repo.risk_events[0]["event_key"]
    assert repo.kill_switch is True
    assert repo.calls.index("set_kill_switch") < repo.calls.index(
        f"save_risk_event:high:{event_key}"
    )
    assert repo.calls.index(f"save_risk_event:high:{event_key}") < repo.calls.index(
        "upsert_account"
    )
    assert repo.risk_events[0]["reason_code"] == "private_sync_divergence"


@pytest.mark.asyncio
async def test_private_sync_dedupes_divergence_events_within_one_request():
    duplicate_order = exchange_order(
        exchange_order_id="ex-unmatched",
        client_order_id="",
        symbol="ETH-USDT",
        side=OrderSide.SELL,
        amount=1.0,
        price=3000.0,
    )
    repo = FakeRepository()
    adapter = FakeAdapter(orders=[duplicate_order, duplicate_order])

    await sync_private_state(repo, adapter, timestamp_ms=lambda: 1700000005000)

    event_keys = [event["event_key"] for event in repo.risk_events]
    assert len(event_keys) == 1
    assert len(event_keys[0]) == 64
    assert repo.calls.count("set_kill_switch") == 1


@pytest.mark.asyncio
async def test_private_sync_persists_only_valid_account_assets():
    repo = FakeRepository()
    account = AccountSnapshot(
        equity=1000.0,
        cash_balance=900.0,
        updated_at=10,
        assets=[None, 1, {"ccy": "USDT", "avail_bal": 850.0}],
    )

    await sync_private_state(repo, FakeAdapter(account=account), timestamp_ms=lambda: 10)

    assert repo.accounts[0].assets == [
        {
            "ccy": "USDT",
            "cash_bal": 0.0,
            "eq": 0.0,
            "eq_utd": 0.0,
            "avail_bal": 850.0,
            "upl": 0.0,
        }
    ]


@pytest.mark.asyncio
async def test_recent_trade_error_does_not_block_account_and_order_persistence():
    class TradeFailingAdapter(FakeAdapter):
        async def fetch_recent_trade_snapshots(self, symbols=None, since=None, limit=100):
            raise TimeoutError("trades timed out")

    repo = FakeRepository()
    adapter = TradeFailingAdapter(
        account=AccountSnapshot(
            initial_equity=1000.0,
            cash_balance=900.0,
            available_balance=850.0,
            equity=1010.0,
            timestamp=1700000000000,
            assets=[AssetBalance(ccy="USDT", cash_bal=900.0, eq=1010.0, avail_bal=850.0)],
        ),
        orders=[exchange_order(exchange_order_id="ex-1", client_order_id="")],
    )

    result = await sync_private_state(repo, adapter, timestamp_ms=lambda: 1700000005000)

    assert result.trades_upserted == 0
    assert repo.accounts[0].available_balance == 850.0
    assert repo.accounts[0].assets == [
        {
            "ccy": "USDT",
            "cash_bal": 900.0,
            "eq": 1010.0,
            "eq_utd": 0.0,
            "avail_bal": 850.0,
            "upl": 0.0,
        }
    ]
    assert [order.order_id for order in repo.upserted_orders] == ["okx:ex-1"]
    assert repo.upserted_trades == []


@pytest.mark.asyncio
async def test_private_sync_promotes_persisted_partial_order_to_filled_from_later_trade():
    repo = FakeRepository(
        orders=[
            local_order(
                status=OrderStatus.PARTIALLY_FILLED.value,
                filled_amount=0.04,
                remaining_amount=0.06,
            )
        ]
    )
    adapter = FakeAdapter(
        trades=[
            exchange_trade(
                exchange_trade_id="trade-final",
                amount=0.06,
                price=51000.0,
                timestamp=1700000004000,
            )
        ]
    )

    result = await sync_private_state(repo, adapter, timestamp_ms=lambda: 1700000005000)

    assert result.orders_upserted == 1
    assert result.trades_upserted == 1
    assert result.risk_events_saved == 1
    assert result.kill_switch_engaged is True
    assert result.divergences[0]["divergence_type"] == "partially_filled_order_missing"
    assert repo.upserted_orders[0].status == OrderStatus.PARTIALLY_FILLED.value
    assert repo.upserted_orders[0].filled_amount == pytest.approx(0.06)
    assert repo.upserted_orders[0].remaining_amount == pytest.approx(0.04)
    assert repo.upserted_orders[0].fill_price == pytest.approx(51000.0)


@pytest.mark.asyncio
async def test_private_sync_keeps_two_reliable_ids_with_identical_trade_fields():
    repo = FakeRepository(orders=[local_order(amount=2.0)])
    trades = [
        exchange_trade(exchange_trade_id="trade-a", amount=0.1, timestamp=123),
        exchange_trade(exchange_trade_id="trade-b", amount=0.1, timestamp=123),
    ]

    result = await sync_private_state(repo, FakeAdapter(trades=trades), timestamp_ms=lambda: 1)

    assert result.trades_upserted == 2
    assert [trade.exchange_trade_id for trade in repo.trades] == ["trade-a", "trade-b"]


@pytest.mark.asyncio
async def test_private_sync_deduplicates_trade_ids_within_and_across_syncs():
    repo = FakeRepository(orders=[local_order()])
    adapter = FakeAdapter(
        trades=[
            exchange_trade(
                exchange_trade_id="trade-repeat",
                amount=0.04,
                timestamp=1700000003000,
            ),
            exchange_trade(
                exchange_trade_id="trade-repeat",
                amount=0.04,
                timestamp=1700000003000,
            ),
        ]
    )

    first = await sync_private_state(repo, adapter, timestamp_ms=lambda: 1700000005000)

    assert first.trades_upserted == 1
    assert repo.upserted_orders[0].filled_amount == pytest.approx(0.04)
    assert repo.upserted_orders[0].status == OrderStatus.PARTIALLY_FILLED.value
    assert len(repo.trades) == 1

    second = await sync_private_state(repo, adapter, timestamp_ms=lambda: 1700000006000)

    assert second.trades_upserted == 0
    assert second.orders_upserted == 0
    assert repo.upserted_orders[0].filled_amount == pytest.approx(0.04)
    assert len(repo.trades) == 1


@pytest.mark.asyncio
async def test_private_sync_skips_identical_replay_of_persisted_trade_without_risk_event():
    repo = FakeRepository(orders=[local_order()])
    existing = persisted_trade(trade_id="reliable-replay", amount=0.04, price=50000.0, timestamp=1)
    existing.fee = 2.5
    repo.trades.append(existing)

    result = await sync_private_state(
        repo,
        FakeAdapter(
            orders=[exchange_order()],
            trades=[exchange_trade(
                exchange_trade_id="reliable-replay",
                amount=0.04,
                price=50000.0,
                timestamp=1,
            )],
        ),
        timestamp_ms=lambda: 2,
    )

    assert result.trades_upserted == 0
    assert result.risk_events_saved == 0
    assert repo.risk_events == []
    assert repo.trades == [existing]


@pytest.mark.asyncio
async def test_private_sync_records_persisted_trade_id_fact_conflict_without_overwriting_it():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    repo = Repository(engine)
    existing = persisted_trade(
        trade_id="reliable-conflict", amount=0.04, price=50000.0, timestamp=1
    )
    repo.upsert_order(local_order())
    repo.upsert_trade(existing)

    conflicting = exchange_trade(
        exchange_trade_id="reliable-conflict",
        amount=0.08,
        price=51000.0,
        timestamp=2,
    )
    first = await sync_private_state(
        repo, FakeAdapter(orders=[exchange_order()], trades=[conflicting]), timestamp_ms=lambda: 3
    )
    second = await sync_private_state(
        repo, FakeAdapter(orders=[exchange_order()], trades=[conflicting]), timestamp_ms=lambda: 4
    )

    persisted = repo.get_trades()[0]
    assert (persisted.amount, persisted.price, persisted.timestamp) == (0.04, 50000.0, 1)
    assert first.trades_upserted == 0
    assert first.risk_events_saved == 1
    assert first.kill_switch_engaged is True
    assert first.divergences[0]["divergence_type"] == "conflicting_exchange_trade_facts"
    assert first.divergences[0]["exchange_trade_id"] == "reliable-conflict"
    assert second.trades_upserted == 0
    assert second.risk_events_saved == 0
    assert len(repo.get_risk_events()) == 1
    assert repo.get_trades()[0].model_dump() == persisted.model_dump()


@pytest.mark.asyncio
async def test_private_sync_keeps_filled_order_filled_for_stale_open_snapshot():
    repo = FakeRepository(
        orders=[
            local_order(
                status=OrderStatus.FILLED.value,
                filled_amount=0.1,
                remaining_amount=0.0,
            )
        ]
    )
    adapter = FakeAdapter(
        orders=[
            exchange_order(
                status=OrderStatus.PENDING,
                filled_amount=0.0,
                remaining_amount=0.1,
                updated_at=1699999998000,
            )
        ]
    )

    result = await sync_private_state(repo, adapter, timestamp_ms=lambda: 1700000005000)

    assert result.orders_upserted == 1
    assert repo.orders[0].status == OrderStatus.FILLED.value
    assert repo.orders[0].filled_amount == pytest.approx(0.1)
    assert repo.orders[0].remaining_amount == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_private_sync_checks_persisted_partial_order_missing_from_open_orders():
    repo = FakeRepository(
        orders=[
            local_order(
                status=OrderStatus.PARTIALLY_FILLED.value,
                filled_amount=0.04,
                remaining_amount=0.06,
            )
        ]
    )
    adapter = FakeAdapter()

    result = await sync_private_state(repo, adapter, timestamp_ms=lambda: 1700000005000)

    assert result.risk_events_saved == 1
    assert result.kill_switch_engaged is True
    assert result.divergences[0]["divergence_type"] == "partially_filled_order_missing"
    assert result.divergences[0]["order_id"] == "local-1"


@pytest.mark.asyncio
async def test_private_sync_symbol_scope_ignores_other_local_pending_and_partial_orders():
    repository = FakeRepository(
        orders=[
            local_order(order_id="btc-pending", symbol="BTC-USDT"),
            local_order(
                order_id="eth-partial",
                exchange_order_id="ex-eth",
                client_order_id="client-eth",
                symbol="ETH-USDT",
                status=OrderStatus.PARTIALLY_FILLED.value,
                filled_amount=0.04,
                remaining_amount=0.06,
            ),
        ]
    )

    result = await sync_private_state(
        repository,
        FakeAdapter(orders=[], trades=[]),
        symbols=["BTC-USDT"],
        timestamp_ms=lambda: 1700000005000,
    )

    assert result.kill_switch_engaged is True
    assert [divergence["order_id"] for divergence in result.divergences] == ["btc-pending"]
    assert all(divergence["symbol"] == "BTC-USDT" for divergence in result.divergences)


@pytest.mark.asyncio
async def test_private_sync_matches_inverse_scope_and_provider_alias_exactly():
    repository = FakeRepository(
        orders=[
            local_order(
                symbol="BTC-USD-260925",
                exchange_order_id="inverse-order",
                client_order_id="inverse-client",
            )
        ]
    )
    provider_order = exchange_order(
        symbol="BTC/USD:BTC-260925",
        exchange_order_id="inverse-order",
        client_order_id="inverse-client",
    )
    wrong_settlement = exchange_order(
        symbol="BTC/USD:USD-260925",
        exchange_order_id="wrong-settlement-order",
        client_order_id="wrong-settlement-client",
    )

    result = await sync_private_state(
        repository,
        FakeAdapter(orders=[provider_order, wrong_settlement], trades=[]),
        symbols=["BTC-USD-260925"],
        timestamp_ms=lambda: 1700000005000,
    )

    assert result.divergences == []
    assert result.kill_switch_engaged is False
    assert repository.orders[0].symbol == "BTC-USD-260925"


@pytest.mark.asyncio
async def test_private_sync_matches_inverse_swap_scope_without_merging_settlements():
    repository = FakeRepository(
        orders=[
            local_order(
                symbol="BTC-USD-SWAP",
                exchange_order_id="inverse-swap-order",
                client_order_id="inverse-swap-client",
            )
        ]
    )
    provider_order = exchange_order(
        symbol="BTC/USD:BTC",
        exchange_order_id="inverse-swap-order",
        client_order_id="inverse-swap-client",
    )
    wrong_settlement = exchange_order(
        symbol="BTC/USD:USD",
        exchange_order_id="wrong-settlement-order",
        client_order_id="wrong-settlement-client",
    )

    result = await sync_private_state(
        repository,
        FakeAdapter(orders=[provider_order, wrong_settlement], trades=[]),
        symbols=["BTC-USD-SWAP"],
        timestamp_ms=lambda: 1700000005000,
    )

    assert result.divergences == []
    assert result.kill_switch_engaged is False
    assert [order.symbol for order in repository.orders] == ["BTC-USD-SWAP"]


@pytest.mark.asyncio
@pytest.mark.parametrize("symbols", [[], ["BTC-USDT", "BTC-USDT"]])
async def test_private_sync_accepts_empty_and_duplicate_symbol_scopes(symbols):
    repository = FakeRepository()

    result = await sync_private_state(
        repository,
        FakeAdapter(orders=[], trades=[]),
        symbols=symbols,
        timestamp_ms=lambda: 1700000005000,
    )

    assert result.divergences == []
    assert result.kill_switch_engaged is False


def persisted_trade(
    *, trade_id: str, amount: float, price: float, timestamp: int = 1
) -> TradeRecord:
    return TradeRecord(
        source="live_exchange",
        runtime_mode="live",
        exchange_trade_id=trade_id,
        order_id="local-1",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        amount=amount,
        price=price,
        fee=0.0,
        timestamp=timestamp,
    )


def test_reconcile_order_uses_contracts_for_derivative_trade_fill():
    local = local_order(amount=100.0, remaining_amount=100.0)
    trade = persisted_trade(trade_id="linear-fill", amount=1.0, price=50000.0)
    trade.contracts = 100.0
    trade.quantity = 1.0
    trade.contract_size = 0.01

    reconciled = private_sync.reconcile_order(local, None, [trade])

    assert reconciled.filled_amount == pytest.approx(100.0)
    assert reconciled.remaining_amount == pytest.approx(0.0)
    assert reconciled.status == OrderStatus.FILLED.value


def test_reconcile_order_snapshot_is_authoritative_over_all_persisted_trades():
    local = local_order(amount=1.0)
    snapshot = exchange_order(
        amount=1.0,
        filled_amount=0.5,
        remaining_amount=0.5,
        fill_price=101.0,
        updated_at=1700000001000,
    )

    reconciled = private_sync.reconcile_order(
        local,
        snapshot,
        [
            persisted_trade(trade_id="one", amount=0.3, price=90.0),
            persisted_trade(trade_id="two", amount=0.3, price=110.0),
        ],
    )

    assert reconciled.filled_amount == pytest.approx(0.5)
    assert reconciled.remaining_amount == pytest.approx(0.5)
    assert reconciled.fill_price == pytest.approx(101.0)
    assert reconciled.snapshot_filled_amount == pytest.approx(0.5)


def test_reconcile_order_newer_filled_snapshot_missing_fill_fields_preserves_local_fill():
    local = local_order(
        amount=1.0,
        status=OrderStatus.PARTIALLY_FILLED.value,
        filled_amount=0.6,
        remaining_amount=0.4,
        fill_price=123.0,
        updated_at=100,
        snapshot_updated_at=100,
    )
    snapshot = ExchangeOrderSnapshot(
        exchange_order_id="ex-local-1",
        client_order_id="client-1",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=1.0,
        price=50000.0,
        status=OrderStatus.FILLED,
        fill_price=None,
        timestamp=200,
        updated_at=200,
    )

    reconciled = private_sync.reconcile_order(local, snapshot, [])

    assert reconciled.status == OrderStatus.PARTIALLY_FILLED.value
    assert reconciled.filled_amount == pytest.approx(0.6)
    assert reconciled.remaining_amount == pytest.approx(0.4)
    assert reconciled.fill_price == pytest.approx(123.0)
    assert reconciled.snapshot_filled_amount == pytest.approx(0.0)


def test_reconcile_order_newer_filled_snapshot_missing_fill_fields_keeps_zero_local_fill():
    local = local_order(
        amount=1.0,
        status=OrderStatus.PENDING.value,
        filled_amount=0.0,
        remaining_amount=1.0,
        fill_price=0.0,
        updated_at=100,
        snapshot_updated_at=100,
    )
    snapshot = ExchangeOrderSnapshot(
        exchange_order_id="ex-local-1",
        client_order_id="client-1",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=1.0,
        price=50000.0,
        status=OrderStatus.FILLED,
        fill_price=None,
        timestamp=200,
        updated_at=200,
    )

    reconciled = private_sync.reconcile_order(local, snapshot, [])

    assert reconciled.status == OrderStatus.PENDING.value
    assert reconciled.filled_amount == pytest.approx(0.0)
    assert reconciled.remaining_amount == pytest.approx(1.0)
    assert reconciled.fill_price == pytest.approx(0.0)


def test_reconcile_order_explicit_zero_filled_filled_snapshot_cannot_erase_local_fill():
    local = local_order(
        amount=1.0,
        status=OrderStatus.PARTIALLY_FILLED.value,
        filled_amount=0.6,
        remaining_amount=0.4,
        fill_price=123.0,
        updated_at=100,
        snapshot_updated_at=100,
    )
    snapshot = exchange_order(
        amount=1.0,
        status=OrderStatus.FILLED,
        filled_amount=0.0,
        remaining_amount=1.0,
        fill_price=0.0,
        updated_at=200,
    )

    reconciled = private_sync.reconcile_order(local, snapshot, [])

    assert reconciled.status == OrderStatus.PARTIALLY_FILLED.value
    assert reconciled.filled_amount == pytest.approx(0.6)
    assert reconciled.remaining_amount == pytest.approx(0.4)
    assert reconciled.fill_price == pytest.approx(123.0)


def test_reconcile_order_valid_filled_quantity_snapshot_is_authoritative():
    local = local_order(
        amount=1.0,
        status=OrderStatus.PARTIALLY_FILLED.value,
        filled_amount=0.6,
        remaining_amount=0.4,
        fill_price=123.0,
        updated_at=100,
        snapshot_updated_at=100,
    )
    snapshot = exchange_order(
        amount=1.0,
        status=OrderStatus.FILLED,
        filled_amount=1.0,
        remaining_amount=0.0,
        fill_price=456.0,
        updated_at=200,
    )

    reconciled = private_sync.reconcile_order(local, snapshot, [])

    assert reconciled.status == OrderStatus.FILLED.value
    assert reconciled.filled_amount == pytest.approx(1.0)
    assert reconciled.remaining_amount == pytest.approx(0.0)
    assert reconciled.fill_price == pytest.approx(456.0)


def test_reconcile_order_closed_snapshot_uses_trades_when_fill_fields_are_missing():
    local = local_order(amount=1.0)
    snapshot = exchange_order(
        amount=1.0,
        status=OrderStatus.FILLED,
        filled_amount=0.0,
        remaining_amount=None,
        fill_price=0.0,
        updated_at=1700000001000,
    )

    reconciled = private_sync.reconcile_order(
        local,
        snapshot,
        [
            persisted_trade(trade_id="one", amount=0.4, price=100.0),
            persisted_trade(trade_id="two", amount=0.3, price=110.0),
        ],
    )

    assert reconciled.status == OrderStatus.PARTIALLY_FILLED.value
    assert reconciled.filled_amount == pytest.approx(0.7)
    assert reconciled.remaining_amount == pytest.approx(0.3)
    assert reconciled.fill_price == pytest.approx((0.4 * 100.0 + 0.3 * 110.0) / 0.7)
    assert reconciled.snapshot_filled_amount == pytest.approx(0.0)


def test_reconcile_order_closed_snapshot_without_trades_does_not_fabricate_fill_quantity():
    local = local_order(amount=1.0)
    snapshot = exchange_order(
        amount=1.0,
        status=OrderStatus.FILLED,
        filled_amount=0.0,
        remaining_amount=None,
        fill_price=0.0,
        updated_at=1700000001000,
    )

    reconciled = private_sync.reconcile_order(local, snapshot, [])

    assert reconciled.status == OrderStatus.PENDING.value
    assert reconciled.filled_amount == pytest.approx(0.0)
    assert reconciled.remaining_amount == pytest.approx(1.0)
    assert reconciled.fill_price == pytest.approx(0.0)


def test_reconcile_order_rejects_trade_evidence_that_exceeds_closed_order_amount():
    local = local_order(amount=1.0)
    snapshot = exchange_order(
        amount=1.0,
        status=OrderStatus.FILLED,
        filled_amount=0.0,
        remaining_amount=None,
        fill_price=0.0,
        updated_at=1700000001000,
    )

    reconciled = private_sync.reconcile_order(
        local,
        snapshot,
        [
            persisted_trade(trade_id="one", amount=0.6, price=100.0),
            persisted_trade(trade_id="two", amount=0.6, price=200.0),
        ],
    )

    assert reconciled.status == OrderStatus.PENDING.value
    assert reconciled.filled_amount == pytest.approx(0.0)
    assert reconciled.remaining_amount == pytest.approx(1.0)
    assert reconciled.fill_price == pytest.approx(0.0)


def test_reconcile_order_uses_normal_trade_evidence_when_within_closed_order_amount():
    local = local_order(amount=1.0)
    snapshot = exchange_order(
        amount=1.0,
        status=OrderStatus.FILLED,
        filled_amount=0.0,
        remaining_amount=None,
        fill_price=0.0,
        updated_at=1700000001000,
    )

    reconciled = private_sync.reconcile_order(
        local,
        snapshot,
        [
            persisted_trade(trade_id="one", amount=0.4, price=100.0),
            persisted_trade(trade_id="two", amount=0.3, price=110.0),
        ],
    )

    assert reconciled.filled_amount == pytest.approx(0.7)
    assert reconciled.fill_price == pytest.approx((0.4 * 100.0 + 0.3 * 110.0) / 0.7)


def test_reconcile_order_uses_all_unique_trades_when_snapshot_is_absent():
    local = local_order(
        amount=1.0,
        status=OrderStatus.PARTIALLY_FILLED.value,
        filled_amount=0.5,
        remaining_amount=0.5,
        fill_price=100.0,
        snapshot_filled_amount=0.5,
    )

    reconciled = private_sync.reconcile_order(
        local,
        None,
        [
            persisted_trade(trade_id="historic", amount=0.4, price=90.0),
            persisted_trade(trade_id="new", amount=0.2, price=110.0),
        ],
    )

    assert reconciled.filled_amount == pytest.approx(0.6)
    assert reconciled.fill_price == pytest.approx((0.4 * 90.0 + 0.2 * 110.0) / 0.6)


def test_reconcile_order_deduplicates_persisted_blank_trades_by_stable_fingerprint():
    local = local_order(amount=1.0, filled_amount=0.0, remaining_amount=1.0)
    duplicate = TradeRecord(
        exchange_trade_id="",
        exchange_order_id="exchange-order-1",
        client_order_id="client-order-1",
        order_id="local-1",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        amount=0.4,
        price=100.0,
        fee=0.01,
        timestamp=123,
    )

    reconciled = private_sync.reconcile_order(local, None, [duplicate, duplicate])

    assert reconciled.filled_amount == pytest.approx(0.4)
    assert reconciled.fill_price == pytest.approx(100.0)


def test_reconcile_order_prefers_identified_trade_over_blank_replay():
    blank = TradeRecord(
        exchange_trade_id="",
        exchange_order_id="exchange-order-1",
        client_order_id="client-order-1",
        order_id="local-1",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        amount=0.4,
        price=100.0,
        fee=0.01,
        timestamp=123,
    )
    identified = TradeRecord(
        exchange_trade_id="identified-1",
        exchange_order_id="exchange-order-1",
        client_order_id="client-order-1",
        order_id="local-1",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        amount=0.4,
        price=100.0,
        fee=0.01,
        timestamp=123,
    )

    unique = private_sync._unique_trade_records([blank, identified])

    assert [trade.exchange_trade_id for trade in unique] == ["identified-1"]


def test_unique_trade_records_keeps_identical_idless_fills_for_different_orders():
    first = persisted_trade(trade_id="", amount=0.1, price=100.0, timestamp=123)
    second = TradeRecord(
        exchange_trade_id="",
        order_id="local-2",
        strategy="other",
        symbol="BTC-USDT",
        side="buy",
        amount=0.1,
        price=100.0,
        fee=0.0,
        timestamp=123,
    )

    unique = private_sync._unique_trade_records([first, second])

    assert unique == [first, second]


def test_unique_trade_records_keeps_same_idless_fill_facts_for_different_orders():
    first = persisted_trade(trade_id="", amount=0.1, price=100.0, timestamp=123)
    second = TradeRecord(
        exchange_trade_id="",
        order_id="local-2",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        amount=0.1,
        price=100.0,
        fee=0.0,
        timestamp=123,
    )

    assert private_sync._unique_trade_records([first, second]) == [first, second]


def test_reconcile_order_keeps_unstable_blank_trades_distinct_across_history():
    local = local_order(amount=1.0, filled_amount=0.0, remaining_amount=1.0)
    first = TradeRecord(
        exchange_trade_id="",
        exchange_order_id="",
        client_order_id="",
        order_id="local-1",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        amount=0.2,
        price=100.0,
        fee=0.01,
        timestamp=123,
    )
    second = TradeRecord(
        exchange_trade_id="",
        exchange_order_id="",
        client_order_id="",
        order_id="local-1",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        amount=0.3,
        price=100.0,
        fee=0.01,
        timestamp=124,
    )

    reconciled = private_sync.reconcile_order(local, None, [first, second])

    assert reconciled.filled_amount == pytest.approx(0.5)


def test_reconcile_order_keeps_local_baseline_when_partial_history_is_incomplete():
    local = local_order(
        amount=1.0,
        status=OrderStatus.PARTIALLY_FILLED.value,
        filled_amount=0.5,
        remaining_amount=0.5,
        fill_price=100.0,
        snapshot_filled_amount=0.5,
    )

    reconciled = private_sync.reconcile_order(
        local,
        None,
        [persisted_trade(trade_id="partial-history", amount=0.4, price=200.0)],
    )

    assert reconciled.filled_amount == pytest.approx(0.5)
    assert reconciled.fill_price == pytest.approx(100.0)


def test_reconcile_order_preserves_cancelled_status_while_applying_delayed_trade():
    local = local_order(
        amount=1.0,
        status="cancelled",
        filled_amount=0.5,
        remaining_amount=0.5,
        fill_price=100.0,
        snapshot_filled_amount=0.5,
    )

    reconciled = private_sync.reconcile_order(
        local,
        None,
        [
            persisted_trade(trade_id="historic", amount=0.5, price=100.0),
            persisted_trade(trade_id="delayed", amount=0.1, price=200.0),
        ],
    )

    assert reconciled.status == "cancelled"
    assert reconciled.filled_amount == pytest.approx(0.6)
    assert reconciled.fill_price == pytest.approx((0.5 * 100.0 + 0.1 * 200.0) / 0.6)


@pytest.mark.asyncio
async def test_private_sync_rejects_unmatched_identity_less_trade_before_risk_events():
    repo = FakeRepository()
    first = exchange_trade(
        exchange_trade_id="",
        exchange_order_id="",
        client_order_id="",
        amount=0.1,
        timestamp=123,
    )
    second = exchange_trade(
        exchange_trade_id="",
        exchange_order_id="",
        client_order_id="",
        amount=0.2,
        timestamp=124,
    )

    with pytest.raises(MalformedProviderData, match="missing external identity"):
        await sync_private_state(repo, FakeAdapter(trades=[first, second]))

    assert repo.trades == []
    assert repo.risk_events == []


@pytest.mark.asyncio
async def test_private_sync_deduplicates_blank_trade_ids_within_one_response():
    repo = FakeRepository(orders=[local_order(amount=1.0)])
    blank_trade = exchange_trade(exchange_trade_id="", amount=0.1, timestamp=123)

    result = await sync_private_state(repo, FakeAdapter(trades=[blank_trade, blank_trade]))

    assert result.trades_upserted == 1
    assert len(repo.trades) == 1
    assert repo.orders[0].filled_amount == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_private_sync_deduplicates_blank_trade_ids_across_syncs_but_keeps_distinct_times():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    repo = Repository(engine)
    repo.upsert_order(local_order(amount=1.0))
    repeated = exchange_trade(exchange_trade_id="", amount=0.1, timestamp=123)

    first = await sync_private_state(repo, FakeAdapter(trades=[repeated]))
    second = await sync_private_state(repo, FakeAdapter(trades=[repeated]))
    third = await sync_private_state(
        repo,
        FakeAdapter(trades=[exchange_trade(exchange_trade_id="", amount=0.1, timestamp=124)]),
    )

    assert (first.trades_upserted, second.trades_upserted, third.trades_upserted) == (1, 0, 1)
    assert [trade.timestamp for trade in repo.get_trades()] == [123, 124]


def test_reconcile_order_newer_snapshot_cannot_erase_trade_confirmed_fill():
    local = local_order(
        amount=1.0, status=OrderStatus.PARTIALLY_FILLED.value,
        filled_amount=0.8, remaining_amount=0.2, fill_price=125.0,
        snapshot_filled_amount=0.4, snapshot_updated_at=100,
    )
    snapshot = exchange_order(
        amount=1.0, filled_amount=0.5, remaining_amount=0.5,
        fill_price=110.0, updated_at=200,
    )

    reconciled = private_sync.reconcile_order(local, snapshot, [])

    assert reconciled.status == OrderStatus.PARTIALLY_FILLED.value
    assert reconciled.filled_amount == pytest.approx(0.8)
    assert reconciled.remaining_amount == pytest.approx(0.2)
    assert reconciled.fill_price == pytest.approx(125.0)


def test_reconcile_order_uses_equal_timestamp_snapshot_as_atomic_authority():
    local = local_order(
        amount=1.0,
        status="partially_filled",
        filled_amount=0.4,
        remaining_amount=0.6,
        updated_at=100,
        snapshot_updated_at=99,
    )
    snapshot = exchange_order(
        amount=0.8,
        filled_amount=0.2,
        remaining_amount=0.6,
        status=OrderStatus.PARTIALLY_FILLED,
        updated_at=100,
    )

    reconciled = private_sync.reconcile_order(
        local,
        snapshot,
        [persisted_trade(trade_id="later-trade", amount=0.7, price=200.0)],
    )

    assert (reconciled.amount, reconciled.filled_amount, reconciled.remaining_amount) == (
        0.8, 0.4, 0.4,
    )
    assert reconciled.snapshot_updated_at == 100


def test_reconcile_order_compares_snapshot_provenance_not_trade_derived_updated_at():
    local = local_order(
        amount=1.0,
        status="partially_filled",
        filled_amount=0.4,
        remaining_amount=0.6,
        updated_at=200,
        snapshot_updated_at=100,
    )
    snapshot = exchange_order(
        amount=0.8,
        filled_amount=0.2,
        remaining_amount=0.6,
        status=OrderStatus.PARTIALLY_FILLED,
        updated_at=150,
    )

    reconciled = private_sync.reconcile_order(
        local,
        snapshot,
        [persisted_trade(trade_id="trade-derived", amount=0.7, price=200.0)],
    )

    assert (reconciled.amount, reconciled.filled_amount, reconciled.remaining_amount) == (
        0.8, 0.4, 0.4,
    )
    assert reconciled.fill_price == pytest.approx(0.0)
    assert reconciled.snapshot_updated_at == 150


def test_reconcile_order_legacy_snapshot_preserves_local_fill_price_when_fill_regresses():
    local = local_order(
        amount=1.0,
        status="partially_filled",
        filled_amount=0.6,
        remaining_amount=0.4,
        fill_price=123.0,
        updated_at=200,
        snapshot_updated_at=None,
    )
    snapshot = exchange_order(
        amount=1.2,
        filled_amount=0.2,
        remaining_amount=1.0,
        fill_price=99.0,
        updated_at=150,
    )

    reconciled = private_sync.reconcile_order(local, snapshot, [])

    assert reconciled.snapshot_updated_at == 150
    assert reconciled.amount == pytest.approx(1.2)
    assert reconciled.filled_amount == pytest.approx(0.6)
    assert reconciled.remaining_amount == pytest.approx(0.6)
    assert reconciled.fill_price == pytest.approx(123.0)
    assert reconciled.status == "partially_filled"


def test_reconcile_order_rejects_snapshot_older_than_snapshot_provenance():
    local = local_order(
        amount=1.0,
        status="partially_filled",
        filled_amount=0.4,
        remaining_amount=0.6,
        updated_at=200,
        snapshot_updated_at=100,
    )
    snapshot = exchange_order(
        amount=0.8,
        filled_amount=0.2,
        remaining_amount=0.6,
        updated_at=90,
    )

    reconciled = private_sync.reconcile_order(local, snapshot, [])

    assert reconciled.amount == 1.0
    assert reconciled.filled_amount == pytest.approx(0.4)
    assert reconciled.snapshot_updated_at == 100


def test_reconcile_order_rejects_untrusted_zero_timestamp_pending_snapshot_for_cancelled_order():
    local = local_order(
        status="cancelled",
        amount=1.0,
        filled_amount=0.4,
        remaining_amount=0.6,
        fill_price=123.0,
        updated_at=300,
        snapshot_updated_at=None,
    )
    snapshot = exchange_order(
        status=OrderStatus.PENDING,
        amount=0.2,
        filled_amount=0.0,
        remaining_amount=0.2,
        fill_price=0.0,
        timestamp=0,
        updated_at=0,
    )

    reconciled = private_sync.reconcile_order(local, snapshot, [])

    assert (reconciled.status, reconciled.amount, reconciled.filled_amount) == (
        "cancelled", 1.0, 0.4
    )
    assert reconciled.remaining_amount == pytest.approx(0.6)
    assert reconciled.fill_price == pytest.approx(123.0)
    assert reconciled.updated_at == 300
    assert reconciled.snapshot_updated_at is None


def test_order_record_does_not_mark_untrusted_zero_timestamp_snapshot_as_provenance():
    record = private_sync._order_record(
        exchange_order(timestamp=0, updated_at=0),
        None,
    )

    assert record.updated_at == 0
    assert record.snapshot_updated_at is None
    assert record._snapshot_authoritative is False


def test_reconcile_order_legacy_snapshot_uses_provenance_and_preserves_quantity():
    local = local_order(
        amount=1.0,
        status="partially_filled",
        filled_amount=0.6,
        remaining_amount=0.4,
        updated_at=300,
        snapshot_updated_at=None,
    )
    snapshot = exchange_order(
        amount=0.8,
        filled_amount=0.2,
        remaining_amount=0.6,
        updated_at=200,
    )

    reconciled = private_sync.reconcile_order(local, snapshot, [])

    assert reconciled.snapshot_updated_at == 200
    assert reconciled.amount == pytest.approx(1.0)
    assert reconciled.filled_amount == pytest.approx(0.6)
    assert reconciled.remaining_amount == pytest.approx(0.4)
    assert reconciled.status == "partially_filled"


def test_reconcile_and_upsert_legacy_order_accepts_later_snapshot_after_recording_old_provenance():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    repo = Repository(engine)
    local = local_order(
        amount=1.0,
        status="partially_filled",
        filled_amount=0.6,
        remaining_amount=0.4,
        updated_at=300,
        snapshot_updated_at=None,
    )
    repo.upsert_order(local)

    old = exchange_order(amount=0.8, filled_amount=0.2, remaining_amount=0.6, updated_at=200)
    repo.upsert_order(private_sync.reconcile_order(repo.get_orders()[0], old, []))
    newer = exchange_order(amount=1.0, filled_amount=0.7, remaining_amount=0.3, updated_at=400)
    persisted = repo.upsert_order(
        private_sync.reconcile_order(repo.get_orders()[0], newer, [])
    )

    assert persisted.snapshot_updated_at == 400
    assert persisted.amount == pytest.approx(1.0)
    assert persisted.filled_amount == pytest.approx(0.7)
    assert persisted.remaining_amount == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_private_sync_upgrades_blank_trade_id_when_identified_replay_arrives():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    repo = Repository(engine)
    repo.upsert_order(local_order(amount=1.0))
    blank = exchange_trade(exchange_trade_id="", timestamp=123)
    identified = exchange_trade(exchange_trade_id="identified-1", timestamp=123)

    first = await sync_private_state(repo, FakeAdapter(trades=[blank]))
    replay = await sync_private_state(repo, FakeAdapter(trades=[identified]))
    same_response = await sync_private_state(
        repo, FakeAdapter(trades=[blank, identified])
    )
    distinct = await sync_private_state(
        repo, FakeAdapter(trades=[exchange_trade(exchange_trade_id="", timestamp=124)])
    )

    trades = repo.get_trades()
    assert first.trades_upserted == 1
    assert replay.trades_upserted == 1
    assert same_response.trades_upserted == 0
    assert distinct.trades_upserted == 1
    assert len(trades) == 2
    assert [trade.exchange_trade_id for trade in trades] == ["identified-1", ""]
    assert [trade.timestamp for trade in trades] == [123, 124]


@pytest.mark.asyncio
async def test_blank_trade_fingerprint_survives_order_strategy_reassignment():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    repo = Repository(engine)
    blank = exchange_trade(
        exchange_trade_id="",
        exchange_order_id="ex-1",
        client_order_id="",
        timestamp=123,
    )

    first = await sync_private_state(repo, FakeAdapter(trades=[blank]))
    repo.save_order(local_order(exchange_order_id="ex-1", client_order_id=""))
    second = await sync_private_state(repo, FakeAdapter(trades=[blank]))

    assert first.trades_upserted == 1
    assert second.trades_upserted == 0
    assert len(repo.get_trades()) == 1


@pytest.mark.asyncio
async def test_blank_trade_without_any_external_id_is_rejected_even_with_unrelated_local_orders():
    repo = FakeRepository(orders=[local_order()])
    blank = exchange_trade(
        exchange_trade_id="",
        exchange_order_id="",
        client_order_id="",
        timestamp=123,
    )

    with pytest.raises(MalformedProviderData, match="missing external identity"):
        await sync_private_state(repo, FakeAdapter(trades=[blank, blank]))

    assert repo.trades == []


def test_blank_trade_fingerprint_ignores_local_order_and_strategy():
    snapshot = exchange_trade(exchange_trade_id="", exchange_order_id="ex-1")

    assert private_sync._trade_fingerprint(
        snapshot, order_id="local-a", strategy="strategy-a"
    ) == private_sync._trade_fingerprint(
        snapshot, order_id="local-b", strategy="strategy-b"
    )


@pytest.mark.asyncio
async def test_private_sync_ignores_non_live_local_orders_when_reloading_for_reconcile():
    repository = Repository(db_path=":memory:")
    repository.upsert_order(local_order(
        order_id="paper-local", source="paper", runtime_mode="paper",
        exchange_order_id="paper-exchange", client_order_id="paper-client",
    ))
    repository.upsert_order(local_order(
        order_id="live-local", source="live_exchange", runtime_mode="live",
        exchange_order_id="live-exchange", client_order_id="live-client",
    ))

    result = await sync_private_state(
        repository,
        FakeAdapter(orders=[exchange_order(
            exchange_order_id="new-live", client_order_id="new-live-client"
        )], trades=[]),
        timestamp_ms=lambda: 1700000005000,
    )

    assert not any(
        divergence["order_id"] == "paper-local" for divergence in result.divergences
    )
    assert repository.get_orders(
        source="paper", runtime_mode="paper", include_inactive=True
    )[0].status == "pending"
    assert repository.get_orders(
        source="live_exchange", runtime_mode="live"
    )
    assert any(order.exchange_order_id == "new-live" for order in repository.get_orders(
        source="live_exchange", runtime_mode="live"
    ))


def test_private_sync_event_keys_are_unambiguous_and_idempotent():
    first: list[dict[str, object]] = []
    second: list[dict[str, object]] = []
    seen: set[str] = set()

    private_sync._append_divergence(
        first,
        seen,
        lambda: 1,
        strategy="__exchange__",
        divergence_type="unmatched_exchange_order",
        reason="reason",
        symbol="A:B",
        order_id="C",
        exchange_order_id="",
        client_order_id="",
    )
    private_sync._append_divergence(
        first,
        seen,
        lambda: 1,
        strategy="__exchange__",
        divergence_type="unmatched_exchange_order",
        reason="reason",
        symbol="A",
        order_id="B:C",
        exchange_order_id="",
        client_order_id="",
    )
    private_sync._append_divergence(
        second,
        set(),
        lambda: 2,
        strategy="__exchange__",
        divergence_type="unmatched_exchange_order",
        reason="different timestamp does not change identity",
        symbol="A:B",
        order_id="C",
        exchange_order_id="",
        client_order_id="",
    )

    assert len(first) == 2
    assert first[0]["event_key"] != first[1]["event_key"]
    assert first[0]["event_key"] == second[0]["event_key"]
    assert len(first[0]["event_key"]) == 64
    assert "A:B" not in first[0]["event_key"]


@pytest.mark.parametrize(
    ("terminal_status", "filled_amount", "remaining_amount"),
    [
        (OrderStatus.FILLED.value, 1.0, 0.0),
        (OrderStatus.CANCELLED.value, 0.6, 0.4),
        (OrderStatus.REJECTED.value, 0.6, 0.4),
    ],
)
def test_reconcile_order_does_not_downgrade_terminal_order_from_newer_open_snapshot(
    terminal_status, filled_amount, remaining_amount
):
    local = local_order(
        status=terminal_status,
        amount=1.0,
        filled_amount=filled_amount,
        remaining_amount=remaining_amount,
        fill_price=123.0,
        updated_at=100,
        snapshot_updated_at=100,
    )
    snapshot = exchange_order(
        status=OrderStatus.PENDING,
        amount=1.0,
        filled_amount=0.0,
        remaining_amount=1.0,
        fill_price=0.0,
        updated_at=200,
    )

    reconciled = private_sync.reconcile_order(local, snapshot, [])

    assert reconciled.status == terminal_status
    assert reconciled.amount == pytest.approx(1.0)
    assert reconciled.filled_amount == pytest.approx(filled_amount)
    assert reconciled.remaining_amount == pytest.approx(remaining_amount)
    assert reconciled.fill_price == pytest.approx(123.0)
    assert reconciled.snapshot_updated_at == 200


@pytest.mark.parametrize(
    ("local_status", "snapshot_status", "filled_amount", "remaining_amount"),
    [
        (OrderStatus.PENDING.value, OrderStatus.FILLED, 1.0, 0.0),
        (OrderStatus.PARTIALLY_FILLED.value, OrderStatus.CANCELLED, 0.6, 0.4),
    ],
)
def test_reconcile_order_allows_forward_progress_into_terminal_snapshot(
    local_status, snapshot_status, filled_amount, remaining_amount
):
    local = local_order(
        status=local_status,
        amount=1.0,
        filled_amount=0.0 if local_status == OrderStatus.PENDING.value else 0.4,
        remaining_amount=1.0 if local_status == OrderStatus.PENDING.value else 0.6,
        updated_at=100,
        snapshot_updated_at=100,
    )
    snapshot = exchange_order(
        amount=1.0,
        status=snapshot_status,
        filled_amount=filled_amount,
        remaining_amount=remaining_amount,
        fill_price=123.0,
        updated_at=200,
    )

    reconciled = private_sync.reconcile_order(local, snapshot, [])

    assert reconciled.status == snapshot_status.value
    assert reconciled.filled_amount == pytest.approx(filled_amount)
    assert reconciled.remaining_amount == pytest.approx(remaining_amount)


@pytest.mark.parametrize(
    ("matcher", "snapshot_factory", "incoming_exchange_id", "incoming_client_id"),
    [
        (private_sync._match_order, exchange_order, "exchange-y", "client-1"),
        (private_sync._match_order, exchange_order, "exchange-1", "client-2"),
        (private_sync._match_trade, exchange_trade, "exchange-y", "client-1"),
        (private_sync._match_trade, exchange_trade, "exchange-1", "client-2"),
    ],
)
def test_external_id_match_rejects_one_known_id_with_conflicting_other_id(
    matcher, snapshot_factory, incoming_exchange_id, incoming_client_id, caplog
):
    first = local_order(
        order_id="local-exchange", exchange_order_id="exchange-1", client_order_id="client-1"
    )
    second = local_order(
        order_id="local-other", exchange_order_id="exchange-y", client_order_id="client-2"
    )
    by_exchange, by_client = private_sync._local_order_indexes([first, second])

    matched = matcher(
        snapshot_factory(
            exchange_order_id=incoming_exchange_id,
            client_order_id=incoming_client_id,
        ),
        by_exchange,
        by_client,
    )

    assert matched is None
    assert "conflicting external IDs" in caplog.text


@pytest.mark.parametrize(
    ("matcher", "snapshot_factory", "incoming_exchange_id", "incoming_client_id"),
    [
        (private_sync._match_order, exchange_order, "exchange-unknown", "client-1"),
        (private_sync._match_order, exchange_order, "ex-local-1", "client-unknown"),
        (private_sync._match_trade, exchange_trade, "exchange-unknown", "client-1"),
        (private_sync._match_trade, exchange_trade, "ex-local-1", "client-unknown"),
    ],
)
def test_external_id_match_rejects_known_id_when_other_incoming_id_conflicts(
    matcher, snapshot_factory, incoming_exchange_id, incoming_client_id, caplog
):
    local = local_order()
    by_exchange, by_client = private_sync._local_order_indexes([local])

    assert matcher(
        snapshot_factory(
            exchange_order_id=incoming_exchange_id,
            client_order_id=incoming_client_id,
        ),
        by_exchange,
        by_client,
    ) is None
    assert "conflicting external IDs" in caplog.text


def test_external_id_match_accepts_both_ids_when_they_identify_one_order():
    local = local_order()
    by_exchange, by_client = private_sync._local_order_indexes([local])

    assert private_sync._match_order(
        exchange_order(), by_exchange, by_client
    ) is local
    assert private_sync._match_trade(
        exchange_trade(), by_exchange, by_client
    ) is local


@pytest.mark.parametrize(
    ("source", "runtime_mode"),
    [
        ("paper", "live"),
        ("backtest", "live"),
        ("live_exchange", "paper"),
    ],
)
def test_live_ownership_rejects_conflicting_provenance(source, runtime_mode, caplog):
    record = local_order(source=source, runtime_mode=runtime_mode)

    assert private_sync._is_live_owned(record) is False
    assert "provenance" in caplog.text


def test_live_ownership_accepts_only_explicit_live_pair():
    record = local_order(source="live_exchange", runtime_mode="live")

    assert private_sync._is_live_owned(record) is True


def test_live_ownership_rejects_legacy_exchange_owner_without_provenance():
    record = local_order(
        strategy="__exchange__",
        source="unknown",
        runtime_mode="unknown",
    )

    assert private_sync._is_live_owned(record) is False


def test_trade_record_matching_rejects_same_order_id_with_conflicting_symbol_or_side():
    order = local_order(order_id="same-local-order", exchange_order_id="", client_order_id="")
    trade = TradeRecord(
        order_id="same-local-order",
        strategy=order.strategy,
        symbol="ETH-USDT",
        side="sell",
        amount=0.1,
        price=50000.0,
        fee=0.0,
        timestamp=1700000003000,
    )

    assert private_sync._trade_record_matches_order(trade, order) is False


def test_trade_record_matching_accepts_same_local_order_id_only_when_facts_do_not_conflict():
    order = local_order(order_id="same-local-order", exchange_order_id="", client_order_id="")
    trade = TradeRecord(
        order_id="same-local-order",
        strategy=order.strategy,
        symbol="BTC/USDT",
        side="BUY",
        amount=0.1,
        price=50000.0,
        fee=0.0,
        timestamp=1700000003000,
    )

    assert private_sync._trade_record_matches_order(trade, order) is True


def test_trade_record_matching_rejects_same_placeholder_id_with_conflicting_facts():
    order = local_order(order_id="okx:shared-placeholder", exchange_order_id="", client_order_id="")
    trade = TradeRecord(
        order_id="okx:shared-placeholder",
        strategy="__exchange__",
        symbol="ETH-USDT",
        side="sell",
        amount=0.1,
        price=50000.0,
        fee=0.0,
        timestamp=1700000003000,
    )

    assert private_sync._trade_record_matches_order(trade, order) is False


def test_match_order_rejects_snapshot_with_crossed_external_ids(caplog):
    first = local_order(
        order_id="local-exchange", exchange_order_id="ex-1", client_order_id="client-1"
    )
    second = local_order(
        order_id="local-client", exchange_order_id="ex-2", client_order_id="client-2"
    )
    by_exchange, by_client = private_sync._local_order_indexes([first, second])

    matched = private_sync._match_order(
        exchange_order(exchange_order_id="ex-1", client_order_id="client-2"),
        by_exchange,
        by_client,
    )

    assert matched is None
    assert "conflicting external IDs" in caplog.text


def test_match_trade_rejects_snapshot_with_crossed_external_ids(caplog):
    first = local_order(
        order_id="local-exchange", exchange_order_id="ex-1", client_order_id="client-1"
    )
    second = local_order(
        order_id="local-client", exchange_order_id="ex-2", client_order_id="client-2"
    )
    by_exchange, by_client = private_sync._local_order_indexes([first, second])

    matched = private_sync._match_trade(
        exchange_trade(exchange_order_id="ex-1", client_order_id="client-2"),
        by_exchange,
        by_client,
    )

    assert matched is None
    assert "conflicting external IDs" in caplog.text


@pytest.mark.parametrize(
    ("snapshot_factory", "overrides"),
    [
        (exchange_order, {"symbol": "ETH-USDT"}),
        (exchange_order, {"side": OrderSide.SELL}),
        (exchange_order, {"type": OrderType.MARKET}),
        (exchange_trade, {"symbol": "ETH-USDT"}),
        (exchange_trade, {"side": OrderSide.SELL}),
    ],
)
def test_external_id_match_rejects_execution_identity_conflicts(
    snapshot_factory, overrides, caplog
):
    local = local_order()
    by_exchange, by_client = private_sync._local_order_indexes([local])
    snapshot = snapshot_factory(**overrides)
    matcher = (
        private_sync._match_order
        if snapshot_factory is exchange_order
        else private_sync._match_trade
    )

    assert matcher(snapshot, by_exchange, by_client) is None
    assert "execution identity conflict" in caplog.text


def test_external_id_match_accepts_matching_execution_identity_with_one_id():
    local = local_order(client_order_id="")
    by_exchange, by_client = private_sync._local_order_indexes([local])

    assert private_sync._match_order(
        exchange_order(client_order_id=""), by_exchange, by_client
    ) is local
    assert private_sync._match_trade(
        exchange_trade(client_order_id=""), by_exchange, by_client
    ) is local


def test_match_order_treats_local_stop_and_provider_trigger_limit_as_same_family():
    local = local_order(type=OrderType.STOP.value)
    by_exchange, by_client = private_sync._local_order_indexes([local])
    snapshot = exchange_order(
        type=OrderType.LIMIT,
        execution_type_family="stop",
    )

    assert private_sync._match_order(snapshot, by_exchange, by_client) is local


def test_match_order_treats_local_stop_and_provider_trigger_market_as_same_family():
    local = local_order(type=OrderType.STOP.value)
    by_exchange, by_client = private_sync._local_order_indexes([local])
    snapshot = exchange_order(
        type=OrderType.MARKET,
        execution_type_family="stop",
    )

    assert private_sync._match_order(snapshot, by_exchange, by_client) is local


def test_match_order_rejects_true_market_limit_execution_type_conflict(caplog):
    local = local_order(type=OrderType.MARKET.value)
    by_exchange, by_client = private_sync._local_order_indexes([local])
    snapshot = exchange_order(
        type=OrderType.LIMIT,
        execution_type_family="limit",
    )

    assert private_sync._match_order(snapshot, by_exchange, by_client) is None
    assert "execution identity conflict" in caplog.text


def test_reconcile_order_fills_both_missing_external_ids_from_snapshot():
    local = local_order(exchange_order_id="", client_order_id="")
    snapshot = exchange_order(
        exchange_order_id="exchange-discovered",
        client_order_id="client-discovered",
    )

    reconciled = private_sync.reconcile_order(local, snapshot, [])

    assert reconciled.exchange_order_id == "exchange-discovered"
    assert reconciled.client_order_id == "client-discovered"


def test_reconcile_order_preserves_local_external_ids_against_empty_or_conflicting_snapshot():
    local = local_order(
        exchange_order_id="local-exchange-id",
        client_order_id="local-client-id",
    )
    snapshot = exchange_order(
        exchange_order_id="conflicting-exchange-id",
        client_order_id="conflicting-client-id",
    )

    reconciled = private_sync.reconcile_order(local, snapshot, [])

    assert reconciled.exchange_order_id == "local-exchange-id"
    assert reconciled.client_order_id == "local-client-id"


def test_reconcile_order_persisted_ids_match_later_snapshot_by_either_id():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    repo = Repository(engine)
    repo.save_order(local_order(exchange_order_id="", client_order_id="client-discovered"))

    snapshot = exchange_order(
        exchange_order_id="exchange-discovered",
        client_order_id="client-discovered",
    )
    persisted = repo.upsert_order(
        private_sync.reconcile_order(repo.get_orders()[0], snapshot, [])
    )

    assert persisted.exchange_order_id == "exchange-discovered"
    assert persisted.client_order_id == "client-discovered"
    assert repo.get_orders()[0].exchange_order_id == "exchange-discovered"
    by_exchange, by_client = private_sync._local_order_indexes(repo.get_orders())
    assert private_sync._match_order(snapshot, by_exchange, by_client) is not None
    assert private_sync._match_order(
        exchange_order(exchange_order_id="", client_order_id="client-discovered"),
        by_exchange,
        by_client,
    ) is not None


@pytest.mark.asyncio
async def test_private_sync_replays_placeholder_trade_to_reconcile_later_local_order():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    repo = Repository(engine)
    trade = exchange_trade(amount=0.4, price=100.0, exchange_trade_id="late-local-trade")

    await sync_private_state(repo, FakeAdapter(trades=[trade]), timestamp_ms=lambda: 1)
    repo.upsert_order(local_order(amount=0.4))

    result = await sync_private_state(repo, FakeAdapter(trades=[trade]), timestamp_ms=lambda: 2)

    persisted_trade = repo.get_trades(order_id="local-1")
    persisted_order = repo.get_orders(order_id="local-1")[0]
    assert result.trades_upserted == 0
    assert len(repo.get_trades()) == 1
    assert len(persisted_trade) == 1
    assert persisted_trade[0].strategy == "ma_cross"
    assert persisted_order.filled_amount == pytest.approx(0.4)
    assert persisted_order.status == OrderStatus.FILLED.value
    assert persisted_order.fill_price == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_private_sync_skips_crossed_trade_identity_without_persisting_or_accounting_it(
    caplog,
):
    first = local_order(
        order_id="local-a", exchange_order_id="X", client_order_id="C1"
    )
    second = local_order(
        order_id="local-b", exchange_order_id="Y", client_order_id="C2"
    )
    repo = FakeRepository(orders=[first, second])
    incoming = exchange_trade(
        exchange_trade_id="conflict-trade",
        exchange_order_id="Y",
        client_order_id="C1",
    )

    result = await sync_private_state(repo, FakeAdapter(trades=[incoming]))

    assert result.trades_upserted == 0
    assert repo.upserted_trades == []
    assert repo.orders[0].filled_amount == 0.0
    assert repo.orders[1].filled_amount == 0.0
    assert "conflicting external IDs" in caplog.text
    assert any(
        divergence["divergence_type"] == "unmatched_exchange_trade"
        for divergence in result.divergences
    )


@pytest.mark.asyncio
async def test_private_sync_rolls_back_authoritative_writes_when_order_write_fails(
    monkeypatch, tmp_path
):
    original_upsert_order = repository_module._AccountingTransaction.upsert_order

    def fail_order_write(transaction, order):
        original_upsert_order(transaction, order)
        raise RuntimeError("order write failed")

    monkeypatch.setattr(
        "src.data.repository._AccountingTransaction.upsert_order", fail_order_write
    )
    repo = Repository(db_path=str(tmp_path / "private-sync-rollback.db"))

    with pytest.raises(RuntimeError, match="order write failed"):
        await sync_private_state(
            repo,
            FakeAdapter(orders=[exchange_order(exchange_order_id="exchange-new")]),
            timestamp_ms=lambda: 1,
        )

    assert repo.get_account() is None
    assert repo.get_orders() == []
    assert repo.get_trades() == []
    assert repo.get_risk_events() == []
    assert repo.get_kill_switch().engaged is False


@pytest.mark.asyncio
async def test_private_sync_authentication_failure_fails_closed_without_account_write():
    class AuthenticationFailureAdapter(FakeAdapter):
        async def fetch_recent_trade_snapshots(self, symbols=None, since=None, limit=100):
            raise AuthenticationError("invalid credentials")

    repository = FakeRepository()

    with pytest.raises(AuthenticationError):
        await sync_private_state(
            repository,
            AuthenticationFailureAdapter(orders=[exchange_order()]),
            timestamp_ms=lambda: 1700000000000,
        )

    assert repository.accounts == []
    assert repository.upserted_orders == []
    assert repository.upserted_trades == []


@pytest.mark.asyncio
async def test_private_sync_permission_failure_fails_closed_without_account_write():
    class PermissionFailureAdapter(FakeAdapter):
        async def fetch_recent_trade_snapshots(self, symbols=None, since=None, limit=100):
            raise PermissionDenied("permission denied")

    repository = FakeRepository()

    with pytest.raises(PermissionDenied):
        await sync_private_state(
            repository,
            PermissionFailureAdapter(orders=[exchange_order()]),
            timestamp_ms=lambda: 1700000000000,
        )

    assert repository.accounts == []
    assert repository.upserted_orders == []
    assert repository.upserted_trades == []


@pytest.mark.asyncio
async def test_private_sync_network_failure_keeps_unavailable_bypass():
    class NetworkFailureAdapter(FakeAdapter):
        async def fetch_recent_trade_snapshots(self, symbols=None, since=None, limit=100):
            raise NetworkError("temporary network failure")

    repository = FakeRepository()
    result = await sync_private_state(
        repository,
        NetworkFailureAdapter(orders=[]),
        timestamp_ms=lambda: 1700000000000,
    )

    assert result.trades_status == "unavailable"
    assert repository.accounts


@pytest.mark.asyncio
async def test_private_sync_trade_provider_failure_preserves_safe_state_without_divergence():
    class FailingTradeAdapter(FakeAdapter):
        async def fetch_recent_trade_snapshots(self, symbols=None, since=None, limit=100):
            raise NetworkError("temporary provider network failure")

    repository = FakeRepository(orders=[local_order()])
    result = await sync_private_state(
        repository,
        FailingTradeAdapter(orders=[]),
        timestamp_ms=lambda: 1700000000000,
    )

    assert result.trades_upserted == 0
    assert result.divergences == []
    assert result.kill_switch_engaged is False
    assert "upsert_account" in repository.calls
    assert "set_kill_switch" not in repository.calls
    assert repository.risk_events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("orders", [None, {}, [object()]])
async def test_private_sync_rejects_malformed_open_order_response_before_writes(orders):
    class MalformedOrderAdapter(FakeAdapter):
        async def fetch_open_order_snapshots(self, symbols=None):
            return orders

    repository = FakeRepository()

    with pytest.raises(MalformedProviderData, match="open-order"):
        await sync_private_state(
            repository,
            MalformedOrderAdapter(),
            timestamp_ms=lambda: 1700000005000,
        )

    assert repository.accounts == []
    assert repository.upserted_orders == []
    assert repository.upserted_trades == []
    assert repository.risk_events == []
    assert repository.kill_switch is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("symbol", ""),
        ("amount", 0.0),
        ("price", 0.0),
        ("side", "hold"),
        ("timestamp", 0),
    ],
)
async def test_private_sync_rejects_invalid_trade_snapshot_fields_before_writes(field, value):
    repository = FakeRepository()
    snapshot = exchange_trade(**{field: value})

    with pytest.raises(MalformedProviderData, match="recent-trade row"):
        await sync_private_state(
            repository,
            FakeAdapter(trades=[snapshot]),
            timestamp_ms=lambda: 1700000005000,
        )

    assert repository.accounts == []
    assert repository.upserted_orders == []
    assert repository.upserted_trades == []
    assert repository.risk_events == []
    assert repository.kill_switch is False


@pytest.mark.asyncio
async def test_private_sync_rejects_valid_and_invalid_trade_batch_atomically():
    repository = FakeRepository()
    valid = exchange_trade(exchange_trade_id="valid-trade")
    invalid = exchange_trade(exchange_trade_id="invalid-trade", amount=0.0)

    with pytest.raises(MalformedProviderData, match="recent-trade row"):
        await sync_private_state(
            repository,
            FakeAdapter(trades=[valid, invalid]),
            timestamp_ms=lambda: 1700000005000,
        )

    assert repository.accounts == []
    assert repository.upserted_orders == []
    assert repository.upserted_trades == []
    assert repository.risk_events == []
    assert repository.kill_switch is False


@pytest.mark.asyncio
async def test_private_sync_malformed_trade_data_fails_closed_without_account_or_order_writes():
    class MalformedTradeAdapter(FakeAdapter):
        async def fetch_recent_trade_snapshots(self, symbols=None, since=None, limit=100):
            raise MalformedProviderData("Malformed provider recent-trade row")

    repository = FakeRepository(orders=[local_order()])

    with pytest.raises(MalformedProviderData, match="recent-trade row"):
        await sync_private_state(
            repository,
            MalformedTradeAdapter(
                account=AccountSnapshot(equity=2000.0, timestamp=1700000000000),
                orders=[exchange_order(exchange_order_id="provider-order")],
            ),
            timestamp_ms=lambda: 1700000005000,
        )

    assert repository.accounts == []
    assert repository.upserted_orders == []
    assert repository.upserted_trades == []
    assert repository.risk_events == []
    assert repository.kill_switch is False


@pytest.mark.asyncio
async def test_private_sync_skips_trade_rebind_and_persisted_trade_reconcile_when_unavailable():
    class RebindTrackingRepository(FakeRepository):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.rebind_calls = 0

        def rebind_trades_to_orders(self, orders=None):
            self.rebind_calls += 1

    repository = RebindTrackingRepository(orders=[
        local_order(
            amount=1.0,
            status=OrderStatus.PARTIALLY_FILLED.value,
            filled_amount=0.4,
            remaining_amount=0.6,
        )
    ])
    repository.trades.append(persisted_trade(trade_id="persisted-fill", amount=0.6, price=51000.0))

    class UnavailableAdapter(FakeAdapter):
        async def fetch_recent_trade_snapshots(self, symbols=None, since=None, limit=100):
            raise TimeoutError("recent trades unavailable")

    result = await sync_private_state(
        repository,
        UnavailableAdapter(orders=[]),
        timestamp_ms=lambda: 1700000005000,
    )

    assert result.trades_status == "unavailable"
    assert result.trades_upserted == 0
    assert result.divergences == []
    assert repository.rebind_calls == 0
    assert repository.orders[0].filled_amount == pytest.approx(0.4)
    assert repository.orders[0].status == OrderStatus.PARTIALLY_FILLED.value


@pytest.mark.asyncio
async def test_private_sync_does_not_write_invalid_first_account_to_new_database(tmp_path, caplog):
    repository = Repository(db_path=str(tmp_path / "invalid-first-account.db"))
    invalid_account = AccountSnapshot(
        equity=1200.0,
        cash_balance=1100.0,
        available_balance=1000.0,
        timestamp=0,
        updated_at=0,
    )

    result = await sync_private_state(
        repository,
        FakeAdapter(account=invalid_account, orders=[], trades=[]),
        timestamp_ms=lambda: 1700000005000,
    )

    assert repository.get_account("__exchange__") is None
    assert result.account_upserted == 0
    assert result.kill_switch_engaged is False
    assert "without trusted timestamp" in caplog.text
    assert set(result.as_response()) == {
        "account_upserted",
        "orders_upserted",
        "trades_upserted",
        "trades_status",
        "risk_events_saved",
        "kill_switch_engaged",
        "divergences",
    }


@pytest.mark.asyncio
async def test_private_sync_preserves_existing_account_for_invalid_snapshot(tmp_path, caplog):
    repository = Repository(db_path=str(tmp_path / "invalid-existing-account.db"))
    existing = AccountRecord(
        strategy="__exchange__",
        initial_equity=900.0,
        cash_balance=850.0,
        available_balance=800.0,
        equity=875.0,
        updated_at=1700000000000,
    )
    repository.upsert_account(existing)
    invalid_account = AccountSnapshot(
        equity=1200.0,
        cash_balance=1100.0,
        available_balance=1000.0,
        timestamp=0,
        updated_at=0,
    )

    result = await sync_private_state(
        repository,
        FakeAdapter(account=invalid_account, orders=[], trades=[]),
        timestamp_ms=lambda: 1700000005000,
    )

    persisted = repository.get_account("__exchange__")
    assert persisted is not None
    assert persisted.equity == 875.0
    assert persisted.updated_at == 1700000000000
    assert result.account_upserted == 0
    assert result.kill_switch_engaged is False
    assert "without trusted timestamp" in caplog.text


@pytest.mark.asyncio
async def test_private_sync_writes_valid_account_snapshot(tmp_path):
    repository = Repository(db_path=str(tmp_path / "valid-account.db"))
    valid_account = AccountSnapshot(
        equity=1200.0,
        cash_balance=1100.0,
        available_balance=1000.0,
        timestamp=1700000000000,
        updated_at=0,
    )

    result = await sync_private_state(
        repository,
        FakeAdapter(account=valid_account, orders=[], trades=[]),
        timestamp_ms=lambda: 1700000005000,
    )

    persisted = repository.get_account("__exchange__")
    assert persisted is not None
    assert persisted.equity == 1200.0
    assert persisted.updated_at == 1700000000000
    assert result.account_upserted == 1


@pytest.mark.asyncio
async def test_private_sync_scope_excludes_persisted_other_symbol_trades_from_reconciliation():
    repository = FakeRepository(orders=[local_order(amount=1.0)])
    repository.trades.append(
        persisted_trade(trade_id="eth-fill", amount=1.0, price=51000.0)
    )
    repository.trades[0].symbol = "ETH-USDT"
    repository.trades[0].exchange_order_id = "ex-local-1"

    result = await sync_private_state(
        repository,
        FakeAdapter(orders=[exchange_order()], trades=[]),
        symbols=[" BTC-USDT ", "BTC-USDT"],
        timestamp_ms=lambda: 1700000005000,
    )

    assert result.trades_status == "available"
    assert repository.orders[0].filled_amount == pytest.approx(0.0)
    assert repository.orders[0].status == OrderStatus.PENDING.value


@pytest.mark.asyncio
async def test_private_sync_scope_indexes_ignore_same_external_ids_on_other_symbol():
    btc = local_order(
        order_id="btc-local",
        exchange_order_id="shared-id",
        client_order_id="shared-client",
    )
    eth = local_order(
        order_id="eth-local",
        symbol="ETH-USDT",
        exchange_order_id="shared-id",
        client_order_id="shared-client",
    )
    repository = FakeRepository(orders=[btc, eth])

    result = await sync_private_state(
        repository,
        FakeAdapter(
            orders=[exchange_order(
                exchange_order_id="shared-id",
                client_order_id="shared-client",
            )]
        ),
        symbols=["BTC-USDT"],
        timestamp_ms=lambda: 1700000005000,
    )

    assert result.kill_switch_engaged is False
    assert result.divergences == []
    assert [order.order_id for order in repository.upserted_orders] == ["btc-local"]
    assert repository.orders[1].status == OrderStatus.PENDING.value


@pytest.mark.asyncio
async def test_private_sync_marks_partial_order_missing_when_known_trades_are_incomplete():
    repository = FakeRepository(orders=[
        local_order(
            amount=1.0,
            status=OrderStatus.PARTIALLY_FILLED.value,
            filled_amount=0.4,
            remaining_amount=0.6,
        )
    ])

    result = await sync_private_state(
        repository,
        FakeAdapter(
            orders=[],
            trades=[exchange_trade(amount=0.1, exchange_trade_id="partial-history")],
        ),
        timestamp_ms=lambda: 1700000005000,
    )

    assert result.kill_switch_engaged is True
    assert result.risk_events_saved == 1
    assert result.divergences[0]["divergence_type"] == "partially_filled_order_missing"
    assert result.divergences[0]["order_id"] == "local-1"


@pytest.mark.asyncio
async def test_private_sync_does_not_mark_partial_order_missing_when_trades_complete_amount():
    repository = FakeRepository(orders=[
        local_order(
            amount=1.0,
            status=OrderStatus.PARTIALLY_FILLED.value,
            filled_amount=0.4,
            remaining_amount=0.6,
        )
    ])

    result = await sync_private_state(
        repository,
        FakeAdapter(
            orders=[],
            trades=[
                exchange_trade(amount=0.4, exchange_trade_id="fill-1"),
                exchange_trade(amount=0.6, exchange_trade_id="fill-2"),
            ],
        ),
        timestamp_ms=lambda: 1700000005000,
    )

    assert result.divergences == []
    assert result.kill_switch_engaged is False
    assert repository.orders[0].status == OrderStatus.FILLED.value


@pytest.mark.asyncio
async def test_private_sync_rejects_identity_less_trade_without_persisting_or_repeating(tmp_path):
    repo = Repository(db_path=str(tmp_path / "unknown-order-trades.db"))
    malformed = exchange_trade(
        exchange_trade_id="",
        exchange_order_id="",
        client_order_id="",
        amount=0.1,
        price=50000.0,
        fee=2.5,
        timestamp=123,
    )

    for _ in range(2):
        with pytest.raises(MalformedProviderData, match="external identity"):
            await sync_private_state(repo, FakeAdapter(trades=[malformed]))

    assert repo.get_trades() == []
    assert repo.get_orders() == []


@pytest.mark.asyncio
async def test_private_sync_matches_spot_slash_scope_to_hyphenated_provider_symbols():
    repository = FakeRepository(orders=[local_order(symbol="BTC/USDT")])
    adapter = FakeAdapter(orders=[exchange_order(symbol="BTC-USDT")])

    result = await sync_private_state(
        repository,
        adapter,
        symbols=["BTC/USDT"],
        timestamp_ms=lambda: 1700000005000,
    )

    assert result.divergences == []
    assert result.kill_switch_engaged is False
    assert [order.order_id for order in repository.upserted_orders] == ["local-1"]


@pytest.mark.asyncio
async def test_private_sync_matches_dated_future_scope_provider_and_local_aliases():
    repository = FakeRepository(orders=[local_order(symbol="BTC-USDT-260925")])
    adapter = FakeAdapter(
        orders=[exchange_order(symbol="BTC/USDT:USDT-260925")],
        trades=[exchange_trade(symbol="BTC/USDT:USDT-260925")],
    )

    result = await sync_private_state(
        repository,
        adapter,
        symbols=["BTC/USDT:USDT-260925"],
        timestamp_ms=lambda: 1700000005000,
    )

    assert result.divergences == []
    assert result.kill_switch_engaged is False
    assert [order.order_id for order in repository.upserted_orders] == ["local-1"]
    assert [trade.order_id for trade in repository.upserted_trades] == ["local-1"]


@pytest.mark.asyncio
async def test_private_sync_does_not_match_dated_future_different_expiry_aliases():
    repository = FakeRepository(orders=[local_order(symbol="BTC-USDT-261225")])
    adapter = FakeAdapter(
        orders=[exchange_order(symbol="BTC/USDT:USDT-260925")],
        trades=[],
    )

    result = await sync_private_state(
        repository,
        adapter,
        symbols=["BTC/USDT:USDT-260925", "BTC-USDT-261225"],
        timestamp_ms=lambda: 1700000005000,
    )

    assert result.kill_switch_engaged is True
    assert any(
        divergence["divergence_type"] == "unmatched_exchange_order"
        for divergence in result.divergences
    )
    assert any(
        divergence["divergence_type"] == "missing_exchange_order"
        for divergence in result.divergences
    )


@pytest.mark.asyncio
async def test_private_sync_reads_local_snapshot_inside_atomic_transaction_after_race(tmp_path):
    real_repository = Repository(db_path=str(tmp_path / "private-sync-race.db"))
    raced_order = local_order(order_id="raced-order", exchange_order_id="raced-exchange")

    class InsertAfterInitialReadRepository:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.inserted = False

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

        def atomic_private_sync(self, callback):
            if not self.inserted:
                self.inserted = True
                self.wrapped.upsert_order(raced_order)
            return self.wrapped.atomic_private_sync(callback)

    repository = InsertAfterInitialReadRepository(real_repository)
    result = await sync_private_state(
        repository,
        FakeAdapter(
            orders=[exchange_order(
                exchange_order_id="raced-exchange", client_order_id="client-1"
            )],
            trades=[],
        ),
        timestamp_ms=lambda: 1700000005000,
    )

    assert result.divergences == []
    assert result.kill_switch_engaged is False
    assert real_repository.get_orders()[0].order_id == "raced-order"


@pytest.mark.asyncio
async def test_private_sync_result_reports_existing_kill_switch_state_on_clean_run(tmp_path):
    repository = Repository(db_path=str(tmp_path / "private-sync-kill-state.db"))
    repository.upsert_order(local_order())

    first = await sync_private_state(
        repository,
        FakeAdapter(orders=[], trades=[]),
        timestamp_ms=lambda: 1700000005000,
    )
    second = await sync_private_state(
        repository,
        FakeAdapter(orders=[exchange_order()], trades=[]),
        timestamp_ms=lambda: 1700000006000,
    )

    assert first.kill_switch_engaged is True
    assert second.divergences == []
    assert second.kill_switch_engaged is True
    assert repository.get_kill_switch().engaged is True


def test_private_sync_normalizes_only_supported_canonical_symbol_forms():
    assert private_sync._normalize_symbols(
        ["BTC/USDT", "BTC-USDT-SWAP", "BTC/USDT:USDT", "BTC-USDT-260925"],
        FakeAdapter(),
    ) == ["BTC-USDT", "BTC-USDT-SWAP", "BTC-USDT-260925"]


@pytest.mark.parametrize(
    ("market_type", "symbol"),
    [
        ("spot", "BTC-USDT-SWAP"),
        ("spot", "BTC-USDT-260925"),
        ("swap", "BTC-USDT-260925"),
        ("future", "BTC-USDT-SWAP"),
        ("option", "BTC-USDT-260925"),
    ],
)
def test_private_sync_normalization_rejects_symbols_from_other_market_types(
    market_type, symbol
):
    adapter = FakeAdapter()
    adapter._default_type = market_type

    with pytest.raises(MalformedProviderData, match="invalid symbol scope"):
        private_sync._normalize_symbols([symbol], adapter)


def test_private_sync_rejects_any_invalid_symbol_in_normalization():
    with pytest.raises(MalformedProviderData, match="invalid symbol scope"):
        private_sync._normalize_symbols(
            ["not-a-symbol", "BTC", "BTC-USDT-not-an-expiry", "garbage"],
            FakeAdapter(),
        )


def test_private_sync_normalizes_decimal_option_scope_without_dropping_contract_identity():
    assert private_sync._normalize_symbols(
        [
            "BTC-USD-260925-100000.5-C",
            "BTC/USD:USD-260925-100000.5-C",
            "BTC-USD-260925-100000.5-P",
            "BTC-USD-260925-100001.5-C",
        ],
        FakeAdapter(),
    ) == [
        "BTC-USD-260925-100000.5-C",
        "BTC-USD-260925-100000.5-P",
        "BTC-USD-260925-100001.5-C",
    ]


def test_private_sync_normalizes_option_scope_without_dropping_contract_identity():
    assert private_sync._normalize_symbols(
        [
            "BTC-USD-260925-100000-C",
            "BTC/USD:USD-260925-100000-C",
            "BTC-USD-260925-100000-P",
        ],
        FakeAdapter(),
    ) == [
        "BTC-USD-260925-100000-C",
        "BTC-USD-260925-100000-P",
    ]


@pytest.mark.asyncio
async def test_private_sync_scopes_decimal_option_orders_and_trades_to_exact_contract():
    repository = FakeRepository()
    adapter = FakeAdapter(
        orders=[
            exchange_order(symbol="BTC/USD:USD-260925-100000.5-C"),
            exchange_order(
                exchange_order_id="other-strike",
                client_order_id="other-strike-client",
                symbol="BTC/USD:USD-260925-100001.5-C",
            ),
        ],
        trades=[
            exchange_trade(symbol="BTC/USD:USD-260925-100000.5-C"),
            exchange_trade(
                exchange_trade_id="other-strike-trade",
                exchange_order_id="other-strike",
                client_order_id="other-strike-client",
                symbol="BTC/USD:USD-260925-100001.5-C",
            ),
        ],
    )

    result = await sync_private_state(
        repository,
        adapter,
        symbols=["BTC-USD-260925-100000.5-C"],
        timestamp_ms=lambda: 1700000005000,
    )

    assert result.orders_upserted == 1
    assert result.trades_upserted == 1
    assert [order.symbol for order in repository.upserted_orders] == [
        "BTC-USD-260925-100000.5-C"
    ]
    assert [trade.symbol for trade in repository.upserted_trades] == [
        "BTC-USD-260925-100000.5-C"
    ]


@pytest.mark.asyncio
async def test_private_sync_scopes_option_orders_and_trades_to_exact_contract():
    repository = FakeRepository()
    adapter = FakeAdapter(
        orders=[
            exchange_order(symbol="BTC/USD:USD-260925-100000-C"),
            exchange_order(
                exchange_order_id="other-strike",
                client_order_id="other-strike-client",
                symbol="BTC/USD:USD-260925-110000-C",
            ),
        ],
        trades=[
            exchange_trade(symbol="BTC/USD:USD-260925-100000-C"),
            exchange_trade(
                exchange_trade_id="other-strike-trade",
                exchange_order_id="other-strike",
                client_order_id="other-strike-client",
                symbol="BTC/USD:USD-260925-110000-C",
            ),
        ],
    )

    result = await sync_private_state(
        repository,
        adapter,
        symbols=["BTC-USD-260925-100000-C"],
        timestamp_ms=lambda: 1700000005000,
    )

    assert result.orders_upserted == 1
    assert result.trades_upserted == 1
    assert [order.symbol for order in repository.upserted_orders] == [
        "BTC-USD-260925-100000-C"
    ]
    assert [trade.symbol for trade in repository.upserted_trades] == [
        "BTC-USD-260925-100000-C"
    ]


@pytest.mark.asyncio
async def test_private_sync_returns_safe_result_without_any_calls_for_explicit_empty_scope():
    adapter = FakeAdapter()
    repository = FakeRepository()

    result = await sync_private_state(repository, adapter, symbols=[])

    assert adapter.account_calls == 0
    assert adapter.order_calls == []
    assert adapter.trade_calls == []
    assert repository.calls == []
    assert result.as_response() == {
        "account_upserted": 0,
        "orders_upserted": 0,
        "trades_upserted": 0,
        "trades_status": "available",
        "risk_events_saved": 0,
        "kill_switch_engaged": False,
        "divergences": [],
    }


@pytest.mark.asyncio
async def test_private_sync_rejects_invalid_explicit_symbol_scope_without_fetching():
    adapter = FakeAdapter()
    repository = FakeRepository()

    with pytest.raises(MalformedProviderData, match="invalid symbol scope"):
        await sync_private_state(repository, adapter, symbols=["", "NOT-A-SYMBOL"])

    assert adapter.account_calls == 0
    assert adapter.order_calls == []
    assert adapter.trade_calls == []
    assert repository.calls == []


@pytest.mark.asyncio
async def test_private_sync_rejects_mixed_valid_and_invalid_symbol_scope_without_fetching():
    adapter = FakeAdapter()

    with pytest.raises(MalformedProviderData, match="invalid symbol scope"):
        await sync_private_state(
            FakeRepository(),
            adapter,
            symbols=["BTC-USDT", "garbage", "ETH/USDT", "BTC-USDT"],
        )

    assert adapter.account_calls == 0
    assert adapter.order_calls == []
    assert adapter.trade_calls == []


@pytest.mark.asyncio
async def test_private_sync_none_symbol_scope_keeps_full_sync():
    adapter = FakeAdapter()

    await sync_private_state(FakeRepository(), adapter, symbols=None)

    assert adapter.account_calls == 1
    assert adapter.order_calls == [None]
    assert adapter.trade_calls == [{"symbols": None, "since": None, "limit": 100}]


@pytest.mark.asyncio
async def test_private_sync_skips_private_symbol_fetches_for_explicit_empty_scope():
    class NoPrivateFetchAdapter(FakeAdapter):
        async def fetch_open_order_snapshots(self, symbols=None):
            raise AssertionError("empty symbol scope must not fetch all orders")

        async def fetch_recent_trade_snapshots(self, symbols=None, since=None, limit=100):
            raise AssertionError("empty symbol scope must not fetch all trades")

    await sync_private_state(FakeRepository(), NoPrivateFetchAdapter(), symbols=[])


@pytest.mark.asyncio
async def test_private_sync_normalizes_symbol_scope_before_provider_requests():
    adapter = FakeAdapter()

    await sync_private_state(
        FakeRepository(),
        adapter,
        symbols=[" BTC-USDT ", "BTC-USDT", "ETH-USDT", "ETH-USDT"],
    )

    assert adapter.trade_calls == [
        {"symbols": ["BTC-USDT", "ETH-USDT"], "since": None, "limit": 100}
    ]


@pytest.mark.asyncio
async def test_private_sync_excludes_out_of_scope_provider_orders_and_trades():
    repository = FakeRepository(orders=[
        local_order(order_id="btc-local", symbol="BTC-USDT"),
        local_order(
            order_id="eth-local",
            exchange_order_id="ex-eth",
            client_order_id="client-eth",
            symbol="ETH-USDT",
        ),
    ])
    adapter = FakeAdapter(
        orders=[
            exchange_order(
                exchange_order_id="ex-eth",
                client_order_id="client-eth",
                symbol="ETH-USDT",
            ),
        ],
        trades=[
            exchange_trade(
                exchange_trade_id="eth-trade",
                exchange_order_id="ex-eth",
                client_order_id="client-eth",
                symbol="ETH-USDT",
            ),
        ],
    )

    result = await sync_private_state(
        repository,
        adapter,
        symbols=["BTC-USDT"],
        timestamp_ms=lambda: 1700000005000,
    )

    assert result.trades_upserted == 0
    assert result.divergences == [
        divergence for divergence in result.divergences
        if divergence["symbol"] == "BTC-USDT"
    ]
    assert all(order.symbol != "ETH-USDT" for order in repository.upserted_orders)
    assert repository.upserted_trades == []
    assert all(event["symbol"] == "BTC-USDT" for event in repository.risk_events)


@pytest.mark.asyncio
async def test_private_sync_scopes_every_provider_side_effect_to_requested_symbol():
    repository = FakeRepository(orders=[local_order()])
    adapter = FakeAdapter(
        orders=[
            exchange_order(),
            exchange_order(
                exchange_order_id="ex-eth",
                client_order_id="client-eth",
                symbol="ETH-USDT",
                side=OrderSide.SELL,
                amount=1.0,
                price=3000.0,
            ),
        ],
        trades=[
            exchange_trade(),
            exchange_trade(
                exchange_trade_id="eth-trade",
                exchange_order_id="ex-eth",
                client_order_id="client-eth",
                symbol="ETH-USDT",
                side=OrderSide.SELL,
                amount=1.0,
                price=3000.0,
                fee=0.0,
            ),
        ],
    )

    result = await sync_private_state(
        repository,
        adapter,
        symbols=["BTC-USDT"],
        timestamp_ms=lambda: 1700000005000,
    )

    assert result.orders_upserted == 1
    assert result.trades_upserted == 1
    assert result.risk_events_saved == 0
    assert result.kill_switch_engaged is False
    assert [order.symbol for order in repository.upserted_orders] == ["BTC-USDT"]
    assert [trade.symbol for trade in repository.upserted_trades] == ["BTC-USDT"]
    assert repository.risk_events == []


@pytest.mark.asyncio
async def test_private_sync_does_not_match_exchange_ids_owned_by_paper_records():
    paper = local_order(
        order_id="paper-order",
        strategy="paper-strategy",
        source="paper",
        runtime_mode="paper",
        exchange_order_id="shared-exchange-id",
        client_order_id="paper-client",
    )
    repository = FakeRepository(orders=[paper])
    incoming = exchange_order(
        exchange_order_id="shared-exchange-id",
        client_order_id="",
    )

    result = await sync_private_state(
        repository,
        FakeAdapter(orders=[incoming], trades=[]),
        timestamp_ms=lambda: 1700000005000,
    )

    assert result.trades_upserted == 0
    assert repository.orders[0] is paper
    assert not any(
        event["divergence_type"] == "live_source_identity_conflict"
        for event in result.divergences
    )
    assert any(
        order.source == "live_exchange" and order.exchange_order_id == "shared-exchange-id"
        for order in repository.orders[1:]
    )


@pytest.mark.asyncio
async def test_private_sync_does_not_match_trade_id_owned_by_backtest_record():
    repository = FakeRepository(orders=[local_order()])
    repository.trades.append(
        persisted_trade(trade_id="shared-trade-id", amount=0.1, price=50000.0)
    )
    repository.trades[0].source = "backtest"
    repository.trades[0].runtime_mode = "backtest"

    result = await sync_private_state(
        repository,
        FakeAdapter(
            orders=[exchange_order()],
            trades=[exchange_trade(exchange_trade_id="shared-trade-id")],
        ),
        timestamp_ms=lambda: 1700000005000,
    )

    assert result.trades_upserted == 1
    assert repository.trades[0].source == "backtest"
    assert repository.trades[0].strategy == "ma_cross"
    assert any(
        divergence["divergence_type"] == "live_source_identity_conflict"
        for divergence in result.divergences
    )


def test_persist_private_state_scopes_provider_orders_before_side_effects():
    repository = FakeRepository(orders=[local_order()])
    adapter = FakeAdapter(
        orders=[
            exchange_order(),
            exchange_order(
                exchange_order_id="ex-eth",
                client_order_id="client-eth",
                symbol="ETH-USDT",
                side=OrderSide.SELL,
                amount=1.0,
                price=3000.0,
            ),
        ],
        trades=[
            exchange_trade(),
            exchange_trade(
                exchange_trade_id="eth-trade",
                exchange_order_id="ex-eth",
                client_order_id="client-eth",
                symbol="ETH-USDT",
                side=OrderSide.SELL,
                amount=1.0,
                price=3000.0,
                fee=0.0,
            ),
        ],
    )
    local_orders = repository.get_orders()
    by_exchange, by_client = private_sync._local_order_indexes(local_orders)

    result, _ = private_sync._persist_private_state(
        repository,
        adapter.account,
        adapter.orders,
        adapter.trades,
        local_orders,
        by_exchange,
        by_client,
        lambda: 1700000005000,
        target_symbols=["BTC-USDT"],
    )

    assert result.orders_upserted == 1
    assert result.trades_upserted == 1
    assert result.risk_events_saved == 0
    assert [order.symbol for order in repository.upserted_orders] == ["BTC-USDT"]
    assert [trade.symbol for trade in repository.upserted_trades] == ["BTC-USDT"]
    assert repository.risk_events == []


@pytest.mark.asyncio
async def test_private_sync_logs_trades_unavailable_without_claiming_empty_trade_history(caplog):
    class FailingTradeAdapter(FakeAdapter):
        async def fetch_recent_trade_snapshots(self, symbols=None, since=None, limit=100):
            raise TimeoutError("trades timed out")

    result = await sync_private_state(
        FakeRepository(orders=[local_order()]),
        FailingTradeAdapter(orders=[]),
        timestamp_ms=lambda: 1700000000000,
    )

    assert result.trades_upserted == 0
    assert result.divergences == []
    assert "trades_unavailable" in caplog.text
    assert "not empty" not in caplog.text.lower()
    assert result.trades_status == "unavailable"


@pytest.mark.asyncio
async def test_private_sync_marks_empty_recent_trades_available():
    result = await sync_private_state(
        FakeRepository(),
        FakeAdapter(trades=[]),
        timestamp_ms=lambda: 1700000000000,
    )

    assert result.trades_status == "available"


@pytest.mark.asyncio
async def test_private_sync_rejects_non_snapshot_trade_rows_before_any_writes():
    class InvalidTradeAdapter(FakeAdapter):
        async def fetch_recent_trade_snapshots(self, symbols=None, since=None, limit=100):
            return [object()]

    repository = FakeRepository()

    with pytest.raises(MalformedProviderData, match="recent-trade"):
        await sync_private_state(
            repository,
            InvalidTradeAdapter(
                account=AccountSnapshot(equity=2000.0, timestamp=1700000000000),
                orders=[exchange_order(exchange_order_id="provider-order")],
            ),
            symbols=["BTC-USDT"],
            timestamp_ms=lambda: 1700000005000,
        )

    assert repository.accounts == []
    assert repository.upserted_orders == []
    assert repository.upserted_trades == []
    assert repository.risk_events == []
    assert repository.kill_switch is False


@pytest.mark.asyncio
async def test_private_sync_scoped_trade_id_collision_keeps_missing_order():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    repo = Repository(engine)
    btc_order = local_order(
        order_id="btc-order",
        exchange_order_id="btc-exchange",
        client_order_id="btc-client",
        symbol="BTC-USDT",
    )
    eth_order = local_order(
        order_id="eth-order",
        exchange_order_id="eth-exchange",
        client_order_id="eth-client",
        symbol="ETH-USDT",
    )
    repo.upsert_order(btc_order)
    repo.upsert_order(eth_order)
    repo.upsert_trade(TradeRecord(
        source="live_exchange",
        runtime_mode="live",
        exchange_trade_id="shared-trade-id",
        exchange_order_id="eth-exchange",
        client_order_id="eth-client",
        order_id="eth-order",
        strategy="ma_cross",
        symbol="ETH-USDT",
        side="buy",
        amount=0.1,
        price=50000.0,
        fee=2.5,
        timestamp=1700000003000,
    ))

    result = await sync_private_state(
        repo,
        FakeAdapter(
            orders=[],
            trades=[exchange_trade(
                exchange_trade_id="shared-trade-id",
                exchange_order_id="btc-exchange",
                client_order_id="btc-client",
            )],
        ),
        symbols=["BTC-USDT"],
        timestamp_ms=lambda: 1700000005000,
    )

    assert result.trades_upserted == 0
    assert result.kill_switch_engaged is True
    assert any(
        divergence["divergence_type"] == "conflicting_exchange_trade_identity"
        and divergence["exchange_trade_id"] == "shared-trade-id"
        for divergence in result.divergences
    )
    assert any(
        divergence["divergence_type"] == "missing_exchange_order"
        and divergence["order_id"] == "btc-order"
        for divergence in result.divergences
    )
    assert repo.get_trades()[0].symbol == "ETH-USDT"
    first_event_keys = {divergence["event_key"] for divergence in result.divergences}

    repeated = await sync_private_state(
        repo,
        FakeAdapter(
            orders=[],
            trades=[exchange_trade(
                exchange_trade_id="shared-trade-id",
                exchange_order_id="btc-exchange",
                client_order_id="btc-client",
            )],
        ),
        symbols=["BTC-USDT"],
        timestamp_ms=lambda: 1700000006000,
    )

    assert repeated.trades_upserted == 0
    assert repeated.risk_events_saved == 0
    assert {divergence["event_key"] for divergence in repeated.divergences} == first_event_keys
    assert len(repo.get_risk_events()) == 2
    assert len({event.event_key for event in repo.get_risk_events()}) == 2


@pytest.mark.asyncio
async def test_private_sync_marks_disappeared_pending_order_partial_trade_as_orphan_divergence():
    repository = FakeRepository(orders=[local_order(amount=1.0)])

    result = await sync_private_state(
        repository,
        FakeAdapter(
            orders=[],
            trades=[exchange_trade(amount=0.2, exchange_trade_id="orphan-fill")],
        ),
        timestamp_ms=lambda: 1700000005000,
    )

    assert result.kill_switch_engaged is True
    assert result.risk_events_saved == 1
    assert result.divergences[0]["divergence_type"] == "orphaned_order_partial"
    assert repository.orders[0].filled_amount == pytest.approx(0.2)
    assert repository.orders[0].status == OrderStatus.PARTIALLY_FILLED.value


@pytest.mark.asyncio
async def test_private_sync_uses_persisted_history_when_classifying_disappeared_partial_order():
    repository = FakeRepository(orders=[local_order(amount=1.0)])
    repository.trades.append(TradeRecord(
        source="live_exchange",
        runtime_mode="live",
        exchange_trade_id="historical-fill",
        exchange_order_id="ex-local-1",
        client_order_id="client-1",
        order_id="local-1",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        amount=0.9,
        price=50000.0,
        fee=0.0,
        timestamp=1700000002000,
    ))

    result = await sync_private_state(
        repository,
        FakeAdapter(
            orders=[],
            trades=[exchange_trade(amount=0.1, exchange_trade_id="latest-fill")],
        ),
        timestamp_ms=lambda: 1700000005000,
    )

    assert not any(
        divergence["divergence_type"] in {
            "orphaned_order_partial",
            "partially_filled_order_missing",
        }
        for divergence in result.divergences
    )
    assert repository.orders[0].filled_amount == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_private_sync_records_overfill_divergence_instead_of_treating_order_as_filled():
    repository = FakeRepository(orders=[local_order(amount=1.0)])

    result = await sync_private_state(
        repository,
        FakeAdapter(orders=[], trades=[exchange_trade(amount=1.1, exchange_trade_id="overfill")]),
        timestamp_ms=lambda: 1700000005000,
    )

    assert result.kill_switch_engaged is True
    assert any(
        divergence["divergence_type"] == "order_overfilled"
        for divergence in result.divergences
    )
    assert repository.orders[0].filled_amount == pytest.approx(1.0)


def test_order_record_persists_stop_execution_family_for_later_trigger_matching():
    local = _order_record(
        exchange_order(
            type=OrderType.MARKET,
            execution_type_family="stop",
            exchange_order_id="trigger-order",
            client_order_id="trigger-client",
        ),
        None,
    )
    persisted = OrderRecord(
        **local.model_dump(),
        execution_type_family=local.execution_type_family,
    )
    later_trigger = exchange_order(
        type=OrderType.MARKET,
        execution_type_family="stop",
        exchange_order_id="trigger-order",
        client_order_id="trigger-client",
    )

    assert persisted.execution_type_family == "stop"
    assert private_sync._match_order(
        later_trigger,
        {persisted.exchange_order_id: persisted},
        {persisted.client_order_id: persisted},
    ) is persisted


def test_unknown_exchange_row_is_not_live_owned_for_sync_matching():
    record = OrderRecord(
        order_id="legacy-order",
        exchange_order_id="okx-order",
        client_order_id="client-order",
        strategy="__exchange__",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=1.0,
        price=100.0,
        status="open",
        source="unknown",
        runtime_mode="unknown",
    )

    assert private_sync._is_live_owned(record) is False


@pytest.mark.asyncio
async def test_trade_identity_conflict_is_saved_after_order_and_trade_conflicts_are_collected():
    repository = FakeRepository(orders=[OrderRecord(
        order_id="paper-order", exchange_order_id="shared-order",
        client_order_id="paper-client", strategy="paper",
        symbol="BTC-USDT", side="buy", type="limit", amount=1.0,
        price=100.0, status="open", source="paper", runtime_mode="paper",
    )])
    trade = exchange_trade(exchange_trade_id="trade-conflict", exchange_order_id="shared-order")

    result = await sync_private_state(
        repository,
        FakeAdapter(orders=[], trades=[trade]),
        timestamp_ms=lambda: 1700000005000,
    )

    assert not any(
        event["divergence_type"] == "live_source_identity_conflict"
        for event in result.divergences
    )
    assert any(
        trade.source == "live_exchange" and trade.exchange_trade_id == "trade-conflict"
        for trade in repository.trades
    )


@pytest.mark.asyncio
async def test_private_sync_detects_foreign_trade_identity_even_when_old_row_is_inactive():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    repository = Repository(engine)
    legacy = repository.upsert_trade(TradeRecord(
        exchange_trade_id="reused-trade-id",
        exchange_order_id="paper-order",
        order_id="paper-order",
        strategy="alpha",
        symbol="BTC-USDT",
        side="buy",
        amount=0.1,
        price=50000.0,
        fee=0.0,
        timestamp=1,
        source="paper",
        runtime_mode="paper",
        active=False,
        stale_at=2,
    ))
    assert legacy.active is False

    result = await sync_private_state(
        repository,
        FakeAdapter(trades=[exchange_trade(
            exchange_trade_id="reused-trade-id",
            exchange_order_id="live-order",
            client_order_id="",
            timestamp=3,
        )]),
        timestamp_ms=lambda: 4,
    )

    persisted = repository.get_trades(include_inactive=True)
    assert len(persisted) == 2
    assert {(trade.source, trade.runtime_mode) for trade in persisted} == {
        ("paper", "paper"),
        ("live_exchange", "live"),
    }
    assert any(
        event["divergence_type"] == "live_source_identity_conflict"
        for event in result.divergences
    )
