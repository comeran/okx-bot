import pytest

from src.core.types import (
    AccountSnapshot,
    ExchangeOrderSnapshot,
    ExchangeTradeSnapshot,
    OrderSide,
    OrderStatus,
    OrderType,
)
from src.data.models import OrderRecord
from src.ops.private_sync import sync_private_state


class FakeRepository:
    def __init__(self, orders=None):
        self.orders = list(orders or [])
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

    def upsert_account(self, account):
        self.calls.append("upsert_account")
        self.accounts.append(account)
        return account

    def upsert_order(self, order):
        self.calls.append(f"upsert_order:{order.order_id}")
        self.upserted_orders.append(order)
        return order

    def upsert_trade(self, trade):
        self.calls.append(f"upsert_trade:{trade.exchange_trade_id}")
        self.upserted_trades.append(trade)
        return trade

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
        return self.account

    async def fetch_open_order_snapshots(self, symbols=None):
        return self.orders

    async def fetch_recent_trade_snapshots(self, symbols=None, since=None, limit=100):
        self.trade_calls.append({"symbols": symbols, "since": since, "limit": limit})
        return self.trades


def local_order(**overrides):
    values = {
        "order_id": "local-1",
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
        "orders_upserted": 2,
        "trades_upserted": 2,
        "risk_events_saved": 2,
        "kill_switch_engaged": True,
        "divergences": repo.risk_events,
    }
    assert adapter.trade_calls == [{"symbols": ["BTC-USDT"], "since": 1700000000000, "limit": 100}]
    assert repo.accounts[0].strategy == "__exchange__"
    assert repo.accounts[0].equity == 1010.0
    assert repo.upserted_orders[0].strategy == "ma_cross"
    assert repo.upserted_orders[0].order_id == "local-1"
    assert repo.upserted_orders[1].strategy == "__exchange__"
    assert repo.upserted_orders[1].order_id == "okx:ex-unmatched"
    assert repo.upserted_trades[0].strategy == "ma_cross"
    assert repo.upserted_trades[0].order_id == "local-1"
    assert repo.upserted_trades[1].strategy == "__exchange__"
    assert repo.upserted_trades[1].order_id == "okx:ex-unmatched"


@pytest.mark.asyncio
async def test_high_risk_divergence_engages_kill_switch_before_events_and_upserts():
    repo = FakeRepository(orders=[local_order(exchange_order_id="ex-missing")])
    adapter = FakeAdapter()

    await sync_private_state(repo, adapter, timestamp_ms=lambda: 1700000005000)

    event_key = "missing_exchange_order:BTC-USDT:local-1:ex-missing:client-1:"
    assert repo.kill_switch is True
    assert repo.calls.index("set_kill_switch") < repo.calls.index(
        f"save_risk_event:high:{event_key}"
    )
    assert repo.calls.index(f"save_risk_event:high:{event_key}") < repo.calls.index("upsert_account")
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

    assert [event["event_key"] for event in repo.risk_events] == [
        "unmatched_exchange_order:ETH-USDT:okx:ex-unmatched:ex-unmatched::"
    ]
    assert repo.calls.count("set_kill_switch") == 1
