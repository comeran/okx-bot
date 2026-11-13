import sqlite3
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, localcontext
from importlib import import_module
from threading import Barrier

import pytest
from sqlalchemy import create_engine, inspect

from src.core.execution_scope import ExecutionScope
from src.data.exchange_store import open_exchange_repository
from src.data.repository import Repository


def module():
    return import_module("src.data.spot_ledger")


def scope(letter="a", environment="sandbox"):
    return ExecutionScope("okx", environment, letter * 64)


@pytest.fixture
def ledger(tmp_path):
    master = Repository(db_path=str(tmp_path / "master.db"))
    facts = open_exchange_repository(scope(), master_engine=master.engine)
    return module().SpotLedgerStore(facts.engine, scope())


def seed(ledger, **changes):
    values = dict(
        strategy="alpha",
        symbol="BTC-USDT",
        initial_quote="100",
        allocation_scope_id="saved-scope",
        draft_revision=1,
        timestamp=1,
    )
    values.update(changes)
    return ledger.initialize_strategy(**values)


def reserve(ledger, **changes):
    values = dict(
        strategy="alpha",
        order_id="o1",
        side="buy",
        amount="1",
        price_cap="90",
        fee_buffer="1",
        timestamp=2,
    )
    values.update(changes)
    return ledger.reserve_order(**values)


def fill(ledger, **changes):
    values = dict(
        strategy="alpha",
        order_id="o1",
        trade_id="t1",
        amount="0.5",
        price="80",
        fee="0.5",
        fee_currency="USDT",
        timestamp=3,
    )
    values.update(changes)
    return ledger.apply_fill(**values)


def terminal(ledger, **changes):
    values = dict(
        strategy="alpha",
        order_id="o1",
        status="canceled",
        confirmed_filled_quantity="0.5",
        timestamp=5,
    )
    values.update(changes)
    return ledger.confirm_terminal(**values)


def state(ledger):
    return (ledger.get_strategy("alpha"), ledger.get_order("o1"), ledger.get_events("alpha"))


def test_cash_only_prepared_seed_is_exact_and_does_not_touch_master(tmp_path):
    master = Repository(db_path=str(tmp_path / "master.db"))
    facts = open_exchange_repository(scope(), master_engine=master.engine)
    store = module().SpotLedgerStore(facts.engine, scope())
    assert store.get_strategy("alpha") is None
    book = seed(store, initial_quote=Decimal("0.3"))
    assert {
        key: book[key]
        for key in (
            "strategy",
            "symbol",
            "currency",
            "base_currency",
            "initial_quote",
            "quote_balance",
            "quote_reserved",
            "quote_available",
            "base_balance",
            "base_reserved",
            "base_available",
            "cost_basis_quote",
            "realized_pnl_quote",
            "allocation_scope_id",
            "draft_revision",
            "status",
            "execution_ready",
        )
    } == dict(
        strategy="alpha",
        symbol="BTC-USDT",
        currency="USDT",
        base_currency="BTC",
        initial_quote="0.3",
        quote_balance="0.3",
        quote_reserved="0",
        quote_available="0.3",
        base_balance="0",
        base_reserved="0",
        base_available="0",
        cost_basis_quote="0",
        realized_pnl_quote="0",
        allocation_scope_id="saved-scope",
        draft_revision=1,
        status="prepared",
        execution_ready=False,
    )
    Repository(engine=master.engine)
    assert not any(
        name.startswith("spot_ledger_") for name in inspect(master.engine).get_table_names()
    )
    assert len(store.get_events("alpha")) == 1
    book["fees"]["USDT"] = "999"
    assert store.get_strategy("alpha")["fees"]["USDT"] == "0"


@pytest.mark.parametrize(
    "invalid",
    [
        True,
        0.1,
        "NaN",
        "Infinity",
        "-1",
        "0",
        "1e2",
        " 1",
        "1000000000000000000",
        "0.0000000000000000001",
        Decimal("1E-1000000000"),
    ],
)
def test_seed_rejects_invalid_or_unbounded_decimal_without_events(ledger, invalid):
    with pytest.raises(module().SpotLedgerError):
        seed(ledger, initial_quote=invalid)
    assert ledger.get_strategy("alpha") is None
    assert ledger.get_events("alpha") == []


@pytest.mark.parametrize(
    "changes",
    [
        dict(initial_quote="101"),
        dict(draft_revision=2),
        dict(allocation_scope_id="new-scope"),
        dict(symbol="ETH-USDT"),
    ],
)
def test_seed_retry_cannot_mint_or_reassign_budget(ledger, changes):
    before = seed(ledger)
    assert seed(ledger, initial_quote="100.0", timestamp=99) == before
    with pytest.raises(module().SpotLedgerError) as error:
        seed(ledger, **changes)
    assert error.value.code == "conflict"
    assert ledger.get_strategy("alpha") == before
    assert len(ledger.get_events("alpha")) == 1


def test_symbol_is_exclusive_but_account_files_can_reuse_identities(tmp_path):
    master = Repository(db_path=str(tmp_path / "master.db"))
    stores = [
        module().SpotLedgerStore(
            open_exchange_repository(identity, master_engine=master.engine).engine,
            identity,
        )
        for identity in [scope(), scope("b"), scope(environment="live")]
    ]
    for store in stores:
        seed(store)
        reserve(store)
        fill(store)
        with pytest.raises(module().SpotLedgerError):
            seed(store, strategy="beta")
    assert [store.get_strategy("alpha")["quote_balance"] for store in stores] == ["59.5"] * 3


def test_constructor_rejects_master_wrong_scope_memory_and_symlink_before_schema(tmp_path):
    master = Repository(db_path=str(tmp_path / "master.db"))
    facts = open_exchange_repository(scope(), master_engine=master.engine)
    link = tmp_path / "linked.db"
    link.symlink_to(facts.engine.url.database)
    engines = [
        (master.engine, scope()),
        (facts.engine, scope("b")),
        (create_engine("sqlite:///:memory:"), scope()),
        (create_engine(f"sqlite:///{link}"), scope()),
    ]
    for engine, identity in engines:
        with pytest.raises(module().SpotLedgerError):
            module().SpotLedgerStore(engine, identity)
    assert not any(
        name.startswith("spot_ledger_") for name in inspect(master.engine).get_table_names()
    )
    assert not any(
        name.startswith("spot_ledger_") for name in inspect(facts.engine).get_table_names()
    )


def test_partial_buy_uses_unspent_fee_buffer_and_cancel_releases_only_remaining(ledger):
    seed(ledger)
    reserve(ledger)
    assert ledger.get_strategy("alpha")["quote_available"] == "9"
    fill(ledger)
    book = ledger.get_strategy("alpha")
    assert (
        book["quote_balance"],
        book["quote_reserved"],
        book["quote_available"],
        book["base_balance"],
        book["cost_basis_quote"],
    ) == ("59.5", "45.5", "14", "0.5", "40.5")
    assert book["fees"] == {"BTC": "0", "USDT": "0.5"}
    ledger.mark_unknown(strategy="alpha", order_id="o1", timestamp=4)
    assert ledger.get_strategy("alpha")["quote_reserved"] == "45.5"
    assert ledger.get_order("o1")["status"] == "unknown"
    terminal(ledger)
    book = ledger.get_strategy("alpha")
    assert (book["quote_balance"], book["quote_reserved"], book["quote_available"]) == (
        "59.5",
        "0",
        "59.5",
    )
    assert [event["event_type"] for event in ledger.get_events("alpha")] == [
        "initialize",
        "reserve",
        "fill",
        "unknown",
        "terminal",
    ]


def test_exact_quote_arithmetic_ignores_global_decimal_context_and_survives_reopen(ledger):
    with localcontext() as context:
        context.prec = 2
        seed(ledger, initial_quote="1.000000000000000001")
        reserve(
            ledger, amount="0.123456789012345678", price_cap="2", fee_buffer="0.000000000000000001"
        )
        fill(ledger, amount="0.123456789012345678", price="2", fee="0.000000000000000001")
    reopened = module().SpotLedgerStore(create_engine(f"sqlite:///{ledger.path}"), scope())
    book = reopened.get_strategy("alpha")
    assert (
        book["quote_balance"],
        book["quote_reserved"],
        book["base_balance"],
        book["cost_basis_quote"],
    ) == (
        "0.753086421975308644",
        "0",
        "0.123456789012345678",
        "0.246913578024691357",
    )
    with sqlite3.connect(ledger.path) as connection:
        assert connection.execute(
            "SELECT typeof(quote_balance), quote_balance FROM spot_ledger_book"
        ).fetchone() == ("text", "0.753086421975308644")


def test_buy_base_fee_is_net_holding_and_capitalized_quote_cost(ledger):
    seed(ledger)
    reserve(ledger)
    fill(ledger, amount="1", fee="0.1", fee_currency="BTC")
    book = ledger.get_strategy("alpha")
    assert (book["base_balance"], book["quote_balance"], book["cost_basis_quote"]) == (
        "0.9",
        "20",
        "80",
    )
    assert book["fees"] == {"BTC": "0.1", "USDT": "0"}


@pytest.mark.parametrize(
    "changes",
    [
        dict(fee="0.5", fee_currency="BTC"),
        dict(fee="0.6", fee_currency="BTC"),
        dict(fee="-0.1"),
        dict(fee_currency="BNB"),
        dict(fee_currency=""),
        dict(fee="2"),
        dict(price="91"),
        dict(amount="1.1"),
        dict(amount=True),
        dict(price=80.0),
    ],
)
def test_invalid_fill_is_fully_rolled_back(ledger, changes):
    seed(ledger)
    reserve(ledger)
    before = state(ledger)
    with pytest.raises(module().SpotLedgerError):
        fill(ledger, **changes)
    assert state(ledger) == before


def buy_inventory(ledger, amount="3", price="10"):
    seed(ledger)
    reserve(ledger, amount=amount, price_cap=price, fee_buffer="0")
    fill(ledger, amount=amount, price=price, fee="0")
    terminal(ledger, status="filled", confirmed_filled_quantity=amount)


def test_sell_base_fee_is_reserved_and_reduces_cost_without_shorting(ledger):
    buy_inventory(ledger)
    reserve(ledger, order_id="sell", side="sell", amount="1", price_cap=None, fee_buffer="0.1")
    book = ledger.get_strategy("alpha")
    assert (book["base_reserved"], book["base_available"]) == ("1.1", "1.9")
    fill(
        ledger,
        order_id="sell",
        trade_id="sell-fill",
        amount="1",
        price="20",
        fee="0.1",
        fee_currency="BTC",
    )
    book = ledger.get_strategy("alpha")
    assert (
        book["base_balance"],
        book["quote_balance"],
        book["cost_basis_quote"],
        book["realized_pnl_quote"],
        book["base_reserved"],
    ) == ("1.9", "90", "19", "9", "0")


def test_sell_quote_fee_can_consume_proceeds_and_final_cost_is_exactly_cleared(ledger):
    buy_inventory(ledger)
    reserve(ledger, order_id="sell", side="sell", amount="3", price_cap=None, fee_buffer="0")
    fill(ledger, order_id="sell", trade_id="s1", amount="1", price="20", fee="20")
    fill(ledger, order_id="sell", trade_id="s2", amount="2", price="20", fee="0")
    book = ledger.get_strategy("alpha")
    assert (
        book["base_balance"],
        book["cost_basis_quote"],
        book["realized_pnl_quote"],
        book["quote_balance"],
    ) == ("0", "0", "10", "110")


def test_recurring_average_cost_has_no_residual_after_final_sell(ledger):
    seed(ledger)
    reserve(ledger, amount="3", price_cap="1", fee_buffer="1")
    fill(ledger, amount="3", price="1", fee="1")
    terminal(ledger, status="filled", confirmed_filled_quantity="3")
    reserve(ledger, order_id="sell", side="sell", amount="3", price_cap=None, fee_buffer="0")
    for index in range(3):
        fill(ledger, order_id="sell", trade_id=f"s{index}", amount="1", price="2", fee="0")
    book = ledger.get_strategy("alpha")
    assert (book["cost_basis_quote"], book["base_balance"], book["realized_pnl_quote"]) == (
        "0",
        "0",
        "2",
    )


def test_sell_quote_fee_cannot_exceed_fill_proceeds(ledger):
    buy_inventory(ledger)
    reserve(ledger, order_id="sell", side="sell", amount="1", price_cap=None, fee_buffer="0")
    before = (ledger.get_strategy("alpha"), ledger.get_order("sell"), ledger.get_events("alpha"))
    with pytest.raises(module().SpotLedgerError):
        fill(ledger, order_id="sell", trade_id="s1", amount="1", price="10", fee="11")
    assert (
        ledger.get_strategy("alpha"),
        ledger.get_order("sell"),
        ledger.get_events("alpha"),
    ) == before


def test_naked_sell_unbounded_buy_and_other_order_cannot_spend_reserved_assets(ledger):
    seed(ledger)
    for changes in [dict(side="sell", amount="0.1"), dict(price_cap=None), dict(amount="2")]:
        with pytest.raises(module().SpotLedgerError):
            reserve(ledger, **changes)
    reserve(ledger)
    with pytest.raises(module().SpotLedgerError):
        reserve(ledger, order_id="o2", amount="0.2", fee_buffer="0")
    assert ledger.get_strategy("alpha")["quote_reserved"] == "91"


def test_replays_do_not_reaccount_and_changed_fill_identity_conflicts(ledger):
    seed(ledger)
    reserve(ledger)
    before_reserve = state(ledger)
    reserve(ledger, amount="1.0", timestamp=99)
    assert state(ledger) == before_reserve
    fill(ledger)
    before_fill = state(ledger)
    fill(ledger, amount="0.50", price="80.0")
    assert state(ledger) == before_fill
    for changes in [dict(amount="0.4"), dict(price="79"), dict(timestamp=999)]:
        with pytest.raises(module().SpotLedgerError) as error:
            fill(ledger, **changes)
        assert error.value.code == "conflict"
        assert state(ledger) == before_fill


def test_order_and_fill_identity_cannot_cross_strategy_boundaries(ledger):
    seed(ledger)
    seed(ledger, strategy="beta", symbol="ETH-USDT")
    reserve(ledger)
    with pytest.raises(module().SpotLedgerError):
        reserve(ledger, strategy="beta")
    fill(ledger)
    reserve(ledger, strategy="beta", order_id="o2")
    before = ledger.get_strategy("beta")
    with pytest.raises(module().SpotLedgerError):
        fill(ledger, strategy="beta", order_id="o2")
    assert ledger.get_strategy("beta") == before


@pytest.mark.parametrize(
    "changes",
    [
        dict(confirmed_filled_quantity="0.6"),
        dict(confirmed_filled_quantity="0.4"),
        dict(status="filled"),
        dict(status="rejected"),
        dict(status="pending"),
        dict(confirmed_filled_quantity=None),
    ],
)
def test_terminal_requires_exact_accounted_checkpoint_before_release(ledger, changes):
    seed(ledger)
    reserve(ledger)
    fill(ledger)
    before = state(ledger)
    with pytest.raises(module().SpotLedgerError):
        terminal(ledger, **changes)
    assert state(ledger) == before


def test_terminal_retry_is_idempotent_but_cannot_revive_or_accept_new_late_fill(ledger):
    seed(ledger)
    reserve(ledger)
    fill(ledger)
    terminal(ledger)
    before = state(ledger)
    terminal(ledger, timestamp=999)
    fill(ledger)
    assert state(ledger) == before
    for operation in [
        lambda: reserve(ledger, amount="2"),
        lambda: terminal(ledger, status="filled"),
        lambda: fill(ledger, trade_id="late"),
    ]:
        with pytest.raises(module().SpotLedgerError):
            operation()
        assert state(ledger) == before


def test_exact_reservation_retry_after_terminal_does_not_revive_or_change_audit(ledger):
    seed(ledger)
    reserve(ledger)
    fill(ledger)
    terminal(ledger)
    before = state(ledger)
    assert reserve(ledger, amount="1.00", timestamp=99) == before[0]
    assert state(ledger) == before


@pytest.mark.parametrize(
    "changes",
    [
        dict(symbol="BTC-BTC"),
        dict(strategy=""),
        dict(allocation_scope_id=""),
        dict(draft_revision=True),
    ],
)
def test_invalid_identity_or_self_pair_never_initializes(ledger, changes):
    with pytest.raises(module().SpotLedgerError):
        seed(ledger, **changes)
    assert ledger.get_strategy("alpha") is None
    assert ledger.get_events("alpha") == []


@pytest.mark.parametrize(
    "changes", [dict(order_id=""), dict(order_id=" o1"), dict(side=[]), dict(fee_buffer=True)]
)
def test_invalid_reservation_identity_or_input_does_not_reserve(ledger, changes):
    seed(ledger)
    before = state(ledger)
    with pytest.raises(module().SpotLedgerError):
        reserve(ledger, **changes)
    assert state(ledger) == before


def test_zero_fill_rejection_releases_once_and_unknown_retries_retain_reserve(ledger):
    seed(ledger)
    reserve(ledger)
    ledger.mark_unknown(strategy="alpha", order_id="o1", timestamp=4)
    before = state(ledger)
    ledger.mark_unknown(strategy="alpha", order_id="o1", timestamp=99)
    assert state(ledger) == before
    terminal(ledger, status="rejected", confirmed_filled_quantity="0")
    assert ledger.get_strategy("alpha")["quote_available"] == "100"
    assert ledger.get_order("o1")["status"] == "rejected"


def test_sell_fill_preserves_another_orders_reserved_holdings(ledger):
    buy_inventory(ledger)
    reserve(ledger, order_id="s1", side="sell", amount="1", fee_buffer="0.1", price_cap=None)
    reserve(ledger, order_id="s2", side="sell", amount="1.9", fee_buffer="0", price_cap=None)
    fill(
        ledger, order_id="s1", trade_id="sf", amount="1", price="20", fee="0.1", fee_currency="BTC"
    )
    book = ledger.get_strategy("alpha")
    assert (book["base_balance"], book["base_reserved"], book["base_available"]) == (
        "1.9",
        "1.9",
        "0",
    )


def test_audit_insert_failure_rolls_back_fill_balances_reservation_and_event(ledger):
    seed(ledger)
    reserve(ledger)
    before = state(ledger)
    with sqlite3.connect(ledger.path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_fill_audit BEFORE INSERT ON spot_ledger_event "
            "WHEN NEW.event_type='fill' BEGIN SELECT RAISE(ABORT, 'test audit failure'); END"
        )
    with pytest.raises(module().SpotLedgerError):
        fill(ledger)
    assert state(ledger) == before
    with sqlite3.connect(ledger.path) as connection:
        connection.execute("DROP TRIGGER fail_fill_audit")
    fill(ledger)
    assert ledger.get_strategy("alpha")["quote_balance"] == "59.5"


def test_concurrent_reservations_cannot_lose_updates_with_plain_engine(ledger):
    seed(ledger)
    barrier = Barrier(2)

    def compete(index):
        store = module().SpotLedgerStore(create_engine(f"sqlite:///{ledger.path}"), scope())
        barrier.wait()
        try:
            reserve(store, order_id=f"o{index}", amount="1", price_cap="60", fee_buffer="0")
            return "accepted"
        except module().SpotLedgerError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, range(2)))
    assert sorted(results) == ["accepted", "insufficient_funds"]
    assert ledger.get_strategy("alpha")["quote_reserved"] == "60"
    assert len(ledger.get_events("alpha")) == 2


def test_concurrent_duplicate_fill_accounts_once_after_reopen(ledger):
    seed(ledger)
    reserve(ledger)
    barrier = Barrier(2)

    def compete(_):
        store = module().SpotLedgerStore(create_engine(f"sqlite:///{ledger.path}"), scope())
        barrier.wait()
        return fill(store)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, range(2)))
    assert results[0] == results[1]
    assert ledger.get_strategy("alpha")["quote_balance"] == "59.5"
    assert len(ledger.get_events("alpha")) == 3
