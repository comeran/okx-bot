"""Explicitly bounded raw ingestion into a scoped durable facts store.

These helpers never retry, certify history completeness, or modify the
compatibility projections, accounting ledger, sync receipts or activation state.
The repository owns atomic commits and validates stored traversal evidence.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from src.core.private_facts import SpotFillPage, canonical_spot_symbol, validate_raw_text
from src.data.models import (
    ExactSpotFillCursorRecord,
    PrivateAccountBalanceRecord,
    PrivateAccountConfigRecord,
    PrivateSpotInstrumentRecord,
    PrivateSpotTradeFeeRecord,
)
from src.exchange.okx_account_facts import (
    AccountConfigFact,
    OKXAccountConfigFactsAdapter,
    RawOKXAccountConfigExchange,
)
from src.exchange.okx_balance_facts import (
    AccountBalanceFact,
    OKXAccountBalanceFactsAdapter,
    RawOKXAccountBalanceExchange,
)
from src.exchange.okx_instrument_facts import (
    OKXSpotInstrumentFactsAdapter,
    RawOKXInstrumentExchange,
    SpotInstrumentFact,
)
from src.exchange.okx_private_facts import OKXSpotFillFactsAdapter, RawOKXFillExchange
from src.exchange.okx_trade_fee_facts import (
    OKXSpotTradeFeeFactsAdapter,
    RawOKXTradeFeeExchange,
    SpotTradeFeeFact,
)


class ExactPrivateFactsRepository(Protocol):
    def get_private_uid(self) -> str | None: ...

    def commit_private_spot_trade_fee(
        self, fact: SpotTradeFeeFact, *, raw_payload: object, received_at: int,
        uid: str | None = None,
    ) -> PrivateSpotTradeFeeRecord: ...

    def commit_private_spot_instruments(
        self, facts: tuple[SpotInstrumentFact, ...], *, raw_payload: object,
        received_at: int, requested_inst_id: str | None = None,
    ) -> list[PrivateSpotInstrumentRecord]: ...

    def commit_private_account_balance(
        self, fact: AccountBalanceFact, *, raw_payload: object, received_at: int,
        uid: str | None = None,
    ) -> PrivateAccountBalanceRecord: ...

    def commit_private_account_config(
        self, fact: AccountConfigFact, *, raw_payload: object, received_at: int,
    ) -> PrivateAccountConfigRecord: ...

    def get_exact_spot_fill_cursor(
        self, *, uid: str, inst_id: str | None = None, before: str | None = None,
    ) -> ExactSpotFillCursorRecord | None: ...

    def commit_exact_spot_fill_page(
        self, page: SpotFillPage, *, uid: str, raw_payload: object, received_at: int,
        expected_cursor_version: int = 0,
    ) -> ExactSpotFillCursorRecord: ...


def _validate_received_at(received_at: object) -> None:
    if type(received_at) is not int or not 0 <= received_at <= 2**63 - 1:
        raise ValueError("received timestamp must be an integer from 0 to 2**63 - 1")


class _CapturedConfigExchange:
    """Retain the adapter's response for the same atomic raw/typed commit."""

    def __init__(self, exchange: RawOKXAccountConfigExchange) -> None:
        self.exchange = exchange
        self.payload: object = None

    async def private_get_account_config(self, params: dict[str, str]) -> object:
        self.payload = await self.exchange.private_get_account_config(params)
        return self.payload


class _CapturedInstrumentExchange:
    def __init__(self, exchange: RawOKXInstrumentExchange) -> None:
        self.exchange = exchange
        self.payload: object = None

    async def public_get_public_instruments(self, params: dict[str, str]) -> object:
        self.payload = await self.exchange.public_get_public_instruments(params)
        return self.payload


async def sync_exact_spot_instruments(
    repository: ExactPrivateFactsRepository,
    exchange: RawOKXInstrumentExchange,
    received_at: int,
    *, inst_id: str | None = None,
) -> list[PrivateSpotInstrumentRecord]:
    """Retain exactly one public SPOT response, without requiring a private UID."""
    _validate_received_at(received_at)
    captured = _CapturedInstrumentExchange(exchange)
    facts = await OKXSpotInstrumentFactsAdapter(captured).fetch(inst_id)
    await asyncio.sleep(0)
    return repository.commit_private_spot_instruments(
        facts, raw_payload=captured.payload, received_at=received_at,
        requested_inst_id=inst_id,
    )


class _CapturedBalanceExchange:
    def __init__(self, exchange: RawOKXAccountBalanceExchange) -> None:
        self.exchange = exchange
        self.payload: object = None

    async def private_get_account_balance(self, params: dict[str, str]) -> object:
        self.payload = await self.exchange.private_get_account_balance(params)
        return self.payload


class _CapturedFillExchange:
    def __init__(self, exchange: RawOKXFillExchange) -> None:
        self.exchange = exchange
        self.payload: object = None

    async def private_get_trade_fills_history(self, params: dict[str, str]) -> object:
        self.payload = await self.exchange.private_get_trade_fills_history(params)
        return self.payload


class _CapturedTradeFeeExchange:
    def __init__(self, exchange: RawOKXTradeFeeExchange) -> None:
        self.exchange = exchange
        self.payload: object = None

    async def private_get_account_trade_fee(self, params: dict[str, str]) -> object:
        self.payload = await self.exchange.private_get_account_trade_fee(params)
        return self.payload


async def sync_exact_spot_trade_fee(
    repository: ExactPrivateFactsRepository, exchange: RawOKXTradeFeeExchange,
    received_at: int, *, inst_id: str, uid: str | None = None,
) -> PrivateSpotTradeFeeRecord:
    """Collect one rate response for an already-bound UID and explicit symbol.

    The caller owns transport for the bound account; this identity-free response
    cannot verify credential ownership. No reserve, fee policy or execution
    permission is derived from these signed raw observations.
    """
    canonical_spot_symbol(inst_id)
    _validate_received_at(received_at)
    bound_uid = repository.get_private_uid()
    if bound_uid is None:
        raise ValueError("Spot trade fee requires an existing private UID binding")
    if uid is not None:
        validate_raw_text(uid, "UID")
        if uid != bound_uid:
            raise ValueError("Private UID binding conflict")
    captured = _CapturedTradeFeeExchange(exchange)
    fact = await OKXSpotTradeFeeFactsAdapter(captured).fetch(inst_id)
    await asyncio.sleep(0)
    return repository.commit_private_spot_trade_fee(
        fact, uid=bound_uid, raw_payload=captured.payload, received_at=received_at,
    )


async def sync_exact_account_config(
    repository: ExactPrivateFactsRepository,
    exchange: RawOKXAccountConfigExchange,
    received_at: int,
) -> PrivateAccountConfigRecord:
    """Fetch one raw config, bind its UID and return detached stored evidence."""
    _validate_received_at(received_at)
    captured = _CapturedConfigExchange(exchange)
    fact = await OKXAccountConfigFactsAdapter(captured).fetch()
    # Honor cancellation already requested by transport before durable writes.
    await asyncio.sleep(0)
    return repository.commit_private_account_config(
        fact, raw_payload=captured.payload, received_at=received_at,
    )


async def sync_exact_account_balance(
    repository: ExactPrivateFactsRepository,
    exchange: RawOKXAccountBalanceExchange,
    received_at: int,
    *,
    uid: str | None = None,
) -> PrivateAccountBalanceRecord:
    """Collect one raw balance for a previously bound UID, without funding policy.

    The endpoint does not supply identity. The caller must provide the transport
    for the already bound account; this helper cannot verify credential ownership.
    """
    _validate_received_at(received_at)
    bound_uid = repository.get_private_uid()
    if bound_uid is None:
        raise ValueError("Account balance requires an existing private UID binding")
    if uid is not None:
        validate_raw_text(uid, "UID")
        if uid != bound_uid:
            raise ValueError("Private UID binding conflict")
    captured = _CapturedBalanceExchange(exchange)
    fact = await OKXAccountBalanceFactsAdapter(captured).fetch()
    await asyncio.sleep(0)
    return repository.commit_private_account_balance(
        fact, uid=bound_uid, raw_payload=captured.payload, received_at=received_at,
    )


async def sync_exact_spot_fill_page(
    repository: ExactPrivateFactsRepository,
    exchange: RawOKXFillExchange,
    *,
    uid: str,
    inst_id: str | None = None,
    before: str | None = None,
    limit: int = 100,
    received_at: int,
) -> ExactSpotFillCursorRecord:
    """Resume one filter-bound traversal and atomically commit exactly one page.

    An empty tail stays unknown. A concurrent writer can invalidate the captured
    version; repository conflicts propagate without retry or cursor substitution.
    """
    _validate_received_at(received_at)
    cursor = repository.get_exact_spot_fill_cursor(uid=uid, inst_id=inst_id, before=before)
    captured = _CapturedFillExchange(exchange)
    page = await OKXSpotFillFactsAdapter(captured).fetch_page(
        inst_id=inst_id, after=cursor.next_after if cursor is not None else None,
        before=before, limit=limit,
    )
    await asyncio.sleep(0)
    return repository.commit_exact_spot_fill_page(
        page, uid=uid, raw_payload=captured.payload, received_at=received_at,
        expected_cursor_version=cursor.version if cursor is not None else 0,
    )


@dataclass(frozen=True)
class ExactSpotFillCursor:
    """Immutable snapshot of the durable traversal returned to a catch-up caller."""

    traversal_id: str
    account_scope_id: str
    uid: str
    endpoint: str
    inst_type: str
    inst_id: str | None
    requested_before: str | None
    version: int
    next_after: str | None
    complete: bool


@dataclass(frozen=True)
class ExactSpotFillCatchUpResult:
    """Bounded observations, never a complete history or an accounting watermark."""

    pages_committed: int
    last_cursor: ExactSpotFillCursor
    coverage: Literal["unknown", "partial"]
    stop_reason: Literal["empty_page", "max_pages"]


async def catch_up_exact_spot_fills(
    repository: ExactPrivateFactsRepository,
    exchange: RawOKXFillExchange,
    *,
    uid: str,
    inst_id: str | None = None,
    before: str | None = None,
    limit: int = 100,
    max_pages: int = 10,
    received_at_factory: int | Callable[[], int],
) -> ExactSpotFillCatchUpResult:
    """Commit sequential pages until an empty observation or explicit page bound.

    Every request resumes the durable filter-bound cursor through the one-page
    helper. Short pages continue; an empty page stays unknown. Caller-supplied
    timestamps are validated before each request/commit. Exceptions and caller
    cancellation propagate without retry, preserving earlier page transactions.
    """
    if type(max_pages) is not int or max_pages <= 0:
        raise ValueError("max_pages must be a positive integer")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer from 1 to 100")
    validate_raw_text(uid, "UID")
    if inst_id is not None:
        canonical_spot_symbol(inst_id)
    if before is not None:
        validate_raw_text(before, "before")
    if not callable(received_at_factory) and type(received_at_factory) is not int:
        raise ValueError("received_at_factory must be an integer or callable")

    last_cursor: ExactSpotFillCursor | None = None
    seen: set[str] = set()
    for pages_committed in range(1, max_pages + 1):
        durable = repository.get_exact_spot_fill_cursor(uid=uid, inst_id=inst_id, before=before)
        previous = ExactSpotFillCursor(**durable.model_dump()) if durable is not None else None
        if last_cursor is not None and previous != last_cursor:
            raise ValueError("Durable cursor changed during catch-up")
        if previous is not None and previous.next_after is not None:
            seen.add(previous.next_after)
        received_at = (
            received_at_factory() if callable(received_at_factory) else received_at_factory
        )
        _validate_received_at(received_at)
        committed = await sync_exact_spot_fill_page(
            repository, exchange, uid=uid, inst_id=inst_id, before=before,
            limit=limit, received_at=received_at,
        )
        last_cursor = ExactSpotFillCursor(**committed.model_dump())
        if last_cursor.version != (previous.version if previous is not None else 0) + 1:
            raise ValueError("Committed cursor version did not advance by one")
        if last_cursor.next_after is None:
            return ExactSpotFillCatchUpResult(pages_committed, last_cursor, "unknown", "empty_page")
        if last_cursor.next_after in seen:
            raise ValueError("Committed cursor did not advance")
        seen.add(last_cursor.next_after)
        if pages_committed == max_pages:
            return ExactSpotFillCatchUpResult(pages_committed, last_cursor, "partial", "max_pages")
    raise AssertionError("positive max_pages must commit a page or raise")
