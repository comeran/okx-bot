"""Read one exact OKX SPOT fills-history page through an injected raw client.

No market loading, unified CCXT trade parsing, persistence, automatic pagination,
or historical completeness inference happens here. The endpoint is limited to
the provider's retained history; exhausting pages cannot prove all-time history.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from src.core.private_facts import (
    MISSING,
    FactCoverage,
    SpotFillFact,
    SpotFillPage,
    canonical_spot_symbol,
    validate_raw_text,
)
from src.exchange.errors import MalformedProviderData


class RawOKXFillExchange(Protocol):
    async def private_get_trade_fills_history(self, params: dict[str, str]) -> object: ...


def parse_okx_spot_fill(row: object) -> SpotFillFact:
    """Fail closed on malformed rows and preserve missing/null/empty optionals.

    A missing fillTime is an error even when ts is present. A native two-asset
    instrument ID alone does not distinguish margin, so instType must be SPOT.
    """
    if not isinstance(row, Mapping) or row.get("instType") != "SPOT":
        raise MalformedProviderData("expected raw OKX SPOT fill")
    try:
        return SpotFillFact(
            bill_id=row.get("billId"),
            inst_id=row.get("instId"),
            trade_id=row.get("tradeId"),
            ord_id=row.get("ordId"),
            side=row.get("side"),
            fill_sz=row.get("fillSz"),
            fill_px=row.get("fillPx"),
            fill_time=row.get("fillTime"),
            record_time=row.get("ts"),
            cl_ord_id=row.get("clOrdId", MISSING),
            fee=row.get("fee", MISSING),
            fee_ccy=row.get("feeCcy", MISSING),
        )
    except ValueError as exc:
        raise MalformedProviderData(str(exc)) from exc


def _financial_payload(fact: SpotFillFact) -> tuple[object, ...]:
    # A changed record timestamp or optional client tag does not alter a fill.
    # Different raw financial strings fail closed even if numerically equal;
    # resolving those ambiguities belongs to reconciliation, not ingestion.
    return (
        fact.inst_id,
        fact.trade_id,
        fact.ord_id,
        fact.side,
        fact.fill_sz,
        fact.fill_px,
        fact.fee,
        fact.fee_ccy,
        fact.fill_time,
    )


def _validate_request(
    *,
    inst_id: str | None,
    after: str | None,
    before: str | None,
    limit: int,
    previous_pages: Sequence[SpotFillPage],
) -> dict[str, SpotFillFact]:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer from 1 to 100")
    if inst_id is not None:
        canonical_spot_symbol(inst_id)
    for name, cursor in (("after", after), ("before", before)):
        if cursor is not None:
            validate_raw_text(cursor, name)
    known: dict[str, SpotFillFact] = {}
    for page in previous_pages:
        if page.inst_id != inst_id or page.requested_before != before:
            raise ValueError("prior pages must use the same instrument and before boundary")
        if page.requested_after == after:
            raise ValueError("repeated request cursor")
        for fact in page.fills:
            observed = known.get(fact.bill_id)
            if observed is not None and _financial_payload(observed) != _financial_payload(fact):
                raise MalformedProviderData("conflicting financial payload in prior pages")
            known[fact.bill_id] = fact
    return known


def parse_okx_spot_fill_page(
    payload: object,
    *,
    inst_id: str | None = None,
    after: str | None = None,
    before: str | None = None,
    limit: int = 100,
    previous_pages: Sequence[SpotFillPage] = (),
) -> SpotFillPage:
    """Validate the raw success envelope and observable cursor progress.

    Cursors are opaque: only equality, repetition, and observed overlaps can be
    checked. Supply every earlier page in this traversal to detect cycles and
    conflicts across calls. Without that evidence, cross-page checks are
    impossible. Neither row count nor undocumented completion flags is proof.
    """
    known = _validate_request(
        inst_id=inst_id,
        after=after,
        before=before,
        limit=limit,
        previous_pages=previous_pages,
    )
    if (
        not isinstance(payload, Mapping)
        or payload.get("code") != "0"
        or not isinstance(payload.get("data"), list)
        or len(payload["data"]) > limit
    ):
        raise MalformedProviderData("invalid OKX fills success envelope")

    fills = tuple(parse_okx_spot_fill(row) for row in payload["data"])
    current: dict[str, SpotFillFact] = {}
    issues: list[str] = []
    for fact in fills:
        if inst_id is not None and fact.inst_id != inst_id:
            raise MalformedProviderData("fill instrument does not match request")
        for observed in (known.get(fact.bill_id), current.get(fact.bill_id)):
            if observed is not None and _financial_payload(observed) != _financial_payload(fact):
                raise MalformedProviderData("conflicting financial payload for duplicate billId")
        if fact.bill_id in current and "duplicate_bill_id" not in issues:
            issues.append("duplicate_bill_id")
        if fact.bill_id in known and "overlapping_bill_id" not in issues:
            issues.append("overlapping_bill_id")
        current[fact.bill_id] = fact

    next_after = fills[-1].bill_id if fills else None
    if any(cursor is not None and cursor in current for cursor in (after, before)):
        raise MalformedProviderData("non-advancing cursor: page includes request boundary")
    if next_after is not None and next_after in known:
        raise MalformedProviderData("non-advancing cursor or duplicate page")
    if current and any(
        set(current) == {fact.bill_id for fact in page.fills} for page in previous_pages
    ):
        raise MalformedProviderData("duplicate page")
    return SpotFillPage(
        fills=fills,
        next_after=next_after,
        coverage=FactCoverage.PARTIAL if fills else FactCoverage.UNKNOWN,
        requested_after=after,
        requested_before=before,
        inst_id=inst_id,
        issues=tuple(issues),
    )


class OKXSpotFillFactsAdapter:
    """A single raw request; the caller owns credentials, client, and traversal.

    A pinned CCXT OKX client exposes private_get_trade_fills_history. Injection
    keeps network transport replaceable without touching unified projections.
    Prior pages are caller-provided evidence, not durable synchronization state.
    """

    def __init__(self, exchange: RawOKXFillExchange) -> None:
        self._exchange = exchange

    async def fetch_page(
        self,
        *,
        inst_id: str | None = None,
        after: str | None = None,
        before: str | None = None,
        limit: int = 100,
        previous_pages: Sequence[SpotFillPage] = (),
    ) -> SpotFillPage:
        _validate_request(
            inst_id=inst_id,
            after=after,
            before=before,
            limit=limit,
            previous_pages=previous_pages,
        )
        params = {"instType": "SPOT", "limit": str(limit)}
        for name, value in (("instId", inst_id), ("after", after), ("before", before)):
            if value is not None:
                params[name] = value
        payload = await self._exchange.private_get_trade_fills_history(params)
        return parse_okx_spot_fill_page(
            payload,
            inst_id=inst_id,
            after=after,
            before=before,
            limit=limit,
            previous_pages=previous_pages,
        )
