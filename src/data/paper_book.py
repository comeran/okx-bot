"""本地模拟资金簿。草稿仍然不授权实盘，这里只隔离本地模拟预算。"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import JSON, Column, UniqueConstraint, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field, Session, SQLModel, select

from src.exchange.base import canonical_symbol, is_supported_canonical_symbol


class PaperBookError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PaperBookNotFound(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PaperReservation:
    strategy: str
    symbol: str
    budget: float
    revision: int
    scope_id: str
    environment: str
    account_fingerprint: str
    draft_revision: int
    created_at: int
    updated_at: int


class PaperBookRecord(SQLModel, table=True):
    __tablename__ = "paper_book"

    book_id: str = Field(primary_key=True)
    equity: float
    updated_at: int


class PaperReservationRecord(SQLModel, table=True):
    __tablename__ = "paper_reservation"
    __table_args__ = (
        UniqueConstraint("book_id", "strategy", name="uq_paper_strategy"),
        UniqueConstraint("book_id", "active_symbol", name="uq_paper_symbol"),
    )

    id: int | None = Field(default=None, primary_key=True)
    book_id: str = Field(index=True)
    strategy: str
    symbol: str
    active_symbol: str | None = None
    budget: float
    revision: int
    scope_id: str
    environment: str
    account_fingerprint: str
    draft_revision: int
    created_at: int
    updated_at: int


class PaperReservationAuditRecord(SQLModel, table=True):
    __tablename__ = "paper_reservation_audit"
    __table_args__ = (
        UniqueConstraint(
            "scope_id", "strategy", "revision", name="uq_paper_reservation_audit",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    scope_id: str = Field(index=True)
    strategy: str
    revision: int
    action: str
    before: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    after: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    timestamp: int


def normalize_spot_symbol(symbol: str) -> str:
    normalized = canonical_symbol(symbol, market_type="spot")
    if not is_supported_canonical_symbol(normalized, market_type="spot"):
        raise ValueError("Paper reservations require one spot symbol")
    return normalized


def _same_money(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=0.0, abs_tol=1e-8)


def _positive_money(value: object, code: str, message: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PaperBookError(code, message)
    amount = float(value)
    if not math.isfinite(amount) or amount <= 0:
        raise PaperBookError(code, message)
    return amount


def _revision(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PaperBookError("revision_conflict", "Allocation revision changed")
    return value


def _reservation(record: PaperReservationRecord) -> PaperReservation:
    return PaperReservation(
        strategy=record.strategy,
        symbol=record.symbol,
        budget=record.budget,
        revision=record.revision,
        scope_id=record.scope_id,
        environment=record.environment,
        account_fingerprint=record.account_fingerprint,
        draft_revision=record.draft_revision,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _is_paper(record: Any) -> bool:
    source = getattr(record, "source", None) or "unknown"
    runtime_mode = getattr(record, "runtime_mode", None) or "unknown"
    return source == "paper" or runtime_mode == "paper"


def retire_flat_paper_state(session: Session, strategy: str, timestamp: int) -> None:
    """平仓后停用旧的本地模拟账户，避免和下一笔预算叠在一起。"""
    from src.data.models import AccountRecord, PositionRecord

    positions = session.exec(select(PositionRecord).where(
        PositionRecord.strategy == strategy,
        or_(PositionRecord.active.is_(True), PositionRecord.active.is_(None)),
    )).all()
    for position in positions:
        if _is_paper(position) and position.amount > 0:
            raise PaperBookError(
                "position_open",
                "Close the paper position before changing its reservation",
            )
    accounts = session.exec(select(AccountRecord).where(
        AccountRecord.strategy == strategy,
        or_(AccountRecord.active.is_(True), AccountRecord.active.is_(None)),
    )).all()
    for account in accounts:
        if not _is_paper(account):
            continue
        account.active = False
        account.stale_at = timestamp
        session.add(account)


class PaperBook:
    def __init__(self, engine: Any) -> None:
        self.engine = engine
        SQLModel.metadata.create_all(engine, tables=[
            PaperBookRecord.__table__,
            PaperReservationRecord.__table__,
            PaperReservationAuditRecord.__table__,
        ])

    def get(self, scope_id: str, strategy: str) -> PaperReservation | None:
        with Session(self.engine) as session:
            record = self._active_record(session, scope_id, strategy)
            return _reservation(record) if record is not None else None

    def owner(self, scope_id: str, symbol: str) -> PaperReservation | None:
        try:
            normalized = normalize_spot_symbol(symbol)
        except ValueError:
            return None
        with Session(self.engine) as session:
            record = session.exec(select(PaperReservationRecord).where(
                PaperReservationRecord.book_id == scope_id,
                PaperReservationRecord.active_symbol == normalized,
            )).first()
            return _reservation(record) if record is not None else None

    def for_strategy(self, strategy: str) -> list[PaperReservation]:
        with Session(self.engine) as session:
            records = session.exec(select(PaperReservationRecord).where(
                PaperReservationRecord.strategy == strategy,
                PaperReservationRecord.active_symbol.is_not(None),
            )).all()
            return [_reservation(record) for record in records]

    def scope_has_active(self, scope_id: str) -> bool:
        with Session(self.engine) as session:
            return session.exec(select(PaperReservationRecord.id).where(
                PaperReservationRecord.book_id == scope_id,
                PaperReservationRecord.active_symbol.is_not(None),
            )).first() is not None

    def has_active(self) -> bool:
        with Session(self.engine) as session:
            return session.exec(select(PaperReservationRecord.id).where(
                PaperReservationRecord.active_symbol.is_not(None),
            )).first() is not None

    @staticmethod
    def _active_record(
        session: Session, scope_id: str, strategy: str,
    ) -> PaperReservationRecord | None:
        record = session.exec(select(PaperReservationRecord).where(
            PaperReservationRecord.book_id == scope_id,
            PaperReservationRecord.strategy == strategy,
        ).with_for_update()).first()
        if record is None or record.active_symbol is None:
            return None
        return record

    def activate(
        self,
        scope_id: str,
        strategy: str,
        *,
        symbol: str,
        expected_revision: int,
        book_equity: float,
        timestamp: int,
    ) -> PaperReservation:
        if not isinstance(scope_id, str) or not scope_id.strip():
            raise PaperBookError(
                "context_unavailable", "Configured spot account context is unavailable",
            )
        revision = _revision(expected_revision)
        equity = _positive_money(book_equity, "invalid_book", "Local paper book equity is invalid")
        normalized = normalize_spot_symbol(symbol)
        if not strategy.strip() or strategy.startswith("__"):
            raise PaperBookError("strategy_not_found", "A strategy name is required")
        try:
            with Session(self.engine) as session, session.begin():
                draft = self._draft(session, scope_id, strategy, revision, normalized)
                budget = _positive_money(
                    draft.budget, "invalid_budget", "Budget must be a positive finite number",
                )
                self._reject_other_scope(session, strategy, scope_id)
                current = session.exec(select(PaperReservationRecord).where(
                    PaperReservationRecord.book_id == scope_id,
                    PaperReservationRecord.strategy == strategy,
                ).with_for_update()).first()
                if current is not None and current.active_symbol is not None:
                    raise PaperBookError(
                        "reservation_active",
                        "Release the local paper reservation before activating it again",
                    )
                active_rows = session.exec(select(PaperReservationRecord).where(
                    PaperReservationRecord.book_id == scope_id,
                    PaperReservationRecord.active_symbol.is_not(None),
                )).all()
                book = session.exec(select(PaperBookRecord).where(
                    PaperBookRecord.book_id == scope_id,
                ).with_for_update()).first()
                if book is not None and active_rows and not _same_money(book.equity, equity):
                    raise PaperBookError(
                        "book_equity_changed", "Local paper book equity changed",
                    )
                cap = book.equity if book is not None and active_rows else equity
                used = sum(row.budget for row in active_rows)
                if used + budget > cap + 1e-8:
                    raise PaperBookError(
                        "budget_exceeds_book", "Paper budgets exceed the local book",
                    )
                if book is None:
                    session.add(PaperBookRecord(
                        book_id=scope_id, equity=cap, updated_at=timestamp,
                    ))
                elif not active_rows and not _same_money(book.equity, cap):
                    book.equity = cap
                    book.updated_at = timestamp
                    session.add(book)
                retire_flat_paper_state(session, strategy, timestamp)
                before = None
                if current is None:
                    current = PaperReservationRecord(
                        book_id=scope_id,
                        strategy=strategy,
                        symbol=normalized,
                        active_symbol=normalized,
                        budget=budget,
                        revision=1,
                        scope_id=scope_id,
                        environment=draft.environment,
                        account_fingerprint=draft.account_fingerprint,
                        draft_revision=draft.revision,
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                else:
                    current.symbol = normalized
                    current.active_symbol = normalized
                    current.budget = budget
                    current.revision += 1
                    current.environment = draft.environment
                    current.account_fingerprint = draft.account_fingerprint
                    current.draft_revision = draft.revision
                    current.updated_at = timestamp
                result = _reservation(current)
                session.add(current)
                session.add(PaperReservationAuditRecord(
                    scope_id=scope_id,
                    strategy=strategy,
                    revision=result.revision,
                    action="activate",
                    before=before,
                    after=asdict(result),
                    timestamp=timestamp,
                ))
            return result
        except IntegrityError as exc:
            self._reraise_unique(exc)

    def release(
        self,
        scope_id: str,
        strategy: str,
        *,
        expected_revision: int,
        symbol: str,
        timestamp: int,
    ) -> None:
        revision = _revision(expected_revision)
        normalized = normalize_spot_symbol(symbol)
        with Session(self.engine) as session, session.begin():
            record = session.exec(select(PaperReservationRecord).where(
                PaperReservationRecord.book_id == scope_id,
                PaperReservationRecord.strategy == strategy,
            ).with_for_update()).first()
            if record is None or record.active_symbol is None:
                raise PaperBookNotFound(
                    "reservation_not_found", "Local paper reservation not found",
                )
            if record.revision != revision:
                raise PaperBookError("revision_conflict", "Allocation revision changed")
            if record.symbol != normalized:
                raise PaperBookError(
                    "reservation_symbol_changed",
                    "Strategy symbol changed; reload the reservation",
                )
            retire_flat_paper_state(session, strategy, timestamp)
            before = asdict(_reservation(record))
            record.active_symbol = None
            record.revision += 1
            record.updated_at = timestamp
            session.add(record)
            session.add(PaperReservationAuditRecord(
                scope_id=scope_id,
                strategy=strategy,
                revision=record.revision,
                action="release",
                before=before,
                after=None,
                timestamp=timestamp,
            ))

    @staticmethod
    def _draft(
        session: Session,
        scope_id: str,
        strategy: str,
        expected_revision: int,
        symbol: str,
    ) -> Any:
        from src.data.allocations import AllocationDraftRecord

        draft = session.exec(select(AllocationDraftRecord).where(
            AllocationDraftRecord.scope_id == scope_id,
            AllocationDraftRecord.strategy == strategy,
        ).with_for_update()).first()
        if draft is None or draft.deleted:
            raise PaperBookNotFound("allocation_not_found", "Allocation draft not found")
        if draft.revision != expected_revision:
            raise PaperBookError("revision_conflict", "Allocation revision changed")
        if draft.symbol != symbol:
            raise PaperBookError("symbol_conflict", "Draft symbol does not match")
        return draft

    @staticmethod
    def _reject_other_scope(session: Session, strategy: str, scope_id: str) -> None:
        other = session.exec(select(PaperReservationRecord).where(
            PaperReservationRecord.strategy == strategy,
            PaperReservationRecord.book_id != scope_id,
            PaperReservationRecord.active_symbol.is_not(None),
        )).first()
        if other is not None:
            raise PaperBookError(
                "reservation_scope_conflict",
                "This strategy already reserved funds on another account",
            )

    @staticmethod
    def _reraise_unique(exc: IntegrityError) -> None:
        message = str(getattr(exc, "orig", exc))
        if "UNIQUE constraint failed" not in message:
            raise exc
        if "active_symbol" in message:
            raise PaperBookError(
                "symbol_conflict", "Another reservation already uses this symbol",
            ) from exc
        raise PaperBookError(
            "reservation_active", "Local paper reservation already exists",
        ) from exc
