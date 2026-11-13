"""Exact raw OKX SPOT order-detail observations.

The parser keeps the provider's identifiers, status and numeric strings intact.
It is diagnostic input only: it does not persist, reconcile, release reserves or
make a submission/cancellation decision.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from src.core.private_facts import (
    MISSING,
    OptionalRawText,
    canonical_spot_symbol,
    validate_raw_text,
)
from src.exchange.errors import MalformedProviderData

_DECIMAL_TEXT = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
_INTEGER_TEXT = re.compile(r"[0-9]+")
_ORDER_STATES = frozenset({
    "live", "partially_filled", "filled", "canceled", "mmp_canceled",
    "waiting", "effective", "order_failed", "amend_canceled",
})


def _malformed(message: str) -> MalformedProviderData:
    return MalformedProviderData(message)


def _required_text(value: object, name: str) -> str:
    try:
        return validate_raw_text(value, name)
    except ValueError as exc:
        raise _malformed(str(exc)) from exc


def _optional_text(value: object, name: str) -> OptionalRawText:
    if value is MISSING or value is None:
        return value
    try:
        return validate_raw_text(value, name, allow_empty=True)
    except ValueError as exc:
        raise _malformed(str(exc)) from exc


def _decimal(
    value: object, name: str, *, positive: bool = False, nonnegative: bool = False,
) -> str:
    if (
        not isinstance(value, str) or not 0 < len(value) <= 128
        or _DECIMAL_TEXT.fullmatch(value) is None
    ):
        raise _malformed(f"invalid {name}: expected bounded decimal string")
    try:
        parsed = Decimal(value)
    except (ValueError, ArithmeticError) as exc:
        raise _malformed(f"invalid {name}: expected bounded decimal string") from exc
    if positive and parsed <= 0:
        raise _malformed(f"invalid {name}: expected positive decimal string")
    if nonnegative and parsed < 0:
        raise _malformed(f"invalid {name}: expected nonnegative decimal string")
    return value


def _optional_decimal(
    value: object, name: str, *, positive: bool = False, nonnegative: bool = False,
) -> OptionalRawText:
    if value is MISSING or value is None or value == "":
        return value
    return _decimal(value, name, positive=positive, nonnegative=nonnegative)


def _timestamp(value: object, name: str) -> str:
    if (
        not isinstance(value, str) or not 0 < len(value) <= 128
        or _INTEGER_TEXT.fullmatch(value) is None or int(value) > 2**63 - 1
    ):
        raise _malformed(f"invalid {name}: expected bounded millisecond string")
    return value


def _optional_timestamp(value: object, name: str) -> OptionalRawText:
    if value is MISSING or value is None or value == "":
        return value
    return _timestamp(value, name)


@dataclass(frozen=True)
class SpotOrderFact:
    """One exact native SPOT order response, without float conversion."""

    inst_type: str
    inst_id: str
    ord_id: str
    cl_ord_id: OptionalRawText = MISSING
    tag: OptionalRawText = MISSING
    side: str = ""
    ord_type: str = ""
    px: OptionalRawText = MISSING
    sz: str = ""
    acc_fill_sz: OptionalRawText = MISSING
    avg_px: OptionalRawText = MISSING
    state: str = ""
    c_time: str = ""
    u_time: str = ""
    fill_time: OptionalRawText = MISSING
    trade_id: OptionalRawText = MISSING
    fee: OptionalRawText = MISSING
    fee_ccy: OptionalRawText = MISSING
    cancel_source: OptionalRawText = MISSING
    cancel_source_reason: OptionalRawText = MISSING
    rebate: OptionalRawText = MISSING
    rebate_ccy: OptionalRawText = MISSING

    def __post_init__(self) -> None:
        if not isinstance(self.inst_type, str) or self.inst_type != "SPOT":
            raise ValueError("expected raw SPOT order instrument type")
        canonical_spot_symbol(self.inst_id)
        for name in ("ord_id",):
            validate_raw_text(getattr(self, name), name)
        if self.cl_ord_id is not MISSING and self.cl_ord_id is not None:
            validate_raw_text(self.cl_ord_id, "clOrdId", allow_empty=True)
        if self.tag is not MISSING and self.tag is not None:
            validate_raw_text(self.tag, "tag", allow_empty=True)
        if not isinstance(self.side, str) or self.side not in {"buy", "sell"}:
            raise ValueError("invalid order side")
        try:
            validate_raw_text(self.ord_type, "ordType")
        except ValueError as exc:
            raise ValueError("invalid order type") from exc
        if not isinstance(self.state, str) or self.state not in _ORDER_STATES:
            raise ValueError("invalid order state")
        if self.px is not MISSING and self.px is not None and self.px != "":
            _decimal(self.px, "px", positive=True)
        _decimal(self.sz, "sz", positive=True)
        if (self.acc_fill_sz is not MISSING
                and self.acc_fill_sz is not None and self.acc_fill_sz != ""):
            _decimal(self.acc_fill_sz, "accFillSz", nonnegative=True)
        for name in ("avg_px", "fee", "rebate"):
            value = getattr(self, name)
            if value is not MISSING and value is not None and value != "":
                if name == "avg_px":
                    _decimal(value, "avgPx", nonnegative=True)
                else:
                    _decimal(value, name)
        _timestamp(self.c_time, "cTime")
        _timestamp(self.u_time, "uTime")
        if self.fill_time is not MISSING and self.fill_time is not None and self.fill_time != "":
            _timestamp(self.fill_time, "fillTime")
        for name in (
            "trade_id", "fee_ccy", "cancel_source", "cancel_source_reason", "rebate_ccy",
        ):
            value = getattr(self, name)
            if value is not MISSING and value is not None:
                validate_raw_text(value, name, allow_empty=True)


def parse_okx_spot_order(
    payload: object, *, inst_id: str, ord_id: str | None = None,
    cl_ord_id: str | None = None,
) -> SpotOrderFact:
    """Validate one successful native SPOT order-detail response."""
    try:
        canonical_spot_symbol(inst_id)
        if (ord_id is None) == (cl_ord_id is None):
            raise ValueError("exactly one order lookup identity is required")
        lookup_name, lookup_value = (
            ("ordId", ord_id) if ord_id is not None else ("clOrdId", cl_ord_id)
        )
        _required_text(lookup_value, lookup_name)
    except ValueError as exc:
        if isinstance(exc, MalformedProviderData):
            raise
        raise ValueError(str(exc)) from exc
    if (
        not isinstance(payload, Mapping) or payload.get("code") != "0"
        or not isinstance(payload.get("data"), list) or len(payload["data"]) != 1
        or not isinstance(payload["data"][0], Mapping)
    ):
        raise _malformed("invalid OKX spot order success envelope")
    row = payload["data"][0]
    if row.get("instType") != "SPOT" or row.get("instId") != inst_id:
        raise _malformed("order response instrument contradicts request")
    response_ord_id = _required_text(row.get("ordId"), "ordId")
    response_cl_ord_id = _optional_text(row.get("clOrdId", MISSING), "clOrdId")
    if ord_id is not None and response_ord_id != ord_id:
        raise _malformed("order response ordId contradicts request")
    if cl_ord_id is not None and response_cl_ord_id != cl_ord_id:
        raise _malformed("order response clOrdId contradicts request")
    try:
        return SpotOrderFact(
            inst_type="SPOT", inst_id=inst_id, ord_id=response_ord_id,
            cl_ord_id=response_cl_ord_id, tag=_optional_text(row.get("tag", MISSING), "tag"),
            side=_required_text(row.get("side"), "side"),
            ord_type=_required_text(row.get("ordType"), "ordType"),
            px=_optional_decimal(row.get("px", MISSING), "px", positive=True),
            sz=_decimal(row.get("sz"), "sz", positive=True),
            acc_fill_sz=_optional_decimal(
                row.get("accFillSz", MISSING), "accFillSz", nonnegative=True,
            ),
            avg_px=_optional_decimal(row.get("avgPx", MISSING), "avgPx", nonnegative=True),
            state=_required_text(row.get("state"), "state"),
            c_time=_timestamp(row.get("cTime"), "cTime"),
            u_time=_timestamp(row.get("uTime"), "uTime"),
            fill_time=_optional_timestamp(row.get("fillTime", MISSING), "fillTime"),
            trade_id=_optional_text(row.get("tradeId", MISSING), "tradeId"),
            fee=_optional_decimal(row.get("fee", MISSING), "fee"),
            fee_ccy=_optional_text(row.get("feeCcy", MISSING), "feeCcy"),
            cancel_source=_optional_text(row.get("cancelSource", MISSING), "cancelSource"),
            cancel_source_reason=_optional_text(
                row.get("cancelSourceReason", MISSING), "cancelSourceReason",
            ),
            rebate=_optional_decimal(row.get("rebate", MISSING), "rebate"),
            rebate_ccy=_optional_text(row.get("rebateCcy", MISSING), "rebateCcy"),
        )
    except (ValueError, TypeError) as exc:
        if isinstance(exc, MalformedProviderData):
            raise
        raise _malformed(str(exc)) from exc


class RawOKXOrderExchange(Protocol):
    async def private_get_trade_order(self, params: dict[str, str]) -> object: ...


class OKXSpotOrderFactsAdapter:
    """Issue one raw order-detail request; callers own retries and persistence."""

    def __init__(self, exchange: RawOKXOrderExchange) -> None:
        self._exchange = exchange

    async def fetch(
        self, *, inst_id: str, ord_id: str | None = None, cl_ord_id: str | None = None,
    ) -> SpotOrderFact:
        canonical_spot_symbol(inst_id)
        if (ord_id is None) == (cl_ord_id is None):
            raise ValueError("exactly one order lookup identity is required")
        identity_name, identity_value = (
            ("ordId", ord_id) if ord_id is not None else ("clOrdId", cl_ord_id)
        )
        validate_raw_text(identity_value, identity_name)
        params = {"instType": "SPOT", "instId": inst_id, identity_name: identity_value}
        payload = await self._exchange.private_get_trade_order(params)
        return parse_okx_spot_order(
            payload, inst_id=inst_id, ord_id=ord_id, cl_ord_id=cl_ord_id,
        )
