import pytest
from ccxt.base.errors import NetworkError

import src.data.repository as repository_module
from src.core.types import AccountSnapshot, AssetBalance, PositionSide, PositionSnapshot
from src.data.models import AccountRecord, PositionRecord
from src.data.repository import Repository
from src.exchange.base import canonical_symbol
from src.exchange.errors import MalformedProviderData
from src.exchange.live_sync import (
    LiveStateSyncService,
    PositionSnapshotResult,
    refresh_okx_live_state,
)


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
        if hasattr(self, "position_response"):
            return self.position_response
        return PositionSnapshotResult(
            self.positions, complete_snapshot=True, snapshot_timestamp=1700000000001
        )


@pytest.mark.asyncio
async def test_live_state_sync_does_not_archive_positions_from_unmarked_partial_response():
    engine = repository_module.create_engine("sqlite:///:memory:", echo=False)
    repository = Repository(engine)
    repository.upsert_position(PositionRecord(
        strategy="__exchange__", source="live_exchange", runtime_mode="live",
        symbol="ETH-USDT-SWAP", side="long", amount=1.0,
        entry_price=2000.0, timestamp=1, updated_at=1,
    ))
    adapter = FakeAdapter()
    adapter.position_response = [adapter.positions[0]]

    await LiveStateSyncService(adapter, repository, timestamp_ms=lambda: 2).refresh(
        "__exchange__"
    )

    retained = repository.get_position(
        "__exchange__", "ETH-USDT-SWAP", "long", source="live_exchange", runtime_mode="live"
    )
    assert retained is not None
    assert retained.active is True


@pytest.mark.asyncio
async def test_refresh_okx_live_state_preserves_provider_error_when_adapter_close_also_fails(
    monkeypatch,
):
    class AdapterWithRootAndCloseErrors:
        async def fetch_account_snapshot(self):
            raise NetworkError("provider network failure")

        async def close(self):
            raise RuntimeError("close failure")

    monkeypatch.setattr(
        "src.exchange.live_sync.create_okx_adapter",
        lambda exchange: AdapterWithRootAndCloseErrors(),
    )

    with pytest.raises(NetworkError, match="provider network failure"):
        await refresh_okx_live_state(
            object(), FakeRepository(), "__exchange__", None, lambda: 1
        )


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
        self.positions[(position.strategy, position.symbol, position.side)] = position
        return position

    def get_position(self, strategy, symbol, side=None):
        if side is not None:
            return self.positions.get((strategy, symbol, side))
        return next(
            (
                position
                for (position_strategy, position_symbol, _), position in self.positions.items()
                if position_strategy == strategy and position_symbol == symbol
            ),
            None,
        )

    def delete_position(self, strategy, symbol, side=None):
        self.deleted.append((strategy, symbol, side))
        if side is None:
            keys = [
                key for key in self.positions if key[:2] == (strategy, symbol)
            ]
        else:
            keys = [(strategy, symbol, side)]
        for key in keys:
            self.positions.pop(key, None)

    def get_open_positions(self, strategy=None):
        return [
            position
            for position in self.positions.values()
            if position.amount != 0 and (strategy is None or position.strategy == strategy)
        ]

    def get_positions(self, strategy=None):
        return [
            position
            for position in self.positions.values()
            if strategy is None or position.strategy == strategy
        ]


class DeleteOnlyLegacyRepository:
    def __init__(self, position):
        self.position = position
        self.deleted = []

    def get_open_positions(self, strategy):
        return [self.position]

    def delete_position(self, strategy, symbol, side=None):
        self.deleted.append((strategy, symbol, side))


@pytest.mark.asyncio
async def test_stale_cleanup_does_not_delete_when_legacy_repository_lacks_archive_marker():
    position = PositionRecord(
        strategy="__exchange__",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
    )
    repository = DeleteOnlyLegacyRepository(position)
    service = LiveStateSyncService(FakeAdapter(), repository, timestamp_ms=lambda: 1)

    service._delete_stale_positions(
        repository,
        "__exchange__",
        ["BTC-USDT-SWAP"],
        set(),
        1700000000001,
    )

    assert repository.deleted == []
    assert position.active is True
    assert position.stale_at is None


@pytest.mark.asyncio
async def test_live_state_sync_empty_symbol_scope_returns_only_strict_live_account():
    adapter = FakeAdapter()
    repository = FakeRepository()
    repository.accounts["__exchange__"] = AccountRecord(
        strategy="__exchange__", source="paper", runtime_mode="paper",
        initial_equity=1, cash_balance=1, equity=1, realized_pnl=0,
        unrealized_pnl=0, daily_pnl=0, fees_paid=0, updated_at=1,
    )
    repository.accounts["__exchange__:live"] = AccountRecord(
        strategy="__exchange__", source="live_exchange", runtime_mode="live",
        initial_equity=2, cash_balance=2, equity=2, realized_pnl=0,
        unrealized_pnl=0, daily_pnl=0, fees_paid=0, updated_at=2,
    )

    result = await LiveStateSyncService(adapter, repository, timestamp_ms=lambda: 3).refresh(
        "strategy", symbols=[]
    )

    assert result.account is None
    assert result.positions == []
    assert adapter.account_calls == 0
    assert adapter.position_calls == []


@pytest.mark.asyncio
async def test_live_state_sync_empty_symbol_scope_ignores_stale_live_account():
    adapter = FakeAdapter()
    repository = FakeRepository()
    repository.accounts["__exchange__"] = AccountRecord(
        strategy="__exchange__", source="live_exchange", runtime_mode="live",
        initial_equity=2, cash_balance=2, equity=2, realized_pnl=0,
        unrealized_pnl=0, daily_pnl=0, fees_paid=0, updated_at=2, active=False,
    )

    result = await LiveStateSyncService(adapter, repository, timestamp_ms=lambda: 3).refresh(
        "strategy", symbols=[]
    )

    assert result.account is None


@pytest.mark.asyncio
async def test_live_state_sync_empty_symbol_scope_keeps_strict_live_account():
    adapter = FakeAdapter()
    repository = FakeRepository()
    live = AccountRecord(
        strategy="__exchange__", source="live_exchange", runtime_mode="live",
        initial_equity=2, cash_balance=2, equity=2, realized_pnl=0,
        unrealized_pnl=0, daily_pnl=0, fees_paid=0, updated_at=2,
    )
    repository.accounts["__exchange__"] = live

    result = await LiveStateSyncService(adapter, repository, timestamp_ms=lambda: 3).refresh(
        "strategy", symbols=[]
    )

    assert result.account is live
    assert result.positions == []
    assert adapter.account_calls == 0


@pytest.mark.asyncio
async def test_live_state_sync_does_not_persist_or_cleanup_when_positions_are_malformed():
    adapter = FakeAdapter()
    adapter.positions = None
    repository = FakeRepository()
    repository.positions[("__exchange__", "BTC-USDT-SWAP", "long")] = PositionRecord(
        strategy="__exchange__", symbol="BTC-USDT-SWAP", side="long", amount=1.0,
        entry_price=50000.0, leverage=1, timestamp=1700000000000,
    )

    with pytest.raises(MalformedProviderData):
        await LiveStateSyncService(
            adapter, repository, timestamp_ms=lambda: 1700000009999
        ).refresh("__exchange__", symbols=["BTC-USDT-SWAP"])

    assert repository.accounts == {}
    assert repository.deleted == []
    assert repository.positions[("__exchange__", "BTC-USDT-SWAP", "long")].amount == 1.0


@pytest.mark.asyncio
async def test_live_state_sync_upserts_provider_alias_into_existing_canonical_position_row():
    adapter = FakeAdapter()
    adapter.positions = [
        PositionSnapshot(
            symbol="BTC/USDT:USDT",
            side=PositionSide.LONG,
            amount=2.5,
            entry_price=50500.0,
            updated_at=1700000000002,
        )
    ]
    repository = FakeRepository()
    repository.positions[("__exchange__", "BTC-USDT-SWAP", "long")] = PositionRecord(
        strategy="__exchange__",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
    )

    result = await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=["BTC/USDT:USDT"])

    assert [(key, position.amount) for key, position in repository.positions.items()] == [
        (("__exchange__", "BTC-USDT-SWAP", "long"), 2.5)
    ]
    assert result.positions[0].symbol == "BTC-USDT-SWAP"
    assert repository.positions[("__exchange__", "BTC-USDT-SWAP", "long")].symbol == (
        "BTC-USDT-SWAP"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored_symbol", "provider_symbol", "canonical"),
    [
        ("BTC/USDT", "BTC-USDT", "BTC-USDT"),
        ("BTC/USDT:USDT", "BTC-USDT-SWAP", "BTC-USDT-SWAP"),
    ],
)
async def test_live_state_sync_does_not_reclassify_existing_legacy_alias(
    stored_symbol, provider_symbol, canonical
):
    adapter = FakeAdapter()
    adapter.positions = [
        PositionSnapshot(
            symbol=provider_symbol,
            side=PositionSide.LONG,
            amount=2.5,
            entry_price=50500.0,
            updated_at=1700000000002,
        )
    ]
    repository = FakeRepository()
    repository.positions[("__exchange__", stored_symbol, "long")] = PositionRecord(
        strategy="__exchange__",
        symbol=stored_symbol,
        side="long",
        amount=1.0,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
    )

    result = await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=[provider_symbol])

    assert set(repository.positions) == {
        ("__exchange__", stored_symbol, "long"),
        ("__exchange__", canonical, "long"),
    }
    legacy = repository.positions[("__exchange__", stored_symbol, "long")]
    live = repository.positions[("__exchange__", canonical, "long")]
    assert (legacy.source, legacy.runtime_mode, legacy.amount) == (
        "unknown", "unknown", 1.0
    )
    assert (live.source, live.runtime_mode, live.amount) == (
        "live_exchange", "live", 2.5
    )
    assert result.positions[0].symbol == canonical
    assert repository.deleted == []


@pytest.mark.asyncio
async def test_live_state_sync_keeps_different_dated_future_expiries_separate():
    adapter = FakeAdapter()
    adapter.positions = [
        PositionSnapshot(
            symbol="BTC/USDT:USDT-260925",
            side=PositionSide.LONG,
            amount=1.0,
            entry_price=50000.0,
            updated_at=1700000000001,
        ),
        PositionSnapshot(
            symbol="BTC/USDT:USDT-261225",
            side=PositionSide.LONG,
            amount=2.0,
            entry_price=51000.0,
            updated_at=1700000000002,
        ),
    ]
    repository = FakeRepository()

    result = await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh(
        "__exchange__",
        symbols=["BTC-USDT-260925", "BTC-USDT-261225"],
    )

    assert sorted(
        (position.symbol, position.amount) for position in result.positions
    ) == [
        ("BTC-USDT-260925", 1.0),
        ("BTC-USDT-261225", 2.0),
    ]
    assert len(repository.positions) == 2


@pytest.mark.asyncio
async def test_live_state_sync_filters_inverse_option_positions_to_exact_requested_contract():
    adapter = FakeAdapter()
    adapter.canonical_symbol = lambda symbol: canonical_symbol(symbol, market_type="option")
    adapter.positions = [
        PositionSnapshot(
            symbol="BTC/USD:BTC-260919-66000-C",
            side=PositionSide.LONG,
            amount=1.0,
            entry_price=1000.0,
            updated_at=1700000000001,
        ),
        PositionSnapshot(
            symbol="BTC/USD:BTC-260919-66000-P",
            side=PositionSide.LONG,
            amount=2.0,
            entry_price=900.0,
            updated_at=1700000000002,
        ),
        PositionSnapshot(
            symbol="BTC/USD:USD-260919-66000-C",
            side=PositionSide.LONG,
            amount=3.0,
            entry_price=800.0,
            updated_at=1700000000003,
        ),
    ]
    repository = FakeRepository()

    result = await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=["BTC-USD-260919-66000-C"])

    assert [position.symbol for position in result.positions] == [
        "BTC-USD-260919-66000-C"
    ]
    assert list(repository.positions) == [
        ("__exchange__", "BTC-USD-260919-66000-C", "long")
    ]


@pytest.mark.asyncio
async def test_live_state_sync_filters_option_provider_positions_to_exact_requested_contract():
    adapter = FakeAdapter()
    adapter.positions = [
        PositionSnapshot(
            symbol="BTC/USD:USD-260925-100000-C",
            side=PositionSide.LONG,
            amount=1.0,
            entry_price=1000.0,
            updated_at=1700000000001,
        ),
        PositionSnapshot(
            symbol="BTC/USD:USD-260925-110000-C",
            side=PositionSide.LONG,
            amount=2.0,
            entry_price=900.0,
            updated_at=1700000000002,
        ),
    ]
    repository = FakeRepository()

    result = await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=["BTC-USD-260925-100000-C"])

    assert [position.symbol for position in result.positions] == [
        "BTC-USD-260925-100000-C"
    ]
    assert list(repository.positions) == [
        ("__exchange__", "BTC-USD-260925-100000-C", "long")
    ]


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_live_state_sync_filters_decimal_option_positions_to_exact_contract():
    adapter = FakeAdapter()
    adapter.positions = [
        PositionSnapshot(
            symbol="BTC/USD:USD-260925-100000.5-C",
            side=PositionSide.LONG,
            amount=1.0,
            entry_price=1000.0,
            updated_at=1700000000001,
        ),
        PositionSnapshot(
            symbol="BTC/USD:USD-260925-100001.5-C",
            side=PositionSide.LONG,
            amount=2.0,
            entry_price=900.0,
            updated_at=1700000000002,
        ),
    ]
    repository = FakeRepository()

    result = await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=["BTC-USD-260925-100000.5-C"])

    assert [position.symbol for position in result.positions] == [
        "BTC-USD-260925-100000.5-C"
    ]
    assert list(repository.positions) == [
        ("__exchange__", "BTC-USD-260925-100000.5-C", "long")
    ]


@pytest.mark.asyncio
async def test_live_state_sync_preserves_option_call_put_scope_identity():
    adapter = FakeAdapter()
    adapter.positions = [
        PositionSnapshot(
            symbol="BTC-USD-260925-100000-P",
            side=PositionSide.LONG,
            amount=1.0,
            entry_price=1000.0,
            updated_at=1700000000001,
        )
    ]
    repository = FakeRepository()

    result = await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=["BTC-USD-260925-100000-C"])

    assert result.positions == []
    assert repository.positions == {}


@pytest.mark.asyncio
async def test_live_state_sync_matches_inverse_swap_scope_without_merging_settlements():
    adapter = FakeAdapter()
    adapter.positions = [
        PositionSnapshot(
            symbol="BTC/USD:BTC",
            side=PositionSide.LONG,
            amount=1.0,
            entry_price=68000.0,
            updated_at=1700000000001,
        ),
        PositionSnapshot(
            symbol="BTC/USD:USD",
            side=PositionSide.LONG,
            amount=2.0,
            entry_price=68000.0,
            updated_at=1700000000002,
        ),
    ]
    repository = FakeRepository()

    result = await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=["BTC-USD-SWAP"])

    assert adapter.position_calls == [["BTC-USD-SWAP"]]
    assert [position.symbol for position in result.positions] == ["BTC-USD-SWAP"]
    assert list(repository.positions) == [("__exchange__", "BTC-USD-SWAP", "long")]


@pytest.mark.asyncio
async def test_live_state_sync_filters_provider_positions_to_requested_symbols():

    adapter = FakeAdapter()
    adapter.positions = [
        PositionSnapshot(
            symbol="BTC-USDT-SWAP",
            side=PositionSide.LONG,
            amount=2.0,
            entry_price=50000.0,
            updated_at=1700000000001,
        ),
        PositionSnapshot(
            symbol="ETH-USDT-SWAP",
            side=PositionSide.LONG,
            amount=3.0,
            entry_price=3000.0,
            updated_at=1700000000002,
        ),
    ]
    repository = FakeRepository()
    repository.positions[("__exchange__", "ETH-USDT-SWAP", "long")] = PositionRecord(
        strategy="__exchange__",
        symbol="ETH-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=3000.0,
        leverage=1,
        timestamp=1700000000000,
    )

    result = await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=["BTC-USDT-SWAP"])

    assert [position.symbol for position in result.positions] == ["BTC-USDT-SWAP"]
    assert ("__exchange__", "BTC-USDT-SWAP", "long") in repository.positions
    assert repository.positions[("__exchange__", "ETH-USDT-SWAP", "long")].amount == 1.0
    assert repository.deleted == []


@pytest.mark.asyncio
async def test_live_state_sync_keeps_all_provider_positions_when_symbol_scope_is_none():
    adapter = FakeAdapter()
    adapter.positions = [
        PositionSnapshot(
            symbol="BTC-USDT-SWAP",
            side=PositionSide.LONG,
            amount=2.0,
            entry_price=50000.0,
            updated_at=1700000000001,
        ),
        PositionSnapshot(
            symbol="ETH-USDT-SWAP",
            side=PositionSide.LONG,
            amount=3.0,
            entry_price=3000.0,
            updated_at=1700000000002,
        ),
    ]
    repository = FakeRepository()

    result = await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=None)

    assert sorted(position.symbol for position in result.positions) == [
        "BTC-USDT-SWAP",
        "ETH-USDT-SWAP",
    ]
    assert len(repository.positions) == 2


@pytest.mark.asyncio
async def test_live_state_sync_canonicalizes_explicit_scope_before_position_fetch():
    adapter = FakeAdapter()
    repository = FakeRepository()

    await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh(
        "__exchange__",
        symbols=[
            "BTC/USDT",
            "BTC-USDT",
            "BTC/USDT:USDT",
            "BTC-USDT-SWAP",
            "BTC/USDT:USDT-260925",
            "BTC-USDT-260925",
        ],
    )

    assert adapter.position_calls == [[
        "BTC-USDT",
        "BTC-USDT-SWAP",
        "BTC-USDT-260925",
    ]]


@pytest.mark.asyncio
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
async def test_live_state_sync_validates_scope_using_adapter_market_type(
    market_type, symbol
):
    adapter = FakeAdapter()
    adapter._default_type = market_type
    repository = FakeRepository()

    with pytest.raises(MalformedProviderData, match="invalid symbol scope"):
        await LiveStateSyncService(
            adapter, repository, timestamp_ms=lambda: 1700000009999
        ).refresh("__exchange__", symbols=[symbol])

    assert adapter.account_calls == 0
    assert adapter.position_calls == []


@pytest.mark.asyncio
async def test_live_state_sync_rejects_invalid_explicit_scope_without_provider_calls():
    adapter = FakeAdapter()
    repository = FakeRepository()

    with pytest.raises(MalformedProviderData, match="invalid symbol scope"):
        await LiveStateSyncService(
            adapter, repository, timestamp_ms=lambda: 1700000009999
        ).refresh("__exchange__", symbols=["", "not-a-symbol", "BTC-USDT-not-an-expiry"])

    assert adapter.account_calls == 0
    assert adapter.position_calls == []


@pytest.mark.asyncio
async def test_live_state_sync_keeps_none_scope_as_full_sync():
    adapter = FakeAdapter()
    repository = FakeRepository()

    await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=None)

    assert adapter.position_calls == [None]


@pytest.mark.asyncio
async def test_live_state_sync_persists_account_and_positions():
    adapter = FakeAdapter()
    repository = FakeRepository()
    service = LiveStateSyncService(adapter, repository, timestamp_ms=lambda: 1700000009999)

    result = await service.refresh("__exchange__", symbols=["BTC-USDT-SWAP"])

    assert adapter.account_calls == 1
    assert adapter.position_calls == [["BTC-USDT-SWAP"]]
    account = repository.accounts["__exchange__"]
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
    position = repository.positions[("__exchange__", "BTC-USDT-SWAP", "long")]
    assert position.side == "long"
    assert position.amount == 2.0
    assert position.entry_price == 50000.0
    assert position.mark_price == 51000.0
    assert position.realized_pnl == 10.0
    assert position.unrealized_pnl == 200.0
    assert position.leverage == 3
    assert position.timestamp == 1700000000001


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested_symbol", "persisted_symbol"),
    [
        ("BTC/USDT", "BTC-USDT"),
        ("BTC/USDT:USDT", "BTC-USDT-SWAP"),
        ("BTC/USDT:USDT-260925", "BTC-USDT-260925"),
    ],
)
async def test_live_state_sync_archives_stale_positions_across_symbol_aliases(
    requested_symbol, persisted_symbol
):
    adapter = FakeAdapter()
    adapter.positions = []
    adapter.position_response = PositionSnapshotResult(
        positions=[], complete_snapshot=True, snapshot_timestamp=1700000000001
    )
    repository = FakeRepository()
    repository.positions[("__exchange__", persisted_symbol, "long")] = PositionRecord(
        strategy="__exchange__",
        symbol=persisted_symbol,
        side="long",
        amount=1.0,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
        source="live_exchange",
        runtime_mode="live",
    )

    await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=[requested_symbol])

    assert repository.deleted == []
    archived = repository.positions[("__exchange__", persisted_symbol, "long")]
    assert archived.active is False
    assert archived.stale_at == 1700000000001


@pytest.mark.asyncio
async def test_live_state_sync_marks_missing_position_inactive_and_keeps_audit_history(tmp_path):
    repository = Repository(db_path=str(tmp_path / "position-close.db"))
    repository.save_position(PositionRecord(
        strategy="__exchange__",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
        source="live_exchange",
        runtime_mode="live",
    ))
    adapter = FakeAdapter()
    adapter.positions = []
    adapter.position_response = PositionSnapshotResult(
        positions=[], complete_snapshot=True, snapshot_timestamp=1700000000001
    )
    adapter.account = AccountSnapshot(
        currency="USDT",
        equity=1000.0,
        cash_balance=1000.0,
        updated_at=1700000000001,
    )

    await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=["BTC-USDT-SWAP"])

    assert repository.get_open_positions("__exchange__") == []
    assert repository.get_positions("__exchange__") == []
    history = repository.get_positions("__exchange__", include_inactive=True)
    assert len(history) == 1
    assert history[0].active is False
    assert history[0].stale_at == 1700000000001


@pytest.mark.asyncio
async def test_live_state_sync_reactivates_the_same_position_identity(tmp_path):
    repository = Repository(db_path=str(tmp_path / "position-reactivate.db"))
    stale = repository.save_position(PositionRecord(
        strategy="__exchange__",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
        source="live_exchange",
        runtime_mode="live",
        active=False,
        stale_at=1700000000001,
    ))
    adapter = FakeAdapter()
    adapter.positions = [PositionSnapshot(
        symbol="BTC-USDT-SWAP",
        side=PositionSide.LONG,
        amount=2.0,
        entry_price=51000.0,
        leverage=2,
        updated_at=1700000000002,
    )]

    await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=["BTC-USDT-SWAP"])

    current = repository.get_position("__exchange__", "BTC-USDT-SWAP", "long")
    assert current.id == stale.id
    assert current.active is True
    assert current.stale_at is None
    assert current.amount == 2.0
    assert len(repository.get_positions("__exchange__", include_inactive=True)) == 1


@pytest.mark.asyncio
async def test_live_state_sync_archives_stale_synced_positions_for_requested_symbols():
    adapter = FakeAdapter()
    adapter.positions = []
    adapter.position_response = PositionSnapshotResult(
        positions=[], complete_snapshot=True, snapshot_timestamp=1700000000001
    )
    repository = FakeRepository()
    repository.positions[("__exchange__", "BTC-USDT-SWAP", "long")] = PositionRecord(
        strategy="__exchange__",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
        source="live_exchange",
        runtime_mode="live",
    )
    repository.positions[("__exchange__", "ETH-USDT-SWAP", "long")] = PositionRecord(
        strategy="__exchange__",
        symbol="ETH-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=3000.0,
        leverage=1,
        timestamp=1700000000000,
        source="live_exchange",
        runtime_mode="live",
    )
    service = LiveStateSyncService(adapter, repository, timestamp_ms=lambda: 1700000009999)

    result = await service.refresh("__exchange__", symbols=["BTC-USDT-SWAP"])

    assert result.positions == []
    assert repository.deleted == []
    archived = repository.positions[("__exchange__", "BTC-USDT-SWAP", "long")]
    assert archived.active is False
    assert archived.stale_at == 1700000000001
    assert ("__exchange__", "ETH-USDT-SWAP", "long") in repository.positions


@pytest.mark.asyncio
@pytest.mark.parametrize("refresh_timestamp", [1699999999999, 0])
async def test_live_state_sync_preserves_newer_persisted_position_on_stale_or_invalid_empty_refresh(
    refresh_timestamp,
):
    adapter = FakeAdapter()
    adapter.positions = []
    adapter.account = AccountSnapshot(
        currency="USDT",
        equity=1000.0,
        cash_balance=975.0,
        available_balance=965.0,
        updated_at=refresh_timestamp,
    )
    repository = FakeRepository()
    repository.positions[("__exchange__", "BTC-USDT-SWAP", "long")] = PositionRecord(
        strategy="__exchange__",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
    )

    await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=["BTC-USDT-SWAP"])

    assert repository.deleted == []
    assert ("__exchange__", "BTC-USDT-SWAP", "long") in repository.positions


@pytest.mark.asyncio
@pytest.mark.parametrize("refresh_timestamp", [1700000000000, 1700000000001])
async def test_live_state_sync_does_not_archive_on_account_newer_empty_refresh(
    refresh_timestamp,
):
    adapter = FakeAdapter()
    adapter.positions = []
    adapter.account = AccountSnapshot(
        currency="USDT",
        equity=1000.0,
        cash_balance=975.0,
        available_balance=965.0,
        updated_at=refresh_timestamp,
    )
    repository = FakeRepository()
    repository.positions[("__exchange__", "BTC-USDT-SWAP", "long")] = PositionRecord(
        strategy="__exchange__",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
    )

    await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=["BTC-USDT-SWAP"])

    assert repository.deleted == []
    retained = repository.positions[("__exchange__", "BTC-USDT-SWAP", "long")]
    assert retained.active is True
    assert retained.stale_at is None


@pytest.mark.asyncio
async def test_live_state_sync_archives_only_explicitly_complete_trusted_empty_snapshot():
    adapter = FakeAdapter()
    adapter.positions = []
    adapter.position_response = PositionSnapshotResult(
        positions=[], complete_snapshot=True, snapshot_timestamp=1700000000001
    )
    repository = FakeRepository()
    repository.positions[("__exchange__", "BTC-USDT-SWAP", "long")] = PositionRecord(
        strategy="__exchange__",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
        source="live_exchange",
        runtime_mode="live",
    )

    await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=["BTC-USDT-SWAP"])

    archived = repository.positions[("__exchange__", "BTC-USDT-SWAP", "long")]
    assert archived.active is False
    assert archived.stale_at == 1700000000001


@pytest.mark.asyncio
async def test_live_state_sync_ignores_invalid_time_position_update_for_existing_position(caplog):
    adapter = FakeAdapter()
    adapter.positions = [
        PositionSnapshot(
            symbol="BTC-USDT-SWAP",
            side=PositionSide.LONG,
            amount=9.0,
            entry_price=90000.0,
            updated_at=0,
            timestamp=0,
        )
    ]
    repository = FakeRepository()
    repository.positions[("__exchange__", "BTC-USDT-SWAP", "long")] = PositionRecord(
        strategy="__exchange__",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
    )

    await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=["BTC-USDT-SWAP"])

    position = repository.positions[("__exchange__", "BTC-USDT-SWAP", "long")]
    assert position.amount == 1.0
    assert position.entry_price == 50000.0
    assert repository.deleted == []
    assert "without trusted timestamp" in caplog.text


@pytest.mark.asyncio
async def test_live_state_sync_skips_invalid_time_new_position_without_persisting_a_phantom():
    adapter = FakeAdapter()
    adapter.positions = [
        PositionSnapshot(
            symbol="BTC-USDT-SWAP",
            side=PositionSide.LONG,
            amount=2.0,
            entry_price=50000.0,
            mark_price=51000.0,
            leverage=3,
            updated_at=0,
            timestamp=0,
        )
    ]
    repository = FakeRepository()

    result = await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=["BTC-USDT-SWAP"])

    assert result.positions == []
    assert repository.positions == {}
    assert repository.deleted == []


@pytest.mark.asyncio
async def test_live_state_sync_does_not_archive_non_live_or_unknown_positions():
    adapter = FakeAdapter()
    adapter.positions = []
    adapter.position_response = PositionSnapshotResult(
        positions=[], complete_snapshot=True, snapshot_timestamp=1700000000002
    )
    repository = FakeRepository()
    for source, runtime_mode, symbol in [
        ("paper", "paper", "BTC-USDT-SWAP"),
        ("backtest", "backtest", "ETH-USDT-SWAP"),
        ("unknown", "unknown", "SOL-USDT-SWAP"),
    ]:
        repository.positions[("__exchange__", symbol, "long")] = PositionRecord(
            strategy="__exchange__", symbol=symbol, side="long", amount=1.0,
            entry_price=100.0, leverage=1, timestamp=1700000000000,
            source=source, runtime_mode=runtime_mode,
        )

    await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=None)

    assert [
        (position.source, position.runtime_mode, position.active, position.stale_at)
        for position in repository.positions.values()
    ] == [
        ("paper", "paper", True, None),
        ("backtest", "backtest", True, None),
        ("unknown", "unknown", True, None),
    ]


@pytest.mark.asyncio
async def test_live_state_sync_does_not_delete_existing_position_for_invalid_time_snapshot():
    adapter = FakeAdapter()
    adapter.positions = [
        PositionSnapshot(
            symbol="BTC-USDT-SWAP",
            side=PositionSide.LONG,
            amount=0.0,
            entry_price=0.0,
            updated_at=0,
            timestamp=0,
        )
    ]
    repository = FakeRepository()
    repository.positions[("__exchange__", "BTC-USDT-SWAP", "long")] = PositionRecord(
        strategy="__exchange__",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
    )

    await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=["BTC-USDT-SWAP"])

    assert repository.positions[("__exchange__", "BTC-USDT-SWAP", "long")].amount == 1.0
    assert repository.deleted == []


@pytest.mark.asyncio
async def test_live_state_sync_archives_stale_opposite_side_for_returned_symbol():
    adapter = FakeAdapter()
    repository = FakeRepository()
    repository.positions[("__exchange__", "BTC-USDT-SWAP", "long")] = PositionRecord(
        strategy="__exchange__",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
        source="live_exchange",
        runtime_mode="live",
    )
    repository.positions[("__exchange__", "BTC-USDT-SWAP", "short")] = PositionRecord(
        strategy="__exchange__",
        symbol="BTC-USDT-SWAP",
        side="short",
        amount=1.0,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
        source="live_exchange",
        runtime_mode="live",
    )

    await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=["BTC-USDT-SWAP"])

    assert ("__exchange__", "BTC-USDT-SWAP", "long") in repository.positions
    archived = repository.positions[("__exchange__", "BTC-USDT-SWAP", "short")]
    assert archived.active is False
    assert archived.stale_at == 1700000000001
    assert repository.deleted == []


@pytest.mark.asyncio
async def test_live_state_sync_initializes_zero_baseline_from_first_nonzero_equity():
    adapter = FakeAdapter()
    repository = FakeRepository()
    repository.accounts["__exchange__"] = AccountRecord(
        strategy="__exchange__",
        initial_equity=0.0,
        cash_balance=0.0,
        equity=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1,
    )

    await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=["BTC-USDT-SWAP"])

    assert repository.accounts["__exchange__"].initial_equity == 1000.0


@pytest.mark.asyncio
async def test_live_state_sync_preserves_existing_daily_pnl_and_fees():
    adapter = FakeAdapter()
    repository = FakeRepository()
    repository.accounts["__exchange__"] = AccountRecord(
        strategy="__exchange__",
        initial_equity=900.0,
        cash_balance=900.0,
        equity=900.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=-12.0,
        fees_paid=3.5,
        updated_at=1699999999999,
        source="live_exchange",
        runtime_mode="live",
    )
    service = LiveStateSyncService(adapter, repository, timestamp_ms=lambda: 1700000009999)

    await service.refresh("__exchange__", symbols=["BTC-USDT-SWAP"])

    account = repository.accounts["__exchange__"]
    assert account.initial_equity == 900.0
    assert account.daily_pnl == -12.0
    assert account.fees_paid == 3.5


@pytest.mark.asyncio
async def test_live_state_sync_does_not_fabricate_account_timestamp_when_provider_time_is_unknown():
    adapter = FakeAdapter()
    adapter.account = AccountSnapshot(
        equity=1200.0,
        cash_balance=1100.0,
        available_balance=1000.0,
        timestamp=0,
        updated_at=0,
    )
    repository = FakeRepository()
    repository.accounts["__exchange__"] = AccountRecord(
        strategy="__exchange__",
        initial_equity=900.0,
        cash_balance=900.0,
        available_balance=800.0,
        equity=900.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1700000000000,
    )

    await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=["BTC-USDT-SWAP"])

    account = repository.accounts["__exchange__"]
    assert account.equity == 900.0
    assert account.updated_at == 1700000000000


@pytest.mark.asyncio
async def test_live_state_sync_skips_untrusted_first_account_snapshot(caplog):
    adapter = FakeAdapter()
    adapter.account = AccountSnapshot(
        equity=1200.0,
        cash_balance=1100.0,
        available_balance=1000.0,
        timestamp=0,
        updated_at=0,
    )
    repository = FakeRepository()

    result = await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=["BTC-USDT-SWAP"])

    assert result.account is None
    assert repository.get_account("__exchange__") is None
    assert "without trusted timestamp" in caplog.text


@pytest.mark.asyncio
async def test_live_state_sync_rolls_back_account_when_position_persistence_fails(
    monkeypatch, tmp_path
):
    repository = Repository(db_path=str(tmp_path / "live-state-rollback.db"))
    adapter = FakeAdapter()
    original_upsert_position = repository_module._AccountingTransaction.upsert_position

    def fail_position(transaction, position):
        original_upsert_position(transaction, position)
        raise RuntimeError("position persistence failed")

    monkeypatch.setattr(
        repository_module._AccountingTransaction, "upsert_position", fail_position
    )

    with pytest.raises(RuntimeError, match="position persistence failed"):
        await LiveStateSyncService(
            adapter, repository, timestamp_ms=lambda: 1700000009999
        ).refresh("__exchange__", symbols=["BTC-USDT-SWAP"])

    assert repository.get_account("__exchange__") is None
    assert repository.get_positions("__exchange__") == []


@pytest.mark.asyncio
async def test_live_state_sync_commits_account_positions_and_stale_cleanup_together(tmp_path):
    repository = Repository(db_path=str(tmp_path / "live-state-success.db"))
    repository.upsert_position(PositionRecord(
        strategy="__exchange__",
        symbol="BTC-USDT-SWAP",
        side="short",
        amount=1.0,
        entry_price=50000.0,
        leverage=1,
        timestamp=1700000000000,
        source="live_exchange",
        runtime_mode="live",
    ))

    result = await LiveStateSyncService(
        FakeAdapter(), repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=["BTC-USDT-SWAP"])

    assert repository.get_account("__exchange__") is not None
    assert [(position.symbol, position.side) for position in result.positions] == [
        ("BTC-USDT-SWAP", "long")
    ]
    archived = repository.get_position(
        "__exchange__", "BTC-USDT-SWAP", "short", include_inactive=True
    )
    assert archived is not None
    assert archived.active is False


@pytest.mark.asyncio
async def test_live_refresh_marks_unreturned_legacy_alias_stale_but_retains_history(tmp_path):
    repository = repository_module.Repository(db_path=str(tmp_path / "alias-stale.db"))
    repository.upsert_position(
        PositionRecord(
            strategy="__exchange__",
            symbol="BTC/USDT:USDT",
            side="long",
            amount=1.0,
            entry_price=50000.0,
            leverage=1,
            timestamp=1700000000000,
            source="live_exchange",
            runtime_mode="live",
        )
    )
    adapter = FakeAdapter()
    adapter.positions = []
    adapter.position_response = PositionSnapshotResult(
        positions=[], complete_snapshot=True, snapshot_timestamp=1700000000001
    )
    adapter.account = AccountSnapshot(
        equity=1000.0,
        cash_balance=900.0,
        available_balance=850.0,
        updated_at=1700000000001,
    )

    result = await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=["BTC-USDT-SWAP"])

    retained = repository.get_positions("__exchange__", include_inactive=True)[0]
    assert result.positions == []
    assert retained.symbol == "BTC/USDT:USDT"
    assert retained.amount == 1.0
    assert retained.active is False
    assert retained.stale_at == 1700000000001
    assert repository.get_open_positions("__exchange__") == []


@pytest.mark.asyncio
async def test_live_refresh_without_trusted_scope_keeps_legacy_alias_active(tmp_path):
    repository = repository_module.Repository(db_path=str(tmp_path / "alias-no-scope.db"))
    repository.upsert_position(
        PositionRecord(
            strategy="__exchange__",
            symbol="BTC/USDT:USDT",
            side="long",
            amount=1.0,
            entry_price=50000.0,
            leverage=1,
            timestamp=1700000000000,
            source="live_exchange",
            runtime_mode="live",
        )
    )
    adapter = FakeAdapter()
    adapter.positions = []
    adapter.account = AccountSnapshot(
        equity=1000.0,
        cash_balance=900.0,
        available_balance=850.0,
        updated_at=0,
        timestamp=0,
    )

    await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=None)

    retained = repository.get_positions("__exchange__", include_inactive=True)[0]
    assert retained.active is True
    assert retained.stale_at is None
    assert repository.get_open_positions("__exchange__") == [retained]


@pytest.mark.asyncio
async def test_live_refresh_keeps_canonical_and_different_expiry_positions_distinct(tmp_path):
    repository = repository_module.Repository(db_path=str(tmp_path / "alias-expiry.db"))
    adapter = FakeAdapter()
    adapter.positions = [
        PositionSnapshot(
            symbol="BTC-USDT-260925",
            side=PositionSide.LONG,
            amount=1.0,
            entry_price=50000.0,
            updated_at=1700000000001,
        ),
        PositionSnapshot(
            symbol="BTC-USDT-261225",
            side=PositionSide.LONG,
            amount=2.0,
            entry_price=51000.0,
            updated_at=1700000000002,
        ),
    ]

    await LiveStateSyncService(
        adapter, repository, timestamp_ms=lambda: 1700000009999
    ).refresh(
        "__exchange__",
        symbols=["BTC-USDT-260925", "BTC-USDT-261225"],
    )

    assert sorted(
        (position.symbol, position.amount)
        for position in repository.get_open_positions("__exchange__")
    ) == [
        ("BTC-USDT-260925", 1.0),
        ("BTC-USDT-261225", 2.0),
    ]


@pytest.mark.asyncio
async def test_live_refresh_retires_conflicting_paper_exchange_rows_and_creates_live_rows(tmp_path):
    repository = repository_module.Repository(db_path=str(tmp_path / "provenance-conflict.db"))
    repository.upsert_account(AccountRecord(
        strategy="__exchange__", initial_equity=900.0, cash_balance=900.0,
        equity=900.0, realized_pnl=0.0, unrealized_pnl=0.0, daily_pnl=0.0,
        fees_paid=0.0, updated_at=1, source="paper", runtime_mode="paper",
    ))
    repository.upsert_position(PositionRecord(
        strategy="__exchange__", symbol="BTC-USDT-SWAP", side="long", amount=1.0,
        entry_price=40000.0, leverage=1, timestamp=1, source="paper", runtime_mode="paper",
    ))

    result = await LiveStateSyncService(
        FakeAdapter(), repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=["BTC-USDT-SWAP"])

    positions = repository.get_positions("__exchange__", include_inactive=True)
    assert result.account is not None
    assert result.account.source == "live_exchange"
    assert result.account.runtime_mode == "live"
    with repository.engine.connect() as connection:
        account_rows = connection.exec_driver_sql(
            "SELECT source, runtime_mode, active FROM accountrecord "
            "WHERE strategy = '__exchange__' ORDER BY id"
        ).all()
    assert account_rows == [("paper", "paper", 1), ("live_exchange", "live", 1)]
    assert [
        (position.source, position.runtime_mode, position.active)
        for position in positions
    ] == [("paper", "paper", True), ("live_exchange", "live", True)]


@pytest.mark.asyncio
async def test_live_refresh_retires_unknown_legacy_exchange_rows_without_reclassifying_them(
    tmp_path,
):
    repository = repository_module.Repository(db_path=str(tmp_path / "unknown-conflict.db"))
    repository.upsert_account(AccountRecord(
        strategy="__exchange__", initial_equity=900.0, cash_balance=900.0,
        equity=900.0, realized_pnl=0.0, unrealized_pnl=0.0, daily_pnl=0.0,
        fees_paid=0.0, updated_at=1,
    ))

    await LiveStateSyncService(
        FakeAdapter(), repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=["BTC-USDT-SWAP"])

    with repository.engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT source, runtime_mode, active FROM accountrecord "
            "WHERE strategy = '__exchange__' ORDER BY id"
        ).all()
    assert rows == [("unknown", "unknown", 1), ("live_exchange", "live", 1)]


@pytest.mark.asyncio
async def test_live_refreshes_from_two_strategies_write_only_shared_exchange_snapshots():
    repository = FakeRepository()
    first = await LiveStateSyncService(
        FakeAdapter(), repository, timestamp_ms=lambda: 1700000009999
    ).refresh("alpha", symbols=["BTC-USDT-SWAP"])
    second = await LiveStateSyncService(
        FakeAdapter(), repository, timestamp_ms=lambda: 1700000009999
    ).refresh("beta", symbols=["BTC-USDT-SWAP"])

    assert first.account is not None
    assert second.account is not None
    assert set(repository.accounts) == {"__exchange__"}
    assert set(repository.positions) == {("__exchange__", "BTC-USDT-SWAP", "long")}


@pytest.mark.asyncio
async def test_live_refresh_does_not_use_non_live_account_as_existing_or_mark_it_stale(tmp_path):
    repository = Repository(db_path=str(tmp_path / "account-domain-fallback.db"))
    repository.upsert_account(AccountRecord(
        strategy="__exchange__", initial_equity=900.0, cash_balance=900.0,
        equity=900.0, realized_pnl=0.0, unrealized_pnl=0.0, daily_pnl=0.0,
        fees_paid=0.0, updated_at=1, source="paper", runtime_mode="paper",
    ))

    result = await LiveStateSyncService(
        FakeAdapter(), repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=["BTC-USDT-SWAP"])

    assert result.account.source == "live_exchange"
    assert result.account.runtime_mode == "live"
    assert result.account.initial_equity == 1000.0
    assert repository.get_account(
        "__exchange__", source="paper", runtime_mode="paper", include_inactive=True
    ).active is True
    assert repository.get_account(
        "__exchange__", source="live_exchange", runtime_mode="live"
    ).active is True


@pytest.mark.asyncio
async def test_live_refresh_does_not_use_non_live_position_as_existing_or_mark_it_stale(tmp_path):
    repository = Repository(db_path=str(tmp_path / "position-domain-fallback.db"))
    repository.upsert_position(PositionRecord(
        strategy="__exchange__", symbol="BTC-USDT-SWAP", side="long", amount=9.0,
        entry_price=40000.0, leverage=1, timestamp=1,
        source="paper", runtime_mode="paper",
    ))

    result = await LiveStateSyncService(
        FakeAdapter(), repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=["BTC-USDT-SWAP"])

    assert result.positions[0].amount == 2.0
    paper = repository.get_position(
        "__exchange__", "BTC-USDT-SWAP", "long",
        source="paper", runtime_mode="paper", include_inactive=True,
    )
    live = repository.get_position(
        "__exchange__", "BTC-USDT-SWAP", "long",
        source="live_exchange", runtime_mode="live",
    )
    assert (paper.amount, paper.active, paper.stale_at) == (9.0, True, None)
    assert live.amount == 2.0


@pytest.mark.asyncio
async def test_live_refresh_keeps_unknown_position_history_separate_from_new_live_row(tmp_path):
    repository = repository_module.Repository(db_path=str(tmp_path / "unknown-position.db"))
    repository.upsert_position(PositionRecord(
        strategy="__exchange__", symbol="BTC-USDT-SWAP", side="long", amount=9.0,
        entry_price=40000.0, leverage=1, timestamp=1,
    ))

    await LiveStateSyncService(
        FakeAdapter(), repository, timestamp_ms=lambda: 1700000009999
    ).refresh("__exchange__", symbols=["BTC-USDT-SWAP"])

    positions = repository.get_positions("__exchange__", include_inactive=True)
    assert [(position.source, position.runtime_mode, position.active, position.amount)
            for position in positions] == [
        ("unknown", "unknown", True, 9.0),
        ("live_exchange", "live", True, 2.0),
    ]
