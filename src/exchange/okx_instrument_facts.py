"""Exact raw OKX SPOT instrument rules, separate from trading authorization.

These are provider rule observations, not permission to trade or freshness proof.
The caller owns transport and observation timing. No market loading, persistence,
allocation, activation or order submission happens here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from src.core.private_facts import (
    MAX_NUMERIC_TEXT_LENGTH,
    MISSING,
    OptionalRawText,
    canonical_spot_symbol,
    validate_raw_text,
)
from src.exchange.errors import MalformedProviderData

INSTRUMENT_OPTIONAL_FIELDS = {
    "state": "state",
    "rule_type": "ruleType",
    "trade_quote_ccy": "tradeQuoteCcy",
    "max_lmt_sz": "maxLmtSz",
    "max_mkt_sz": "maxMktSz",
    "lever": "lever",
}
_DECIMAL_TEXT = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")


def _validate_positive_decimal(value: object, name: str) -> None:
    # Bound and check syntax before Decimal construction. Never reserialize it.
    if (
        not isinstance(value, str)
        or not 0 < len(value) <= MAX_NUMERIC_TEXT_LENGTH
        or _DECIMAL_TEXT.fullmatch(value) is None
        or Decimal(value) <= 0
    ):
        raise ValueError(f"invalid {name}: expected bounded positive decimal string")


@dataclass(frozen=True)
class SpotInstrumentFact:
    """Immutable exact rules; optional provider text states remain distinct.

    Minimums and increments are evidence only. This object neither checks orders
    nor infers account permissions, instrument availability or rule freshness.
    """

    inst_id: str
    base_ccy: str
    quote_ccy: str
    tick_sz: str
    lot_sz: str
    min_sz: str
    state: OptionalRawText = MISSING
    rule_type: OptionalRawText = MISSING
    trade_quote_ccy: OptionalRawText = MISSING
    max_lmt_sz: OptionalRawText = MISSING
    max_mkt_sz: OptionalRawText = MISSING
    lever: OptionalRawText = MISSING

    def __post_init__(self) -> None:
        canonical_spot_symbol(self.inst_id)
        validate_raw_text(self.base_ccy, "baseCcy")
        validate_raw_text(self.quote_ccy, "quoteCcy")
        if self.inst_id != f"{self.base_ccy}-{self.quote_ccy}":
            raise ValueError("instrument currencies do not match native spot instId")
        for name in ("tick_sz", "lot_sz", "min_sz"):
            _validate_positive_decimal(getattr(self, name), name)
        for name in INSTRUMENT_OPTIONAL_FIELDS:
            value = getattr(self, name)
            if value is not MISSING and value is not None:
                validate_raw_text(value, name, allow_empty=True)


def parse_okx_spot_instruments(
    payload: object, *, inst_id: str | None = None,
) -> tuple[SpotInstrumentFact, ...]:
    """Require a raw success envelope and unique native SPOT instrument rows.

    An empty response remains empty evidence. Duplicate identities fail closed,
    including identical repetitions and conflicting raw observations. No row is
    selected as newer, preferred or safe to trade.
    """
    if inst_id is not None:
        canonical_spot_symbol(inst_id)
    if (
        not isinstance(payload, Mapping)
        or payload.get("code") != "0"
        or not isinstance(payload.get("data"), list)
    ):
        raise MalformedProviderData("invalid OKX instruments success envelope")
    facts: dict[str, SpotInstrumentFact] = {}
    for row in payload["data"]:
        if not isinstance(row, Mapping) or row.get("instType") != "SPOT":
            raise MalformedProviderData("expected raw OKX SPOT instrument")
        try:
            fact = SpotInstrumentFact(
                inst_id=row.get("instId"),
                base_ccy=row.get("baseCcy"),
                quote_ccy=row.get("quoteCcy"),
                tick_sz=row.get("tickSz"),
                lot_sz=row.get("lotSz"),
                min_sz=row.get("minSz"),
                **{name: row.get(raw_name, MISSING)
                   for name, raw_name in INSTRUMENT_OPTIONAL_FIELDS.items()},
            )
        except ValueError as exc:
            raise MalformedProviderData(str(exc)) from exc
        if inst_id is not None and fact.inst_id != inst_id:
            raise MalformedProviderData("instrument does not match request")
        if fact.inst_id in facts:
            if facts[fact.inst_id] != fact:
                raise MalformedProviderData("conflicting duplicate instrument observations")
            raise MalformedProviderData("duplicate instrument rows")
        facts[fact.inst_id] = fact
    return tuple(facts.values())


class RawOKXInstrumentExchange(Protocol):
    async def public_get_public_instruments(self, params: dict[str, str]) -> object: ...


class OKXSpotInstrumentFactsAdapter:
    """Fetch one raw public response using caller-owned transport, without retries."""

    def __init__(self, exchange: RawOKXInstrumentExchange) -> None:
        self._exchange = exchange

    async def fetch(self, inst_id: str | None = None) -> tuple[SpotInstrumentFact, ...]:
        if inst_id is not None:
            canonical_spot_symbol(inst_id)
        params = {"instType": "SPOT"}
        if inst_id is not None:
            params["instId"] = inst_id
        payload = await self._exchange.public_get_public_instruments(params)
        return parse_okx_spot_instruments(payload, inst_id=inst_id)
