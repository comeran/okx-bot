import pytest
from sqlmodel import Session, select

from src.data.allocations import AllocationScope, AllocationStore
from src.data.models import AccountRecord, PositionRecord
from src.data.paper_book import (
    PaperBook,
    PaperBookError,
    PaperBookNotFound,
    PaperReservationAuditRecord,
)
from src.data.repository import Repository


def scope(**overrides):
    return AllocationScope(**{
        "account_fingerprint": "a" * 64,
        "environment": "sandbox",
        "market_type": "spot",
        **overrides,
    })


def build(path):
    repository = Repository(db_path=str(path))
    store = AllocationStore(repository.engine)
    return repository, store, PaperBook(repository.engine)


def save(store, book_scope, **overrides):
    return store.save_draft(book_scope, **{
        "strategy": "alpha",
        "symbol": "BTC-USDT",
        "budget": 600,
        "expected_revision": 0,
        "timestamp": 1000,
        **overrides,
    })


def activate(book, book_scope, strategy, symbol, revision, equity=1000, timestamp=2000):
    return book.activate(
        book_scope.scope_id, strategy, symbol=symbol,
        expected_revision=revision, book_equity=equity, timestamp=timestamp,
    )


class _Code:
    def __init__(self, code):
        self.code = code

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, _traceback):
        if exc is None:
            raise AssertionError(f"expected {self.code}")
        if not isinstance(exc, PaperBookError) or exc.code != self.code:
            return False
        return True


def test_activation_is_exclusive_releasable_and_audited(tmp_path):
    _repository, store, book = build(tmp_path / "paper.db")
    primary = scope()
    save(store, primary)
    save(store, primary, strategy="beta", symbol="ETH-USDT", budget=700)
    first = activate(book, primary, "alpha", "BTC-USDT", 1, equity=1200)
    assert (first.budget, first.revision, first.scope_id) == (600, 1, primary.scope_id)
    with _Code("budget_exceeds_book"):
        activate(book, primary, "beta", "ETH-USDT", 1, equity=1200, timestamp=3000)
    save(
        store, primary, strategy="beta", symbol="ETH-USDT",
        budget=400, expected_revision=1, timestamp=3500,
    )
    assert activate(
        book, primary, "beta", "ETH-USDT", 2, equity=1200, timestamp=4000,
    ).budget == 400
    save(store, primary, strategy="gamma", symbol="SOL-USDT", budget=50)
    with _Code("book_equity_changed"):
        activate(book, primary, "gamma", "SOL-USDT", 1, equity=2000, timestamp=4500)
    other = scope(account_fingerprint="b" * 64)
    save(store, other, budget=100)
    with _Code("reservation_scope_conflict"):
        activate(book, other, "alpha", "BTC-USDT", 1, timestamp=5000)
    store.delete_draft(primary, "alpha", expected_revision=1, timestamp=7000)
    save(store, primary, strategy="delta", symbol="BTC-USDT", budget=100)
    with _Code("symbol_conflict"):
        activate(book, primary, "delta", "BTC-USDT", 1, equity=1200, timestamp=8000)
    book.release(
        primary.scope_id, "alpha", expected_revision=1,
        symbol="BTC-USDT", timestamp=9000,
    )
    assert book.get(primary.scope_id, "alpha") is None
    assert activate(
        book, primary, "delta", "BTC-USDT", 1, equity=1200, timestamp=10000,
    ).budget == 100
    with Session(book.engine) as session:
        actions = [row.action for row in session.exec(select(PaperReservationAuditRecord)).all()]
    assert "release" in actions
    assert actions.count("activate") >= 2


def test_open_paper_position_blocks_activation_and_flat_account_is_retired(tmp_path):
    repository, store, book = build(tmp_path / "paper.db")
    primary = scope()
    save(store, primary, budget=100)
    with Session(book.engine) as session:
        session.add(PositionRecord(
            strategy="alpha", symbol="BTC-USDT", side="long", amount=1,
            entry_price=10, leverage=1, timestamp=1,
            source="paper", runtime_mode="paper",
        ))
        session.commit()
    with _Code("position_open"):
        activate(book, primary, "alpha", "BTC-USDT", 1, timestamp=2)
    assert book.get(primary.scope_id, "alpha") is None
    with Session(book.engine) as session:
        position = session.exec(select(PositionRecord)).one()
        position.active = False
        session.add(AccountRecord(
            strategy="alpha", source="paper", runtime_mode="paper",
            initial_equity=50, cash_balance=50, equity=50,
            realized_pnl=0, unrealized_pnl=0, daily_pnl=0, fees_paid=0, updated_at=1,
        ))
        session.commit()
    activate(book, primary, "alpha", "BTC-USDT", 1, timestamp=3)
    assert repository.get_account("alpha", source="paper", runtime_mode="paper") is None
    retired = repository.get_account(
        "alpha", source="paper", runtime_mode="paper", include_inactive=True,
    )
    assert retired is not None and retired.active is False
    with _Code("reservation_active"):
        activate(book, primary, "alpha", "BTC-USDT", 1, timestamp=4)


def test_missing_draft_does_not_reserve(tmp_path):
    _repository, _store, book = build(tmp_path / "paper.db")
    with pytest.raises(PaperBookNotFound) as caught:
        activate(book, scope(), "alpha", "BTC-USDT", 0, timestamp=1)
    assert caught.value.code == "allocation_not_found"
