"""Single-page raw ingestion into a scoped durable facts store.

These helpers never retry, loop, certify history completeness, or modify the
compatibility projections, accounting ledger, sync receipts or activation state.
The repository owns atomic commits and validates stored traversal evidence.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from src.core.private_facts import SpotFillPage, validate_raw_text
from src.data.models import (
    ExactSpotFillCursorRecord,
    PrivateAccountBalanceRecord,
    PrivateAccountConfigRecord,
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
from src.exchange.okx_private_facts import OKXSpotFillFactsAdapter, RawOKXFillExchange


class ExactPrivateFactsRepository(Protocol):
    def get_private_uid(self) -> str | None: ...

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


class _CapturedConfigExchange:
    """Retain the adapter's response for the same atomic raw/typed commit."""

    def __init__(self, exchange: RawOKXAccountConfigExchange) -> None:
        self.exchange = exchange
        self.payload: object = None

    async def private_get_account_config(self, params: dict[str, str]) -> object:
        self.payload = await self.exchange.private_get_account_config(params)
        return self.payload


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


async def sync_exact_account_config(
    repository: ExactPrivateFactsRepository,
    exchange: RawOKXAccountConfigExchange,
    received_at: int,
) -> PrivateAccountConfigRecord:
    """Fetch one raw config, bind its UID and return detached stored evidence."""
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
