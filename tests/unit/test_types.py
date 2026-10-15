import math

import pytest

from src.core.types import (
    AccountSnapshot,
    AssetBalance,
    Bar,
    ExchangeOrderSnapshot,
    ExchangeTradeSnapshot,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionSide,
    PositionSnapshot,
    _safe_timestamp,
    stable_blank_trade_fingerprint,
)
from src.data.models import (
    AccountRecord,
    CashLedgerRecord,
    KlineCache,
    OrderRecord,
    PositionRecord,
    TradeRecord,
)


@pytest.mark.parametrize("raw", [2, -1, 2.0, -1.0, "2", "-1"])
def test_exchange_quantity_snapshots_reject_invalid_inverse_flags(raw):
    with pytest.raises(ValueError, match="is_inverse"):
        PositionSnapshot(
            symbol="BTC-USDT-SWAP",
            side=PositionSide.LONG,
            amount=1.0,
            entry_price=100.0,
            is_inverse=raw,
        )
    with pytest.raises(ValueError, match="is_inverse"):
        ExchangeTradeSnapshot(
            exchange_trade_id="trade",
            exchange_order_id="order",
            client_order_id="",
            symbol="BTC-USDT-SWAP",
            side=OrderSide.BUY,
            amount=1.0,
            price=100.0,
            fee=0.0,
            timestamp=1,
            is_inverse=raw,
        )


def test_sparse_partial_order_snapshot_preserves_provider_status():
    snapshot = ExchangeOrderSnapshot(
        exchange_order_id="exchange-1",
        client_order_id="client-1",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=1.0,
        price=100.0,
        status=OrderStatus.PARTIALLY_FILLED,
        fill_price=None,
        timestamp=1,
        updated_at=2,
    )

    assert snapshot.status is OrderStatus.PARTIALLY_FILLED


def test_account_snapshot_creation():
    snapshot = AccountSnapshot(
        initial_equity=1000.0,
        cash_balance=900.0,
        equity=1010.0,
        realized_pnl=10.0,
        unrealized_pnl=0.0,
        daily_pnl=10.0,
        fees_paid=1.5,
        timestamp=1700000000000,
    )

    assert snapshot.cash_balance == 900.0
    assert snapshot.equity == 1010.0
    assert snapshot.timestamp == 1700000000000


def test_account_snapshot_assets_cannot_be_mutated_after_validation():
    snapshot = AccountSnapshot(
        assets=[AssetBalance(ccy="USDT", avail_bal=850.0)]
    )

    with pytest.raises(TypeError):
        snapshot.assets.append(AssetBalance(ccy="BTC"))


def test_account_snapshot_assets_are_not_aliased_to_input_list():
    assets = [AssetBalance(ccy="USDT", avail_bal=850.0)]
    snapshot = AccountSnapshot(assets=assets)
    assets.clear()

    assert snapshot.assets == [AssetBalance(ccy="USDT", avail_bal=850.0)]


def test_account_record_assets_are_deeply_immutable():
    from src.data.models import AccountRecord

    account = AccountRecord(
        strategy="nested-assets",
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
            "metadata": {"levels": [{"name": "cash"}, {"name": "margin"}]},
            "tags": {"primary", "settled"},
        }],
    )

    with pytest.raises(TypeError):
        account.assets[0]["metadata"]["levels"][0]["name"] = "changed"
    with pytest.raises(TypeError):
        account.assets[0]["metadata"]["levels"].append({"name": "new"})
    with pytest.raises(TypeError):
        account.assets[0]["metadata"]["levels"][0]["new"] = True
    with pytest.raises(TypeError):
        account.assets[0]["tags"].add("new")


def test_account_snapshot_post_init_preserves_assets_and_derives_defaults():
    snapshot = AccountSnapshot(
        cash_balance=900.0,
        equity=1000.0,
        timestamp=1700000000000,
        assets=[AssetBalance(ccy="USDT", cash_bal=900.0, eq=1000.0, avail_bal=850.0)],
    )

    assert snapshot.initial_equity == 1000.0
    assert snapshot.available_balance == 900.0
    assert isinstance(snapshot.available_balance, float)
    assert snapshot.updated_at == 1700000000000
    assert snapshot.assets == [AssetBalance(ccy="USDT", cash_bal=900.0, eq=1000.0, avail_bal=850.0)]


def test_account_snapshot_skips_invalid_asset_entries_and_preserves_valid_mapping():
    snapshot = AccountSnapshot(
        assets=[None, 1, {"ccy": "USDT", "avail_bal": 850.0}],
    )

    assert snapshot.assets == [AssetBalance(ccy="USDT", avail_bal=850.0)]


def test_account_snapshot_post_init_preserves_explicit_zero_available_balance():
    snapshot = AccountSnapshot(cash_balance=900.0, available_balance=0.0)

    assert snapshot.available_balance == 0.0
    assert isinstance(snapshot.available_balance, float)


def test_position_snapshot_creation():
    snapshot = PositionSnapshot(
        symbol="BTC/USDT:USDT",
        side=PositionSide.LONG,
        amount=0.5,
        entry_price=50000.0,
        mark_price=50100.0,
        realized_pnl=5.0,
        unrealized_pnl=50.0,
        leverage=2,
        timestamp=1700000000000,
    )

    assert snapshot.symbol == "BTC/USDT:USDT"
    assert snapshot.side == PositionSide.LONG
    assert snapshot.unrealized_pnl == 50.0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_position_snapshot_sanitizes_nonfinite_values(value):
    snapshot = PositionSnapshot(
        symbol="BTC-USDT-SWAP",
        side=PositionSide.SHORT,
        amount=-value if value == -float("inf") else value,
        entry_price=value,
        mark_price=value,
        realized_pnl=value,
        unrealized_pnl=value,
        leverage=value,
        timestamp=value,
        updated_at=value,
    )

    assert snapshot.amount >= 0.0
    assert math.isfinite(snapshot.amount)
    assert snapshot.entry_price == 0.0
    assert snapshot.mark_price is None
    assert snapshot.realized_pnl == 0.0
    assert snapshot.unrealized_pnl == 0.0
    assert snapshot.leverage == 1
    assert snapshot.timestamp == 0
    assert snapshot.updated_at == 0
    assert snapshot.side == PositionSide.SHORT


def test_position_snapshot_preserves_negative_pnl_but_sanitizes_all_numeric_fields():
    snapshot = PositionSnapshot(
        symbol="BTC-USDT-SWAP",
        side=PositionSide.SHORT,
        amount=-2.0,
        entry_price=-1.0,
        mark_price=-2.0,
        realized_pnl=-3.5,
        unrealized_pnl=-float("inf"),
        leverage=0,
        timestamp=-1,
    )

    assert snapshot.amount == 2.0
    assert snapshot.entry_price == 0.0
    assert snapshot.mark_price is None
    assert snapshot.realized_pnl == pytest.approx(-3.5)
    assert snapshot.unrealized_pnl == 0.0
    assert snapshot.leverage == 1
    assert snapshot.timestamp == 0
    assert snapshot.side == PositionSide.SHORT


@pytest.mark.parametrize(
    "value",
    [
        2**63,
        10**1000,
        "9223372036854775808",
        "999999999999999999999999",
        float("nan"),
        float("inf"),
        -1,
    ],
)
def test_position_leverage_rejects_values_outside_signed_int64(value):
    snapshot = PositionSnapshot(
        symbol="BTC-USDT-SWAP",
        side=PositionSide.LONG,
        amount=1.0,
        entry_price=100.0,
        leverage=value,
    )
    position = PositionRecord(
        strategy="s",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=100.0,
        leverage=value,
        timestamp=1,
    )

    assert snapshot.leverage == 1
    assert position.leverage == 1


def test_position_leverage_preserves_signed_int64_maximum():
    maximum = 2**63 - 1

    assert PositionSnapshot(
        symbol="BTC-USDT-SWAP",
        side=PositionSide.LONG,
        amount=1.0,
        entry_price=100.0,
        leverage=maximum,
    ).leverage == maximum
    assert PositionRecord(
        strategy="s",
        symbol="BTC-USDT-SWAP",
        side="long",
        amount=1.0,
        entry_price=100.0,
        leverage=str(maximum),
        timestamp=1,
    ).leverage == maximum


def test_position_record_normalizes_direct_construction():
    position = PositionRecord(
        strategy="s",
        symbol="BTC-USDT-SWAP",
        side="short",
        amount=-2.0,
        entry_price=float("inf"),
        leverage=-4,
        timestamp=-1,
        mark_price=float("nan"),
        realized_pnl=-3.0,
        unrealized_pnl=float("inf"),
    )

    assert position.amount == 2.0
    assert position.entry_price == 0.0
    assert position.mark_price is None
    assert position.realized_pnl == -3.0
    assert position.unrealized_pnl == 0.0
    assert position.leverage == 1
    assert position.timestamp == 0
    assert position.side == "short"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_kline_cache_normalizes_invalid_market_values(value):
    kline = KlineCache(
        symbol="BTC-USDT",
        timeframe="1m",
        timestamp=value,
        open=value,
        high=value,
        low=value,
        close=value,
        volume=value,
    )

    assert kline.timestamp == 0
    assert (kline.open, kline.high, kline.low, kline.close, kline.volume) == (
        0.0, 0.0, 0.0, 0.0, 0.0
    )


def test_kline_cache_rejects_impossible_ohlcv_candle_bounds_without_reordering_values():
    with pytest.raises(ValueError, match="invalid OHLC"):
        KlineCache(
            symbol="BTC-USDT", timeframe="1m", timestamp=1,
            open=105.0, high=100.0, low=110.0, close=115.0, volume=2.0,
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_cash_ledger_normalizes_invalid_amounts_and_timestamp(value):
    entry = CashLedgerRecord(
        strategy="s", event_type="fill", amount=value, balance_after=value, timestamp=value
    )

    assert entry.amount == 0.0
    assert entry.balance_after == 0.0
    assert entry.timestamp == 0


def test_accounting_audit_records_keep_explicit_provenance():
    ledger = CashLedgerRecord(
        strategy="alpha",
        event_type="fill",
        amount=1.0,
        balance_after=99.0,
        timestamp=10,
        source="paper",
        runtime_mode="paper",
    )
    assert (ledger.source, ledger.runtime_mode) == ("paper", "paper")


def test_bar_creation():
    bar = Bar(
        timestamp=1700000000000,
        open=50000.0,
        high=51000.0,
        low=49000.0,
        close=50500.0,
        volume=100.5,
    )
    assert bar.timestamp == 1700000000000
    assert bar.open == 50000.0
    assert bar.close == 50500.0


@pytest.mark.parametrize(
    "values",
    [
        {"timestamp": -1},
        {"timestamp": 2**63},
        {"open": float("nan")},
        {"high": 90.0, "low": 100.0},
        {"open": 120.0},
        {"close": 80.0},
        {"volume": -1.0},
    ],
)
def test_bar_direct_construction_rejects_unsafe_ohlcv_values(values):
    defaults = {
        "timestamp": 1,
        "open": 100.0,
        "high": 110.0,
        "low": 90.0,
        "close": 105.0,
        "volume": 1.0,
    }
    defaults.update(values)

    with pytest.raises(ValueError, match="invalid OHLCV bar"):
        Bar(**defaults)


def test_bar_direct_construction_normalizes_safe_numeric_values():
    bar = Bar("1", "100", "110", "90", "105", "1.5")

    assert bar == Bar(1, 100.0, 110.0, 90.0, 105.0, 1.5)


def test_order_creation():
    order = Order(
        id="test-001",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=0.1,
        price=50000.0,
        stop_loss=49000.0,
        take_profit=52000.0,
    )
    assert order.id == "test-001"
    assert order.status == OrderStatus.PENDING
    assert order.fill_price is None


def test_order_status_includes_partial_fill_without_changing_existing_values():
    assert OrderStatus.PARTIALLY_FILLED.value == "partially_filled"
    assert OrderStatus.PENDING.value == "pending"
    assert OrderStatus.FILLED.value == "filled"
    assert OrderStatus.CANCELLED.value == "cancelled"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0])
def test_exchange_trade_snapshot_sanitizes_invalid_prices(value):
    trade = ExchangeTradeSnapshot(
        exchange_trade_id="trade-invalid-price",
        exchange_order_id="ex-invalid-price",
        client_order_id="client-invalid-price",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        amount=1.0,
        price=value,
        fee=0.01,
        timestamp=1,
    )

    assert trade.price == 0.0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0])
def test_exchange_order_snapshot_sanitizes_invalid_prices(value):
    snapshot = ExchangeOrderSnapshot(
        exchange_order_id="ex-invalid-price",
        client_order_id="client-invalid-price",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=1.0,
        price=value,
        status=OrderStatus.PENDING,
        fill_price=value,
        timestamp=1,
        updated_at=2,
    )

    assert snapshot.price == 0.0
    assert snapshot.fill_price == 0.0
    assert math.isfinite(snapshot.price)
    assert math.isfinite(snapshot.fill_price)


@pytest.mark.parametrize("timestamp", [float("nan"), float("inf"), -1.0, "invalid"])
def test_exchange_order_snapshot_normalizes_invalid_timestamps(timestamp):
    snapshot = ExchangeOrderSnapshot(
        exchange_order_id="ex-invalid-timestamp",
        client_order_id="client-invalid-timestamp",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=1.0,
        price=100.0,
        status=OrderStatus.PENDING,
        fill_price=100.0,
        timestamp=timestamp,
        updated_at=timestamp,
    )

    assert snapshot.timestamp == 0
    assert snapshot.updated_at == 0


def test_exchange_snapshots_expose_filled_amounts():
    order = ExchangeOrderSnapshot(
        exchange_order_id="ex-1",
        client_order_id="client-1",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=1.0,
        price=100.0,
        status=OrderStatus.PARTIALLY_FILLED,
        fill_price=100.0,
        timestamp=1,
        updated_at=2,
        filled_amount=0.25,
    )
    trade = ExchangeTradeSnapshot(
        exchange_trade_id="trade-1",
        exchange_order_id="ex-1",
        client_order_id="client-1",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        amount=0.25,
        price=100.0,
        fee=0.01,
        timestamp=2,
    )

    assert order.filled_amount == 0.25
    assert order.remaining_amount == 0.75
    assert trade.filled_amount == 0.25


def test_exchange_order_snapshot_preserves_explicit_remaining_as_authoritative():
    order = ExchangeOrderSnapshot(
        exchange_order_id="ex-remaining",
        client_order_id="client-remaining",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=1.0,
        price=100.0,
        status=OrderStatus.PARTIALLY_FILLED,
        fill_price=100.0,
        timestamp=1,
        updated_at=2,
        remaining_amount=0.6,
    )

    assert order.remaining_amount == 0.6
    assert order.filled_amount == 0.4


def test_exchange_order_snapshot_sanitizes_invalid_fill_amounts():
    order = ExchangeOrderSnapshot(
        exchange_order_id="ex-invalid",
        client_order_id="client-invalid",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=1.0,
        price=100.0,
        status=OrderStatus.PENDING,
        fill_price=100.0,
        timestamp=1,
        updated_at=2,
        filled_amount=-1.0,
        remaining_amount=float("nan"),
    )

    assert order.filled_amount == 0.0
    assert order.remaining_amount == 1.0


@pytest.mark.parametrize(
    ("status", "filled_amount", "expected_status"),
    [
        (OrderStatus.PENDING, 0.5, OrderStatus.PARTIALLY_FILLED),
        (OrderStatus.FILLED, 0.0, OrderStatus.FILLED),
        (OrderStatus.FILLED, 0.5, OrderStatus.PARTIALLY_FILLED),
        (OrderStatus.PARTIALLY_FILLED, 0.0, OrderStatus.PENDING),
        (OrderStatus.PENDING, 1.0, OrderStatus.FILLED),
        (OrderStatus.CANCELLED, 0.0, OrderStatus.CANCELLED),
        (OrderStatus.REJECTED, 0.0, OrderStatus.REJECTED),
    ],
)
def test_exchange_order_snapshot_normalizes_status_from_filled_amount(
    status, filled_amount, expected_status
):
    order = ExchangeOrderSnapshot(
        exchange_order_id="ex-status",
        client_order_id="client-status",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=1.0,
        price=100.0,
        status=status,
        fill_price=100.0,
        timestamp=1,
        updated_at=2,
        filled_amount=filled_amount,
    )

    assert order.status == expected_status


def test_exchange_order_snapshot_uses_filled_when_remaining_exceeds_amount():
    order = ExchangeOrderSnapshot(
        exchange_order_id="ex-invalid-remaining",
        client_order_id="client-invalid-remaining",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=1.0,
        price=100.0,
        status=OrderStatus.PENDING,
        fill_price=100.0,
        timestamp=1,
        updated_at=2,
        filled_amount=0.25,
        remaining_amount=2.0,
    )

    assert order.filled_amount == 0.25
    assert order.remaining_amount == 0.75


def test_exchange_order_snapshot_rejects_inconsistent_explicit_fill_group():
    with pytest.raises(ValueError, match="inconsistent filled and remaining amounts"):
        ExchangeOrderSnapshot(
            exchange_order_id="ex-authoritative-remaining",
            client_order_id="client-authoritative-remaining",
            symbol="BTC-USDT",
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            amount=1.0,
            price=100.0,
            status=OrderStatus.PARTIALLY_FILLED,
            fill_price=100.0,
            timestamp=1,
            updated_at=2,
            filled_amount=0.4000000003,
            remaining_amount=0.6,
        )


def test_exchange_order_snapshot_preserves_decimal_float_total_invariant():
    order = ExchangeOrderSnapshot(
        exchange_order_id="ex-float-invariant",
        client_order_id="client-float-invariant",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=0.3,
        price=100.0,
        status=OrderStatus.PARTIALLY_FILLED,
        fill_price=100.0,
        timestamp=1,
        updated_at=2,
        remaining_amount=0.1,
    )

    assert order.filled_amount + order.remaining_amount == order.amount
    assert order.remaining_amount == 0.1


def test_exchange_order_snapshot_preserves_high_precision_total_invariant():
    amount = 0.1234567890123
    remaining = 0.1

    order = ExchangeOrderSnapshot(
        exchange_order_id="ex-high-precision",
        client_order_id="client-high-precision",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=amount,
        price=100.0,
        status=OrderStatus.PARTIALLY_FILLED,
        fill_price=100.0,
        timestamp=1,
        updated_at=2,
        remaining_amount=remaining,
    )

    assert order.filled_amount == amount - remaining
    assert order.filled_amount + order.remaining_amount == order.amount


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0])
def test_exchange_trade_snapshot_normalizes_invalid_amounts(value):
    trade = ExchangeTradeSnapshot(
        exchange_trade_id="trade-invalid",
        exchange_order_id="ex-invalid",
        client_order_id="client-invalid",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        amount=value,
        price=100.0,
        fee=0.01,
        timestamp=1,
        filled_amount=value,
    )

    assert math.isfinite(trade.amount)
    assert trade.amount >= 0.0
    assert math.isfinite(trade.filled_amount)
    assert trade.filled_amount >= 0.0


@pytest.mark.parametrize("timestamp", [float("nan"), float("inf"), -float("inf"), "invalid"])
def test_exchange_trade_snapshot_normalizes_invalid_timestamp(timestamp):
    trade = ExchangeTradeSnapshot(
        exchange_trade_id="trade-invalid-timestamp",
        exchange_order_id="ex-1",
        client_order_id="client-1",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        amount=0.1,
        price=100.0,
        fee=0.01,
        timestamp=timestamp,
    )

    assert trade.timestamp == 0


def test_blank_trade_fingerprint_uses_exchange_side_fields_without_external_ids():
    fingerprint = stable_blank_trade_fingerprint(
        exchange_order_id="",
        client_order_id="",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        amount=0.1,
        price=100.0,
        fee=0.01,
        timestamp=123,
    )

    assert fingerprint
    assert fingerprint == stable_blank_trade_fingerprint(
        exchange_order_id="",
        client_order_id="",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        amount=0.1,
        price=100.0,
        fee=0.01,
        timestamp=123,
    )
    assert fingerprint != stable_blank_trade_fingerprint(
        exchange_order_id="",
        client_order_id="",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        amount=0.1,
        price=100.0,
        fee=0.01,
        timestamp=124,
    )


def test_exchange_trade_snapshot_defaults_filled_amount_to_normalized_amount():
    trade = ExchangeTradeSnapshot(
        exchange_trade_id="trade-default",
        exchange_order_id="ex-default",
        client_order_id="client-default",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        amount=float("inf"),
        price=100.0,
        fee=0.01,
        timestamp=1,
    )

    assert trade.amount == 0.0
    assert trade.filled_amount == 0.0


@pytest.mark.parametrize("field", ["amount", "price", "fee"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0])
def test_trade_record_sanitizes_invalid_numeric_fields(field, value):
    trade = TradeRecord(
        exchange_trade_id="trade-invalid",
        order_id="order-1",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        amount=value if field == "amount" else 0.1,
        price=value if field == "price" else 50000.0,
        fee=value if field == "fee" else 0.01,
        timestamp=1700000000000,
    )

    assert getattr(trade, field) == 0.0


def test_trade_record_serializes_normalized_values():
    trade = TradeRecord(
        exchange_trade_id="trade-valid",
        order_id="order-1",
        strategy="ma_cross",
        symbol="BTC-USDT",
        side="buy",
        amount=0.1,
        price=50000.0,
        fee=0.01,
        timestamp=1700000000000,
    )

    assert trade.model_dump()["amount"] == 0.1
    assert trade.model_dump()["price"] == 50000.0
    assert trade.model_dump()["fee"] == 0.01


def test_position_creation():
    pos = Position(
        symbol="BTC-USDT-SWAP",
        side=PositionSide.LONG,
        amount=0.5,
        entry_price=50000.0,
        unrealized_pnl=250.0,
        leverage=10,
    )
    assert pos.symbol == "BTC-USDT-SWAP"
    assert pos.leverage == 10


@pytest.mark.parametrize("timestamp", [2**63, 1e308])
def test_exchange_snapshots_reject_timestamps_outside_sqlite_int64(timestamp):
    order = ExchangeOrderSnapshot(
        exchange_order_id="ex-overflow",
        client_order_id="client-overflow",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=1.0,
        price=100.0,
        status=OrderStatus.PENDING,
        fill_price=0.0,
        timestamp=timestamp,
        updated_at=timestamp,
        filled_amount=float("inf"),
    )
    trade = ExchangeTradeSnapshot(
        exchange_trade_id="trade-overflow",
        exchange_order_id="ex-overflow",
        client_order_id="client-overflow",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        amount=0.1,
        price=100.0,
        fee=0.0,
        timestamp=timestamp,
    )

    assert (order.timestamp, order.updated_at, trade.timestamp) == (0, 0, 0)
    assert order.filled_amount == 0.0


def test_timestamp_parsing_accepts_exact_max_int64_without_float_rounding():
    max_int64 = 2**63 - 1

    assert _safe_timestamp(max_int64) == max_int64
    assert _safe_timestamp(str(max_int64)) == max_int64
    assert _safe_timestamp(float(1)) == 1


@pytest.mark.parametrize("value", [2**63, -1, 1.5, "1.5", float("nan"), float("inf")])
def test_timestamp_parsing_rejects_non_integer_or_unsafe_values(value):
    assert _safe_timestamp(value) == 0


def test_order_record_snapshot_provenance_is_excluded_from_serialization():
    order = OrderRecord(
        order_id="order-1",
        strategy="s",
        symbol="BTC-USDT",
        side="buy",
        type="limit",
        amount=1.0,
        price=100.0,
        status="pending",
        fill_price=0.0,
        timestamp=1,
        snapshot_updated_at=2,
    )

    assert order.snapshot_updated_at == 2
    assert "snapshot_updated_at" not in order.model_dump()


@pytest.mark.parametrize("value", [10**1000])
def test_exchange_order_snapshot_sanitizes_numeric_conversion_overflow(value):
    snapshot = ExchangeOrderSnapshot(
        exchange_order_id="ex-numeric-overflow",
        client_order_id="client-numeric-overflow",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=value,
        price=value,
        status=OrderStatus.PENDING,
        fill_price=value,
        timestamp=1,
        updated_at=2,
        filled_amount=value,
        remaining_amount=value,
    )
    trade = ExchangeTradeSnapshot(
        exchange_trade_id="trade-numeric-overflow",
        exchange_order_id="ex-numeric-overflow",
        client_order_id="client-numeric-overflow",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        amount=value,
        price=value,
        fee=value,
        timestamp=1,
        filled_amount=value,
    )

    assert (snapshot.amount, snapshot.price, snapshot.fill_price) == (0.0, 0.0, 0.0)
    assert (snapshot.filled_amount, snapshot.remaining_amount) == (0.0, 0.0)
    assert (trade.amount, trade.price, trade.fee, trade.filled_amount) == (
        0.0,
        0.0,
        0.0,
        0.0,
    )


def test_account_snapshot_uses_authoritative_updated_at_for_timestamp():
    snapshot = AccountSnapshot(timestamp=10, updated_at=20)

    assert snapshot.timestamp == 20
    assert snapshot.updated_at == 20


def test_account_snapshot_normalizes_balances_pnl_assets_and_timestamps():
    snapshot = AccountSnapshot(
        initial_equity="bad",
        equity=-1,
        cash_balance=float("inf"),
        available_balance=float("nan"),
        realized_pnl=-3.5,
        unrealized_pnl="bad",
        daily_pnl=-float("inf"),
        fees_paid=-2,
        timestamp=10,
        updated_at=2**63,
        assets=[
            AssetBalance(
                ccy="USDT",
                cash_bal=-1,
                eq=float("inf"),
                eq_utd="bad",
                avail_bal=float("nan"),
                upl=-4.5,
            )
        ],
    )

    assert (
        snapshot.initial_equity,
        snapshot.equity,
        snapshot.cash_balance,
        snapshot.available_balance,
        snapshot.realized_pnl,
        snapshot.unrealized_pnl,
        snapshot.daily_pnl,
        snapshot.fees_paid,
    ) == (0.0, 0.0, 0.0, 0.0, -3.5, 0.0, 0.0, 0.0)
    assert (snapshot.timestamp, snapshot.updated_at) == (10, 10)
    assert snapshot.assets == [
        AssetBalance(ccy="USDT", upl=-4.5)
    ]


def test_account_snapshot_skips_invalid_assets_and_requires_nonempty_currency():
    snapshot = AccountSnapshot(
        assets=[
            AssetBalance(ccy="USDT", avail_bal="2.5"),
            {"ccy": "", "avail_bal": 3},
            {"ccy": "   ", "avail_bal": 4},
            {"avail_bal": 5},
            None,
            1,
            {"ccy": "BTC", "avail_bal": "bad", "upl": "bad"},
        ]
    )

    assert snapshot.assets == [
        AssetBalance(ccy="USDT", avail_bal=2.5),
        AssetBalance(ccy="BTC"),
    ]


def test_account_record_normalizes_direct_assets_like_account_snapshot():
    account = AccountRecord(
        strategy="s",
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
            {"ccy": "", "avail_bal": 3},
            {"ccy": "   "},
            {"avail_bal": 4},
            None,
            1,
            {"ccy": "BTC", "avail_bal": "bad", "upl": "bad"},
        ],
    )

    assert account.assets == [
        {"ccy": "USDT", "cash_bal": 0.0, "eq": 0.0, "eq_utd": 0.0, "avail_bal": 2.5, "upl": 0.0},
        {"ccy": "BTC", "avail_bal": 0.0, "upl": 0.0},
    ]


def test_account_record_normalizes_direct_construction_values():
    account = AccountRecord(
        strategy="s",
        initial_equity=-1,
        cash_balance=float("inf"),
        available_balance=float("nan"),
        equity="bad",
        realized_pnl=-3.5,
        unrealized_pnl=float("inf"),
        daily_pnl=-float("inf"),
        fees_paid=-2,
        updated_at=2**63,
        assets=[{"ccy": "USDT", "cash_bal": -1, "eq": float("inf"), "upl": -4.5}],
    )

    assert (
        account.initial_equity,
        account.cash_balance,
        account.available_balance,
        account.equity,
        account.realized_pnl,
        account.unrealized_pnl,
        account.daily_pnl,
        account.fees_paid,
        account.updated_at,
    ) == (0.0, 0.0, 0.0, 0.0, -3.5, 0.0, 0.0, 0.0, 0)
    assert account.assets == [
        {"ccy": "USDT", "cash_bal": 0.0, "eq": 0.0, "upl": -4.5}
    ]


def test_runtime_order_normalizes_direct_construction_values():
    order = Order(
        id="unsafe-order",
        symbol="BTC-USDT",
        side=OrderSide.SELL,
        type=OrderType.LIMIT,
        amount=-float("inf"),
        price=float("nan"),
        fill_price=-1.0,
        fill_time=2**63,
        updated_at=-1,
    )

    assert order.amount == 0.0
    assert order.price is None
    assert order.fill_price is None
    assert order.fill_time == 0
    assert order.updated_at == 0
    assert order.side is OrderSide.SELL


def test_runtime_position_normalizes_direct_construction_values():
    position = Position(
        symbol="BTC-USDT-SWAP",
        side=PositionSide.SHORT,
        amount=-2.0,
        entry_price=float("inf"),
        unrealized_pnl=-3.5,
        leverage=2**63,
        mark_price=float("nan"),
        realized_pnl=float("inf"),
        timestamp=-1,
        updated_at=2**63,
    )

    assert position.amount == 2.0
    assert position.entry_price == 0.0
    assert position.mark_price is None
    assert position.unrealized_pnl == pytest.approx(-3.5)
    assert position.realized_pnl == 0.0
    assert position.leverage == 1
    assert position.timestamp == 0
    assert position.updated_at == 0
    assert position.side is PositionSide.SHORT


def test_exchange_order_snapshot_preserves_exchange_terminal_status_without_fill_data():
    for status in (OrderStatus.FILLED,):
        snapshot = ExchangeOrderSnapshot(
            exchange_order_id="terminal-without-fill",
            client_order_id="client-terminal",
            symbol="BTC-USDT",
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            amount=1.0,
            price=100.0,
            status=status,
            fill_price=0.0,
            timestamp=1,
            updated_at=2,
            filled_amount=float("nan"),
            remaining_amount=float("nan"),
        )

        assert snapshot.status is OrderStatus.FILLED
        assert snapshot.filled_amount == 0.0
        assert snapshot.remaining_amount == snapshot.amount


def test_order_remaining_amount_is_authoritative_and_derives_filled_amount():
    order = Order(
        id="order-remaining",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=10.0,
        status=OrderStatus.PARTIALLY_FILLED,
        remaining_amount=3.0,
    )

    assert order.filled_amount == 7.0
    assert order.remaining_amount == 3.0
    assert order.filled_amount + order.remaining_amount == order.amount
    assert order.status is OrderStatus.PARTIALLY_FILLED


def test_order_preserves_explicit_partial_fill_and_remaining_quantities():
    order = Order(
        id="order-partial",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=10.0,
        status=OrderStatus.PARTIALLY_FILLED,
        filled_amount=4.0,
        remaining_amount=6.0,
    )

    assert (order.filled_amount, order.remaining_amount) == (4.0, 6.0)


def test_order_rejects_inconsistent_explicit_fill_group():
    with pytest.raises(ValueError, match="inconsistent filled and remaining amounts"):
        Order(
            id="order-inconsistent",
            symbol="BTC-USDT",
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            amount=10.0,
            status=OrderStatus.PARTIALLY_FILLED,
            filled_amount=4.0,
            remaining_amount=5.0,
        )


def test_order_terminal_statuses_preserve_cancel_and_reject_semantics():
    for status in (OrderStatus.CANCELLED, OrderStatus.REJECTED):
        order = Order(
            id=f"order-{status.value}",
            symbol="BTC-USDT",
            side=OrderSide.SELL,
            type=OrderType.MARKET,
            amount=10.0,
            status=status,
            remaining_amount=3.0,
        )

        assert order.status is status
        assert order.filled_amount == 7.0
        assert order.remaining_amount == 3.0


def test_order_filled_status_without_quantity_input_derives_full_fill():
    order = Order(
        id="order-filled",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=10.0,
        status=OrderStatus.FILLED,
    )

    assert order.status is OrderStatus.FILLED
    assert order.filled_amount == 10.0
    assert order.remaining_amount == 0.0
