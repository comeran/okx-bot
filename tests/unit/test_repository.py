import json
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import SQLModel, create_engine

import src.data.repository as repository_module
from src.core.types import AssetBalance, Order, OrderSide, OrderStatus, OrderType
from src.data.models import (
    AccountRecord,
    BacktestResultRecord,
    BacktestTradeRecord,
    CashLedgerRecord,
    KillSwitchRecord,
    KlineCache,
    OrderRecord,
    PositionRecord,
    RiskEventRecord,
    StrategyConfigRecord,
    TradeRecord,
)
from src.data.repository import Repository, _record_provenance
from src.order.accounting import PaperAccountingService


@pytest.fixture
def repo():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    return Repository(engine)


def test_save_and_get_trade(repo: Repository):
    trade = TradeRecord(
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        amount=0.1,
        price=50000.0,
        fee=2.5,
        timestamp=1700000000000,
    )
    repo.save_trade(trade)
    trades = repo.get_trades(strategy="ma_cross")
    assert len(trades) == 1
    assert trades[0].symbol == "BTC-USDT"


def test_save_trade_on_file_database_is_visible_to_a_new_repository(tmp_path):
    db_path = tmp_path / "trades.db"
    repository = Repository(db_path=str(db_path))
    trade = TradeRecord(
        order_id="local-trade-1",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        amount=0.1,
        price=50000.0,
        fee=2.5,
        timestamp=1700000000000,
    )

    saved = repository.save_trade(trade)
    reloaded = Repository(db_path=str(db_path))

    assert saved.id is not None
    assert [item.order_id for item in reloaded.get_trades()] == ["local-trade-1"]


def test_save_and_upsert_trade_persist_only_normalized_numeric_values(repo: Repository):
    trade = TradeRecord(
        exchange_trade_id="trade-invalid-values",
        order_id="order-1",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        amount=float("nan"),
        price=float("inf"),
        fee=-1.0,
        timestamp=1700000000000,
    )

    saved = repo.save_trade(trade)
    upserted = repo.upsert_trade(trade)
    persisted = repo.get_trades(strategy="ma_cross")

    assert saved.amount == 0.0
    assert saved.price == 0.0
    assert saved.fee == 0.0
    assert upserted.model_dump()["amount"] == 0.0
    assert upserted.model_dump()["price"] == 0.0
    assert upserted.model_dump()["fee"] == 0.0
    assert [(item.amount, item.price, item.fee) for item in persisted] == [(0.0, 0.0, 0.0)]


def test_order_record_sanitizes_numeric_conversion_overflow():
    value = 10**1000
    order = OrderRecord(
        order_id="numeric-overflow",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=value,
        price=value,
        status="pending",
        fill_price=value,
        timestamp=1,
        updated_at=2,
        filled_amount=value,
        remaining_amount=value,
        snapshot_filled_amount=value,
    )
    trade = TradeRecord(
        exchange_trade_id="numeric-overflow-trade",
        order_id="numeric-overflow",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        amount=value,
        price=value,
        fee=value,
        timestamp=1,
    )

    assert (
        order.amount,
        order.price,
        order.fill_price,
        order.filled_amount,
        order.remaining_amount,
        order.snapshot_filled_amount,
    ) == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert (trade.amount, trade.price, trade.fee) == (0.0, 0.0, 0.0)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0])
def test_order_record_sanitizes_invalid_prices(value):
    order = OrderRecord(
        order_id="invalid-price",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=1.0,
        price=value,
        status="pending",
        fill_price=value,
        timestamp=1,
    )

    assert order.price == 0.0
    assert order.fill_price == 0.0
    assert all(
        value == 0.0 or value is None
        for value in (order.model_dump()["price"], order.model_dump()["fill_price"])
    )


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), 2**63, "bad"])
def test_direct_kill_switch_and_risk_event_records_normalize_timestamps(value):
    kill_switch = KillSwitchRecord(updated_at=value)
    risk_event = RiskEventRecord(event_type="risk", timestamp=value)

    assert kill_switch.updated_at == 0
    assert risk_event.timestamp == 0


def test_saved_kill_switch_and_risk_event_records_have_safe_timestamps(repo):
    kill_switch = repo.set_kill_switch(True, "test", -1)
    risk_event = repo.save_risk_event({"type": "risk", "timestamp": float("inf")})

    assert kill_switch.updated_at == 0
    assert risk_event.timestamp == 0
    assert repo.get_kill_switch().updated_at == 0
    assert repo.get_risk_events()[0].timestamp == 0


@pytest.mark.parametrize("timestamp", [float("nan"), float("inf"), -1.0, "invalid"])
def test_order_record_normalizes_invalid_timestamps(timestamp):
    order = OrderRecord(
        order_id="invalid-timestamp",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=1.0,
        price=50000.0,
        status="pending",
        fill_price=0.0,
        timestamp=timestamp,
        updated_at=timestamp,
    )

    assert order.timestamp == 0
    assert order.updated_at == 0


@pytest.mark.parametrize("timestamp", [float("nan"), float("inf"), -1.0, "invalid"])
def test_trade_record_normalizes_invalid_timestamps(timestamp):
    trade = TradeRecord(
        exchange_trade_id="invalid-timestamp",
        order_id="order-1",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        amount=0.1,
        price=50000.0,
        fee=0.0,
        timestamp=timestamp,
    )

    assert trade.timestamp == 0


def test_order_record_normalizes_partial_fill_and_invalid_remaining():
    order = OrderRecord(
        order_id="partial",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=1.0,
        price=50000.0,
        status="partially_filled",
        fill_price=50000.0,
        timestamp=1,
        filled_amount=0.25,
    )

    assert order.filled_amount == 0.25
    assert order.remaining_amount == 0.75

    order = OrderRecord(
        order_id="overfilled",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=1.0,
        price=50000.0,
        status="filled",
        fill_price=50000.0,
        timestamp=1,
        filled_amount=2.0,
    )
    assert order.remaining_amount == 0.0

    order = OrderRecord(
        order_id="invalid-remaining",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=1.0,
        price=50000.0,
        status="pending",
        fill_price=0.0,
        timestamp=1,
        filled_amount=0.25,
        remaining_amount=2.0,
    )
    assert order.filled_amount == 0.25
    assert order.remaining_amount == 0.75


def test_order_record_preserves_canceled_compatibility_status():
    for status in ("canceled", "cancelled"):
        order = OrderRecord(
            order_id=f"{status}-order",
            strategy="ma_cross",
            symbol="BTC-USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=50000.0,
            status=status,
            fill_price=0.0,
            timestamp=1,
            filled_amount=0.0,
        )
        assert order.status == status


@pytest.mark.parametrize(
    ("status", "filled_amount", "expected_status"),
    [
        ("pending", 0.5, "partially_filled"),
        ("filled", 0.5, "partially_filled"),
        ("partially_filled", 0.0, "pending"),
        ("pending", 1.0, "filled"),
        ("cancelled", 0.0, "cancelled"),
        ("rejected", 0.0, "rejected"),
    ],
)
def test_order_record_normalizes_status_from_filled_amount(
    status, filled_amount, expected_status
):
    order = OrderRecord(
        order_id="status-normalized",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=1.0,
        price=50000.0,
        status=status,
        fill_price=50000.0,
        timestamp=1,
        filled_amount=filled_amount,
    )

    assert order.status == expected_status


def test_order_record_preserves_decimal_float_total_invariant():
    order = OrderRecord(
        order_id="order-float-invariant",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=0.3,
        price=100.0,
        status="partially_filled",
        fill_price=100.0,
        timestamp=1,
        remaining_amount=0.1,
    )

    assert order.filled_amount + order.remaining_amount == order.amount
    assert order.remaining_amount == 0.1


def test_order_record_preserves_high_precision_total_invariant():
    amount = 0.1234567890123
    remaining = 0.1

    order = OrderRecord(
        order_id="high-precision",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=amount,
        price=50000.0,
        status="partially_filled",
        fill_price=50000.0,
        timestamp=1,
        remaining_amount=remaining,
    )

    assert order.filled_amount == amount - remaining
    assert order.filled_amount + order.remaining_amount == order.amount


def test_order_record_normalizes_filled_zero_even_when_status_is_filled():
    order = OrderRecord(
        order_id="zero-filled",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=1.0,
        price=50000.0,
        status="filled",
        fill_price=50000.0,
        timestamp=1,
        filled_amount=0.0,
    )

    assert order.status == "filled"


def test_order_record_rejects_inconsistent_explicit_fill_group():
    with pytest.raises(ValueError, match="inconsistent filled and remaining amounts"):
        OrderRecord(
            order_id="authoritative-remaining",
            strategy="ma_cross",
            symbol="BTC-USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=50000.0,
            status="partially_filled",
            fill_price=50000.0,
            timestamp=1,
            filled_amount=0.4000000003,
            remaining_amount=0.6,
        )


def test_save_and_get_order(repo: Repository):
    order = OrderRecord(
        order_id="ord-001",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=0.1,
        price=50000.0,
        status="filled",
        fill_price=50000.0,
        timestamp=1700000000000,
        filled_amount=0.1,
    )
    repo.save_order(order)
    orders = repo.get_orders(order_id="ord-001")
    assert len(orders) == 1
    assert orders[0].status == "filled"


def test_position_save_and_upsert_persist_normalized_values(repo: Repository):
    saved = repo.save_position(
        PositionRecord(
            strategy="s",
            symbol="BTC-USDT-SWAP",
            side="short",
            amount=-2.0,
            entry_price=float("inf"),
            leverage=-2,
            timestamp=-1,
            mark_price=float("nan"),
            realized_pnl=-1.5,
            unrealized_pnl=float("inf"),
        )
    )
    updated = repo.upsert_position(
        PositionRecord(
            strategy="s",
            symbol="BTC-USDT-SWAP",
            side="short",
            amount=3.0,
            entry_price=100.0,
            leverage=5,
            timestamp=10,
            mark_price=101.0,
            realized_pnl=-2.0,
            unrealized_pnl=4.0,
        )
    )

    assert saved.amount == 2.0
    assert saved.entry_price == 0.0
    assert saved.mark_price is None
    assert saved.timestamp == 0
    assert updated.amount == 3.0
    assert updated.side == "short"
    assert updated.unrealized_pnl == 4.0


@pytest.mark.parametrize(
    "value",
    [2**63, 10**1000, "9223372036854775808", float("nan"), float("inf"), -1],
)
def test_save_position_never_persists_out_of_range_leverage(repo: Repository, value):
    saved = repo.save_position(
        PositionRecord(
            strategy="s",
            symbol=f"BTC-USDT-SWAP-{str(value)[:8]}",
            side="long",
            amount=1.0,
            entry_price=100.0,
            leverage=value,
            timestamp=1,
        )
    )

    assert saved.leverage == 1
    assert repo.get_position(saved.strategy, saved.symbol).leverage == 1


@pytest.mark.parametrize(
    ("method", "record"),
    [
        (
            "upsert_account",
            AccountRecord(
                strategy="locked-account",
                initial_equity=1.0,
                cash_balance=1.0,
                equity=1.0,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
                daily_pnl=0.0,
                fees_paid=0.0,
                updated_at=1,
            ),
        ),
        (
            "upsert_position",
            PositionRecord(
                strategy="locked-position",
                symbol="BTC-USDT",
                side="long",
                amount=1.0,
                entry_price=1.0,
                leverage=1,
                timestamp=1,
            ),
        ),
        (
            "upsert_order",
            OrderRecord(
                order_id="locked-order",
                strategy="locked-order",
                symbol="BTC-USDT",
                side="buy",
                type="limit",
                amount=1.0,
                price=1.0,
                status="pending",
                fill_price=0.0,
                timestamp=1,
            ),
        ),
    ],
)
def test_upserts_retry_a_transient_sqlite_lock(repo, monkeypatch, method, record):
    original_flush = repository_module.Session.flush
    attempts = 0

    def locked_once(session, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OperationalError("FLUSH", {}, Exception("database is locked"))
        return original_flush(session, *args, **kwargs)

    monkeypatch.setattr(repository_module.Session, "flush", locked_once)

    assert getattr(repo, method)(record).id is not None
    assert attempts >= 2


def test_save_and_get_position(repo: Repository):
    pos = PositionRecord(
        strategy="ma_cross",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=0.5,
        entry_price=50000.0,
        leverage=10,
        timestamp=1700000000000,
    )
    repo.save_position(pos)
    positions = repo.get_positions(strategy="ma_cross")
    assert len(positions) == 1
    assert positions[0].leverage == 10


def test_upsert_position_id_conflict_preserves_foreign_provenance_and_inserts_isolated_row(
    repo: Repository,
):
    paper = repo.save_position(PositionRecord(
        strategy="__exchange__",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=40000.0,
        leverage=1,
        timestamp=10,
        source="paper",
        runtime_mode="paper",
    ))

    live = repo.upsert_position(PositionRecord(
        id=paper.id,
        strategy="__exchange__",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=2.0,
        entry_price=50000.0,
        leverage=2,
        timestamp=20,
        source="live_exchange",
        runtime_mode="live",
    ))

    rows = repo.get_positions("__exchange__", include_inactive=True)
    assert live.id != paper.id
    assert [(row.id, row.source, row.runtime_mode, row.amount) for row in rows] == [
        (paper.id, "paper", "paper", 1.0),
        (live.id, "live_exchange", "live", 2.0),
    ]


def test_upsert_position_same_domain_id_merges_existing_row(repo: Repository):
    original = repo.save_position(PositionRecord(
        strategy="__exchange__",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=40000.0,
        leverage=1,
        timestamp=10,
        source="live_exchange",
        runtime_mode="live",
    ))

    merged = repo.upsert_position(PositionRecord(
        id=original.id,
        strategy="__exchange__",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=2.0,
        entry_price=50000.0,
        leverage=2,
        timestamp=20,
        source="live_exchange",
        runtime_mode="live",
    ))

    assert merged.id == original.id
    rows = repo.get_positions("__exchange__", include_inactive=True)
    assert len(rows) == 1
    assert rows[0].amount == 2.0


def test_position_reads_default_to_active_rows_and_allow_explicit_history(repo: Repository):
    active = repo.save_position(PositionRecord(
        strategy="history",
        symbol="BTC-USDT",
        side="long",
        amount=2.0,
        entry_price=100.0,
        leverage=1,
        timestamp=20,
        source="live_exchange",
        runtime_mode="live",
        active=True,
    ))
    inactive = repo.save_position(PositionRecord(
        strategy="history",
        symbol="BTC-USDT",
        side="long",
        amount=1.0,
        entry_price=90.0,
        leverage=1,
        timestamp=10,
        source="live_exchange",
        runtime_mode="live",
        active=False,
        stale_at=20,
    ))

    assert repo.get_position("history", "BTC-USDT", "long").id == active.id
    assert [row.id for row in repo.get_positions("history")] == [active.id]
    assert repo.get_position(
        "history", "BTC-USDT", "long", include_inactive=True
    ).id == active.id
    assert {row.id for row in repo.get_positions("history", include_inactive=True)} == {
        active.id,
        inactive.id,
    }


def _order_for_provenance(
    *, order_id: str, source: str, runtime_mode: str, status: str = "pending",
    strategy: str = "same-strategy",
):
    return OrderRecord(
        order_id=order_id,
        exchange_order_id="shared-exchange-id",
        client_order_id="shared-client-id",
        strategy=strategy,
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=1.0,
        price=100.0,
        status=status,
        fill_price=0.0,
        timestamp=1,
        updated_at=1,
        source=source,
        runtime_mode=runtime_mode,
    )


@pytest.mark.parametrize(
    ("source", "runtime_mode"), [("paper", "paper"), ("backtest", "backtest")]
)
def test_live_order_upsert_fails_closed_on_non_live_order_with_same_external_ids(
    repo: Repository, caplog, source, runtime_mode
):
    order_id = f"{source}-order"
    repo.upsert_order(
        _order_for_provenance(order_id=order_id, source=source, runtime_mode=runtime_mode)
    )

    with caplog.at_level("WARNING"), pytest.raises(ValueError, match="provenance"):
        repo.upsert_order(
            _order_for_provenance(
                order_id=order_id, source="live_exchange", runtime_mode="live", status="filled"
            )
        )

    persisted = repo.get_orders(include_inactive=True)
    assert len(persisted) == 1
    assert (persisted[0].order_id, persisted[0].source, persisted[0].runtime_mode) == (
        order_id, source, runtime_mode
    )
    assert persisted[0].status == "pending"
    assert "provenance" in caplog.text.lower()


def test_atomic_order_upsert_fails_closed_and_rolls_back_cross_provenance_order(repo):
    repo.upsert_order(_order_for_provenance(
        order_id="paper-order", source="paper", runtime_mode="paper"
    ))
    incoming = _order_for_provenance(
        order_id="paper-order", source="live_exchange", runtime_mode="live", status="filled"
    )

    with pytest.raises(ValueError, match="provenance"):
        repo.atomic_accounting(lambda tx: tx.upsert_order(incoming))

    persisted = repo.get_orders(include_inactive=True)
    assert len(persisted) == 1
    assert (persisted[0].source, persisted[0].runtime_mode, persisted[0].status) == (
        "paper", "paper", "pending"
    )


def test_unknown_legacy_order_is_not_upgraded_by_live_order_upsert(repo: Repository, caplog):
    repo.upsert_order(
        OrderRecord(
            order_id="legacy-order",
            exchange_order_id="shared-exchange-id",
            client_order_id="shared-client-id",
            strategy="legacy-strategy",
            symbol="BTC-USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=100.0,
            status="pending",
            fill_price=0.0,
            timestamp=1,
            updated_at=1,
        )
    )

    with caplog.at_level("WARNING"), pytest.raises(ValueError, match="provenance"):
        repo.upsert_order(
            _order_for_provenance(
                order_id="legacy-order",
                source="live_exchange",
                runtime_mode="live",
                status="filled",
            )
        )

    persisted = repo.get_orders(include_inactive=True)
    assert len(persisted) == 1
    assert (persisted[0].source, persisted[0].runtime_mode, persisted[0].strategy) == (
        "unknown", "unknown", "legacy-strategy"
    )
    assert persisted[0].status == "pending"
    assert "provenance" in caplog.text.lower()


@pytest.mark.parametrize(
    ("source", "runtime_mode"),
    [("paper", "paper"), ("backtest", "backtest"), ("unknown", "unknown")],
)
def test_atomic_live_order_upsert_rejects_every_foreign_provenance(repo, source, runtime_mode):
    repo.upsert_order(_order_for_provenance(
        order_id=f"{source}-order", source=source, runtime_mode=runtime_mode
    ))
    incoming = _order_for_provenance(
        order_id=f"{source}-order", source="live_exchange", runtime_mode="live"
    )

    with pytest.raises(ValueError, match="provenance"):
        repo.atomic_accounting(lambda tx: tx.upsert_order(incoming))

    assert [
        (order.source, order.runtime_mode)
        for order in repo.get_orders(include_inactive=True)
    ] == [(source, runtime_mode)]


def test_live_orders_with_same_provenance_merge_by_external_identity(repo: Repository):
    first = repo.upsert_order(
        _order_for_provenance(order_id="live-order", source="live_exchange", runtime_mode="live")
    )
    merged = repo.upsert_order(
        _order_for_provenance(
            order_id="live-order",
            source="live_exchange",
            runtime_mode="live",
            status="filled",
        )
    )

    assert merged.id == first.id
    persisted = repo.get_orders(source="live_exchange", runtime_mode="live")
    assert len(persisted) == 1
    assert persisted[0].status == "filled"


def test_paper_orders_with_same_provenance_merge_by_external_identity(repo: Repository):
    first = repo.upsert_order(
        _order_for_provenance(order_id="paper-order", source="paper", runtime_mode="paper")
    )
    merged = repo.upsert_order(
        _order_for_provenance(
            order_id="paper-order",
            source="paper",
            runtime_mode="paper",
            status="filled",
        )
    )

    assert merged.id == first.id
    persisted = repo.get_orders(source="paper", runtime_mode="paper")
    assert len(persisted) == 1
    assert persisted[0].status == "filled"


def test_upsert_trade_preserves_active_legacy_row_when_live_reuses_trade_id(repo: Repository):
    legacy = repo.upsert_trade(TradeRecord(
        exchange_trade_id="shared-trade",
        exchange_order_id="legacy-order",
        order_id="legacy-order",
        strategy="legacy-strategy",
        symbol="BTC-USDT",
        side="buy",
        amount=1.0,
        price=100.0,
        fee=0.1,
        timestamp=10,
    ))

    live = repo.upsert_trade(TradeRecord(
        exchange_trade_id="shared-trade",
        exchange_order_id="live-order",
        order_id="live-order",
        strategy="__exchange__",
        symbol="BTC-USDT",
        side="buy",
        amount=1.0,
        price=100.0,
        fee=0.1,
        timestamp=10,
        source="live_exchange",
        runtime_mode="live",
    ))

    rows = repo.get_trades(include_inactive=True)
    assert legacy.active is True
    assert live.id != legacy.id
    assert [(row.source, row.runtime_mode, row.active) for row in rows] == [
        ("unknown", "unknown", True),
        ("live_exchange", "live", True),
    ]
    assert [row.id for row in repo.get_trades(source="live_exchange", runtime_mode="live")] == [
        live.id
    ]


def test_atomic_private_sync_reads_rows_written_in_same_transaction(repo: Repository):
    order = OrderRecord(
        order_id="atomic-read-order",
        strategy="__exchange__",
        symbol="BTC-USDT",
        side="buy",
        type="market",
        amount=1.0,
        price=100.0,
        status="filled",
        fill_price=100.0,
        timestamp=1,
        source="live_exchange",
        runtime_mode="live",
    )
    trade = TradeRecord(
        exchange_trade_id="atomic-read-trade",
        order_id=order.order_id,
        strategy=order.strategy,
        symbol=order.symbol,
        side=order.side,
        amount=1.0,
        price=100.0,
        fee=0.0,
        timestamp=1,
        source="live_exchange",
        runtime_mode="live",
    )

    seen = repo.atomic_private_sync(lambda tx: (
        tx.upsert_order(order),
        tx.upsert_trade(trade),
        tx.get_orders(source="live_exchange", runtime_mode="live"),
        tx.get_trades(source="live_exchange", runtime_mode="live"),
    ))

    assert [row.order_id for row in seen[2]] == [order.order_id]
    assert [row.exchange_trade_id for row in seen[3]] == [trade.exchange_trade_id]


def test_upsert_trade_does_not_merge_same_facts_across_provenance_pairs(repo: Repository):
    paper = repo.upsert_trade(TradeRecord(
        exchange_trade_id="shared-trade",
        exchange_order_id="shared-order",
        order_id="paper-order",
        strategy="paper-strategy",
        symbol="BTC-USDT",
        side="buy",
        amount=1.0,
        price=100.0,
        fee=0.1,
        timestamp=10,
        source="paper",
        runtime_mode="paper",
    ))

    live = repo.upsert_trade(TradeRecord(
        exchange_trade_id="shared-trade",
        exchange_order_id="shared-order",
        order_id="live-order",
        strategy="__exchange__",
        symbol="BTC-USDT",
        side="buy",
        amount=1.0,
        price=100.0,
        fee=0.1,
        timestamp=10,
        source="live_exchange",
        runtime_mode="live",
    ))

    assert live.id != paper.id
    assert (paper.strategy, paper.source, paper.runtime_mode) == (
        "paper-strategy", "paper", "paper"
    )
    assert (live.strategy, live.source, live.runtime_mode) == (
        "__exchange__", "live_exchange", "live"
    )


def test_upsert_and_list_strategy_configs(repo: Repository):
    repo.upsert_strategy_config(
        StrategyConfigRecord(
            name="ma_cross_btc",
            strategy_type="ma_cross",
            symbol="BTC-USDT",
            timeframe="1h",
            params={"fast_window": 5, "slow_window": 20},
            enabled=True,
            created_at=1700000000000,
            updated_at=1700000000000,
        )
    )
    repo.upsert_strategy_config(
        StrategyConfigRecord(
            name="ma_cross_eth",
            strategy_type="ma_cross",
            symbol="ETH-USDT",
            timeframe="4h",
            params={"fast_window": 10, "slow_window": 30},
            enabled=False,
            created_at=1700000001000,
            updated_at=1700000001000,
        )
    )
    repo.upsert_strategy_config(
        StrategyConfigRecord(
            name="ma_cross_btc",
            strategy_type="ma_cross",
            symbol="BTC-USDT-SWAP",
            timeframe="15m",
            params={"fast_window": 7, "slow_window": 21},
            enabled=True,
            created_at=1,
            updated_at=1700000002000,
        )
    )

    configs = repo.get_strategy_configs()
    config = repo.get_strategy_config("ma_cross_btc")

    assert [item.name for item in configs] == ["ma_cross_eth", "ma_cross_btc"]
    assert config is not None
    assert config.symbol == "BTC-USDT-SWAP"
    assert config.timeframe == "15m"
    assert config.params == {"fast_window": 7, "slow_window": 21}
    assert config.created_at == 1700000000000
    assert config.updated_at == 1700000002000


def test_kline_cache(repo: Repository):
    kline = KlineCache(
        symbol="BTC-USDT",
        timeframe="1h",
        timestamp=1700000000000,
        open=50000.0,
        high=51000.0,
        low=49000.0,
        close=50500.0,
        volume=100.0,
    )
    repo.save_kline(kline)
    klines = repo.get_klines("BTC-USDT", "1h", 1700000000000, 1700003600000)
    assert len(klines) == 1


def test_save_and_list_backtest_results_newest_first(repo: Repository):
    repo.save_backtest_result(
        BacktestResultRecord(
            id="bt-old",
            strategy="ma_cross",
            symbol="BTC-USDT",
            timeframe="1h",
            start_time=1700000000000,
            end_time=1700003600000,
            initial_capital=100000.0,
            total_return=0.01,
            sharpe_ratio=1.2,
            max_drawdown=0.03,
            win_rate=0.5,
            total_trades=2,
            created_at=1700003600000,
        )
    )
    repo.save_backtest_result(
        BacktestResultRecord(
            id="bt-new",
            strategy="ma_cross",
            symbol="ETH-USDT",
            timeframe="1h",
            start_time=1700007200000,
            end_time=1700010800000,
            initial_capital=200000.0,
            total_return=-0.02,
            sharpe_ratio=-0.5,
            max_drawdown=0.04,
            win_rate=0.25,
            total_trades=4,
            created_at=1700010800000,
        )
    )

    results = repo.get_backtest_results()

    assert [result.id for result in results] == ["bt-new", "bt-old"]
    assert results[0].symbol == "ETH-USDT"
    assert results[0].total_trades == 4


def test_list_backtest_results_honors_limit(repo: Repository):
    for index in range(3):
        repo.save_backtest_result(
            BacktestResultRecord(
                id=f"bt-{index}",
                strategy="ma_cross",
                symbol="BTC-USDT",
                timeframe="1h",
                start_time=1700000000000 + index,
                end_time=1700003600000 + index,
                initial_capital=100000.0,
                total_return=0.01,
                sharpe_ratio=1.0,
                max_drawdown=0.02,
                win_rate=0.5,
                total_trades=1,
                created_at=1700000000000 + index,
            )
        )

    assert [result.id for result in repo.get_backtest_results(limit=2)] == ["bt-2", "bt-1"]


def test_get_backtest_result_returns_matching_result(repo: Repository):
    repo.save_backtest_result(
        BacktestResultRecord(
            id="bt-detail",
            strategy="ma_cross",
            symbol="BTC-USDT",
            timeframe="1h",
            start_time=1700000000000,
            end_time=1700003600000,
            initial_capital=100000.0,
            total_return=0.01,
            sharpe_ratio=1.0,
            max_drawdown=0.02,
            win_rate=0.5,
            total_trades=1,
            created_at=1700003600000,
        )
    )

    result = repo.get_backtest_result("bt-detail")

    assert result is not None
    assert result.id == "bt-detail"
    assert result.symbol == "BTC-USDT"
    assert repo.get_backtest_result("missing") is None


def test_save_backtest_result_with_trades_persists_result_and_trades(repo: Repository):
    result = BacktestResultRecord(
        id="bt-combined",
        strategy="ma_cross",
        symbol="BTC-USDT",
        timeframe="1h",
        start_time=1700000000000,
        end_time=1700007200000,
        initial_capital=100000.0,
        total_return=0.015,
        sharpe_ratio=1.3,
        max_drawdown=0.02,
        win_rate=0.5,
        total_trades=2,
        created_at=1700007200000,
    )
    trades = [
        BacktestTradeRecord(
            result_id="bt-combined",
            symbol="BTC-USDT",
            side="buy",
            timestamp=1700003600000,
            price=100.0,
            amount=0.1,
            fee=0.01,
            pnl=-10.01,
        ),
        BacktestTradeRecord(
            result_id="bt-combined",
            symbol="BTC-USDT",
            side="sell",
            timestamp=1700007200000,
            price=110.0,
            amount=0.1,
            fee=0.011,
            pnl=10.989,
        ),
    ]

    repo.save_backtest_result_with_trades(result, trades)

    persisted_result = repo.get_backtest_result("bt-combined")
    persisted_trades = repo.get_backtest_trades("bt-combined")
    assert persisted_result is not None
    assert persisted_result.id == "bt-combined"
    assert persisted_result.total_trades == 2
    assert [trade.side for trade in persisted_trades] == ["buy", "sell"]
    assert [trade.timestamp for trade in persisted_trades] == [1700003600000, 1700007200000]
    assert persisted_trades[1].price == 110.0
    assert persisted_trades[1].amount == 0.1
    assert persisted_trades[1].fee == 0.011
    assert persisted_trades[1].pnl == 10.989


def test_save_and_get_backtest_trades_filters_and_orders_by_timestamp_then_id(repo: Repository):
    saved = repo.save_backtest_trades(
        [
            BacktestTradeRecord(
                result_id="bt-target",
                symbol="BTC-USDT",
                side="sell",
                timestamp=1700003600000,
                price=101.0,
                amount=0.3,
                fee=0.03,
                pnl=1.5,
            ),
            BacktestTradeRecord(
                result_id="bt-other",
                symbol="ETH-USDT",
                side="buy",
                timestamp=1700000000000,
                price=2000.0,
                amount=1.0,
                fee=2.0,
                pnl=-2002.0,
            ),
            BacktestTradeRecord(
                result_id="bt-target",
                symbol="BTC-USDT",
                side="buy",
                timestamp=1700000000000,
                price=100.0,
                amount=0.1,
                fee=0.01,
                pnl=-10.01,
            ),
            BacktestTradeRecord(
                result_id="bt-target",
                symbol="BTC-USDT",
                side="buy",
                timestamp=1700000000000,
                price=100.5,
                amount=0.2,
                fee=0.02,
                pnl=-20.12,
            ),
        ]
    )

    trades = repo.get_backtest_trades("bt-target")

    assert all(trade.id is not None for trade in saved)
    assert [(trade.timestamp, trade.id) for trade in trades] == sorted(
        (trade.timestamp, trade.id) for trade in trades
    )
    assert [trade.result_id for trade in trades] == ["bt-target", "bt-target", "bt-target"]
    assert [trade.price for trade in trades] == [100.0, 100.5, 101.0]


def test_get_trades_filters(repo: Repository):
    for i in range(5):
        repo.save_trade(
            TradeRecord(
                strategy="strat_a" if i < 3 else "strat_b",
                symbol="BTC-USDT",
                side="buy",
                amount=0.1,
                price=50000.0 + i * 100,
                fee=2.5,
                timestamp=1700000000000 + i * 1000,
            )
        )
    assert len(repo.get_trades(strategy="strat_a")) == 3
    assert len(repo.get_trades(strategy="strat_b")) == 2
    assert len(repo.get_trades()) == 5


def test_account_read_assets_cannot_mutate_persisted_shape(repo: Repository):
    repo.upsert_account(
        AccountRecord(
            strategy="immutable-assets",
            initial_equity=1.0,
            cash_balance=1.0,
            equity=1.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            daily_pnl=0.0,
            fees_paid=0.0,
            updated_at=1,
            assets=[{"ccy": "USDT", "avail_bal": 1.0}],
        )
    )
    loaded = repo.get_account("immutable-assets")

    assert loaded is not None
    with pytest.raises(TypeError):
        loaded.assets[0]["avail_bal"] = 99.0
    with pytest.raises(TypeError):
        loaded.assets.append({"ccy": "BTC"})

    reread = repo.get_account("immutable-assets")
    assert reread is not None
    assert reread.assets == [{"ccy": "USDT", "avail_bal": 1.0}]


def test_account_assets_deep_immutable_copy_round_trips_as_json(repo: Repository):
    repo.upsert_account(
        AccountRecord(
            strategy="nested-serialization",
            initial_equity=1.0,
            cash_balance=1.0,
            equity=1.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            daily_pnl=0.0,
            fees_paid=0.0,
            updated_at=1,
            assets=[{
                "ccy": "USDT",
                "metadata": {"levels": [{"name": "cash"}]},
                "tags": {"primary"},
            }],
        )
    )

    loaded = repo.get_account("nested-serialization")

    assert loaded is not None
    assert json.loads(loaded.model_dump_json())["assets"] == [{
        "ccy": "USDT",
        "metadata": {"levels": [{"name": "cash"}]},
        "tags": ["primary"],
    }]


def test_upsert_and_get_account(repo: Repository):
    repo.upsert_account(
        AccountRecord(
            strategy="ma_cross",
            initial_equity=100000.0,
            cash_balance=95000.0,
            available_balance=94000.0,
            equity=100500.0,
            assets=[{"ccy": "USDT", "avail_bal": 94000.0}],
            realized_pnl=500.0,
            unrealized_pnl=0.0,
            daily_pnl=500.0,
            fees_paid=2.0,
            updated_at=1700000000000,
        )
    )
    repo.upsert_account(
        AccountRecord(
            strategy="ma_cross",
            initial_equity=100000.0,
            cash_balance=96000.0,
            available_balance=95000.0,
            equity=101000.0,
            assets=[{"ccy": "BTC", "avail_bal": 1.0}],
            realized_pnl=1000.0,
            unrealized_pnl=0.0,
            daily_pnl=1000.0,
            fees_paid=3.0,
            updated_at=1700000001000,
        )
    )

    account = repo.get_account("ma_cross")

    assert account is not None
    assert account.cash_balance == 96000.0
    assert account.available_balance == 95000.0
    assert account.realized_pnl == 1000.0
    assert account.assets == [{"ccy": "BTC", "avail_bal": 1.0}]


def test_get_account_aggregates_all_strategies(repo: Repository):
    repo.upsert_account(
        AccountRecord(
            strategy="a",
            initial_equity=100.0,
            cash_balance=90.0,
            available_balance=80.0,
            equity=110.0,
            realized_pnl=10.0,
            unrealized_pnl=1.0,
            daily_pnl=5.0,
            fees_paid=0.5,
            updated_at=1,
        )
    )
    repo.upsert_account(
        AccountRecord(
            strategy="b",
            initial_equity=200.0,
            cash_balance=180.0,
            available_balance=170.0,
            equity=210.0,
            realized_pnl=20.0,
            unrealized_pnl=2.0,
            daily_pnl=6.0,
            fees_paid=0.7,
            updated_at=2,
        )
    )

    account = repo.get_account()

    assert account is not None
    assert account.initial_equity == 300.0
    assert account.cash_balance == 270.0
    assert account.available_balance == 250.0
    assert account.assets == []
    assert account.updated_at == 2


def test_get_account_without_strategy_prefers_exchange_account(repo: Repository):
    repo.upsert_account(
        AccountRecord(
            strategy="__exchange__",
            initial_equity=1000.0,
            cash_balance=900.0,
            available_balance=850.0,
            equity=1010.0,
            realized_pnl=10.0,
            unrealized_pnl=20.0,
            daily_pnl=5.0,
            fees_paid=1.0,
            updated_at=3,
            assets=[{"ccy": "USDT", "avail_bal": 850.0}],
        )
    )
    repo.upsert_account(
        AccountRecord(
            strategy="ma_cross",
            initial_equity=100.0,
            cash_balance=90.0,
            available_balance=80.0,
            equity=110.0,
            realized_pnl=1.0,
            unrealized_pnl=2.0,
            daily_pnl=3.0,
            fees_paid=0.1,
            updated_at=4,
        )
    )

    account = repo.get_account()

    assert account is not None
    assert account.strategy == "__exchange__"
    assert account.cash_balance == 900.0
    assert account.available_balance == 850.0
    assert account.assets == [{"ccy": "USDT", "avail_bal": 850.0}]


def test_cash_ledger_filters_by_strategy(repo: Repository):
    repo.save_cash_ledger(
        CashLedgerRecord(
            strategy="a",
            symbol="BTC-USDT",
            order_id="1",
            event_type="fill",
            amount=-100.0,
            balance_after=99900.0,
            timestamp=2,
        )
    )
    repo.save_cash_ledger(
        CashLedgerRecord(
            strategy="b",
            symbol="ETH-USDT",
            order_id="2",
            event_type="fill",
            amount=-50.0,
            balance_after=99950.0,
            timestamp=1,
        )
    )

    assert [entry.order_id for entry in repo.get_cash_ledger()] == ["2", "1"]
    assert [entry.order_id for entry in repo.get_cash_ledger("a")] == ["1"]


def test_repository_exposes_source_and_runtime_filters_for_live_queries(repo: Repository):
    repo.save_trade(
        TradeRecord(
            exchange_trade_id="paper-id",
            strategy="paper",
            symbol="BTC-USDT",
            side="buy",
            amount=1.0,
            price=100.0,
            fee=0.0,
            timestamp=1,
            source="paper",
            runtime_mode="paper",
        )
    )
    repo.save_trade(
        TradeRecord(
            exchange_trade_id="live-id",
            strategy="__exchange__",
            symbol="BTC-USDT",
            side="buy",
            amount=1.0,
            price=100.0,
            fee=0.0,
            timestamp=2,
            source="live_exchange",
            runtime_mode="live",
        )
    )

    live_trades = repo.get_trades(source="live_exchange", runtime_mode="live")
    assert [trade.exchange_trade_id for trade in live_trades] == ["live-id"]


def test_open_positions_filters_archived_positions_but_get_positions_retains_them(repo: Repository):
    repo.upsert_position(
        PositionRecord(
            strategy="__exchange__",
            symbol="BTC/USDT:USDT",
            side="long",
            amount=1.0,
            entry_price=50000.0,
            leverage=1,
            timestamp=10,
            source="live_exchange",
            runtime_mode="live",
            active=False,
            stale_at=20,
        )
    )

    assert len(repo.get_positions("__exchange__", include_inactive=True)) == 1
    assert repo.get_open_positions("__exchange__") == []


def test_open_position_audit_can_explicitly_include_inactive_rows(repo: Repository):
    repo.upsert_position(
        PositionRecord(
            strategy="__exchange__",
            symbol="BTC-USDT-SWAP",
            side="long",
            amount=1.0,
            entry_price=50000.0,
            leverage=1,
            timestamp=10,
            source="live_exchange",
            runtime_mode="live",
            active=False,
            stale_at=20,
        )
    )

    current = repo.get_open_positions("__exchange__")
    audit = repo.get_open_positions("__exchange__", include_inactive=True)

    assert current == []
    assert [(position.active, position.stale_at) for position in audit] == [(False, 20)]


def test_upsert_position_and_open_positions_filter_flat_rows(repo: Repository):
    repo.upsert_position(
        PositionRecord(
            strategy="ma_cross",
            symbol="BTC-USDT",
            side="long",
            amount=0.1,
            entry_price=50000.0,
            leverage=1,
            timestamp=1700000000000,
        )
    )
    repo.upsert_position(
        PositionRecord(
            strategy="ma_cross",
            symbol="ETH-USDT",
            side="long",
            amount=0.0,
            entry_price=0.0,
            leverage=1,
            timestamp=1700000001000,
        )
    )
    repo.upsert_position(
        PositionRecord(
            strategy="ma_cross",
            symbol="BTC-USDT",
            side="long",
            amount=0.2,
            entry_price=51000.0,
            leverage=1,
            timestamp=1700000002000,
            mark_price=52000.0,
            realized_pnl=10.0,
            unrealized_pnl=20.0,
        )
    )

    position = repo.get_position("ma_cross", "BTC-USDT")
    open_positions = repo.get_open_positions("ma_cross")

    assert position is not None
    assert position.amount == 0.2
    assert position.mark_price == 52000.0
    assert [(pos.symbol, pos.amount) for pos in open_positions] == [("BTC-USDT", 0.2)]


def test_kill_switch_defaults_to_disengaged(repo: Repository):
    state = repo.get_kill_switch()

    assert state.engaged is False
    assert state.reason == ""
    assert state.updated_at == 0


def test_set_kill_switch_persists_latest_state(repo: Repository):
    repo.set_kill_switch(True, "operator emergency stop", 1700000000000)
    repo.set_kill_switch(False, "demo reset", 1700000005000)

    state = repo.get_kill_switch()

    assert state.engaged is False
    assert state.reason == "demo reset"
    assert state.updated_at == 1700000005000


def test_save_risk_event_persists_payload_fields(repo: Repository):
    saved = repo.save_risk_event(
        {
            "type": "risk_event",
            "strategy": "ma_cross",
            "reason_code": "kill_switch_engaged",
            "reason": "Kill switch engaged",
            "symbol": "BTC/USDT",
            "timestamp": 1700000000000,
        }
    )

    events = repo.get_risk_events()

    assert len(events) == 1
    assert events[0].id == saved.id
    assert events[0].event_type == "risk_event"
    assert events[0].strategy == "ma_cross"
    assert events[0].reason_code == "kill_switch_engaged"
    assert events[0].payload["symbol"] == "BTC/USDT"


def test_upsert_order_merges_fill_and_snapshot_baseline_monotonically(repo: Repository):
    repo.upsert_order(
        OrderRecord(
            order_id="ord-monotonic",
            exchange_order_id="okx-monotonic",
            client_order_id="client-monotonic",
            strategy="ma_cross",
            symbol="BTC/USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=50000.0,
            status="filled",
            fill_price=50000.0,
            timestamp=10,
            updated_at=10,
            filled_amount=1.0,
            remaining_amount=0.0,
            snapshot_filled_amount=0.8,
        )
    )

    persisted = repo.upsert_order(
        OrderRecord(
            order_id="ord-monotonic",
            exchange_order_id="okx-monotonic",
            client_order_id="client-monotonic",
            strategy="ma_cross",
            symbol="BTC/USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=50000.0,
            status="pending",
            fill_price=0.0,
            timestamp=1,
            updated_at=1,
            filled_amount=0.2,
            remaining_amount=0.8,
            snapshot_filled_amount=0.2,
        )
    )

    assert persisted.status == "filled"
    assert persisted.filled_amount == pytest.approx(1.0)
    assert persisted.remaining_amount == pytest.approx(0.0)
    assert persisted.snapshot_filled_amount == pytest.approx(0.8)


def test_upsert_order_rejects_stale_payload_without_regressing_order_state(repo: Repository):
    current = repo.upsert_order(
        OrderRecord(
            order_id="ord-stale-state",
            exchange_order_id="okx-stale-state",
            strategy="ma_cross",
            symbol="BTC/USDT",
            side="buy",
            type="limit",
            amount=1.2,
            price=50000.0,
            status="partially_filled",
            fill_price=50100.0,
            timestamp=20,
            updated_at=20,
            filled_amount=0.8,
            remaining_amount=0.4,
            snapshot_filled_amount=0.8,
        )
    )

    stale = repo.upsert_order(
        OrderRecord(
            order_id="ord-stale-state",
            exchange_order_id="okx-stale-state",
            strategy="old-strategy",
            symbol="ETH/USDT",
            side="sell",
            type="market",
            amount=0.5,
            price=1.0,
            status="pending",
            fill_price=0.0,
            timestamp=10,
            updated_at=19,
            filled_amount=0.1,
            remaining_amount=0.4,
            snapshot_filled_amount=0.1,
        )
    )

    assert stale.id == current.id
    assert stale.amount == pytest.approx(1.2)
    assert stale.filled_amount == pytest.approx(0.8)
    assert stale.remaining_amount == pytest.approx(0.4)
    assert stale.snapshot_filled_amount == pytest.approx(0.8)
    assert (stale.status, stale.timestamp, stale.updated_at) == (
        "partially_filled",
        20,
        20,
    )


def test_upsert_order_newer_snapshot_replaces_filled_remaining_atomically(repo: Repository):
    repo.upsert_order(
        OrderRecord(
            order_id="ord-snapshot-regression",
            exchange_order_id="okx-snapshot-regression",
            strategy="ma_cross",
            symbol="BTC/USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=50000.0,
            status="partially_filled",
            fill_price=50000.0,
            timestamp=10,
            updated_at=10,
            filled_amount=0.8,
            remaining_amount=0.2,
            snapshot_filled_amount=0.8,
            snapshot_updated_at=10,
        )
    )

    persisted = repo.upsert_order(
        OrderRecord(
            order_id="ord-snapshot-regression",
            exchange_order_id="okx-snapshot-regression",
            strategy="ma_cross",
            symbol="BTC/USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=50000.0,
            status="partially_filled",
            fill_price=50000.0,
            timestamp=11,
            updated_at=11,
            filled_amount=0.6,
            remaining_amount=0.4,
            snapshot_filled_amount=0.6,
            snapshot_updated_at=11,
        )
    )

    assert persisted.filled_amount == pytest.approx(0.6)
    assert persisted.remaining_amount == pytest.approx(0.4)
    assert persisted.snapshot_filled_amount == pytest.approx(0.6)


def test_upsert_order_allows_new_fill_progress_without_status_downgrade(repo: Repository):
    repo.upsert_order(
        OrderRecord(
            order_id="ord-progress",
            exchange_order_id="okx-progress",
            client_order_id="client-progress",
            strategy="ma_cross",
            symbol="BTC/USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=50000.0,
            status="partially_filled",
            fill_price=50000.0,
            timestamp=10,
            updated_at=10,
            filled_amount=0.4,
            snapshot_filled_amount=0.4,
        )
    )

    persisted = repo.upsert_order(
        OrderRecord(
            order_id="ord-progress",
            exchange_order_id="okx-progress",
            client_order_id="client-progress",
            strategy="ma_cross",
            symbol="BTC/USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=50000.0,
            status="partially_filled",
            fill_price=50100.0,
            timestamp=11,
            updated_at=11,
            filled_amount=0.7,
            snapshot_filled_amount=0.7,
        )
    )

    assert persisted.status == "partially_filled"
    assert persisted.filled_amount == pytest.approx(0.7)
    assert persisted.remaining_amount == pytest.approx(0.3)
    assert persisted.snapshot_filled_amount == pytest.approx(0.7)


def test_upsert_order_preserves_cancelled_terminal_status_against_stale_snapshot(repo: Repository):
    repo.upsert_order(
        OrderRecord(
            order_id="ord-cancelled",
            exchange_order_id="okx-cancelled",
            client_order_id="client-cancelled",
            strategy="ma_cross",
            symbol="BTC/USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=50000.0,
            status="cancelled",
            fill_price=0.0,
            timestamp=10,
            updated_at=10,
            filled_amount=0.0,
            remaining_amount=1.0,
            snapshot_filled_amount=0.0,
        )
    )

    persisted = repo.upsert_order(
        OrderRecord(
            order_id="ord-cancelled",
            exchange_order_id="okx-cancelled",
            client_order_id="client-cancelled",
            strategy="ma_cross",
            symbol="BTC/USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=50000.0,
            status="pending",
            fill_price=0.0,
            timestamp=1,
            updated_at=1,
            filled_amount=0.0,
            remaining_amount=1.0,
            snapshot_filled_amount=0.0,
        )
    )

    assert persisted.status == "cancelled"


@pytest.mark.parametrize(
    ("terminal_status", "filled_amount", "remaining_amount"),
    [("filled", 1.0, 0.0), ("cancelled", 0.6, 0.4), ("rejected", 0.6, 0.4)],
)
def test_upsert_order_does_not_downgrade_terminal_order_from_newer_open_snapshot(
    repo: Repository, terminal_status, filled_amount, remaining_amount
):
    repo.upsert_order(
        OrderRecord(
            order_id=f"terminal-{terminal_status}",
            exchange_order_id=f"okx-terminal-{terminal_status}",
            strategy="ma_cross",
            symbol="BTC/USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=50000.0,
            status=terminal_status,
            fill_price=123.0,
            timestamp=100,
            updated_at=100,
            filled_amount=filled_amount,
            remaining_amount=remaining_amount,
            snapshot_filled_amount=filled_amount,
            snapshot_updated_at=100,
        )
    )

    persisted = repo.upsert_order(
        OrderRecord(
            order_id=f"terminal-{terminal_status}",
            exchange_order_id=f"okx-terminal-{terminal_status}",
            strategy="ma_cross",
            symbol="BTC/USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=50000.0,
            status="pending",
            fill_price=0.0,
            timestamp=200,
            updated_at=200,
            filled_amount=0.0,
            remaining_amount=1.0,
            snapshot_filled_amount=0.0,
            snapshot_updated_at=200,
        )
    )

    assert persisted.status == terminal_status
    assert persisted.amount == pytest.approx(1.0)
    assert persisted.filled_amount == pytest.approx(filled_amount)
    assert persisted.remaining_amount == pytest.approx(remaining_amount)
    assert persisted.fill_price == pytest.approx(123.0)
    assert persisted.snapshot_updated_at == 200


def test_persist_order_and_accounting_keeps_stale_paper_fill_monotonic_and_accounts_only_progress(
    repo,
):
    service = PaperAccountingService(repo, initial_equity=1000.0)

    def persist(filled_amount: float, updated_at: int) -> None:
        order = Order(
            id="atomic-stale-fill",
            symbol="BTC-USDT",
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            amount=1.0,
            price=100.0,
            status=OrderStatus.PARTIALLY_FILLED,
            fill_price=100.0,
            filled_amount=filled_amount,
            remaining_amount=1.0 - filled_amount,
        )
        record = OrderRecord(
            order_id=order.id,
            strategy="ma_cross",
            symbol=order.symbol,
            side=order.side.value,
            type=order.type.value,
            amount=order.amount,
            price=order.price,
            status=order.status.value,
            fill_price=order.fill_price,
            timestamp=updated_at,
            updated_at=updated_at,
            filled_amount=filled_amount,
            remaining_amount=1.0 - filled_amount,
            snapshot_filled_amount=filled_amount,
        )
        repo.persist_order_and_accounting(
            record,
            lambda transaction: service.process_filled_order(
                order, "ma_cross", updated_at, transaction=transaction
            ),
        )

    persist(0.8, 20)
    persist(0.4, 10)
    persisted = repo.get_orders(order_id="atomic-stale-fill")[0]
    assert (persisted.filled_amount, persisted.remaining_amount) == pytest.approx((0.8, 0.2))
    assert persisted.snapshot_filled_amount == pytest.approx(0.8)
    trades = repo.get_trades(order_id=persisted.order_id)
    assert [trade.amount for trade in trades] == pytest.approx([0.8])

    persist(0.9, 21)
    trades = repo.get_trades(order_id=persisted.order_id)
    assert [trade.amount for trade in trades] == pytest.approx([0.8, 0.1])


def test_upsert_order_updates_snapshot_filled_amount(repo: Repository):
    repo.upsert_order(
        OrderRecord(
            order_id="ord-1",
            exchange_order_id="okx-1",
            client_order_id="client-1",
            strategy="ma_cross",
            symbol="BTC/USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=50000.0,
            status="partially_filled",
            fill_price=50000.0,
            timestamp=1,
            updated_at=1,
            filled_amount=0.4,
            snapshot_filled_amount=0.4,
        )
    )
    repo.upsert_order(
        OrderRecord(
            order_id="ord-1",
            exchange_order_id="okx-1",
            client_order_id="client-1",
            strategy="ma_cross",
            symbol="BTC/USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=50000.0,
            status="partially_filled",
            fill_price=50000.0,
            timestamp=1,
            updated_at=2,
            filled_amount=0.4,
            snapshot_filled_amount=0.4,
        )
    )

    persisted = repo.get_orders()[0]

    assert persisted.snapshot_filled_amount == 0.4


def test_upsert_order_rejects_conflicting_external_identity_without_mutating_existing_order(
    repo: Repository, caplog
):
    repo.upsert_order(
        OrderRecord(
            order_id="local-1",
            exchange_order_id="okx-1",
            client_order_id="client-1",
            strategy="ma_cross",
            symbol="BTC/USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=50000.0,
            status="pending",
            fill_price=0.0,
            timestamp=1700000000000,
            updated_at=1700000000000,
        )
    )
    persisted = repo.upsert_order(
        OrderRecord(
            order_id="local-2",
            exchange_order_id="okx-1",
            client_order_id="client-1",
            strategy="other-strategy",
            symbol="ETH/USDT",
            side="sell",
            type="market",
            amount=1.0,
            price=50000.0,
            status="filled",
            fill_price=50100.0,
            timestamp=1700000000000,
            updated_at=1700000005000,
            filled_amount=1.0,
        )
    )

    orders = repo.get_orders()

    assert len(orders) == 1
    assert persisted.order_id == "local-1"
    assert (persisted.strategy, persisted.symbol, persisted.side, persisted.type) == (
        "ma_cross", "BTC/USDT", "buy", "limit",
    )
    assert persisted.exchange_order_id == "okx-1"
    assert persisted.status == "pending"
    assert persisted.filled_amount == 0.0
    assert persisted.fill_price == 0.0
    assert persisted.updated_at == 1700000000000
    assert "immutable" in caplog.text


def test_sqlite_migrates_legacy_order_and_risk_tables_idempotently():
    engine = create_engine("sqlite:///:memory:", echo=False)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE orderrecord (
                    id INTEGER PRIMARY KEY,
                    order_id VARCHAR NOT NULL,
                    exchange_order_id VARCHAR NOT NULL,
                    client_order_id VARCHAR NOT NULL,
                    strategy VARCHAR NOT NULL,
                    symbol VARCHAR NOT NULL,
                    side VARCHAR NOT NULL,
                    type VARCHAR NOT NULL,
                    amount FLOAT NOT NULL,
                    price FLOAT NOT NULL,
                    status VARCHAR NOT NULL,
                    fill_price FLOAT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                "INSERT INTO orderrecord VALUES "
                "(1, 'filled', '', '', 's', 'BTC-USDT', 'buy', 'limit', "
                "2.0, 100.0, 'filled', 100.0, 1, 1), "
                "(2, 'pending', '', '', 's', 'BTC-USDT', 'buy', 'limit', "
                "2.0, 100.0, 'pending', 0.0, 2, 2)"
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE riskeventrecord (
                    id INTEGER PRIMARY KEY,
                    event_type VARCHAR NOT NULL,
                    strategy VARCHAR NOT NULL,
                    reason_code VARCHAR NOT NULL,
                    reason VARCHAR NOT NULL,
                    payload JSON NOT NULL,
                    timestamp INTEGER NOT NULL
                )
                """
            )
        )

    first = Repository(engine)
    second = Repository(engine)

    with engine.connect() as connection:
        order_columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(orderrecord)"))
        }
        risk_columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(riskeventrecord)"))
        }
        rows = connection.execute(
            text(
                "SELECT status, filled_amount, remaining_amount "
                "FROM orderrecord ORDER BY id"
            )
        ).all()

    assert {"filled_amount", "remaining_amount"} <= order_columns
    assert "event_key" in risk_columns
    assert rows == [("filled", 2.0, 0.0), ("pending", 0.0, 2.0)]
    assert first.get_orders()[0].remaining_amount == 0.0
    assert second.get_orders()[1].remaining_amount == 2.0


def test_sqlite_order_id_migration_retains_duplicate_external_ids_and_local_ids():
    engine = create_engine("sqlite:///:memory:", echo=False)
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE orderrecord (
                id INTEGER PRIMARY KEY, order_id VARCHAR NOT NULL,
                exchange_order_id VARCHAR NOT NULL, client_order_id VARCHAR NOT NULL,
                strategy VARCHAR NOT NULL, symbol VARCHAR NOT NULL, side VARCHAR NOT NULL,
                type VARCHAR NOT NULL, amount FLOAT NOT NULL, price FLOAT NOT NULL,
                status VARCHAR NOT NULL, fill_price FLOAT NOT NULL,
                timestamp INTEGER NOT NULL, updated_at INTEGER NOT NULL
            )
        """))
        connection.execute(text("""
            INSERT INTO orderrecord VALUES
            (2, 'local-b', 'exchange-1', 'client-1', 's', 'BTC-USDT', 'buy', 'limit',
             1, 100, 'pending', 0, 2, 2),
            (1, 'local-a', 'exchange-1', 'client-1', 's', 'BTC-USDT', 'buy', 'limit',
             1, 100, 'pending', 0, 1, 1),
            (3, 'local-a', '', '', 's', 'BTC-USDT', 'buy', 'limit',
             1, 100, 'pending', 0, 3, 3)
        """))

    Repository(engine)

    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT id, order_id, exchange_order_id, client_order_id "
            "FROM orderrecord ORDER BY id"
        )).all()
        indexes = connection.execute(text("PRAGMA index_list(orderrecord)")).all()

    assert rows == [
        (1, "local-a", "exchange-1", "client-1"),
        (2, "local-b", "exchange-1", "client-1"),
        (3, "local-a", "", ""),
    ]
    assert not any(row[1] == "uq_orderrecord_exchange_order_id" for row in indexes)
    assert not any(row[1] == "uq_orderrecord_client_order_id" for row in indexes)


def test_sqlite_partial_order_fill_migration_preserves_existing_filled_amount():
    engine = create_engine("sqlite:///:memory:", echo=False)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE orderrecord (
                    id INTEGER PRIMARY KEY,
                    order_id VARCHAR NOT NULL,
                    exchange_order_id VARCHAR NOT NULL,
                    client_order_id VARCHAR NOT NULL,
                    strategy VARCHAR NOT NULL,
                    symbol VARCHAR NOT NULL,
                    side VARCHAR NOT NULL,
                    type VARCHAR NOT NULL,
                    amount FLOAT NOT NULL,
                    price FLOAT NOT NULL,
                    status VARCHAR NOT NULL,
                    fill_price FLOAT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    filled_amount FLOAT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO orderrecord (
                    id, order_id, exchange_order_id, client_order_id, strategy,
                    symbol, side, type, amount, price, status, fill_price,
                    timestamp, updated_at, filled_amount
                ) VALUES (
                    1, 'partial', 'ex-1', 'client-1', 's', 'BTC-USDT',
                    'buy', 'limit', 1.0, 100.0, 'partially_filled', 100.0,
                    1, 1, 0.25
                )
                """
            )
        )

    first = Repository(engine)
    second = Repository(engine)

    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT filled_amount, remaining_amount FROM orderrecord")
        ).one()

    assert row == (0.25, 0.75)
    assert first.get_orders()[0].filled_amount == 0.25
    assert first.get_orders()[0].remaining_amount == 0.75
    assert second.get_orders()[0].filled_amount == 0.25
    assert second.get_orders()[0].remaining_amount == 0.75


def test_sqlite_migration_backfills_null_filled_and_remaining_using_exchange_data():
    engine = create_engine("sqlite:///:memory:", echo=False)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE orderrecord (
                    id INTEGER PRIMARY KEY,
                    order_id VARCHAR NOT NULL,
                    exchange_order_id VARCHAR NOT NULL,
                    client_order_id VARCHAR NOT NULL,
                    strategy VARCHAR NOT NULL,
                    symbol VARCHAR NOT NULL,
                    side VARCHAR NOT NULL,
                    type VARCHAR NOT NULL,
                    amount FLOAT NOT NULL,
                    price FLOAT NOT NULL,
                    status VARCHAR NOT NULL,
                    fill_price FLOAT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    filled_amount FLOAT,
                    remaining_amount FLOAT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO orderrecord (
                    id, order_id, exchange_order_id, client_order_id, strategy,
                    symbol, side, type, amount, price, status, fill_price,
                    timestamp, updated_at, filled_amount, remaining_amount
                ) VALUES
                    (1, 'partial', '', '', 's', 'BTC-USDT', 'buy', 'limit',
                     2.0, 100.0, 'partially_filled', 100.0, 1, 1, NULL, 0.75),
                    (2, 'filled', '', '', 's', 'BTC-USDT', 'buy', 'limit',
                     2.0, 100.0, 'filled', 100.0, 2, 2, NULL, NULL),
                    (3, 'invalid-remaining', '', '', 's', 'BTC-USDT', 'buy', 'limit',
                     2.0, 100.0, 'pending', 0.0, 3, 3, NULL, -1.0),
                    (4, 'existing-filled', '', '', 's', 'BTC-USDT', 'buy', 'limit',
                     2.0, 100.0, 'partially_filled', 100.0, 4, 4, 0.5, NULL)
                """
            )
        )

    Repository(engine)
    Repository(engine)

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT id, filled_amount, remaining_amount "
                "FROM orderrecord ORDER BY id"
            )
        ).all()

    assert rows == [
        (1, 1.25, 0.75),
        (2, 2.0, 0.0),
        (3, 0.0, 2.0),
        (4, 0.5, 1.5),
    ]


def test_sqlite_migration_normalizes_status_from_migrated_fill_amounts():
    engine = create_engine("sqlite:///:memory:", echo=False)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE orderrecord (
                    id INTEGER PRIMARY KEY,
                    order_id VARCHAR NOT NULL,
                    exchange_order_id VARCHAR NOT NULL,
                    client_order_id VARCHAR NOT NULL,
                    strategy VARCHAR NOT NULL,
                    symbol VARCHAR NOT NULL,
                    side VARCHAR NOT NULL,
                    type VARCHAR NOT NULL,
                    amount FLOAT NOT NULL,
                    price FLOAT NOT NULL,
                    status VARCHAR NOT NULL,
                    fill_price FLOAT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    filled_amount FLOAT,
                    remaining_amount FLOAT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO orderrecord (
                    id, order_id, exchange_order_id, client_order_id, strategy,
                    symbol, side, type, amount, price, status, fill_price,
                    timestamp, updated_at, filled_amount, remaining_amount
                ) VALUES
                    (1, 'filled-with-partial-data', '', '', 's', 'BTC-USDT',
                     'buy', 'limit', 1.0, 100.0, 'filled', 100.0, 1, 1, 0.25, 0.75),
                    (2, 'partial-with-zero-data', '', '', 's', 'BTC-USDT',
                     'buy', 'limit', 2.0, 100.0, 'partially_filled', 0.0, 2, 2, 0.0, 2.0),
                    (3, 'open-with-full-data', '', '', 's', 'BTC-USDT',
                     'buy', 'limit', 1.0, 100.0, 'open', 100.0, 3, 3, 1.0, 0.0),
                    (4, 'cancelled-with-zero-data', '', '', 's', 'BTC-USDT',
                     'buy', 'limit', 1.0, 100.0, 'cancelled', 0.0, 4, 4, 0.0, 1.0),
                    (5, 'rejected-with-partial-data', '', '', 's', 'BTC-USDT',
                     'buy', 'limit', 1.0, 100.0, 'rejected', 100.0, 5, 5, 0.5, 0.5)
                """
            )
        )

    first = Repository(engine)
    second = Repository(engine)

    orders = {order.order_id: order for order in first.get_orders()}
    second_orders = {order.order_id: order for order in second.get_orders()}

    assert {
        order_id: (order.status, order.filled_amount, order.remaining_amount)
        for order_id, order in orders.items()
    } == {
        "filled-with-partial-data": ("partially_filled", 0.25, 0.75),
        "partial-with-zero-data": ("pending", 0.0, 2.0),
        "open-with-full-data": ("filled", 1.0, 0.0),
        "cancelled-with-zero-data": ("cancelled", 0.0, 1.0),
        "rejected-with-partial-data": ("rejected", 0.5, 0.5),
    }
    assert {
        order_id: (order.status, order.filled_amount, order.remaining_amount)
        for order_id, order in second_orders.items()
    } == {
        order_id: (order.status, order.filled_amount, order.remaining_amount)
        for order_id, order in orders.items()
    }


def test_sqlite_migration_backfills_filled_from_existing_remaining():
    engine = create_engine("sqlite:///:memory:", echo=False)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE orderrecord (
                    id INTEGER PRIMARY KEY,
                    order_id VARCHAR NOT NULL,
                    exchange_order_id VARCHAR NOT NULL,
                    client_order_id VARCHAR NOT NULL,
                    strategy VARCHAR NOT NULL,
                    symbol VARCHAR NOT NULL,
                    side VARCHAR NOT NULL,
                    type VARCHAR NOT NULL,
                    amount FLOAT,
                    price FLOAT NOT NULL,
                    status VARCHAR NOT NULL,
                    fill_price FLOAT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    remaining_amount FLOAT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO orderrecord (
                    id, order_id, exchange_order_id, client_order_id, strategy,
                    symbol, side, type, amount, price, status, fill_price,
                    timestamp, updated_at, remaining_amount
                ) VALUES
                    (1, 'partial', '', '', 's', 'BTC-USDT', 'buy', 'limit',
                     1.0, 100.0, 'pending', 0.0, 1, 1, 0.25),
                    (2, 'invalid', '', '', 's', 'BTC-USDT', 'buy', 'limit',
                     NULL, 100.0, 'filled', 0.0, 2, 2, -1.0)
                """
            )
        )

    Repository(engine)

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT id, amount, filled_amount, remaining_amount "
                "FROM orderrecord ORDER BY id"
            )
        ).all()

    assert rows == [(1, 1.0, 0.75, 0.25), (2, 0.0, 0.0, 0.0)]


def test_sqlite_migration_preserves_canceled_status_spellings():
    engine = create_engine("sqlite:///:memory:", echo=False)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE orderrecord (
                    id INTEGER PRIMARY KEY, order_id VARCHAR NOT NULL,
                    exchange_order_id VARCHAR NOT NULL, client_order_id VARCHAR NOT NULL,
                    strategy VARCHAR NOT NULL, symbol VARCHAR NOT NULL, side VARCHAR NOT NULL,
                    type VARCHAR NOT NULL, amount FLOAT NOT NULL, price FLOAT NOT NULL,
                    status VARCHAR NOT NULL, fill_price FLOAT NOT NULL,
                    timestamp INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                    filled_amount FLOAT, remaining_amount FLOAT
                )
                """
            )
        )
        connection.execute(
            text(
                "INSERT INTO orderrecord VALUES "
                "(1, 'a', '', '', 's', 'BTC-USDT', 'buy', 'limit', 1, 100, "
                "'canceled', 0, 1, 1, 0, 1), "
                "(2, 'b', '', '', 's', 'BTC-USDT', 'buy', 'limit', 1, 100, "
                "'cancelled', 0, 1, 1, 0, 1), "
                "(3, 'c', '', '', 's', 'BTC-USDT', 'buy', 'limit', 1, 100, "
                "'cancel', 0, 1, 1, 0, 1)"
            )
        )

    Repository(engine)

    with engine.connect() as connection:
        statuses = connection.execute(
            text("SELECT status FROM orderrecord ORDER BY id")
        ).scalars().all()
    assert statuses == ["canceled", "cancelled", "cancel"]


def test_sqlite_migration_retains_existing_duplicate_trade_ids_for_audit():
    engine = create_engine("sqlite:///:memory:", echo=False)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE traderecord (
                    id INTEGER PRIMARY KEY, exchange_trade_id VARCHAR NOT NULL,
                    order_id VARCHAR NOT NULL, strategy VARCHAR NOT NULL, symbol VARCHAR NOT NULL,
                    side VARCHAR NOT NULL, amount FLOAT NOT NULL, price FLOAT NOT NULL,
                    fee FLOAT NOT NULL, timestamp INTEGER NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                "INSERT INTO traderecord VALUES "
                "(2, 'dup', 'o2', 's', 'BTC-USDT', 'buy', 1, 100, 1, 2), "
                "(1, 'dup', 'o1', 's', 'BTC-USDT', 'buy', 1, 100, 1, 1), "
                "(3, '', 'o3', 's', 'BTC-USDT', 'buy', 1, 100, 1, 3)"
            )
        )

    Repository(engine)
    Repository(engine)

    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT id, exchange_trade_id FROM traderecord ORDER BY id")
        ).all()
        connection.execute(
            text(
                "INSERT INTO traderecord "
                "(exchange_trade_id, order_id, strategy, symbol, side, amount, "
                "price, fee, timestamp) "
                "VALUES ('dup', 'o4', 's', 'BTC-USDT', 'buy', 1, 100, 1, 4)"
            )
        )

    assert rows == [(1, "dup"), (2, "dup"), (3, "")]


def test_legacy_trade_duplicate_migration_keeps_unknown_rows_active_and_read_only():
    engine = create_engine("sqlite:///:memory:", echo=False)
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE traderecord (
                id INTEGER PRIMARY KEY, exchange_trade_id VARCHAR NOT NULL,
                exchange_order_id VARCHAR NOT NULL, client_order_id VARCHAR NOT NULL,
                order_id VARCHAR NOT NULL, strategy VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL, side VARCHAR NOT NULL, amount FLOAT NOT NULL,
                price FLOAT NOT NULL, fee FLOAT NOT NULL, timestamp INTEGER NOT NULL
            )
        """))
        connection.execute(text(
            "INSERT INTO traderecord VALUES "
            "(1, 'legacy-duplicate', '', '', 'old', 'legacy', 'BTC-USDT', 'buy', 1, 100, 0, 10), "
            "(2, 'legacy-duplicate', '', '', 'new', 'legacy', 'BTC-USDT', 'buy', 1, 101, 0, 20)"
        ))

    Repository(engine)

    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT id, exchange_trade_id, order_id, source, runtime_mode, active "
            "FROM traderecord ORDER BY id"
        )).all()

    assert rows == [
        (1, "legacy-duplicate", "old", "unknown", "unknown", 1),
        (2, "legacy-duplicate", "new", "unknown", "unknown", 1),
    ]


def test_sqlite_migration_deduplicates_blank_trade_fingerprints():
    engine = create_engine("sqlite:///:memory:", echo=False)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE traderecord (
                    id INTEGER PRIMARY KEY,
                    exchange_trade_id VARCHAR,
                    exchange_order_id VARCHAR,
                    order_id VARCHAR NOT NULL,
                    strategy VARCHAR NOT NULL,
                    symbol VARCHAR NOT NULL,
                    side VARCHAR NOT NULL,
                    amount FLOAT NOT NULL,
                    price FLOAT NOT NULL,
                    fee FLOAT NOT NULL,
                    timestamp INTEGER NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO traderecord VALUES
                    (2, '', 'ex-1', 'local-b', 'strategy-b', 'BTC-USDT', 'buy',
                     0.1, 100.0, 0.01, 123),
                    (1, NULL, 'ex-1', 'local-a', 'strategy-a', 'BTC-USDT', 'buy',
                     0.1, 100.0, 0.01, 123),
                    (3, '', '', 'local-c', 'strategy-c', 'BTC-USDT', 'buy',
                     0.1, 100.0, 0.01, 123)
                """
            )
        )

    Repository(engine)
    Repository(engine)

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT id, exchange_trade_id, blank_trade_fingerprint "
                "FROM traderecord ORDER BY id"
            )
        ).all()
        indexes = connection.execute(text("PRAGMA index_list(traderecord)")).all()

    assert len(rows) == 3
    assert rows[0][1] in (None, "")
    assert rows[0][2]
    assert rows[1][1] in (None, "")
    assert rows[1][2] == rows[0][2]
    assert rows[2][1] == ""
    assert rows[2][2] == ""
    assert not any(row[1] == "uq_traderecord_blank_trade_fingerprint" for row in indexes)


def test_repository_timestamp_migration_accepts_exact_max_int64_and_sanitizes_overflow():
    engine = create_engine("sqlite:///:memory:", echo=False)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE traderecord (
                    id INTEGER PRIMARY KEY,
                    exchange_trade_id VARCHAR NOT NULL,
                    order_id VARCHAR NOT NULL,
                    strategy VARCHAR NOT NULL,
                    symbol VARCHAR NOT NULL,
                    side VARCHAR NOT NULL,
                    amount FLOAT NOT NULL,
                    price FLOAT NOT NULL,
                    fee FLOAT NOT NULL,
                    timestamp INTEGER NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                "INSERT INTO traderecord VALUES "
                "(1, '', 'o1', 's', 'BTC-USDT', 'buy', 1, 100, 0, :timestamp)"
            ),
            {"timestamp": 2**63 - 1},
        )

    Repository(engine)

    with engine.connect() as connection:
        timestamp = connection.execute(text("SELECT timestamp FROM traderecord")).scalar_one()

    assert timestamp == 2**63 - 1


def test_sqlite_migration_normalizes_legacy_trade_values_at_database_level():
    engine = create_engine("sqlite:///:memory:", echo=False)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE traderecord (
                    id INTEGER PRIMARY KEY,
                    exchange_trade_id VARCHAR NOT NULL,
                    order_id VARCHAR NOT NULL,
                    strategy VARCHAR NOT NULL,
                    symbol VARCHAR NOT NULL,
                    side VARCHAR NOT NULL,
                    amount FLOAT NOT NULL,
                    price FLOAT NOT NULL,
                    fee FLOAT NOT NULL,
                    timestamp INTEGER NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO traderecord VALUES
                    (1, 'legacy-invalid', 'o1', 's', 'BTC-USDT', 'buy',
                     'nan', 'inf', -1.0, 'invalid'),
                    (2, 'legacy-negative', 'o2', 's', 'BTC-USDT', 'buy',
                     -0.1, -100.0, 'nan', -1),
                    (3, 'legacy-valid', 'o3', 's', 'BTC-USDT', 'buy',
                     0.5, 100.0, 0.01, 1700000000000)
                """
            )
        )

    first = Repository(engine)
    Repository(engine)

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT amount, price, fee, timestamp FROM traderecord "
                "ORDER BY id"
            )
        ).all()

    assert rows == [
        (0.0, 0.0, 0.0, 0),
        (0.0, 0.0, 0.0, 0),
        (0.5, 100.0, 0.01, 1700000000000),
    ]
    assert [
        (trade.amount, trade.price, trade.fee, trade.timestamp)
        for trade in first.get_trades()
    ] == rows


def test_account_save_and_read_normalize_mutated_values(repo: Repository):
    account = AccountRecord(
        strategy="unsafe",
        initial_equity=100.0,
        cash_balance=90.0,
        available_balance=80.0,
        equity=100.0,
        realized_pnl=1.0,
        unrealized_pnl=2.0,
        daily_pnl=3.0,
        fees_paid=0.5,
        updated_at=10,
        assets=[{"ccy": "USDT", "cash_bal": 90.0}],
    )
    account.cash_balance = float("nan")
    account.realized_pnl = -4.0
    account.updated_at = 2**63
    account.assets = [{"ccy": "USDT", "cash_bal": -1.0, "upl": -2.0}]

    saved = repo.upsert_account(account)
    loaded = repo.get_account("unsafe")

    assert loaded is not None
    assert saved.cash_balance == 0.0
    assert saved.realized_pnl == -4.0
    assert saved.updated_at == 0
    assert loaded.assets == [{"ccy": "USDT", "cash_bal": 0.0, "upl": -2.0}]


def test_account_save_read_normalizes_asset_balance_objects_and_invalid_assets(repo):
    account = AccountRecord(
        strategy="asset-normalization",
        initial_equity=1,
        cash_balance=1,
        equity=1,
        realized_pnl=0,
        unrealized_pnl=0,
        daily_pnl=0,
        fees_paid=0,
        updated_at=1,
        assets=[
            AssetBalance(ccy="USDT", avail_bal="2.5"),
            None,
            1,
            {"ccy": "", "avail_bal": 4},
            {"ccy": "BTC", "avail_bal": "bad", "upl": "bad"},
        ],
    )

    repo.upsert_account(account)
    loaded = repo.get_account("asset-normalization")

    assert loaded is not None
    assert loaded.assets == [
        {"ccy": "USDT", "cash_bal": 0.0, "eq": 0.0, "eq_utd": 0.0, "avail_bal": 2.5, "upl": 0.0},
        {"ccy": "BTC", "avail_bal": 0.0, "upl": 0.0},
    ]


def test_record_provenance_requires_exact_source_runtime_pair():
    assert _record_provenance(
        SimpleNamespace(source="live_exchange", runtime_mode="live")
    ) == "live"
    assert _record_provenance(
        SimpleNamespace(source="live_exchange", runtime_mode=None)
    ) == "unknown"
    assert _record_provenance(
        SimpleNamespace(source="unknown", runtime_mode="backtest")
    ) == "unknown"
    assert _record_provenance(
        SimpleNamespace(source="unknown", runtime_mode="unknown")
    ) == "unknown"


def test_order_table_rebuild_preserves_source_and_runtime_mode_columns_and_values():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE orderrecord"))
        connection.execute(text("""
            CREATE TABLE orderrecord (
                id INTEGER PRIMARY KEY,
                order_id VARCHAR NOT NULL,
                exchange_order_id VARCHAR NOT NULL,
                client_order_id VARCHAR NOT NULL,
                strategy VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                side VARCHAR NOT NULL,
                type VARCHAR NOT NULL,
                amount FLOAT NOT NULL,
                price FLOAT NOT NULL,
                status VARCHAR NOT NULL,
                fill_price FLOAT NOT NULL,
                timestamp INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                filled_amount FLOAT,
                remaining_amount FLOAT,
                snapshot_filled_amount FLOAT,
                snapshot_updated_at INTEGER,
                execution_type_family VARCHAR,
                source VARCHAR,
                runtime_mode VARCHAR
            )
        """))
        connection.execute(text(
            "CREATE UNIQUE INDEX old_order_exchange ON orderrecord (exchange_order_id)"
        ))
        connection.execute(text(
            "CREATE UNIQUE INDEX old_order_client ON orderrecord (client_order_id)"
        ))
        connection.execute(text("""
            INSERT INTO orderrecord VALUES
            (1, 'o1', 'ex1', 'cl1', 'alpha', 'BTC-USDT', 'buy', 'market',
             1.0, 100.0, 'filled', 100.0, 10, 10, 1.0, 0.0, 1.0, 10,
             'market', 'live_exchange', 'live')
        """))

    repository = Repository(engine)
    order = repository.get_orders()[0]

    assert order.source == "live_exchange"
    assert order.runtime_mode == "live"


def test_sqlite_migration_normalizes_legacy_account_assets_and_drops_invalid_entries():
    engine = create_engine("sqlite:///:memory:", echo=False)
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE accountrecord (
                id INTEGER PRIMARY KEY, strategy VARCHAR NOT NULL,
                initial_equity FLOAT NOT NULL, cash_balance FLOAT NOT NULL,
                equity FLOAT NOT NULL, realized_pnl FLOAT NOT NULL,
                unrealized_pnl FLOAT NOT NULL, daily_pnl FLOAT NOT NULL,
                fees_paid FLOAT NOT NULL, updated_at INTEGER NOT NULL,
                assets JSON
            )
        """))
        connection.execute(text(
            "INSERT INTO accountrecord VALUES "
            "(1, 'legacy-assets', 1, 1, 1, 0, 0, 0, 0, 1, "
            "'[null, 2, {}, {\"ccy\": \"\"}, {\"ccy\": \"   \"}, "
            "{\"ccy\": \"USDT\", \"avail_bal\": \"bad\", \"upl\": \"-2.5\"}]')"
        ))

    Repository(engine)

    with engine.connect() as connection:
        assets = connection.execute(
            text("SELECT assets FROM accountrecord WHERE strategy = 'legacy-assets'")
        ).scalar_one()
    assert assets == '[{"ccy": "USDT", "avail_bal": 0.0, "upl": -2.5}]'


def test_sqlite_migration_normalizes_legacy_account_values_idempotently():
    engine = create_engine("sqlite:///:memory:", echo=False)
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE accountrecord (
                id INTEGER PRIMARY KEY, strategy VARCHAR NOT NULL,
                initial_equity FLOAT NOT NULL, cash_balance FLOAT NOT NULL,
                equity FLOAT NOT NULL, realized_pnl FLOAT NOT NULL,
                unrealized_pnl FLOAT NOT NULL, daily_pnl FLOAT NOT NULL,
                fees_paid FLOAT NOT NULL, updated_at INTEGER NOT NULL
            )
        """))
        connection.execute(text("""
            INSERT INTO accountrecord VALUES
            (1, 'legacy-invalid', 'nan', -1, 'inf', -2.5, 'bad', '-inf', -3, '9223372036854775808'),
            (2, 'legacy-valid', 100, 90, 110, -2, 3, -1, 0.5, 9223372036854775807)
        """))

    first = Repository(engine)
    Repository(engine)

    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT initial_equity, cash_balance, available_balance, equity, "
            "realized_pnl, unrealized_pnl, daily_pnl, fees_paid, updated_at, assets "
            "FROM accountrecord ORDER BY id"
        )).all()

    assert rows == [
        (0.0, 0.0, 0.0, 0.0, -2.5, 0.0, 0.0, 0.0, 0, "[]"),
        (100.0, 90.0, 0.0, 110.0, -2.0, 3.0, -1.0, 0.5, 2**63 - 1, "[]"),
    ]
    assert first.get_account("legacy-invalid").updated_at == 0


def test_upsert_account_rejects_stale_snapshot_and_accepts_new_snapshot(repo: Repository):
    repo.upsert_account(
        AccountRecord(
            strategy="__exchange__",
            initial_equity=1000.0,
            cash_balance=900.0,
            available_balance=850.0,
            equity=1010.0,
            realized_pnl=10.0,
            unrealized_pnl=2.0,
            daily_pnl=1.0,
            fees_paid=1.0,
            updated_at=20,
            assets=[{"ccy": "USDT", "avail_bal": 850.0}],
        )
    )

    stale = repo.upsert_account(
        AccountRecord(
            strategy="__exchange__",
            initial_equity=1000.0,
            cash_balance=1.0,
            available_balance=1.0,
            equity=1.0,
            realized_pnl=1.0,
            unrealized_pnl=1.0,
            daily_pnl=1.0,
            fees_paid=99.0,
            updated_at=19,
            assets=[{"ccy": "BTC", "avail_bal": 1.0}],
        )
    )

    assert stale.updated_at == 20
    assert stale.cash_balance == 900.0
    assert stale.assets == [{"ccy": "USDT", "avail_bal": 850.0}]

    current = repo.upsert_account(
        AccountRecord(
            strategy="__exchange__",
            initial_equity=1000.0,
            cash_balance=880.0,
            available_balance=830.0,
            equity=1005.0,
            realized_pnl=5.0,
            unrealized_pnl=3.0,
            daily_pnl=0.5,
            fees_paid=2.0,
            updated_at=21,
            assets=[{"ccy": "ETH", "avail_bal": 2.0}],
        )
    )

    assert current.updated_at == 21
    assert current.cash_balance == 880.0
    assert current.assets == [{"ccy": "ETH", "avail_bal": 2.0}]


def test_upsert_account_equal_timestamp_external_conflict_preserves_existing(repo: Repository):
    repo.upsert_account(
        AccountRecord(
            strategy="equal-conflict",
            initial_equity=0.0,
            cash_balance=100.0,
            available_balance=90.0,
            equity=100.0,
            realized_pnl=1.0,
            unrealized_pnl=2.0,
            daily_pnl=3.0,
            fees_paid=4.0,
            updated_at=10,
            assets=[{"ccy": "USDT", "avail_bal": 90.0}],
        )
    )

    result = repo.upsert_account(
        AccountRecord(
            strategy="equal-conflict",
            initial_equity=250.0,
            cash_balance=200.0,
            available_balance=190.0,
            equity=200.0,
            realized_pnl=11.0,
            unrealized_pnl=12.0,
            daily_pnl=13.0,
            fees_paid=14.0,
            updated_at=10,
            assets=[{"ccy": "BTC", "avail_bal": 1.0}],
        )
    )

    assert result.initial_equity == 250.0
    assert result.cash_balance == 100.0
    assert result.equity == 100.0
    assert result.realized_pnl == 1.0
    assert result.assets == [{"ccy": "USDT", "avail_bal": 90.0}]


def test_upsert_account_equal_timestamp_identical_external_snapshot_preserves_existing(repo):
    original = AccountRecord(
        strategy="equal-identical",
        initial_equity=100.0,
        cash_balance=90.0,
        available_balance=80.0,
        equity=100.0,
        realized_pnl=1.0,
        unrealized_pnl=2.0,
        daily_pnl=3.0,
        fees_paid=4.0,
        updated_at=10,
        assets=[{"ccy": "USDT", "avail_bal": 80.0}],
    )
    saved = repo.upsert_account(original)

    result = repo.upsert_account(
        AccountRecord(
            strategy="equal-identical",
            initial_equity=100.0,
            cash_balance=90.0,
            available_balance=80.0,
            equity=100.0,
            realized_pnl=1.0,
            unrealized_pnl=2.0,
            daily_pnl=3.0,
            fees_paid=4.0,
            updated_at=10,
            assets=[{"ccy": "USDT", "avail_bal": 80.0}],
        )
    )

    assert result.id == saved.id
    assert result.model_dump() == saved.model_dump()


def test_upsert_account_equal_timestamp_same_id_allows_explicit_local_update(repo):
    saved = repo.upsert_account(
        AccountRecord(
            strategy="equal-local",
            initial_equity=100.0,
            cash_balance=90.0,
            equity=100.0,
            realized_pnl=1.0,
            unrealized_pnl=2.0,
            daily_pnl=3.0,
            fees_paid=4.0,
            updated_at=10,
        )
    )

    result = repo.upsert_account(
        AccountRecord(
            id=saved.id,
            strategy="equal-local",
            initial_equity=100.0,
            cash_balance=95.0,
            equity=105.0,
            realized_pnl=6.0,
            unrealized_pnl=7.0,
            daily_pnl=8.0,
            fees_paid=9.0,
            updated_at=10,
        )
    )

    assert result.id == saved.id
    assert result.cash_balance == 95.0
    assert result.equity == 105.0
    assert result.realized_pnl == 6.0


def test_upsert_account_newer_snapshot_replaces_equal_timestamp_baseline(repo):
    repo.upsert_account(
        AccountRecord(
            strategy="newer-account",
            initial_equity=100.0,
            cash_balance=90.0,
            available_balance=80.0,
            equity=100.0,
            realized_pnl=1.0,
            unrealized_pnl=2.0,
            daily_pnl=3.0,
            fees_paid=4.0,
            updated_at=10,
        )
    )

    result = repo.upsert_account(
        AccountRecord(
            strategy="newer-account",
            initial_equity=999.0,
            cash_balance=190.0,
            available_balance=180.0,
            equity=200.0,
            realized_pnl=11.0,
            unrealized_pnl=12.0,
            daily_pnl=13.0,
            fees_paid=14.0,
            updated_at=11,
        )
    )

    assert result.updated_at == 11
    assert result.cash_balance == 190.0
    assert result.equity == 200.0
    assert result.initial_equity == 100.0


def test_upsert_account_equal_timestamp_insert_race_preserves_competitor(repo, monkeypatch):
    incoming = AccountRecord(
        strategy="equal-race",
        initial_equity=200.0,
        cash_balance=200.0,
        equity=200.0,
        realized_pnl=2.0,
        unrealized_pnl=2.0,
        daily_pnl=2.0,
        fees_paid=2.0,
        updated_at=10,
    )
    competitor = AccountRecord(
        strategy="equal-race",
        initial_equity=100.0,
        cash_balance=100.0,
        equity=100.0,
        realized_pnl=1.0,
        unrealized_pnl=1.0,
        daily_pnl=1.0,
        fees_paid=1.0,
        updated_at=10,
    )
    original_exec = repository_module.Session.exec
    inserted = False

    def interleave(session, statement, *args, **kwargs):
        nonlocal inserted
        result = original_exec(session, statement, *args, **kwargs)
        if not inserted and "accountrecord" in str(statement) and "WHERE" in str(statement):
            inserted = True
            repo.upsert_account(competitor)

            class EmptyResult:
                def first(self):
                    return None

            return EmptyResult()
        return result

    monkeypatch.setattr(repository_module.Session, "exec", interleave)
    result = repo.upsert_account(incoming)

    assert result.cash_balance == 100.0
    assert result.equity == 100.0
    assert result.realized_pnl == 1.0


def test_fresh_sqlite_schema_creates_partial_external_order_id_indexes_and_allows_blanks():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Repository(engine)
    Repository(engine)

    with engine.connect() as connection:
        indexes = {
            row[1]: connection.execute(
                text("SELECT sql FROM sqlite_schema WHERE type = 'index' AND name = :name"),
                {"name": row[1]},
            ).scalar_one()
            for row in connection.execute(text("PRAGMA index_list(orderrecord)"))
            if row[1] in {
                "uq_orderrecord_exchange_order_id",
                "uq_orderrecord_client_order_id",
            }
        }
        connection.execute(text(
            "INSERT INTO orderrecord "
            "(order_id, exchange_order_id, client_order_id, strategy, symbol, side, type, "
            "amount, price, status, fill_price, timestamp, updated_at, filled_amount, "
            "remaining_amount, snapshot_filled_amount) VALUES "
            "('blank-a', NULL, NULL, 's', 'BTC-USDT', 'buy', 'limit', 1, 100, "
            "'pending', 0, 1, 1, 0, 1, 0), "
            "('blank-b', '', '', 's', 'BTC-USDT', 'buy', 'limit', 1, 100, "
            "'pending', 0, 2, 2, 0, 1, 0), "
            "('first-exchange', 'exchange-1', 'client-1', 's', 'BTC-USDT', 'buy', 'limit', "
            "1, 100, 'pending', 0, 4, 4, 0, 1, 0)"
        ))
        with pytest.raises(IntegrityError):
            connection.execute(text(
                "INSERT INTO orderrecord "
                "(order_id, exchange_order_id, client_order_id, strategy, symbol, side, type, "
                "amount, price, status, fill_price, timestamp, updated_at, filled_amount, "
                "remaining_amount, snapshot_filled_amount) VALUES "
                "('duplicate-exchange', 'exchange-1', '', 's', 'BTC-USDT', 'buy', 'limit', "
                "1, 100, 'pending', 0, 3, 3, 0, 1, 0)"
            ))
    assert set(indexes) == {
        "uq_orderrecord_exchange_order_id",
        "uq_orderrecord_client_order_id",
    }
    assert "WHERE exchange_order_id IS NOT NULL AND exchange_order_id != ''" in indexes[
        "uq_orderrecord_exchange_order_id"
    ]
    assert "WHERE client_order_id IS NOT NULL AND client_order_id != ''" in indexes[
        "uq_orderrecord_client_order_id"
    ]


def test_concurrent_order_upserts_keep_one_row_per_external_identity(tmp_path):
    repository = Repository(db_path=str(tmp_path / "concurrent-orders.db"))
    orders = [
        OrderRecord(
            order_id=f"local-{index}",
            exchange_order_id="exchange-concurrent",
            client_order_id="client-concurrent",
            strategy="s",
            symbol="BTC-USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=100.0,
            status="pending",
            fill_price=0.0,
            timestamp=index,
            updated_at=index,
        )
        for index in range(8)
    ]

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(repository.upsert_order, orders))

    persisted = repository.get_orders()
    assert len(persisted) == 1
    assert all(result.id == persisted[0].id for result in results)
    assert (persisted[0].exchange_order_id, persisted[0].client_order_id) == (
        "exchange-concurrent",
        "client-concurrent",
    )


def test_legacy_live_ownership_audit_is_read_only(repo):
    repo.upsert_account(AccountRecord(
        strategy="legacy-strategy", initial_equity=100.0, cash_balance=90.0,
        equity=100.0, realized_pnl=0.0, unrealized_pnl=0.0, daily_pnl=0.0,
        fees_paid=0.0, updated_at=1,
    ))
    repo.upsert_position(PositionRecord(
        strategy="legacy-strategy", symbol="BTC-USDT-SWAP", side="long",
        amount=1.0, entry_price=100.0, leverage=1, timestamp=1,
    ))
    before = (
        [item.model_dump() for item in repo.get_accounts()],
        [item.model_dump() for item in repo.get_positions()],
    )

    audit = repo.audit_legacy_live_state_ownership()

    after = (
        [item.model_dump() for item in repo.get_accounts()],
        [item.model_dump() for item in repo.get_positions()],
    )
    assert audit["legacy_strategy_accounts"] == 0
    assert audit["legacy_strategy_positions"] == 0
    assert audit["unknown_strategy_accounts"] == 1
    assert audit["unknown_strategy_positions"] == 1
    assert before == after


def test_order_external_ids_are_unique_only_when_nonempty_without_making_local_order_id_unique(
    repo,
):
    first = repo.upsert_order(
        OrderRecord(
            order_id="local-a",
            exchange_order_id="exchange-a",
            client_order_id="client-a",
            strategy="s",
            symbol="BTC-USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=100.0,
            status="pending",
            fill_price=0.0,
            timestamp=1,
        )
    )

    with pytest.raises(IntegrityError):
        repo.save_order(
            OrderRecord(
                order_id="local-b",
                exchange_order_id="exchange-a",
                client_order_id="client-b",
                strategy="s",
                symbol="BTC-USDT",
                side="buy",
                type="limit",
                amount=1.0,
                price=100.0,
                status="pending",
                fill_price=0.0,
                timestamp=2,
            )
        )

    repo.save_order(
        OrderRecord(
            order_id="local-a",
            exchange_order_id="",
            client_order_id="",
            strategy="s",
            symbol="BTC-USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=100.0,
            status="pending",
            fill_price=0.0,
            timestamp=3,
        )
    )
    repo.save_order(
        OrderRecord(
            order_id="local-a",
            exchange_order_id="",
            client_order_id="",
            strategy="s",
            symbol="BTC-USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=100.0,
            status="pending",
            fill_price=0.0,
            timestamp=4,
        )
    )

    assert len(repo.get_orders()) == 3
    assert first.order_id == "local-a"


def test_upsert_order_rejects_external_id_conflict_with_explicit_local_order_id(repo, caplog):
    existing = repo.upsert_order(
        OrderRecord(
            order_id="local-a",
            exchange_order_id="exchange-1",
            client_order_id="client-a",
            strategy="strategy-a",
            symbol="BTC-USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=100.0,
            status="pending",
            fill_price=0.0,
            timestamp=1,
            updated_at=1,
        )
    )

    result = repo.upsert_order(
        OrderRecord(
            order_id="local-b",
            exchange_order_id="exchange-1",
            client_order_id="client-b",
            strategy="strategy-b",
            symbol="ETH-USDT",
            side="sell",
            type="market",
            amount=2.0,
            price=200.0,
            status="filled",
            fill_price=200.0,
            timestamp=2,
            updated_at=2,
            filled_amount=2.0,
        )
    )

    assert result.id == existing.id
    persisted = repo.get_orders()
    assert len(persisted) == 1
    assert persisted[0].model_dump() == existing.model_dump()
    assert "conflicting order identity candidates" in caplog.text


def test_upsert_order_rejects_local_id_conflict_when_only_one_external_alias_matches(
    repo, caplog
):
    existing = repo.upsert_order(
        OrderRecord(
            order_id="local-a",
            exchange_order_id="exchange-1",
            client_order_id="client-a",
            strategy="strategy-a",
            symbol="BTC-USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=100.0,
            status="pending",
            fill_price=0.0,
            timestamp=1,
            updated_at=1,
        )
    )

    result = repo.upsert_order(
        OrderRecord(
            order_id="local-b",
            exchange_order_id="exchange-1",
            client_order_id="",
            strategy="strategy-b",
            symbol="ETH-USDT",
            side="sell",
            type="market",
            amount=2.0,
            price=200.0,
            status="filled",
            fill_price=200.0,
            timestamp=2,
            updated_at=2,
            filled_amount=2.0,
        )
    )

    assert result.id == existing.id
    assert result.status == "pending"
    assert "conflicting order identity candidates" in caplog.text


def test_upsert_order_rejects_multiple_external_identity_candidates(repo, caplog):
    repo.upsert_order(
        OrderRecord(
            order_id="local-a",
            exchange_order_id="exchange-a",
            client_order_id="client-a",
            strategy="strategy-a",
            symbol="BTC-USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=100.0,
            status="pending",
            fill_price=0.0,
            timestamp=1,
            updated_at=1,
        )
    )
    repo.upsert_order(
        OrderRecord(
            order_id="local-b",
            exchange_order_id="exchange-b",
            client_order_id="client-b",
            strategy="strategy-b",
            symbol="ETH-USDT",
            side="sell",
            type="limit",
            amount=1.0,
            price=200.0,
            status="pending",
            fill_price=0.0,
            timestamp=1,
            updated_at=1,
        )
    )

    with pytest.raises(ValueError, match="conflicting order identity candidates"):
        repo.upsert_order(
            OrderRecord(
                order_id="local-c",
                exchange_order_id="exchange-a",
                client_order_id="client-b",
                strategy="strategy-c",
                symbol="SOL-USDT",
                side="buy",
                type="market",
                amount=3.0,
                price=300.0,
                status="filled",
                fill_price=300.0,
                timestamp=2,
                updated_at=2,
                filled_amount=3.0,
            )
        )

    assert [(order.order_id, order.status) for order in repo.get_orders()] == [
        ("local-a", "pending"),
        ("local-b", "pending"),
    ]
    assert "conflicting order identity candidates" in caplog.text


def test_upsert_order_returns_existing_record_when_external_id_insert_races(repo, monkeypatch):
    raced = OrderRecord(
        order_id="local-b",
        exchange_order_id="exchange-race",
        client_order_id="client-race",
        strategy="other",
        symbol="ETH-USDT",
        side="sell",
        type="limit",
        amount=2.0,
        price=200.0,
        status="filled",
        fill_price=200.0,
        timestamp=2,
        updated_at=2,
        filled_amount=2.0,
    )
    existing = OrderRecord(
        order_id="local-a",
        exchange_order_id="exchange-race",
        client_order_id="client-race",
        strategy="s",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=2.0,
        price=200.0,
        status="pending",
        fill_price=0.0,
        timestamp=1,
    )
    original_exec = repository_module.Session.exec
    inserted_by_competitor = False

    def interleave_competitor(session, statement, *args, **kwargs):
        nonlocal inserted_by_competitor
        result = original_exec(session, statement, *args, **kwargs)
        if not inserted_by_competitor and "exchange_order_id" in str(statement):
            inserted_by_competitor = True
            repo.save_order(existing)
            class EmptyResult:
                def first(self):
                    return None
            return EmptyResult()
        return result

    monkeypatch.setattr(repository_module.Session, "exec", interleave_competitor)

    result = repo.upsert_order(raced)

    assert result.id == existing.id
    assert len(repo.get_orders()) == 1
    assert result.status == "pending"
    assert result.filled_amount == pytest.approx(0.0)
    assert result.updated_at == 0
    assert result.strategy == existing.strategy
    assert result.symbol == existing.symbol


def test_public_and_atomic_order_upserts_reject_crossed_external_ids_without_cross_contamination(
    repo, caplog
):
    repo.upsert_order(OrderRecord(
        order_id="order-a", exchange_order_id="X", client_order_id="C1",
        strategy="a", symbol="BTC-USDT", side="buy", type="limit",
        amount=1.0, price=100.0, status="pending", fill_price=0.0, timestamp=1,
    ))
    repo.upsert_order(OrderRecord(
        order_id="order-b", exchange_order_id="Y", client_order_id="C2",
        strategy="b", symbol="ETH-USDT", side="sell", type="limit",
        amount=1.0, price=200.0, status="pending", fill_price=0.0, timestamp=1,
    ))
    incoming = OrderRecord(
        order_id="order-c", exchange_order_id="X", client_order_id="C2",
        strategy="c", symbol="SOL-USDT", side="buy", type="market",
        amount=3.0, price=300.0, status="filled", fill_price=300.0, timestamp=2,
        filled_amount=3.0,
    )

    with pytest.raises(ValueError, match="conflicting order identity candidates"):
        repo.upsert_order(incoming)
    with pytest.raises(ValueError, match="conflicting order identity candidates"):
        repo.atomic_accounting(lambda tx: tx.upsert_order(incoming))

    assert [(order.order_id, order.exchange_order_id, order.client_order_id)
            for order in repo.get_orders()] == [
        ("order-a", "X", "C1"),
        ("order-b", "Y", "C2"),
    ]
    assert "conflicting order identity candidates" in caplog.text


def test_repository_round_trips_execution_type_family_without_exposing_it_in_model_dump(repo):
    repo.save_order(OrderRecord(
        order_id="trigger-order",
        exchange_order_id="trigger-exchange",
        client_order_id="trigger-client",
        strategy="ma_cross",
        symbol="BTC-USDT-SWAP",
        side="buy",
        type="market",
        execution_type_family="stop",
        amount=1.0,
        price=100.0,
        status="pending",
        fill_price=0.0,
        timestamp=1,
    ))

    persisted = repo.get_orders()[0]
    assert persisted.execution_type_family == "stop"
    assert "execution_type_family" not in persisted.model_dump()


def test_repository_rebinds_placeholder_trade_using_canonical_spot_symbol_aliases(repo):
    repo.save_order(OrderRecord(
        order_id="spot-order", exchange_order_id="X", client_order_id="C",
        strategy="spot", symbol="BTC/USDT", side="buy", type="limit",
        amount=1.0, price=100.0, status="pending", fill_price=0.0, timestamp=1,
    ))
    trade = TradeRecord(
        exchange_order_id="X", client_order_id="C", order_id="okx:X",
        strategy="__exchange__", symbol="BTC-USDT", side="buy",
        amount=0.1, price=100.0, fee=0.0, timestamp=2,
    )
    repo.save_trade(trade)

    repo.rebind_trades_to_orders()

    rebound = repo.get_trades()[0]
    assert (rebound.order_id, rebound.strategy) == ("spot-order", "spot")


def test_repository_does_not_rebind_legacy_trade_to_live_order(repo):
    repo.save_order(OrderRecord(
        order_id="live-order", exchange_order_id="live-exchange", client_order_id="live-client",
        strategy="live-strategy", symbol="BTC-USDT", side="buy", type="market",
        amount=1.0, price=100.0, status="filled", fill_price=100.0, timestamp=1,
        source="live_exchange", runtime_mode="live",
    ))
    repo.save_trade(TradeRecord(
        exchange_trade_id="legacy-trade", exchange_order_id="live-exchange",
        client_order_id="live-client", order_id="legacy-order", strategy="__exchange__",
        symbol="BTC-USDT", side="buy", amount=0.1, price=100.0, fee=0.0, timestamp=2,
        source="unknown", runtime_mode="unknown",
    ))

    repo.rebind_trades_to_orders()

    legacy = repo.get_trades()[0]
    assert (legacy.order_id, legacy.strategy) == ("legacy-order", "__exchange__")


def test_repository_rebinds_strictly_live_trade_to_live_order(repo):
    repo.save_order(OrderRecord(
        order_id="live-order", exchange_order_id="live-exchange", client_order_id="live-client",
        strategy="live-strategy", symbol="BTC-USDT", side="buy", type="market",
        amount=1.0, price=100.0, status="filled", fill_price=100.0, timestamp=1,
        source="live_exchange", runtime_mode="live",
    ))
    repo.save_trade(TradeRecord(
        exchange_trade_id="live-trade", exchange_order_id="live-exchange",
        client_order_id="live-client", order_id="okx:live-exchange", strategy="__exchange__",
        symbol="BTC-USDT", side="buy", amount=0.1, price=100.0, fee=0.0, timestamp=2,
        source="live_exchange", runtime_mode="live",
    ))

    repo.rebind_trades_to_orders()

    live = repo.get_trades()[0]
    assert (live.order_id, live.strategy) == ("live-order", "live-strategy")


def test_repository_rebind_rejects_placeholder_with_crossed_external_id_matches(repo, caplog):
    repo.save_order(OrderRecord(
        order_id="exchange-owner",
        exchange_order_id="X",
        client_order_id="",
        strategy="exchange-owner",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=1.0,
        price=100.0,
        status="pending",
        fill_price=0.0,
        timestamp=1,
    ))
    repo.save_order(OrderRecord(
        order_id="client-owner",
        exchange_order_id="",
        client_order_id="C",
        strategy="client-owner",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=1.0,
        price=100.0,
        status="pending",
        fill_price=0.0,
        timestamp=1,
    ))
    repo.save_trade(TradeRecord(
        exchange_trade_id="crossed-trade",
        exchange_order_id="X",
        client_order_id="C",
        order_id="okx:X",
        strategy="__exchange__",
        symbol="BTC-USDT",
        side="buy",
        amount=0.1,
        price=100.0,
        fee=0.0,
        timestamp=2,
    ))

    repo.rebind_trades_to_orders()

    placeholder = repo.get_trades()[0]
    assert (placeholder.order_id, placeholder.strategy) == ("okx:X", "__exchange__")
    assert "ambiguous" in caplog.text.lower()


def test_repository_rebinds_placeholder_trade_when_side_matches(repo):
    repo.save_order(OrderRecord(
        order_id="same-side-order", exchange_order_id="same-side-exchange", client_order_id="",
        strategy="same-side", symbol="BTC-USDT", side="sell", type="market",
        amount=1.0, price=100.0, status="pending", fill_price=0.0, timestamp=1,
    ))
    repo.save_trade(TradeRecord(
        exchange_order_id="same-side-exchange", order_id="okx:same-side-exchange",
        strategy="__exchange__", symbol="BTC-USDT", side="sell",
        amount=0.1, price=100.0, fee=0.0, timestamp=2,
    ))

    repo.rebind_trades_to_orders()

    rebound = repo.get_trades()[0]
    assert (rebound.order_id, rebound.strategy) == ("same-side-order", "same-side")


def test_repository_does_not_rebind_same_external_identity_with_opposite_side(repo, caplog):
    repo.save_order(OrderRecord(
        order_id="opposite-side-order", exchange_order_id="shared-id", client_order_id="",
        strategy="opposite-side", symbol="BTC-USDT", side="buy", type="market",
        amount=1.0, price=100.0, status="pending", fill_price=0.0, timestamp=1,
    ))
    repo.save_trade(TradeRecord(
        exchange_order_id="shared-id", order_id="okx:shared-id",
        strategy="__exchange__", symbol="BTC-USDT", side="sell",
        amount=0.1, price=100.0, fee=0.0, timestamp=2,
    ))

    repo.rebind_trades_to_orders()

    unmatched = repo.get_trades()[0]
    assert (unmatched.order_id, unmatched.strategy) == ("okx:shared-id", "__exchange__")
    assert "side conflict" in caplog.text


def test_repository_rebinds_alias_symbol_only_when_side_matches(repo):
    repo.save_order(OrderRecord(
        order_id="alias-side-order", exchange_order_id="alias-id", client_order_id="",
        strategy="alias-side", symbol="BTC/USDT", side="buy", type="market",
        amount=1.0, price=100.0, status="pending", fill_price=0.0, timestamp=1,
    ))
    repo.save_trade(TradeRecord(
        exchange_order_id="alias-id", order_id="okx:alias-id",
        strategy="__exchange__", symbol="BTC-USDT", side="buy",
        amount=0.1, price=100.0, fee=0.0, timestamp=2,
    ))

    repo.rebind_trades_to_orders()

    rebound = repo.get_trades()[0]
    assert (rebound.order_id, rebound.strategy) == ("alias-side-order", "alias-side")


def test_upsert_trade_returns_existing_record_when_unique_insert_races(
    repo: Repository, monkeypatch, caplog
):
    raced = TradeRecord(
        exchange_trade_id="race-trade", order_id="o2", strategy="other", symbol="ETH-USDT",
        side="sell", amount=2, price=200, fee=2, timestamp=2,
    )
    existing = TradeRecord(
        exchange_trade_id="race-trade", order_id="o1", strategy="s", symbol="BTC-USDT",
        side="buy", amount=1, price=100, fee=1, timestamp=1,
    )
    original_exec = repository_module.Session.exec
    inserted_by_competitor = False

    def interleave_competitor(session, statement, *args, **kwargs):
        nonlocal inserted_by_competitor
        result = original_exec(session, statement, *args, **kwargs)
        if not inserted_by_competitor and "exchange_trade_id" in str(statement):
            inserted_by_competitor = True
            repo.save_trade(existing)
            class EmptyResult:
                def first(self):
                    return None
            return EmptyResult()
        return result

    monkeypatch.setattr(repository_module.Session, "exec", interleave_competitor)

    result = repo.upsert_trade(raced)

    assert result.id != existing.id
    assert len(repo.get_trades(include_inactive=True)) == 2
    persisted = repo.get_trades(order_id="o1", include_inactive=True)[0]
    assert (
        persisted.strategy,
        persisted.symbol,
        persisted.side,
        persisted.amount,
        persisted.price,
        persisted.timestamp,
    ) == ("s", "BTC-USDT", "buy", 1.0, 100.0, 1)


def test_sqlite_migration_retains_existing_duplicate_event_keys_for_audit():
    engine = create_engine("sqlite:///:memory:", echo=False)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE riskeventrecord (
                    id INTEGER PRIMARY KEY,
                    event_type VARCHAR NOT NULL,
                    strategy VARCHAR NOT NULL,
                    reason_code VARCHAR NOT NULL,
                    reason VARCHAR NOT NULL,
                    payload JSON NOT NULL,
                    timestamp INTEGER NOT NULL,
                    event_key VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                "INSERT INTO riskeventrecord VALUES "
                "(2, 'risk_event', '', '', '', '{}', 2, 'duplicate'), "
                "(1, 'risk_event', '', '', '', '{}', 1, 'duplicate'), "
                "(3, 'risk_event', '', '', '', '{}', 3, 'unique')"
            )
        )

    Repository(engine)

    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT id, event_key FROM riskeventrecord ORDER BY id")
        ).all()
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO riskeventrecord "
                    "(event_type, strategy, reason_code, reason, payload, timestamp, event_key) "
                    "VALUES ('risk_event', '', '', '', '{}', 4, 'duplicate')"
                )
            )

    assert rows == [(1, "duplicate"), (2, "duplicate"), (3, "unique")]


def test_sqlite_migration_normalizes_kill_switch_and_risk_timestamps():
    engine = create_engine("sqlite:///:memory:", echo=False)
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE killswitchrecord (id INTEGER PRIMARY KEY, engaged BOOLEAN, "
            "reason VARCHAR, updated_at INTEGER)"
        ))
        connection.execute(text(
            "CREATE TABLE riskeventrecord (id INTEGER PRIMARY KEY, event_type VARCHAR NOT NULL, "
            "strategy VARCHAR NOT NULL, reason_code VARCHAR NOT NULL, reason VARCHAR NOT NULL, "
            "payload JSON NOT NULL, timestamp INTEGER NOT NULL, event_key VARCHAR)"
        ))
        connection.execute(text(
            "INSERT INTO killswitchrecord VALUES (1, 1, 'old', -1)"
        ))
        connection.execute(text(
            "INSERT INTO riskeventrecord VALUES (1, 'risk', '', '', '', '{}', -1, NULL)"
        ))

    Repository(engine)

    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT updated_at FROM killswitchrecord")
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT timestamp FROM riskeventrecord")
        ).scalar_one() == 0


def test_save_risk_event_persists_explicit_provenance(repo):
    saved = repo.save_risk_event({
        "type": "risk_event",
        "strategy": "alpha",
        "source": "live_exchange",
        "runtime_mode": "live",
        "event_key": "provenance:test",
        "timestamp": 10,
    })

    assert (saved.source, saved.runtime_mode) == ("live_exchange", "live")
    persisted = repo.get_risk_events()[0]
    assert (persisted.source, persisted.runtime_mode) == ("live_exchange", "live")


def test_save_risk_event_deduplicates_nonempty_event_key_and_allows_null_keys(repo):
    first = repo.save_risk_event(
        {"type": "risk_event", "event_key": "sync:event:1", "timestamp": 1}
    )
    second = repo.save_risk_event(
        {"type": "risk_event", "event_key": "sync:event:1", "timestamp": 2}
    )
    repo.save_risk_event({"type": "risk_event", "event_key": "sync:event:2", "timestamp": 3})
    repo.save_risk_event({"type": "risk_event", "timestamp": 4})
    repo.save_risk_event({"type": "risk_event", "timestamp": 5})

    events = repo.get_risk_events()
    assert second.id == first.id
    assert [event.event_key for event in events] == ["sync:event:1", "sync:event:2", None, None]


def test_save_risk_event_deduplicates_within_domain_but_not_across_domains(repo):
    event = {"type": "risk_event", "event_key": "same-key", "timestamp": 1}

    live_first = repo.save_risk_event(event, source="live_exchange", runtime_mode="live")
    live_repeat = repo.save_risk_event(event, source="live_exchange", runtime_mode="live")
    paper = repo.save_risk_event(event, source="paper", runtime_mode="paper")

    assert live_repeat.id == live_first.id
    assert paper.id != live_first.id
    assert len(repo.get_risk_events()) == 2
    assert {(row.source, row.runtime_mode) for row in repo.get_risk_events()} == {
        ("live_exchange", "live"), ("paper", "paper")
    }


def test_upsert_trade_deduplicates_exchange_trade_id(repo: Repository):
    trade = TradeRecord(
        exchange_trade_id="trade-1",
        order_id="okx-1",
        strategy="ma_cross",
        symbol="BTC/USDT",
        side="buy",
        amount=1.0,
        price=50000.0,
        fee=1.2,
        timestamp=1700000000000,
    )

    repo.upsert_trade(trade)
    repo.upsert_trade(trade)

    trades = repo.get_trades("ma_cross")

    assert len(trades) == 1
    assert trades[0].exchange_trade_id == "trade-1"


def test_upsert_trade_preserves_immutable_facts_when_replayed_with_same_id(repo, caplog):
    original = TradeRecord(
        exchange_trade_id="immutable-trade",
        exchange_order_id="exchange-order-1",
        client_order_id="client-order-1",
        order_id="order-1",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        amount=1.25,
        price=100.0,
        fee=0.5,
        timestamp=1700000000000,
    )
    repo.upsert_trade(original)

    replay = TradeRecord(
        exchange_trade_id="immutable-trade",
        exchange_order_id="exchange-order-wrong",
        client_order_id="client-order-wrong",
        order_id="order-wrong",
        strategy="other",
        symbol="ETH-USDT",
        side="sell",
        amount=9.0,
        price=999.0,
        fee=1.5,
        timestamp=1700000009999,
    )

    persisted = repo.upsert_trade(replay)

    assert persisted.id == original.id
    assert (
        persisted.order_id,
        persisted.strategy,
        persisted.symbol,
        persisted.side,
        persisted.amount,
        persisted.price,
        persisted.timestamp,
    ) == ("order-1", "ma_cross", "BTC-USDT", "buy", 1.25, 100.0, 1700000000000)
    assert persisted.exchange_order_id == "exchange-order-1"
    assert persisted.client_order_id == "client-order-1"
    assert "Trade identity immutable conflict" in caplog.text


def test_upsert_trade_fills_missing_exchange_ids_without_changing_facts(repo):
    repo.upsert_trade(
        TradeRecord(
            exchange_trade_id="identified-trade",
            exchange_order_id="",
            client_order_id="",
            order_id="order-1",
            strategy="ma_cross",
            symbol="BTC-USDT",
            side="buy",
            amount=1.25,
            price=100.0,
            fee=0.5,
            timestamp=1700000000000,
        )
    )

    persisted = repo.upsert_trade(
        TradeRecord(
            exchange_trade_id="identified-trade",
            exchange_order_id="exchange-order-1",
            client_order_id="client-order-1",
            order_id="wrong",
            strategy="other",
            symbol="ETH-USDT",
            side="sell",
            amount=9.0,
            price=999.0,
            fee=1.5,
            timestamp=1700000009999,
        )
    )

    assert persisted.exchange_order_id == "exchange-order-1"
    assert persisted.client_order_id == "client-order-1"
    assert persisted.amount == 1.25
    assert persisted.price == 100.0
    assert persisted.timestamp == 1700000000000


def test_create_strategy_config_inserts_and_duplicate_raises(repo: Repository):
    config = StrategyConfigRecord(
        name="ma_cross_btc",
        strategy_type="ma_cross",
        symbol="BTC-USDT",
        timeframe="1h",
        params={"fast_window": 5, "slow_window": 20},
        enabled=True,
        created_at=1700000000000,
        updated_at=1700000000000,
    )

    created = repo.create_strategy_config(config)

    assert created.id is not None
    assert repo.get_strategy_config("ma_cross_btc") is not None
    with pytest.raises(IntegrityError):
        repo.create_strategy_config(
            StrategyConfigRecord(
                name="ma_cross_btc",
                strategy_type="ma_cross",
                symbol="ETH-USDT",
                timeframe="4h",
                params={},
                enabled=False,
                created_at=1700000001000,
                updated_at=1700000001000,
            )
        )


def test_update_strategy_config_preserves_identity_and_replaces_params(repo: Repository):
    created = repo.create_strategy_config(
        StrategyConfigRecord(
            name="ma_cross_btc",
            strategy_type="ma_cross",
            symbol="BTC-USDT",
            timeframe="1h",
            params={"fast_window": 5, "slow_window": 20, "stale": True},
            enabled=True,
            created_at=1700000000000,
            updated_at=1700000000000,
        )
    )

    updated = repo.update_strategy_config(
        "ma_cross_btc",
        StrategyConfigRecord(
            name="ignored_new_name",
            strategy_type="rsi_mean_reversion",
            symbol="ETH-USDT",
            timeframe="15m",
            params={"period": 14},
            enabled=False,
            created_at=1,
            updated_at=1700000005000,
        ),
    )

    assert updated is not None
    assert updated.id == created.id
    assert updated.name == "ma_cross_btc"
    assert updated.created_at == 1700000000000
    assert updated.updated_at == 1700000005000
    assert updated.strategy_type == "rsi_mean_reversion"
    assert updated.symbol == "ETH-USDT"
    assert updated.timeframe == "15m"
    assert updated.params == {"period": 14}
    assert updated.enabled is False
    assert repo.update_strategy_config("missing", updated) is None


def test_delete_strategy_config_removes_only_config_not_history(repo: Repository):
    repo.create_strategy_config(
        StrategyConfigRecord(
            name="ma_cross_btc",
            strategy_type="ma_cross",
            symbol="BTC-USDT",
            timeframe="1h",
            params={},
            enabled=True,
            created_at=1700000000000,
            updated_at=1700000000000,
        )
    )
    repo.save_trade(
        TradeRecord(
            strategy="ma_cross_btc",
            symbol="BTC-USDT",
            side="buy",
            amount=0.1,
            price=50000.0,
            fee=1.0,
            timestamp=1700000000000,
        )
    )
    repo.save_order(
        OrderRecord(
            order_id="order-1",
            strategy="ma_cross_btc",
            symbol="BTC-USDT",
            side="buy",
            type="limit",
            amount=0.1,
            price=50000.0,
            status="filled",
            fill_price=50000.0,
            timestamp=1700000000000,
        )
    )
    repo.upsert_position(
        PositionRecord(
            strategy="ma_cross_btc",
            symbol="BTC-USDT",
            side="long",
            amount=0.1,
            entry_price=50000.0,
            leverage=1,
            timestamp=1700000000000,
        )
    )

    assert repo.delete_strategy_config("ma_cross_btc") is True
    assert repo.delete_strategy_config("ma_cross_btc") is False
    assert repo.get_strategy_config("ma_cross_btc") is None
    assert len(repo.get_trades("ma_cross_btc")) == 1
    assert len([order for order in repo.get_orders() if order.strategy == "ma_cross_btc"]) == 1


def test_upsert_order_replaces_quantity_group_for_newer_authoritative_snapshot(repo):
    repo.upsert_order(
        OrderRecord(
            order_id="amount-conflict", exchange_order_id="okx-amount-conflict",
            strategy="ma_cross", symbol="BTC-USDT", side="buy", type="limit",
            amount=1.0, price=100.0, status="partially_filled", fill_price=100.0,
            timestamp=10, updated_at=10, filled_amount=0.4, remaining_amount=0.6,
            snapshot_filled_amount=0.4,
            snapshot_updated_at=10,
        )
    )

    persisted = repo.upsert_order(
        OrderRecord(
            order_id="amount-conflict", exchange_order_id="okx-amount-conflict",
            strategy="ma_cross", symbol="BTC-USDT", side="buy", type="limit",
            amount=0.8, price=100.0, status="partially_filled", fill_price=101.0,
            timestamp=11, updated_at=11, filled_amount=0.2, remaining_amount=0.6,
            snapshot_filled_amount=0.2, snapshot_updated_at=11,
        )
    )

    assert (persisted.amount, persisted.filled_amount, persisted.remaining_amount) == (
        0.8, 0.2, 0.6,
    )


def test_upsert_order_rejects_invalid_authoritative_snapshot_amount(repo, caplog):
    original = repo.upsert_order(
        OrderRecord(
            order_id="invalid-amount", exchange_order_id="okx-invalid-amount",
            strategy="ma_cross", symbol="BTC-USDT", side="buy", type="limit",
            amount=1.0, price=100.0, status="partially_filled", fill_price=100.0,
            timestamp=10, updated_at=10, filled_amount=0.4, remaining_amount=0.6,
            snapshot_filled_amount=0.4,
        )
    )

    rejected = repo.upsert_order(
        OrderRecord(
            order_id="invalid-amount", exchange_order_id="okx-invalid-amount",
            strategy="ma_cross", symbol="BTC-USDT", side="buy", type="limit",
            amount=0.0, price=100.0, status="pending", fill_price=0.0,
            timestamp=11, updated_at=11, filled_amount=0.0, remaining_amount=0.0,
            snapshot_filled_amount=0.0, snapshot_updated_at=11,
        )
    )

    assert rejected.id == original.id
    assert (rejected.amount, rejected.filled_amount, rejected.remaining_amount) == (1.0, 0.4, 0.6)
    assert "invalid authoritative snapshot amount" in caplog.text


def test_sqlite_migration_sanitizes_legacy_order_timestamps_idempotently():
    engine = create_engine("sqlite:///:memory:", echo=False)
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE orderrecord (
                id INTEGER PRIMARY KEY, order_id VARCHAR NOT NULL,
                exchange_order_id VARCHAR NOT NULL, client_order_id VARCHAR NOT NULL,
                strategy VARCHAR NOT NULL, symbol VARCHAR NOT NULL, side VARCHAR NOT NULL,
                type VARCHAR NOT NULL, amount FLOAT NOT NULL, price FLOAT NOT NULL,
                status VARCHAR NOT NULL, fill_price FLOAT NOT NULL,
                timestamp INTEGER NOT NULL, updated_at INTEGER NOT NULL
            )
        """))
        connection.execute(text("""
            INSERT INTO orderrecord VALUES
            (1, 'invalid-time', '', '', 's', 'BTC-USDT', 'buy', 'limit', 1, 100,
             'pending', 0, 'nan', 'inf'),
            (2, 'overflow-time', '', '', 's', 'BTC-USDT', 'buy', 'limit', 1, 100,
             'pending', 0, -1, '9223372036854775808'),
            (3, 'valid-time', '', '', 's', 'BTC-USDT', 'buy', 'limit', 1, 100,
             'pending', 0, 12, 13)
        """))

    Repository(engine)
    Repository(engine)

    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT order_id, timestamp, updated_at FROM orderrecord ORDER BY id")
        ).all()
    assert rows == [("invalid-time", 0, 0), ("overflow-time", 0, 0), ("valid-time", 12, 13)]


def test_upsert_order_legacy_snapshot_preserves_local_fill_price_when_only_fill_regresses(repo):
    repo.upsert_order(
        OrderRecord(
            order_id="legacy-fill-price", exchange_order_id="okx-legacy-fill-price",
            strategy="ma_cross", symbol="BTC-USDT", side="buy", type="limit",
            amount=1.0, price=100.0, status="partially_filled", fill_price=123.0,
            timestamp=100, updated_at=200, filled_amount=0.6, remaining_amount=0.4,
        )
    )

    persisted = repo.upsert_order(
        OrderRecord(
            order_id="legacy-fill-price", exchange_order_id="okx-legacy-fill-price",
            strategy="ma_cross", symbol="BTC-USDT", side="buy", type="limit",
            amount=1.2, price=100.0, status="partially_filled", fill_price=99.0,
            timestamp=150, updated_at=150, filled_amount=0.2, remaining_amount=1.0,
            snapshot_updated_at=150,
        )
    )

    assert persisted.snapshot_updated_at == 150
    assert persisted.amount == pytest.approx(1.2)
    assert persisted.filled_amount == pytest.approx(0.6)
    assert persisted.remaining_amount == pytest.approx(0.6)
    assert persisted.fill_price == pytest.approx(123.0)
    assert persisted.status == "partially_filled"


def test_upsert_trade_keeps_distinct_reliable_ids_for_identical_order_fills(repo):
    repo.save_order(OrderRecord(
        order_id="order-1", exchange_order_id="exchange-order-1", client_order_id="client-order-1",
        strategy="s", symbol="BTC-USDT", side="buy", type="limit", amount=1.0,
        price=100.0, status="pending", fill_price=0.0, timestamp=100,
    ))
    for exchange_trade_id in ("reliable-trade-1", "reliable-trade-2"):
        repo.upsert_trade(TradeRecord(
            exchange_trade_id=exchange_trade_id,
            exchange_order_id="exchange-order-1",
            client_order_id="client-order-1",
            order_id="order-1",
            strategy="s",
            symbol="BTC-USDT",
            side="buy",
            amount=0.1,
            price=100.0,
            fee=0.01,
            timestamp=123,
        ))

    trades = repo.get_trades(order_id="order-1")

    assert [trade.exchange_trade_id for trade in trades] == [
        "reliable-trade-1",
        "reliable-trade-2",
    ]
    assert sum(trade.amount for trade in trades) == pytest.approx(0.2)


def test_upsert_trade_upgrades_blank_id_by_exchange_fingerprint(repo):
    blank = TradeRecord(
        exchange_trade_id="", exchange_order_id="okx-1", client_order_id="client-1",
        order_id="o1", strategy="s", symbol="BTC-USDT", side="buy", amount=0.1,
        price=100.0, fee=0.01, timestamp=123,
    )
    identified = TradeRecord(
        exchange_trade_id="trade-1", exchange_order_id="okx-1", client_order_id="client-1",
        order_id="o1", strategy="s", symbol="BTC-USDT", side="buy", amount=0.1,
        price=100.0, fee=0.01, timestamp=123,
    )

    repo.upsert_trade(blank)
    persisted = repo.upsert_trade(identified)

    assert persisted.exchange_trade_id == "trade-1"
    assert len(repo.get_trades()) == 1


def test_upsert_order_legacy_snapshot_records_provenance_without_regressing_fill_group(repo):
    repo.upsert_order(
        OrderRecord(
            order_id="legacy-snapshot",
            exchange_order_id="okx-legacy-snapshot",
            strategy="ma_cross",
            symbol="BTC-USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=100.0,
            status="partially_filled",
            fill_price=101.0,
            timestamp=100,
            updated_at=300,
            filled_amount=0.6,
            remaining_amount=0.4,
        )
    )

    incoming = OrderRecord(
        order_id="legacy-snapshot",
        exchange_order_id="okx-legacy-snapshot",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=0.8,
        price=100.0,
        status="partially_filled",
        fill_price=99.0,
        timestamp=200,
        updated_at=200,
        filled_amount=0.2,
        remaining_amount=0.6,
        snapshot_filled_amount=0.2,
        snapshot_updated_at=200,
    )
    incoming._snapshot_authoritative = True

    persisted = repo.upsert_order(incoming)

    assert persisted.snapshot_updated_at == 200
    assert (persisted.amount, persisted.filled_amount, persisted.remaining_amount) == (
        1.0,
        0.6,
        0.4,
    )
    assert persisted.fill_price == 101.0
    assert persisted.status == "partially_filled"


def test_save_trade_uses_external_order_fingerprint_but_keeps_idless_trades_distinct(repo):
    stable_blank = TradeRecord(
        exchange_trade_id="",
        exchange_order_id="okx-stable",
        client_order_id="client-stable",
        order_id="o1",
        strategy="s",
        symbol="BTC-USDT",
        side="buy",
        amount=0.1,
        price=100.0,
        fee=0.01,
        timestamp=123,
    )

    repo.save_trade(stable_blank)
    repo.save_trade(
        TradeRecord(
            exchange_order_id="okx-stable",
            client_order_id="client-stable",
            order_id="o1",
            strategy="s",
            symbol="BTC-USDT",
            side="buy",
            amount=0.1,
            price=100.0,
            fee=0.01,
            timestamp=123,
        )
    )

    assert len(repo.get_trades()) == 1

    repo.save_trade(
        TradeRecord(
            exchange_order_id="",
            client_order_id="",
            order_id="o1",
            strategy="s",
            symbol="BTC-USDT",
            side="buy",
            amount=0.1,
            price=100.0,
            fee=0.01,
            timestamp=123,
        )
    )
    repo.save_trade(
        TradeRecord(
            exchange_order_id="",
            client_order_id="",
            order_id="o1",
            strategy="s",
            symbol="BTC-USDT",
            side="buy",
            amount=0.1,
            price=100.0,
            fee=0.01,
            timestamp=123,
        )
    )
    assert len(repo.get_trades()) == 3


def test_save_trade_upgrades_blank_trade_when_reliable_exchange_trade_id_arrives(repo):
    repo.save_trade(
        TradeRecord(
            exchange_trade_id="",
            exchange_order_id="okx-upgrade",
            order_id="o1",
            strategy="s",
            symbol="BTC-USDT",
            side="buy",
            amount=0.1,
            price=100.0,
            fee=0.01,
            timestamp=123,
        )
    )

    persisted = repo.save_trade(
        TradeRecord(
            exchange_trade_id="trade-identified",
            exchange_order_id="okx-upgrade",
            order_id="o1",
            strategy="s",
            symbol="BTC-USDT",
            side="buy",
            amount=0.1,
            price=100.0,
            fee=0.01,
            timestamp=123,
        )
    )

    assert persisted.exchange_trade_id == "trade-identified"
    assert len(repo.get_trades()) == 1


def test_sqlite_migration_normalizes_legacy_order_prices_and_fill_prices():
    engine = create_engine("sqlite:///:memory:", echo=False)
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE orderrecord (
                id INTEGER PRIMARY KEY, order_id VARCHAR NOT NULL,
                exchange_order_id VARCHAR NOT NULL, client_order_id VARCHAR NOT NULL,
                strategy VARCHAR NOT NULL, symbol VARCHAR NOT NULL, side VARCHAR NOT NULL,
                type VARCHAR NOT NULL, amount FLOAT NOT NULL, price FLOAT NOT NULL,
                status VARCHAR NOT NULL, fill_price FLOAT NOT NULL,
                timestamp INTEGER NOT NULL, updated_at INTEGER NOT NULL
            )
        """))
        connection.execute(text("""
            INSERT INTO orderrecord VALUES
            (1, 'nan', '', '', 's', 'BTC-USDT', 'buy', 'limit', 1, 'nan',
             'pending', 'nan', 1, 1),
            (2, 'inf', '', '', 's', 'BTC-USDT', 'buy', 'limit', 1, 'inf',
             'pending', '-inf', 2, 2),
            (3, 'negative', '', '', 's', 'BTC-USDT', 'buy', 'limit', 1, -100,
             'pending', -1, 3, 3),
            (4, 'invalid', '', '', 's', 'BTC-USDT', 'buy', 'limit', 1, 'bad',
             'pending', 'bad', 4, 4),
            (5, 'valid', '', '', 's', 'BTC-USDT', 'buy', 'limit', 1, 100,
             'filled', 101, 5, 5)
        """))

    first = Repository(engine)
    Repository(engine)

    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT order_id, price, fill_price FROM orderrecord ORDER BY id")
        ).all()

    assert rows == [
        ("nan", 0.0, 0.0),
        ("inf", 0.0, 0.0),
        ("negative", 0.0, 0.0),
        ("invalid", 0.0, 0.0),
        ("valid", 100.0, 101.0),
    ]
    assert [
        (order.price, order.fill_price) for order in first.get_orders()
    ] == [(price, fill_price) for _, price, fill_price in rows]


def test_sqlite_migration_normalizes_legacy_order_prices_when_columns_are_text():
    engine = create_engine("sqlite:///:memory:", echo=False)
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE orderrecord (
                id INTEGER PRIMARY KEY, order_id VARCHAR NOT NULL,
                exchange_order_id VARCHAR NOT NULL, client_order_id VARCHAR NOT NULL,
                strategy VARCHAR NOT NULL, symbol VARCHAR NOT NULL, side VARCHAR NOT NULL,
                type VARCHAR NOT NULL, amount FLOAT NOT NULL, price FLOAT NOT NULL,
                status VARCHAR NOT NULL, fill_price FLOAT NOT NULL,
                timestamp INTEGER NOT NULL, updated_at INTEGER NOT NULL
            )
        """))
        connection.execute(text("""
            INSERT INTO orderrecord VALUES
            (1, 'text-invalid', '', '', 's', 'BTC-USDT', 'buy', 'limit', 1, 'bad',
             'pending', 'nan', 1, 1),
            (2, 'text-valid', '', '', 's', 'BTC-USDT', 'buy', 'limit', 1, '100.5',
             'filled', '101.5', 2, 2)
        """))

    Repository(engine)

    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT price, fill_price FROM orderrecord ORDER BY id")
        ).all()

    assert rows == [(0.0, 0.0), (100.5, 101.5)]


def test_upsert_trade_re_normalizes_mutated_record_before_persist(repo):
    trade = TradeRecord(
        exchange_trade_id="trade-mutate",
        exchange_order_id="order-mutate",
        client_order_id="client-mutate",
        order_id="local-mutate",
        strategy="s",
        symbol="BTC-USDT",
        side="buy",
        amount=1.0,
        price=100.0,
        fee=1.0,
        timestamp=10,
    )
    trade.amount = float("nan")
    trade.price = float("inf")
    trade.fee = -1.0
    trade.timestamp = 2**63
    trade.exchange_trade_id = " trade-mutate "
    trade.blank_trade_fingerprint = "stale"

    saved = repo.upsert_trade(trade)

    assert (saved.amount, saved.price, saved.fee, saved.timestamp) == (0.0, 0.0, 0.0, 0)
    assert saved.exchange_trade_id == "trade-mutate"
    assert saved.blank_trade_fingerprint != "stale"
    persisted = repo.get_trades()[0]
    assert (persisted.amount, persisted.price, persisted.fee, persisted.timestamp) == (
        0.0, 0.0, 0.0, 0
    )


def test_upsert_position_re_normalizes_mutated_record_before_persist(repo):
    position = PositionRecord(
        strategy="s",
        symbol="BTC-USDT",
        side="long",
        amount=1.0,
        entry_price=100.0,
        leverage=2,
        timestamp=10,
    )
    position.amount = float("nan")
    position.entry_price = float("inf")
    position.mark_price = float("nan")
    position.realized_pnl = float("inf")
    position.unrealized_pnl = float("nan")
    position.leverage = 2**63
    position.timestamp = 2**63

    saved = repo.upsert_position(position)

    assert (saved.amount, saved.entry_price, saved.mark_price) == (0.0, 0.0, None)
    assert (saved.realized_pnl, saved.unrealized_pnl, saved.leverage, saved.timestamp) == (
        0.0, 0.0, 1, 0
    )
    loaded = repo.get_position("s", "BTC-USDT")
    assert loaded is not None
    assert loaded.amount == 0.0


def test_upsert_trade_merges_client_only_blank_with_exchange_only_identified_trade(
    repo,
):
    repo.save_order(OrderRecord(
        order_id="local-order-1",
        exchange_order_id="exchange-order-1",
        client_order_id="client-order-1",
        strategy="s",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=1.0,
        price=100.0,
        status="filled",
        fill_price=100.0,
        timestamp=100,
    ))
    repo.upsert_trade(TradeRecord(
        exchange_trade_id="",
        exchange_order_id="",
        client_order_id="client-order-1",
        order_id="okx:exchange-order-1",
        strategy="__exchange__",
        symbol="BTC-USDT",
        side="buy",
        amount=0.1,
        price=100.0,
        fee=0.01,
        timestamp=123,
    ))

    persisted = repo.upsert_trade(TradeRecord(
        exchange_trade_id="trade-upgrade",
        exchange_order_id="exchange-order-1",
        client_order_id="",
        order_id="local-order-1",
        strategy="s",
        symbol="BTC-USDT",
        side="buy",
        amount=0.1,
        price=100.0,
        fee=0.01,
        timestamp=123,
    ))

    assert persisted.exchange_trade_id == "trade-upgrade"
    assert persisted.exchange_order_id == "exchange-order-1"
    assert persisted.client_order_id == "client-order-1"
    assert persisted.order_id == "local-order-1"
    assert len(repo.get_trades()) == 1


def test_upsert_trade_matches_client_only_blank_trade_after_exchange_id_upgrade(
    repo,
):
    repo.save_order(OrderRecord(
        order_id="local-order-1",
        exchange_order_id="exchange-order-1",
        client_order_id="client-order-1",
        strategy="s",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=1.0,
        price=100.0,
        status="filled",
        fill_price=100.0,
        timestamp=100,
    ))
    repo.upsert_trade(TradeRecord(
        exchange_trade_id="",
        exchange_order_id="",
        client_order_id="client-order-1",
        order_id="okx:exchange-order-1",
        strategy="__exchange__",
        symbol="BTC-USDT",
        side="buy",
        amount=0.1,
        price=100.0,
        fee=0.01,
        timestamp=123,
    ))

    persisted = repo.upsert_trade(TradeRecord(
        exchange_trade_id="trade-upgrade",
        exchange_order_id="exchange-order-1",
        client_order_id="",
        order_id="local-order-1",
        strategy="s",
        symbol="BTC-USDT",
        side="buy",
        amount=0.1,
        price=100.0,
        fee=0.01,
        timestamp=123,
    ))

    assert persisted.exchange_trade_id == "trade-upgrade"
    assert persisted.exchange_order_id == "exchange-order-1"
    assert persisted.client_order_id == "client-order-1"
    assert persisted.order_id == "local-order-1"
    assert len(repo.get_trades()) == 1


def test_upsert_trade_matches_exchange_only_blank_trade_after_client_id_upgrade(
    repo,
):
    repo.save_order(OrderRecord(
        order_id="local-order-1",
        exchange_order_id="exchange-order-1",
        client_order_id="client-order-1",
        strategy="s",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=1.0,
        price=100.0,
        status="filled",
        fill_price=100.0,
        timestamp=100,
    ))
    repo.upsert_trade(TradeRecord(
        exchange_trade_id="",
        exchange_order_id="exchange-order-1",
        client_order_id="",
        order_id="okx:exchange-order-1",
        strategy="__exchange__",
        symbol="BTC-USDT",
        side="buy",
        amount=0.1,
        price=100.0,
        fee=0.01,
        timestamp=123,
    ))

    persisted = repo.upsert_trade(TradeRecord(
        exchange_trade_id="trade-upgrade",
        exchange_order_id="",
        client_order_id="client-order-1",
        order_id="local-order-1",
        strategy="s",
        symbol="BTC-USDT",
        side="buy",
        amount=0.1,
        price=100.0,
        fee=0.01,
        timestamp=123,
    ))

    assert persisted.exchange_trade_id == "trade-upgrade"
    assert persisted.exchange_order_id == "exchange-order-1"
    assert persisted.client_order_id == "client-order-1"
    assert len(repo.get_trades()) == 1


def test_upsert_trade_does_not_mix_aliases_from_different_orders(repo):
    for suffix in ("1", "2"):
        repo.save_order(OrderRecord(
            order_id=f"local-order-{suffix}",
            exchange_order_id=f"exchange-order-{suffix}",
            client_order_id=f"client-order-{suffix}",
            strategy="s",
            symbol="BTC-USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=100.0,
            status="filled",
            fill_price=100.0,
            timestamp=100,
        ))
    repo.upsert_trade(TradeRecord(
        exchange_trade_id="",
        exchange_order_id="",
        client_order_id="client-order-1",
        order_id="okx:exchange-order-1",
        strategy="__exchange__",
        symbol="BTC-USDT",
        side="buy",
        amount=0.1,
        price=100.0,
        fee=0.01,
        timestamp=123,
    ))

    persisted = repo.upsert_trade(TradeRecord(
        exchange_trade_id="trade-order-2",
        exchange_order_id="exchange-order-2",
        client_order_id="",
        order_id="local-order-2",
        strategy="s",
        symbol="BTC-USDT",
        side="buy",
        amount=0.1,
        price=100.0,
        fee=0.01,
        timestamp=123,
    ))

    assert persisted.exchange_trade_id == "trade-order-2"
    assert len(repo.get_trades()) == 2


def test_upsert_trade_retries_after_integrity_error_and_merges_competing_blank(repo, monkeypatch):
    blank = TradeRecord(
        exchange_trade_id="",
        exchange_order_id="",
        client_order_id="client-race",
        order_id="okx:exchange-race",
        strategy="__exchange__",
        symbol="BTC-USDT",
        side="buy",
        amount=0.1,
        price=100.0,
        fee=0.01,
        timestamp=123,
    )
    repo.save_order(OrderRecord(
        order_id="local-race",
        exchange_order_id="exchange-race",
        client_order_id="client-race",
        strategy="s",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=1.0,
        price=100.0,
        status="filled",
        fill_price=100.0,
        timestamp=100,
    ))
    incoming = TradeRecord(
        exchange_trade_id="trade-race",
        exchange_order_id="exchange-race",
        client_order_id="",
        order_id="local-race",
        strategy="s",
        symbol="BTC-USDT",
        side="buy",
        amount=0.1,
        price=100.0,
        fee=0.01,
        timestamp=123,
    )
    original_commit = repository_module.Session.commit
    state = {"raised": False}

    def competing_commit(session):
        if not state["raised"]:
            state["raised"] = True
            repo.save_trade(blank)
            raise IntegrityError("simulated race", {}, Exception("unique"))
        return original_commit(session)

    monkeypatch.setattr(repository_module.Session, "commit", competing_commit)

    persisted = repo.upsert_trade(incoming)

    assert persisted.exchange_trade_id == "trade-race"
    assert len(repo.get_trades()) == 1


def test_upsert_trade_matches_client_only_blank_fingerprint_after_exchange_id_upgrade(repo):
    repo.upsert_trade(TradeRecord(
        exchange_trade_id="",
        exchange_order_id="",
        client_order_id="client-upgrade",
        order_id="o1",
        strategy="s",
        symbol="BTC-USDT",
        side="buy",
        amount=0.1,
        price=100.0,
        fee=0.01,
        timestamp=123,
    ))

    persisted = repo.upsert_trade(TradeRecord(
        exchange_trade_id="trade-upgrade",
        exchange_order_id="exchange-order-1",
        client_order_id="client-upgrade",
        order_id="o1",
        strategy="s",
        symbol="BTC-USDT",
        side="buy",
        amount=0.1,
        price=100.0,
        fee=0.01,
        timestamp=123,
    ))

    assert persisted.exchange_trade_id == "trade-upgrade"
    assert len(repo.get_trades()) == 1


def test_sqlite_migration_groups_accounts_by_explicit_domain():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO accountrecord "
            "(id, strategy, source, runtime_mode, initial_equity, cash_balance, "
            "available_balance, equity, realized_pnl, unrealized_pnl, daily_pnl, "
            "fees_paid, updated_at, active) VALUES "
            "(1, 'same', 'paper', 'paper', 100, 100, 100, 100, 0, 0, 0, 0, 10, 1), "
            "(2, 'same', 'paper', 'paper', 110, 110, 110, 110, 0, 0, 0, 0, 20, 1), "
            "(3, 'same', 'live_exchange', 'live', 200, 200, 200, 200, 0, 0, 0, 0, 10, 1), "
            "(4, 'same', 'live_exchange', 'live', 210, 210, 210, 210, 0, 0, 0, 0, 20, 1)"
        ))

    Repository(engine)

    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT id, source, runtime_mode, active FROM accountrecord ORDER BY id"
        )).all()
        indexes = connection.execute(text("PRAGMA index_list(accountrecord)")).all()

    assert rows == [
        (1, "paper", "paper", 0),
        (2, "paper", "paper", 1),
        (3, "live_exchange", "live", 0),
        (4, "live_exchange", "live", 1),
    ]
    assert any(row[1] == "uq_accountrecord_active_strategy" for row in indexes)


def test_sqlite_migration_retains_position_history_and_adds_active_unique_index():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO positionrecord "
            "(id, strategy, symbol, side, amount, entry_price, leverage, timestamp, "
            "realized_pnl, unrealized_pnl) VALUES "
            "(1, 's', 'BTC-USDT', 'long', 1, 100, 1, 10, 0, 0), "
            "(2, 's', 'BTC-USDT', 'long', 2, 200, 1, 20, 0, 0), "
            "(3, 's', 'BTC-USDT', 'short', 3, 300, 1, 15, 0, 0)"
        ))
    Repository(engine)
    Repository(engine)

    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT id, side, amount, active FROM positionrecord ORDER BY id"
        )).all()
        indexes = connection.execute(text("PRAGMA index_list(positionrecord)")).all()

    assert rows == [(1, "long", 1.0, 0), (2, "long", 2.0, 1), (3, "short", 3.0, 1)]
    assert any(row[1] == "uq_positionrecord_active_identity" for row in indexes)
    assert not any(row[1] == "uq_positionrecord_strategy_symbol" for row in indexes)


def test_sqlite_migration_groups_positions_by_domain_and_canonical_symbol():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO positionrecord "
            "(id, strategy, symbol, side, source, runtime_mode, amount, entry_price, "
            "leverage, timestamp, realized_pnl, unrealized_pnl, active) VALUES "
            "(1, 'same', 'BTC/USDT:USDT', 'long', 'paper', 'paper', 1, 100, 1, 10, 0, 0, 1), "
            "(2, 'same', 'BTC-USDT-SWAP', 'long', 'paper', 'paper', 2, 200, 1, 20, 0, 0, 1), "
            "(3, 'same', 'BTC-USDT-SWAP', 'long', 'live_exchange', 'live', "
            "3, 300, 1, 10, 0, 0, 1), "
            "(4, 'same', 'BTC-USDT-SWAP', 'long', 'live_exchange', 'live', 4, 400, 1, 20, 0, 0, 1)"
        ))

    Repository(engine)

    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT id, symbol, source, runtime_mode, active FROM positionrecord ORDER BY id"
        )).all()

    assert rows == [
        (1, "BTC/USDT:USDT", "paper", "paper", 1),
        (2, "BTC-USDT-SWAP", "paper", "paper", 1),
        (3, "BTC-USDT-SWAP", "live_exchange", "live", 0),
        (4, "BTC-USDT-SWAP", "live_exchange", "live", 1),
    ]


def test_sqlite_migration_preserves_legacy_position_symbol_for_audit():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO positionrecord "
            "(id, strategy, symbol, side, amount, entry_price, leverage, timestamp, "
            "realized_pnl, unrealized_pnl) VALUES "
            "(1, 's', 'BTC/USDT:USDT', 'long', 1, 100, 1, 10, 0, 0)"
        ))

    Repository(engine)

    with engine.connect() as connection:
        row = connection.execute(text(
            "SELECT symbol, active, source, runtime_mode FROM positionrecord WHERE id = 1"
        )).one()
    assert row == ("BTC/USDT:USDT", 1, "unknown", "unknown")


def test_sqlite_migration_canonicalizes_position_aliases_and_keeps_latest_per_identity():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO positionrecord "
            "(id, strategy, symbol, side, amount, entry_price, leverage, timestamp, "
            "realized_pnl, unrealized_pnl) VALUES "
            "(1, 's', 'BTC/USDT:USDT', 'long', 1, 100, 1, 10, 0, 0), "
            "(2, 's', 'BTC-USDT-SWAP', 'long', 2, 200, 1, 20, 0, 0), "
            "(3, 's', 'BTC/USDT:USDT-260925', 'long', 3, 300, 1, 30, 0, 0), "
            "(4, 's', 'BTC-USDT-261225', 'long', 4, 400, 1, 40, 0, 0)"
        ))

    Repository(engine)
    Repository(engine)

    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT id, symbol, amount, active FROM positionrecord ORDER BY id"
        )).all()

    assert rows == [
        (1, "BTC/USDT:USDT", 1.0, 1),
        (2, "BTC-USDT-SWAP", 2.0, 1),
        (3, "BTC/USDT:USDT-260925", 3.0, 1),
        (4, "BTC-USDT-261225", 4.0, 1),
    ]


def test_sqlite_migration_replaces_legacy_position_unique_index_idempotently():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO positionrecord "
            "(id, strategy, symbol, side, amount, entry_price, leverage, timestamp, "
            "realized_pnl, unrealized_pnl) "
            "VALUES (1, 's', 'BTC-USDT', 'long', 1, 100, 1, 10, 0, 0)"
        ))
        connection.execute(text(
            "CREATE UNIQUE INDEX uq_positionrecord_strategy_symbol "
            "ON positionrecord (strategy, symbol)"
        ))

    Repository(engine)
    Repository(engine)

    with engine.connect() as connection:
        indexes = connection.execute(text("PRAGMA index_list(positionrecord)")).all()

    assert any(row[1] == "uq_positionrecord_active_identity" for row in indexes)
    assert not any(row[1] == "uq_positionrecord_strategy_symbol" for row in indexes)


def test_delete_position_archives_only_current_domain_and_retains_audit_rows(repo: Repository):
    legacy = repo.save_position(PositionRecord(
        strategy="same", symbol="BTC-USDT", side="long", amount=9,
        entry_price=90, leverage=1, timestamp=1,
    ))
    current = repo.upsert_position(PositionRecord(
        strategy="same", symbol="BTC-USDT", side="long", amount=1,
        entry_price=100, leverage=1, timestamp=2,
        source="paper", runtime_mode="paper",
    ))

    repo.delete_position(
        "same", "BTC-USDT", side="long", source="paper", runtime_mode="paper",
        stale_at=99,
    )

    assert repo.get_position(
        "same", "BTC-USDT", side="long", source="paper", runtime_mode="paper"
    ) is None
    rows = repo.get_positions("same", "BTC-USDT", include_inactive=True)
    assert {row.id for row in rows} == {legacy.id, current.id}
    archived = next(row for row in rows if row.id == current.id)
    assert archived.active is False
    assert archived.stale_at == 99
    assert next(row for row in rows if row.id == legacy.id).active is True


def test_delete_position_refuses_unscoped_archive(repo: Repository):
    repo.upsert_position(PositionRecord(
        strategy="same", symbol="BTC-USDT", side="long", amount=1,
        entry_price=100, leverage=1, timestamp=1,
        source="paper", runtime_mode="paper",
    ))

    repo.delete_position("same", "BTC-USDT", side="long")

    assert repo.get_position(
        "same", "BTC-USDT", side="long", source="paper", runtime_mode="paper"
    ).amount == 1


def test_delete_position_requires_matching_domain(repo: Repository):
    repo.upsert_position(PositionRecord(
        strategy="same", symbol="BTC-USDT", side="long", amount=1,
        entry_price=100, leverage=1, timestamp=1,
        source="paper", runtime_mode="paper",
    ))
    repo.upsert_position(PositionRecord(
        strategy="same", symbol="BTC-USDT", side="long", amount=2,
        entry_price=200, leverage=1, timestamp=1,
        source="live_exchange", runtime_mode="live",
    ))

    repo.delete_position(
        "same", "BTC-USDT", side="long", source="paper", runtime_mode="paper"
    )

    assert repo.get_position(
        "same", "BTC-USDT", side="long", source="paper", runtime_mode="paper"
    ) is None
    assert repo.get_position(
        "same", "BTC-USDT", side="long", source="live_exchange", runtime_mode="live"
    ).amount == 2


def test_upsert_position_keeps_long_and_short_separate_and_rejects_stale_snapshots(repo):
    long_position = repo.upsert_position(PositionRecord(
        strategy="s", symbol="BTC-USDT", side="long", amount=2.0,
        entry_price=100.0, leverage=1, timestamp=20, realized_pnl=4.0,
    ))
    short_position = repo.upsert_position(PositionRecord(
        strategy="s", symbol="BTC-USDT", side="short", amount=3.0,
        entry_price=200.0, leverage=2, timestamp=20, realized_pnl=-2.0,
    ))

    stale = repo.upsert_position(PositionRecord(
        strategy="s", symbol="BTC-USDT", side="long", amount=0.5,
        entry_price=50.0, leverage=1, timestamp=19, realized_pnl=-99.0,
    ))

    assert long_position.id != short_position.id
    assert stale.id == long_position.id
    assert repo.get_position("s", "BTC-USDT", side="long").amount == 2.0
    assert repo.get_position("s", "BTC-USDT", side="short").amount == 3.0
    assert repo.get_position("s", "BTC-USDT", side="long").realized_pnl == 4.0


def test_sqlite_migration_retains_account_history_and_adds_active_unique_index():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO accountrecord "
            "(id, strategy, initial_equity, cash_balance, available_balance, equity, "
            "realized_pnl, unrealized_pnl, daily_pnl, fees_paid, updated_at) VALUES "
            "(1, 's', 1, 1, 0, 1, 0, 0, 0, 0, 10), "
            "(2, 's', 2, 2, 0, 2, 0, 0, 0, 0, 20)"
        ))

    Repository(engine)

    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT id, equity, active FROM accountrecord ORDER BY id"
        )).all()
        indexes = connection.execute(text("PRAGMA index_list(accountrecord)")).all()

    assert rows == [(1, 1.0, 0), (2, 2.0, 1)]
    assert any(row[1] == "uq_accountrecord_active_strategy" for row in indexes)


def test_upsert_position_returns_competitor_when_insert_races(repo, monkeypatch):
    incoming = PositionRecord(
        strategy="race", symbol="BTC-USDT", side="long", amount=1,
        entry_price=100, leverage=1, timestamp=2,
    )
    competitor = PositionRecord(
        strategy="race", symbol="BTC-USDT", side="long", amount=2,
        entry_price=200, leverage=1, timestamp=3,
    )
    original_exec = repository_module.Session.exec
    inserted = False

    def interleave(session, statement, *args, **kwargs):
        nonlocal inserted
        result = original_exec(session, statement, *args, **kwargs)
        if not inserted and "positionrecord" in str(statement) and "WHERE" in str(statement):
            inserted = True
            repo.save_position(competitor)
            class EmptyResult:
                def first(self):
                    return None
            return EmptyResult()
        return result

    monkeypatch.setattr(repository_module.Session, "exec", interleave)
    result = repo.upsert_position(incoming)

    assert result.id == competitor.id
    assert result.timestamp == 3
    assert result.amount == 2
    assert len(repo.get_positions()) == 1


def test_upsert_position_equal_timestamp_overwrites_competitor_snapshot(repo):
    repo.upsert_position(PositionRecord(
        strategy="equal", symbol="BTC-USDT", side="long", amount=1,
        entry_price=100, leverage=1, timestamp=5,
    ))
    result = repo.upsert_position(PositionRecord(
        strategy="equal", symbol="BTC-USDT", side="long", amount=2,
        entry_price=200, leverage=2, timestamp=5,
    ))

    assert result.amount == 2
    assert result.entry_price == 200
    assert result.leverage == 2


def test_atomic_accounting_reuses_account_monotonic_merge_rules(repo):
    repo.upsert_account(AccountRecord(
        strategy="atomic-account",
        initial_equity=100.0,
        cash_balance=90.0,
        equity=110.0,
        realized_pnl=10.0,
        unrealized_pnl=0.0,
        daily_pnl=10.0,
        fees_paid=1.0,
        updated_at=20,
    ))

    repo.atomic_accounting(lambda tx: tx.upsert_account(AccountRecord(
        strategy="atomic-account",
        initial_equity=1.0,
        cash_balance=1.0,
        equity=1.0,
        realized_pnl=1.0,
        unrealized_pnl=1.0,
        daily_pnl=1.0,
        fees_paid=99.0,
        updated_at=19,
    )))

    account = repo.get_account("atomic-account")
    assert account.updated_at == 20
    assert account.cash_balance == 90.0
    assert account.equity == 110.0


def test_atomic_accounting_reuses_position_monotonic_merge_rules(repo):
    repo.upsert_position(PositionRecord(
        strategy="atomic-position",
        symbol="BTC-USDT",
        side="long",
        amount=2.0,
        entry_price=100.0,
        leverage=1,
        timestamp=20,
    ))

    repo.atomic_accounting(lambda tx: tx.upsert_position(PositionRecord(
        strategy="atomic-position",
        symbol="BTC-USDT",
        side="long",
        amount=1.0,
        entry_price=50.0,
        leverage=2,
        timestamp=19,
    )))

    position = repo.get_position("atomic-position", "BTC-USDT", side="long")
    assert position.timestamp == 20
    assert position.amount == 2.0
    assert position.entry_price == 100.0


def test_upsert_account_returns_competitor_when_insert_races(repo, monkeypatch):
    incoming = AccountRecord(
        strategy="account-race", initial_equity=2, cash_balance=2, equity=2,
        realized_pnl=0, unrealized_pnl=0, daily_pnl=0, fees_paid=0, updated_at=2,
    )
    competitor = AccountRecord(
        strategy="account-race", initial_equity=1, cash_balance=1, equity=1,
        realized_pnl=0, unrealized_pnl=0, daily_pnl=0, fees_paid=0, updated_at=1,
    )
    original_exec = repository_module.Session.exec
    inserted = False

    def interleave(session, statement, *args, **kwargs):
        nonlocal inserted
        result = original_exec(session, statement, *args, **kwargs)
        if not inserted and "accountrecord" in str(statement) and "WHERE" in str(statement):
            inserted = True
            repo.upsert_account(competitor)
            class EmptyResult:
                def first(self):
                    return None
            return EmptyResult()
        return result

    monkeypatch.setattr(repository_module.Session, "exec", interleave)
    result = repo.upsert_account(incoming)

    assert result.id == competitor.id
    assert len(repo.get_accounts()) == 1


def test_sqlite_migration_normalizes_legacy_position_values():
    engine = create_engine("sqlite:///:memory:", echo=False)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE positionrecord (
                    id INTEGER PRIMARY KEY, strategy VARCHAR NOT NULL, symbol VARCHAR NOT NULL,
                    side VARCHAR NOT NULL, amount FLOAT NOT NULL, entry_price FLOAT NOT NULL,
                    leverage INTEGER NOT NULL, timestamp INTEGER NOT NULL, mark_price FLOAT,
                    realized_pnl FLOAT NOT NULL, unrealized_pnl FLOAT NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                "INSERT INTO positionrecord VALUES "
                "(1, 's', 'BTC-USDT-SWAP', 'short', -2, 'inf', -3, -1, 'nan', 'nan', 'inf'), "
                "(2, 's', 'ETH-USDT-SWAP', 'long', 1.5, 100, 5, 9223372036854775807, 101, -2, 3)"
            )
        )

    repo = Repository(engine)

    with engine.connect() as connection:
        raw_rows = connection.execute(
            text(
                "SELECT amount, entry_price, mark_price, realized_pnl, unrealized_pnl, "
                "leverage, timestamp FROM positionrecord ORDER BY id"
            )
        ).all()

    assert raw_rows == [
        (2.0, 0.0, None, 0.0, 0.0, 1, 0),
        (1.5, 100.0, 101.0, -2.0, 3.0, 5, 2**63 - 1),
    ]
    assert [
        (
            position.side,
            position.amount,
            position.entry_price,
            position.mark_price,
            position.realized_pnl,
            position.unrealized_pnl,
            position.leverage,
            position.timestamp,
        )
        for position in repo.get_positions()
    ] == [
        ("short", 2.0, 0.0, None, 0.0, 0.0, 1, 0),
        ("long", 1.5, 100.0, 101.0, -2.0, 3.0, 5, 2**63 - 1),
    ]


def test_upsert_account_keeps_initial_equity_as_first_snapshot_baseline(repo: Repository):
    repo.upsert_account(
        AccountRecord(
            strategy="baseline",
            initial_equity=100.0,
            cash_balance=100.0,
            equity=100.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            daily_pnl=0.0,
            fees_paid=0.0,
            updated_at=1,
        )
    )

    saved = repo.upsert_account(
        AccountRecord(
            strategy="baseline",
            initial_equity=999.0,
            cash_balance=120.0,
            equity=120.0,
            realized_pnl=20.0,
            unrealized_pnl=0.0,
            daily_pnl=20.0,
            fees_paid=0.0,
            updated_at=2,
        )
    )

    assert saved.initial_equity == 100.0
    assert repo.get_account("baseline").initial_equity == 100.0


def test_persisted_records_carry_explicit_provenance_and_unknown_legacy_audit(repo):
    account = repo.upsert_account(AccountRecord(
        strategy="paper-a",
        initial_equity=100.0,
        cash_balance=100.0,
        equity=100.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        daily_pnl=0.0,
        fees_paid=0.0,
        updated_at=1,
        source="paper",
        runtime_mode="paper",
    ))
    position = repo.upsert_position(PositionRecord(
        strategy="paper-a", symbol="BTC-USDT", side="long", amount=1.0,
        entry_price=100.0, leverage=1, timestamp=1,
        source="paper", runtime_mode="paper",
    ))
    order = repo.upsert_order(OrderRecord(
        order_id="paper-order", strategy="paper-a", symbol="BTC-USDT", side="buy",
        type="market", amount=1.0, price=100.0, status="filled", fill_price=100.0,
        timestamp=1, source="paper", runtime_mode="paper",
    ))
    trade = repo.upsert_trade(TradeRecord(
        order_id="paper-order", strategy="paper-a", symbol="BTC-USDT", side="buy",
        amount=1.0, price=100.0, fee=0.1, timestamp=1,
        source="paper", runtime_mode="paper",
    ))

    assert (account.source, account.runtime_mode) == ("paper", "paper")
    assert (position.source, position.runtime_mode) == ("paper", "paper")
    assert (order.source, order.runtime_mode) == ("paper", "paper")
    assert (trade.source, trade.runtime_mode) == ("paper", "paper")
    audit = repo.audit_legacy_live_state_ownership()
    assert audit["paper_backtest_accounts"] == 1
    assert audit["paper_backtest_positions"] == 1
    assert audit["paper_backtest_orders"] == 1
    assert audit["paper_backtest_trades"] == 1


def test_legacy_rows_without_provenance_are_unknown_and_exchange_orders_are_audited(repo):
    repo.upsert_order(OrderRecord(
        order_id="exchange-order", strategy="__exchange__", symbol="BTC-USDT",
        side="buy", type="market", amount=1.0, price=100.0, status="filled",
        fill_price=100.0, timestamp=1,
    ))
    repo.upsert_trade(TradeRecord(
        exchange_trade_id="exchange-trade", order_id="exchange-order",
        strategy="__exchange__", symbol="BTC-USDT", side="buy", amount=1.0,
        price=100.0, fee=0.1, timestamp=1,
    ))
    audit = repo.audit_legacy_live_state_ownership()
    assert audit["unknown_strategy_orders"] == 0
    assert audit["unknown_strategy_trades"] == 0
    assert audit["exchange_orders"] == 1
    assert audit["exchange_trades"] == 1


def test_legacy_duplicate_migration_retains_rows_and_active_queries_select_current_rows():
    engine = create_engine("sqlite:///:memory:", echo=False)
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE accountrecord (
                id INTEGER PRIMARY KEY, strategy VARCHAR NOT NULL,
                initial_equity FLOAT NOT NULL, cash_balance FLOAT NOT NULL,
                equity FLOAT NOT NULL, realized_pnl FLOAT NOT NULL,
                unrealized_pnl FLOAT NOT NULL, daily_pnl FLOAT NOT NULL,
                fees_paid FLOAT NOT NULL, updated_at INTEGER NOT NULL
            )
        """))
        connection.execute(text("""
            INSERT INTO accountrecord VALUES
            (1, 'legacy', 100, 90, 100, 0, 0, 0, 0, 10),
            (2, 'legacy', 200, 190, 200, 0, 0, 0, 0, 20)
        """))
        connection.execute(text("""
            CREATE TABLE positionrecord (
                id INTEGER PRIMARY KEY, strategy VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL, side VARCHAR NOT NULL,
                amount FLOAT NOT NULL, entry_price FLOAT NOT NULL,
                leverage INTEGER NOT NULL, timestamp INTEGER NOT NULL
            )
        """))
        connection.execute(text("""
            INSERT INTO positionrecord VALUES
            (1, 'legacy', 'BTC-USDT', 'long', 1, 100, 1, 10),
            (2, 'legacy', 'BTC-USDT', 'long', 2, 110, 1, 20)
        """))
        connection.execute(text("""
            CREATE TABLE traderecord (
                id INTEGER PRIMARY KEY, exchange_trade_id VARCHAR NOT NULL,
                order_id VARCHAR NOT NULL, strategy VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL, side VARCHAR NOT NULL,
                amount FLOAT NOT NULL, price FLOAT NOT NULL,
                fee FLOAT NOT NULL, timestamp INTEGER NOT NULL
            )
        """))
        connection.execute(text("""
            INSERT INTO traderecord VALUES
            (1, 'duplicate', 'old', 'legacy', 'BTC-USDT', 'buy', 1, 100, 0, 10),
            (2, 'duplicate', 'new', 'legacy', 'BTC-USDT', 'buy', 1, 101, 0, 20)
        """))

    repository = Repository(engine)

    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM accountrecord")).scalar_one() == 2
        assert connection.execute(text("SELECT COUNT(*) FROM positionrecord")).scalar_one() == 2
        assert connection.execute(text("SELECT COUNT(*) FROM traderecord")).scalar_one() == 2

    assert repository.get_account("legacy").updated_at == 20
    assert [
        (row.amount, row.active)
        for row in repository.get_open_positions("legacy")
    ] == [(2.0, True)]
    assert [row.order_id for row in repository.get_trades(strategy="legacy")] == ["old", "new"]
    assert [
        row.order_id
        for row in repository.get_trades(strategy="legacy", include_inactive=True)
    ] == ["old", "new"]


def test_record_provenance_mismatched_pairs_are_unknown():
    assert _record_provenance(
        SimpleNamespace(source="live_exchange", runtime_mode="live")
    ) == "live"
    assert _record_provenance(
        SimpleNamespace(source="paper", runtime_mode="paper")
    ) == "paper"
    assert _record_provenance(
        SimpleNamespace(source="backtest", runtime_mode="backtest")
    ) == "backtest"
    assert _record_provenance(
        SimpleNamespace(source="live_exchange", runtime_mode="paper")
    ) == "unknown"
    assert _record_provenance(
        SimpleNamespace(source="paper", runtime_mode="live")
    ) == "unknown"
    assert _record_provenance(SimpleNamespace(source=None, runtime_mode=None)) == "unknown"
    assert _record_provenance(
        SimpleNamespace(strategy="__exchange__", source=None, runtime_mode=None)
    ) == "unknown"


def test_legacy_audit_counts_current_history_and_exchange_rows_without_mutation(repo):
    repo.save_trade(TradeRecord(
        order_id="old", strategy="legacy", symbol="BTC-USDT", side="buy",
        amount=1, price=100, fee=0, timestamp=1, active=False,
    ))
    repo.save_trade(TradeRecord(
        order_id="current", strategy="legacy", symbol="BTC-USDT", side="buy",
        amount=1, price=100, fee=0, timestamp=2,
    ))
    repo.save_trade(TradeRecord(
        order_id="exchange", strategy="__exchange__", symbol="BTC-USDT", side="buy",
        amount=1, price=100, fee=0, timestamp=3,
    ))

    before = [row.model_dump() for row in repo.get_trades(include_inactive=True)]
    audit = repo.audit_legacy_live_state_ownership()
    after = [row.model_dump() for row in repo.get_trades(include_inactive=True)]

    assert audit["active_trades"] == 2
    assert audit["inactive_trades"] == 1
    assert audit["unknown_trades"] == 2
    assert audit["unknown_strategy_trades"] == 2
    assert audit["exchange_trades"] == 1
    assert before == after


def test_upsert_order_renormalizes_mutated_record_before_sqlite_write(repo: Repository):
    order = OrderRecord(
        order_id="mutated-order",
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
        filled_amount=0.25,
        remaining_amount=0.75,
        snapshot_filled_amount=0.25,
        snapshot_updated_at=1,
    )
    order.amount = float("nan")
    order.filled_amount = float("inf")
    order.remaining_amount = -1.0
    order.price = float("inf")
    order.fill_price = float("nan")
    order.timestamp = 2**63
    order.updated_at = float("inf")
    order.snapshot_filled_amount = float("nan")
    order.snapshot_updated_at = 2**63

    saved = repo.upsert_order(order)

    assert (
        saved.amount,
        saved.filled_amount,
        saved.remaining_amount,
        saved.price,
        saved.fill_price,
        saved.timestamp,
        saved.updated_at,
        saved.snapshot_filled_amount,
        saved.snapshot_updated_at,
    ) == (0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0.0, 0)


def test_upsert_account_keeps_live_exchange_row_separate_from_paper_and_unknown(repo):
    repo.upsert_account(AccountRecord(
        strategy="__exchange__", source="paper", runtime_mode="paper",
        initial_equity=100.0, cash_balance=90.0, equity=100.0,
        realized_pnl=0.0, unrealized_pnl=0.0, daily_pnl=0.0, fees_paid=0.0,
        updated_at=10,
    ))
    repo.upsert_account(AccountRecord(
        strategy="__exchange__", source="unknown", runtime_mode="unknown",
        initial_equity=200.0, cash_balance=190.0, equity=200.0,
        realized_pnl=0.0, unrealized_pnl=0.0, daily_pnl=0.0, fees_paid=0.0,
        updated_at=20,
    ))

    repo.upsert_account(AccountRecord(
        strategy="__exchange__", source="live_exchange", runtime_mode="live",
        initial_equity=1000.0, cash_balance=900.0, equity=1010.0,
        realized_pnl=10.0, unrealized_pnl=0.0, daily_pnl=0.0, fees_paid=1.0,
        updated_at=30,
    ))

    live = repo.get_account("__exchange__", source="live_exchange", runtime_mode="live")
    paper = repo.get_account("__exchange__", source="paper", runtime_mode="paper")
    unknown = repo.get_account("__exchange__", source="unknown", runtime_mode="unknown")

    assert live is not None and live.equity == 1010.0
    assert paper is not None and paper.equity == 100.0
    assert unknown is not None and unknown.equity == 200.0
    assert len(repo.get_accounts(source="live_exchange", runtime_mode="live")) == 1


def test_upsert_position_keeps_live_exchange_row_separate_from_paper(repo):
    repo.upsert_position(PositionRecord(
        strategy="__exchange__", symbol="BTC-USDT", side="long", amount=1.0,
        entry_price=100.0, leverage=1, timestamp=10,
        source="paper", runtime_mode="paper",
    ))
    repo.upsert_position(PositionRecord(
        strategy="__exchange__", symbol="BTC-USDT", side="long", amount=2.0,
        entry_price=100.0, leverage=1, timestamp=20,
        source="live_exchange", runtime_mode="live",
    ))

    positions = repo.get_positions("__exchange__", source="live_exchange", runtime_mode="live")
    assert len(positions) == 1
    assert positions[0].amount == 2.0
    assert len(repo.get_positions("__exchange__", include_inactive=True)) == 2


def test_upsert_order_rebinds_placeholder_trade_to_confirmed_local_identity(repo):
    placeholder = repo.upsert_trade(
        TradeRecord(
            exchange_trade_id="trade-before-order",
            exchange_order_id="exchange-before-order",
            client_order_id="client-before-order",
            order_id="okx:exchange-before-order",
            strategy="__exchange__",
            symbol="BTC-USDT",
            side="buy",
            amount=0.4,
            price=100.0,
            fee=0.01,
            timestamp=123,
        )
    )

    repo.upsert_order(
        OrderRecord(
            order_id="local-before-order",
            exchange_order_id="exchange-before-order",
            client_order_id="client-before-order",
            strategy="ma_cross",
            symbol="BTC-USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=100.0,
            status="pending",
            fill_price=0.0,
            timestamp=100,
        )
    )
    repo.upsert_trade(
        TradeRecord(
            exchange_trade_id="trade-before-order",
            exchange_order_id="exchange-before-order",
            client_order_id="client-before-order",
            order_id="okx:exchange-before-order",
            strategy="__exchange__",
            symbol="BTC-USDT",
            side="buy",
            amount=0.4,
            price=100.0,
            fee=0.01,
            timestamp=123,
        )
    )

    trades = repo.get_trades(order_id="local-before-order")
    assert len(repo.get_trades()) == 1
    assert trades[0].id == placeholder.id
    assert (trades[0].order_id, trades[0].strategy) == ("local-before-order", "ma_cross")


def test_upsert_trade_does_not_replace_confirmed_local_identity_on_conflicting_replay(repo):
    repo.upsert_trade(
        TradeRecord(
            exchange_trade_id="confirmed-trade",
            exchange_order_id="confirmed-order",
            client_order_id="confirmed-client",
            order_id="local-confirmed",
            strategy="ma_cross",
            symbol="BTC-USDT",
            side="buy",
            amount=0.1,
            price=100.0,
            fee=0.01,
            timestamp=123,
        )
    )

    persisted = repo.upsert_trade(
        TradeRecord(
            exchange_trade_id="confirmed-trade",
            exchange_order_id="wrong-order",
            client_order_id="wrong-client",
            order_id="local-wrong",
            strategy="other",
            symbol="BTC-USDT",
            side="buy",
            amount=0.1,
            price=100.0,
            fee=0.01,
            timestamp=123,
        )
    )

    assert (persisted.order_id, persisted.strategy) == ("local-confirmed", "ma_cross")
    assert (persisted.exchange_order_id, persisted.client_order_id) == (
        "confirmed-order",
        "confirmed-client",
    )


def test_upsert_trade_combines_blank_id_upgrade_with_placeholder_identity_upgrade(repo):
    repo.upsert_trade(
        TradeRecord(
            exchange_trade_id="",
            exchange_order_id="exchange-combined",
            client_order_id="client-combined",
            order_id="okx:exchange-combined",
            strategy="__exchange__",
            symbol="BTC-USDT",
            side="buy",
            amount=0.1,
            price=100.0,
            fee=0.01,
            timestamp=123,
        )
    )
    repo.upsert_order(
        OrderRecord(
            order_id="local-combined",
            exchange_order_id="exchange-combined",
            client_order_id="client-combined",
            strategy="ma_cross",
            symbol="BTC-USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=100.0,
            status="pending",
            fill_price=0.0,
            timestamp=100,
        )
    )

    persisted = repo.upsert_trade(
        TradeRecord(
            exchange_trade_id="identified-combined",
            exchange_order_id="exchange-combined",
            client_order_id="client-combined",
            order_id="local-combined",
            strategy="ma_cross",
            symbol="BTC-USDT",
            side="buy",
            amount=0.1,
            price=100.0,
            fee=0.01,
            timestamp=123,
        )
    )

    assert len(repo.get_trades()) == 1
    assert (persisted.exchange_trade_id, persisted.order_id, persisted.strategy) == (
        "identified-combined",
        "local-combined",
        "ma_cross",
    )


def test_upsert_order_does_not_rebind_placeholder_trade_for_another_exchange_order(repo):
    repo.upsert_trade(
        TradeRecord(
            exchange_trade_id="unrelated-trade",
            exchange_order_id="exchange-a",
            order_id="okx:exchange-a",
            strategy="__exchange__",
            symbol="BTC-USDT",
            side="buy",
            amount=0.1,
            price=100.0,
            fee=0.01,
            timestamp=123,
        )
    )

    repo.upsert_order(
        OrderRecord(
            order_id="local-b",
            exchange_order_id="exchange-b",
            strategy="ma_cross",
            symbol="BTC-USDT",
            side="buy",
            type="limit",
            amount=1.0,
            price=100.0,
            status="pending",
            fill_price=0.0,
            timestamp=100,
        )
    )

    persisted = repo.get_trades()[0]
    assert (persisted.order_id, persisted.strategy) == ("okx:exchange-a", "__exchange__")


def test_kline_cache_requires_explicit_domain_and_filters_each_runtime(repo):
    for source, runtime_mode, close in [
        ("paper", "paper", 101.0),
        ("live_exchange", "live", 102.0),
        ("backtest", "backtest", 103.0),
    ]:
        repo.save_kline(KlineCache(
            symbol="BTC-USDT", timeframe="1m", timestamp=1,
            open=100.0, high=110.0, low=90.0, close=close, volume=1.0,
            source=source, runtime_mode=runtime_mode,
        ))
    repo.save_kline(KlineCache(
        symbol="BTC-USDT", timeframe="1m", timestamp=2,
        open=100.0, high=110.0, low=90.0, close=104.0, volume=1.0,
    ))

    assert [item.close for item in repo.get_klines(
        "BTC-USDT", "1m", 0, 10, source="paper", runtime_mode="paper"
    )] == [101.0]
    assert [item.close for item in repo.get_klines(
        "BTC-USDT", "1m", 0, 10, source="live_exchange", runtime_mode="live"
    )] == [102.0]
    assert [item.close for item in repo.get_klines(
        "BTC-USDT", "1m", 0, 10, source="backtest", runtime_mode="backtest"
    )] == [103.0]
    assert [item.close for item in repo.get_klines("BTC-USDT", "1m", 0, 10)] == [104.0]
    assert len(repo.get_klines(
        "BTC-USDT", "1m", 0, 10, include_inactive=True
    )) == 1


def test_mark_account_stale_only_archives_requested_domain(repo):
    repo.upsert_account(AccountRecord(
        strategy="alpha", source="live_exchange", runtime_mode="live",
        initial_equity=100.0, cash_balance=100.0, equity=100.0,
        realized_pnl=0.0, unrealized_pnl=0.0, daily_pnl=0.0, fees_paid=0.0,
        updated_at=1,
    ))
    repo.upsert_account(AccountRecord(
        strategy="alpha", source="paper", runtime_mode="paper",
        initial_equity=200.0, cash_balance=200.0, equity=200.0,
        realized_pnl=0.0, unrealized_pnl=0.0, daily_pnl=0.0, fees_paid=0.0,
        updated_at=2,
    ))

    repo.mark_account_stale(
        "alpha", 10, source="live_exchange", runtime_mode="live"
    )

    live = repo.get_account("alpha", source="live_exchange", runtime_mode="live")
    paper = repo.get_account("alpha", source="paper", runtime_mode="paper")
    assert live is None
    assert paper is not None and paper.active is True


def test_mark_account_stale_without_domain_is_read_only_for_legacy_compatibility(repo):
    repo.upsert_account(AccountRecord(
        strategy="alpha", source="unknown", runtime_mode="unknown",
        initial_equity=100.0, cash_balance=100.0, equity=100.0,
        realized_pnl=0.0, unrealized_pnl=0.0, daily_pnl=0.0, fees_paid=0.0,
        updated_at=1,
    ))

    repo.mark_account_stale("alpha", 10)

    account = repo.get_account(
        "alpha", source="unknown", runtime_mode="unknown", include_inactive=True
    )
    assert account is not None and account.active is True and account.stale_at is None


def test_save_kline_skips_mutated_invalid_ohlcv_without_reordering_values(repo, caplog):
    invalid = KlineCache(
        symbol="BTC-USDT", timeframe="1m", timestamp=1,
        open=100.0, high=110.0, low=90.0, close=105.0, volume=1.0,
    )
    invalid.high = 80.0

    assert repo.save_kline(invalid) is None
    assert repo.get_klines("BTC-USDT", "1m", 0, 2) == []
    assert "invalid OHLC" in caplog.text


def test_save_cash_ledger_normalizes_nonfinite_money_and_timestamp(repo):
    saved = repo.save_cash_ledger(CashLedgerRecord(
        strategy="s", event_type="fill", amount=float("nan"),
        balance_after=float("inf"), timestamp=2**63,
    ))

    assert (saved.amount, saved.balance_after, saved.timestamp) == (0.0, 0.0, 0)
    assert repo.get_cash_ledger()[0].timestamp == 0


@pytest.mark.parametrize("timestamp", [float("nan"), float("inf"), -1, 2**63, "invalid"])
def test_save_risk_event_normalizes_unsafe_timestamp(repo, timestamp):
    saved = repo.save_risk_event({
        "type": "risk_event", "event_key": f"unsafe:{timestamp!s}",
        "timestamp": timestamp,
    })

    assert saved.timestamp == 0


def test_sqlite_migration_retains_invalid_kline_as_inactive_without_changing_ohlc():
    engine = create_engine("sqlite:///:memory:", echo=False)
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE klinecache (
                id INTEGER PRIMARY KEY, symbol VARCHAR NOT NULL, timeframe VARCHAR NOT NULL,
                timestamp INTEGER NOT NULL, open FLOAT NOT NULL, high FLOAT NOT NULL,
                low FLOAT NOT NULL, close FLOAT NOT NULL, volume FLOAT NOT NULL
            )
        """))
        connection.execute(text(
            "INSERT INTO klinecache VALUES (1, 'BTC-USDT', '1m', 10, 120, 100, 110, 115, 2)"
        ))

    repository = Repository(engine)

    with engine.connect() as connection:
        row = connection.execute(text(
            "SELECT open, high, low, close, active FROM klinecache WHERE id = 1"
        )).one()

    assert row == (120.0, 100.0, 110.0, 115.0, 0)
    assert repository.get_klines("BTC-USDT", "1m", 0, 20) == []


def test_sqlite_migration_normalizes_legacy_kline_and_cash_rows():
    engine = create_engine("sqlite:///:memory:", echo=False)
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE klinecache (
                id INTEGER PRIMARY KEY, symbol VARCHAR NOT NULL, timeframe VARCHAR NOT NULL,
                timestamp INTEGER NOT NULL, open FLOAT NOT NULL, high FLOAT NOT NULL,
                low FLOAT NOT NULL, close FLOAT NOT NULL, volume FLOAT NOT NULL
            )
        """))
        connection.execute(text("""
            INSERT INTO klinecache VALUES
            (1, 'BTC-USDT', '1m', -1, 'nan', 100, 110, 'inf', -2)
        """))
        connection.execute(text("""
            CREATE TABLE cashledgerrecord (
                id INTEGER PRIMARY KEY, strategy VARCHAR NOT NULL, symbol VARCHAR,
                order_id VARCHAR, trade_id VARCHAR, event_type VARCHAR NOT NULL,
                amount FLOAT NOT NULL, balance_after FLOAT NOT NULL, timestamp INTEGER NOT NULL
            )
        """))
        connection.execute(text("""
            INSERT INTO cashledgerrecord VALUES
            (1, 's', NULL, NULL, NULL, 'fill', 'nan', '-inf', 2**63)
        """.replace("2**63", "9223372036854775808")))

    Repository(engine)

    with engine.connect() as connection:
        kline = connection.execute(text(
            "SELECT timestamp, open, high, low, close, volume, active FROM klinecache"
        )).all()
        cash = connection.execute(text(
            "SELECT amount, balance_after, timestamp FROM cashledgerrecord"
        )).one()
    assert kline == [(-1, "nan", 100.0, 110.0, "inf", -2.0, 0)]
    assert cash == (0.0, 0.0, 0)


def test_sqlite_migration_keeps_distinct_reliable_trade_ids_with_same_fingerprint():
    engine = create_engine("sqlite:///:memory:", echo=False)
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE traderecord (
                id INTEGER PRIMARY KEY, exchange_trade_id VARCHAR NOT NULL,
                exchange_order_id VARCHAR, client_order_id VARCHAR, order_id VARCHAR NOT NULL,
                strategy VARCHAR NOT NULL, symbol VARCHAR NOT NULL, side VARCHAR NOT NULL,
                amount FLOAT NOT NULL, price FLOAT NOT NULL, fee FLOAT NOT NULL,
                timestamp INTEGER NOT NULL
            )
        """))
        connection.execute(text("""
            INSERT INTO traderecord VALUES
            (1, 'trade-a', 'ex-1', 'client-1', 'o', 's', 'BTC-USDT', 'buy', 1, 100, 1, 10),
            (2, 'trade-b', 'ex-1', 'client-1', 'o', 's', 'BTC-USDT', 'buy', 1, 100, 1, 10)
        """))

    Repository(engine)

    with engine.connect() as connection:
        ids = connection.execute(text(
            "SELECT exchange_trade_id FROM traderecord ORDER BY id"
        )).scalars().all()
    assert ids == ["trade-a", "trade-b"]


def test_sqlite_order_migration_restores_existing_indexes_except_external_id_constraints():
    engine = create_engine("sqlite:///:memory:", echo=False)
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE orderrecord (
                id INTEGER PRIMARY KEY, order_id VARCHAR NOT NULL,
                exchange_order_id VARCHAR NOT NULL, client_order_id VARCHAR NOT NULL,
                strategy VARCHAR NOT NULL, symbol VARCHAR NOT NULL, side VARCHAR NOT NULL,
                type VARCHAR NOT NULL, amount FLOAT NOT NULL, price FLOAT NOT NULL,
                status VARCHAR NOT NULL, fill_price FLOAT NOT NULL,
                timestamp INTEGER NOT NULL, updated_at INTEGER NOT NULL
            )
        """))
        connection.execute(text("""
            INSERT INTO orderrecord VALUES
            (1, 'local-1', 'exchange-1', 'client-1', 's', 'BTC-USDT', 'buy',
             'limit', 1, 100, 'pending', 0, 1, 1),
            (2, 'local-2', '', '', 's', 'ETH-USDT', 'buy',
             'limit', 1, 100, 'pending', 0, 2, 2)
        """))
        connection.execute(text(
            "CREATE INDEX ix_legacy_orderrecord_strategy ON orderrecord (strategy)"
        ))
        connection.execute(text(
            "CREATE UNIQUE INDEX uq_legacy_orderrecord_strategy_symbol "
            "ON orderrecord (strategy, symbol)"
        ))
        connection.execute(text(
            "CREATE INDEX ix_legacy_orderrecord_pending "
            "ON orderrecord (status) WHERE status = 'pending'"
        ))
        connection.execute(text(
            "CREATE UNIQUE INDEX uq_legacy_orderrecord_exchange "
            "ON orderrecord (exchange_order_id)"
        ))
        connection.execute(text(
            "CREATE UNIQUE INDEX uq_legacy_orderrecord_client "
            "ON orderrecord (client_order_id)"
        ))

    Repository(engine)

    with engine.connect() as connection:
        indexes = {
            row[1]: connection.execute(
                text('SELECT sql FROM sqlite_schema WHERE type = \'index\' AND name = :name'),
                {"name": row[1]},
            ).scalar_one()
            for row in connection.execute(text("PRAGMA index_list(orderrecord)"))
        }
        connection.execute(text(
            "INSERT INTO orderrecord "
            "(order_id, exchange_order_id, client_order_id, strategy, symbol, side, type, "
            "amount, price, status, fill_price, timestamp, updated_at) "
            "VALUES ('local-3', NULL, NULL, 's', 'SOL-USDT', 'buy', 'limit', 1, 100, "
            "'pending', 0, 3, 3)"
        ))

    assert "ix_legacy_orderrecord_strategy" in indexes
    assert "uq_legacy_orderrecord_strategy_symbol" in indexes
    assert "ix_legacy_orderrecord_pending" in indexes
    assert "uq_legacy_orderrecord_exchange" not in indexes
    assert "uq_legacy_orderrecord_client" not in indexes
    assert "uq_orderrecord_exchange_order_id" in indexes
    assert "uq_orderrecord_client_order_id" in indexes
    assert "WHERE status = 'pending'" in indexes["ix_legacy_orderrecord_pending"]


def test_sqlite_order_migration_is_repeatable_with_indexes_and_file_database(tmp_path):
    db_path = tmp_path / "legacy-orders.db"
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE orderrecord (
                id INTEGER PRIMARY KEY, order_id VARCHAR NOT NULL,
                exchange_order_id VARCHAR NOT NULL, client_order_id VARCHAR NOT NULL,
                strategy VARCHAR NOT NULL, symbol VARCHAR NOT NULL, side VARCHAR NOT NULL,
                type VARCHAR NOT NULL, amount FLOAT NOT NULL, price FLOAT NOT NULL,
                status VARCHAR NOT NULL, fill_price FLOAT NOT NULL,
                timestamp INTEGER NOT NULL, updated_at INTEGER NOT NULL
            )
        """))
        connection.execute(text(
            "CREATE INDEX ix_legacy_orderrecord_order_id ON orderrecord (order_id)"
        ))
        connection.execute(text(
            "INSERT INTO orderrecord VALUES "
            "(1, 'local-1', '', '', 's', 'BTC-USDT', 'buy', 'limit', 1, 100, "
            "'pending', 0, 1, 1)"
        ))
    engine.dispose()

    Repository(db_path=str(db_path))
    Repository(db_path=str(db_path))

    check_engine = create_engine(f"sqlite:///{db_path}", echo=False)
    with check_engine.connect() as connection:
        indexes = {
            row[1] for row in connection.execute(text("PRAGMA index_list(orderrecord)"))
        }
        assert "ix_legacy_orderrecord_order_id" in indexes
        assert "uq_orderrecord_exchange_order_id" in indexes
        assert connection.execute(text("SELECT COUNT(*) FROM orderrecord")).scalar_one() == 1
    check_engine.dispose()


def test_atomic_private_sync_distinguishes_empty_orders_from_unset_orders(repo):
    btc_order = OrderRecord(
        order_id="btc-order",
        exchange_order_id="btc-exchange",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=1.0,
        price=100.0,
        status="pending",
        fill_price=0.0,
        timestamp=1,
    )
    eth_order = OrderRecord(
        order_id="eth-order",
        exchange_order_id="eth-exchange",
        strategy="ma_cross",
        symbol="ETH-USDT",
        side="buy",
        type="limit",
        amount=1.0,
        price=100.0,
        status="pending",
        fill_price=0.0,
        timestamp=1,
    )
    repo.upsert_order(btc_order)
    repo.upsert_order(eth_order)
    placeholder = repo.atomic_private_sync(lambda transaction: transaction.save_trade(
        TradeRecord(
            exchange_trade_id="",
            exchange_order_id="eth-exchange",
            order_id="okx:eth-exchange",
            strategy="__exchange__",
            symbol="ETH-USDT",
            side="buy",
            amount=0.1,
            price=100.0,
            fee=0.0,
            timestamp=2,
        )
    ))

    repo.atomic_private_sync(lambda transaction: transaction.rebind_trades_to_orders([]))
    untouched = repo.get_trades()[0]
    assert untouched.id == placeholder.id
    assert (untouched.order_id, untouched.strategy) == ("okx:eth-exchange", "__exchange__")

    repo.atomic_private_sync(lambda transaction: transaction.rebind_trades_to_orders(None))
    rebound = repo.get_trades()[0]
    assert (rebound.order_id, rebound.strategy) == ("eth-order", "ma_cross")
