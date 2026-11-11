"""Account-bound allocation drafts; these records never authorize trading."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import JSON, Column, Engine, UniqueConstraint
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field, Session, SQLModel, select

from src.exchange.base import canonical_symbol, is_supported_canonical_symbol


class AllocationConflict(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AllocationNotFound(ValueError):
    pass


@dataclass(frozen=True)
class AllocationScope:
    account_fingerprint: str
    environment: str
    market_type: str = "spot"
    exchange: str = "okx"

    def __post_init__(self) -> None:
        if self.exchange != "okx" or self.market_type != "spot":
            raise ValueError("Allocation drafts support OKX spot only")
        if self.environment not in {"sandbox", "live"}:
            raise ValueError("Invalid exchange environment")
        if not re.fullmatch(r"[0-9a-f]{64}", self.account_fingerprint):
            raise ValueError("A configured credential fingerprint is required")

    @property
    def scope_id(self) -> str:
        identity = [self.exchange, self.environment, self.market_type, self.account_fingerprint]
        return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class AllocationDraft:
    strategy: str
    symbol: str
    currency: str
    budget: float
    revision: int
    scope_id: str
    environment: str
    account_fingerprint: str
    market_type: str
    created_at: int
    updated_at: int
    status: str = "draft"


class AllocationDraftRecord(SQLModel, table=True):
    __tablename__ = "allocation_draft"
    __table_args__ = (
        UniqueConstraint("scope_id", "strategy", name="uq_allocation_strategy"),
        UniqueConstraint("scope_id", "active_symbol", name="uq_allocation_symbol"),
    )

    id: int | None = Field(default=None, primary_key=True)
    scope_id: str = Field(index=True)
    strategy: str
    symbol: str
    # NULL releases the exclusive symbol while retaining the revision tombstone.
    active_symbol: str | None = None
    currency: str
    budget: float
    revision: int
    environment: str
    account_fingerprint: str
    market_type: str
    created_at: int
    updated_at: int
    deleted: bool = False


class AllocationAuditRecord(SQLModel, table=True):
    __tablename__ = "allocation_audit"
    __table_args__ = (
        UniqueConstraint("scope_id", "strategy", "revision", name="uq_allocation_audit_revision"),
    )

    id: int | None = Field(default=None, primary_key=True)
    scope_id: str = Field(index=True)
    strategy: str
    revision: int
    action: str
    before: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    after: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    timestamp: int


def spot_symbol(symbol: str) -> tuple[str, str]:
    normalized = canonical_symbol(symbol, market_type="spot")
    if not is_supported_canonical_symbol(normalized, market_type="spot"):
        raise ValueError("Allocation drafts require one spot symbol")
    return normalized, normalized.split("-")[1]


def _revision(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("Expected revision must be a nonnegative integer")


def _draft(record: AllocationDraftRecord) -> AllocationDraft:
    return AllocationDraft(**{
        field: getattr(record, field)
        for field in AllocationDraft.__dataclass_fields__
        if field != "status"
    })


def release_strategy_drafts(session: Session, strategy: str, timestamp: int) -> None:
    """Release configuration in the caller's strategy-deletion transaction."""
    records = session.exec(select(AllocationDraftRecord).where(
        AllocationDraftRecord.strategy == strategy,
        AllocationDraftRecord.deleted.is_(False),
    ).with_for_update()).all()
    for record in records:
        before = asdict(_draft(record))
        record.deleted = True
        record.active_symbol = None
        record.revision += 1
        record.updated_at = timestamp
        session.add(record)
        session.add(AllocationAuditRecord(
            scope_id=record.scope_id, strategy=strategy, revision=record.revision,
            action="strategy_deleted", before=before, after=None, timestamp=timestamp,
        ))


class AllocationStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        SQLModel.metadata.create_all(engine, tables=[
            AllocationDraftRecord.__table__, AllocationAuditRecord.__table__,
        ])

    def get_draft(self, scope: AllocationScope, strategy: str) -> AllocationDraft | None:
        with Session(self.engine) as session:
            record = self._record(session, scope, strategy)
            return _draft(record) if record is not None and not record.deleted else None

    @staticmethod
    def _record(session: Session, scope: AllocationScope, strategy: str):
        return session.exec(select(AllocationDraftRecord).where(
            AllocationDraftRecord.scope_id == scope.scope_id,
            AllocationDraftRecord.strategy == strategy,
        ).with_for_update()).first()

    def save_draft(
        self,
        scope: AllocationScope,
        *,
        strategy: str,
        symbol: str,
        budget: float,
        expected_revision: int,
        timestamp: int,
    ) -> AllocationDraft:
        _revision(expected_revision)
        if isinstance(budget, bool) or not isinstance(budget, (int, float)):
            raise ValueError("Budget must be a positive finite number")
        if not math.isfinite(budget) or budget <= 0:
            raise ValueError("Budget must be a positive finite number")
        if not strategy.strip() or strategy.startswith("__"):
            raise ValueError("A strategy name is required")
        symbol, currency = spot_symbol(symbol)
        try:
            with Session(self.engine) as session, session.begin():
                record = self._record(session, scope, strategy)
                before = None
                if record is not None and not record.deleted:
                    if record.revision != expected_revision:
                        raise AllocationConflict("revision_conflict", "Allocation revision changed")
                    if record.symbol != symbol:
                        raise AllocationConflict(
                            "symbol_changed",
                            "Remove the existing draft before changing its symbol",
                        )
                    before = asdict(_draft(record))
                    record.budget = float(budget)
                    record.revision += 1
                    record.updated_at = timestamp
                    action = "update"
                else:
                    if expected_revision != 0:
                        raise AllocationConflict("revision_conflict", "Allocation revision changed")
                    revision = record.revision + 1 if record is not None else 1
                    values = dict(
                        scope_id=scope.scope_id, strategy=strategy, symbol=symbol,
                        active_symbol=symbol, currency=currency, budget=float(budget),
                        revision=revision, environment=scope.environment,
                        account_fingerprint=scope.account_fingerprint,
                        market_type=scope.market_type, created_at=timestamp,
                        updated_at=timestamp, deleted=False,
                    )
                    if record is None:
                        record = AllocationDraftRecord(**values)
                    else:
                        for key, value in values.items():
                            setattr(record, key, value)
                    action = "create"
                result = _draft(record)
                session.add(record)
                session.add(AllocationAuditRecord(
                    scope_id=scope.scope_id, strategy=strategy, revision=result.revision,
                    action=action, before=before, after=asdict(result), timestamp=timestamp,
                ))
            return result
        except IntegrityError as exc:
            if "UNIQUE constraint failed" not in str(exc.orig):
                raise
            raise AllocationConflict(
                "symbol_conflict", "Another draft already configures this symbol",
            ) from exc

    def delete_draft(
        self,
        scope: AllocationScope,
        strategy: str,
        *,
        expected_revision: int,
        timestamp: int,
    ) -> None:
        _revision(expected_revision)
        with Session(self.engine) as session, session.begin():
            record = self._record(session, scope, strategy)
            if record is None or record.deleted:
                raise AllocationNotFound("Allocation draft not found")
            if record.revision != expected_revision:
                raise AllocationConflict("revision_conflict", "Allocation revision changed")
            before = asdict(_draft(record))
            record.deleted = True
            record.active_symbol = None
            record.revision += 1
            record.updated_at = timestamp
            session.add(record)
            session.add(AllocationAuditRecord(
                scope_id=scope.scope_id, strategy=strategy, revision=record.revision,
                action="delete", before=before, after=None, timestamp=timestamp,
            ))
