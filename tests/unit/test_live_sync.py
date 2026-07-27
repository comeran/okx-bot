import pytest

from src.core.types import AccountSnapshot, AssetBalance, PositionSide, PositionSnapshot
from src.data.models import AccountRecord, PositionRecord
from src.exchange.live_sync import LiveStateSyncService


class FakeAdapter:
    def __init__(self):
        self.account = AccountSnapshot(
            currency="USDT",
            equity=1000.0,
            cash_balance=975.0,
            available_balance=965.0,
            unrealized_pnl=25.0,
            realized_pnl=4.0,
            updated_at=1700000000000,
            assets=[AssetBalance(ccy="USDT", cash_bal=975.0, eq=1000.0, avail_bal=965.0)],
        )
        self.positions = [
            PositionSnapshot(
                symbol="BTC-USDT-SWAP",
                side=PositionSide.LONG,
                amount=2.0,
                entry_price=50000.0,
                mark_price=51000.0,
                unrealized_pnl=200.0,
                realized_pnl=10.0,
                leverage=3,
                updated_at=1700000000001,
            )
        ]
        self.account_calls = 0
        self.position_calls = []

    async def fetch_account_snapshot(self):
        self.account_calls += 1
        return self.account

    async def fetch_position_snapshots(self, symbols=None):
        self.position_calls.append(symbols)
        return self.positions


class FakeRepository:
    def __init__(self):
        self.accounts = {}
        self.positions = {}
        self.deleted = []

    def get_account(self, strategy):
        return self.accounts.get(strategy)

    def upsert_account(self, account):
        self.accounts[account.strategy] = account
        return account

    def upsert_position(self, position):
        self.positions[(position.strategy, position.symbol)] = position
        return position

    def delete_position(self, strategy, symbol):
        self.deleted.append((strategy, symbol))
        self.positions.pop((strategy, symbol), None)

    def get_open_positions(self, strategy=None):
        return [
            position
            for position in self.positions.values()
            if position.amount != 0 and (strategy is None or position.strategy == strategy)
        ]


@pytest.mark.asyncio
async def test_live_state_sync_persists_account_and_positions():
    adapter = FakeAdapter()
    repository = FakeRepository()
    service = LiveStateSyncService(adapter, repository, timestamp_ms=lambda: 1700000009999)

    result = await service.refresh("ma_cross", symbols=["BTC-USDT-SWAP"])

    assert adapter.account_calls == 1
    assert adapter.position_calls == [["BTC-USDT-SWAP"]]
    account = repository.accounts["ma_cross"]
    assert account == result.account
    assert account.initial_equity == 1000.0
    assert account.cash_balance == 975.0
    assert account.available_balance == 965.0
    assert account.equity == 1000.0
    assert account.realized_pnl == 4.0
    assert account.unrealized_pnl == 25.0
    assert account.daily_pnl == 0.0
    assert account.fees_paid == 0.0
    assert account.updated_at == 1700000000000
    assert account.assets == [
        {
            "ccy": "USDT",
            "cash_bal": 975.0,
            "eq": 1000.0,
            "eq_utd": 0.0,
            "avail_bal": 965.0,
            "upl": 0.0,
        }
    ]
    position = repository.positions[("ma_cross", "BTC-USDT-SWAP")]
    assert position.side == "long"
    assert position.amount == 2.0
    assert position.entry_price == 50000.0
    assert position.mark_price == 51000.0
    assert position.realized_pnl == 10.0
    assert position.unrealized_pnl == 200.0
    assert position.leverage == 3
    assert position.timestamp == 1700000000001


@pytest.mark.asyncio
async def test_live_state_sync_removes_stale_synced_positions_for_requested_symbols():
    adapter = FakeAdapter()
    adapter.positions = []
    repository = FakeRepository()
    repository.positions[("ma_cross", "BTC-USDT-SWAP")] = PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
    )
    repository.positions[("ma_cross", "ETH-USDT-SWAP")] = PositionRecord(
        strategy="ma_cross",
        symbol="ETH-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=3000.0,
        leverage=1,
        timestamp=1700000000000,
    )
    service = LiveStateSyncService(adapter, repository, timestamp_ms=lambda: 1700000009999)

    result = await service.refresh("ma_cross", symbols=["BTC-USDT-SWAP"])

    assert result.positions == []
    assert repository.deleted == [("ma_cross", "BTC-USDT-SWAP")]
    assert ("ma_cross", "BTC-USDT-SWAP") not in repository.positions
    assert ("ma_cross", "ETH-USDT-SWAP") in repository.positions


@pytest.mark.asyncio
async def test_live_state_sync_preserves_existing_daily_pnl_and_fees():
    adapter = FakeAdapter()
    repository = FakeRepository()
    repository.accounts["ma_cross"] = AccountRecord(
        strategy="ma_cross",
        initial_equity=900.0,
        cash_balance=900.0,
        equity=900.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=-12.0,
        fees_paid=3.5,
        updated_at=1699999999999,
    )
    service = LiveStateSyncService(adapter, repository, timestamp_ms=lambda: 1700000009999)

    await service.refresh("ma_cross", symbols=["BTC-USDT-SWAP"])

    account = repository.accounts["ma_cross"]
    assert account.initial_equity == 900.0
    assert account.daily_pnl == -12.0
    assert account.fees_paid == 3.5
