"""Exact private observations, separate from float-based trading projections.

These objects prove neither account ownership nor historical coverage. They do
not post ledger entries, update sync receipts, or authorize strategy activation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, StrEnum

MAX_RAW_TEXT_LENGTH = 256
MAX_NUMERIC_TEXT_LENGTH = 128
_SPOT_INSTRUMENT = re.compile(r"([A-Z0-9]+)-([A-Z0-9]+)")
_DECIMAL_TEXT = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
_INTEGER_TEXT = re.compile(r"[0-9]+")


class MissingField(Enum):
    """A provider key was absent, distinct from JSON null or an empty string."""

    MISSING = "missing"


MISSING = MissingField.MISSING
OptionalRawText = str | None | MissingField


class FactCoverage(StrEnum):
    UNKNOWN = "unknown"
    PARTIAL = "partial"
    COMPLETE = "complete"


def validate_raw_text(value: object, name: str, *, allow_empty: bool = False) -> str:
    """Reject coercion and resource-exhausting strings; never strip provider text."""
    if (
        not isinstance(value, str)
        or len(value) > MAX_RAW_TEXT_LENGTH
        or (not value and not allow_empty)
        or value != value.strip()
    ):
        raise ValueError(f"invalid {name}: expected bounded raw string")
    return value


def canonical_spot_symbol(inst_id: object) -> str:
    """Use the project's native BASE-QUOTE identity; never infer derivatives.

    Exact OKX facts require native instId input, so no unified alias conversion,
    whitespace trimming, or case rewriting is necessary here.
    """
    raw = validate_raw_text(inst_id, "instId")
    match = _SPOT_INSTRUMENT.fullmatch(raw)
    if match is None:
        raise ValueError("invalid native spot instId")
    return raw


def _decimal_text(value: object, name: str, *, positive: bool = False) -> None:
    # Plain bounded decimal syntax excludes exponent bombs and non-finite values
    # before Decimal construction. Raw decimal strings are never reserialized.
    if (
        not isinstance(value, str)
        or not 0 < len(value) <= MAX_NUMERIC_TEXT_LENGTH
        or _DECIMAL_TEXT.fullmatch(value) is None
        or (positive and Decimal(value) <= 0)
    ):
        raise ValueError(f"invalid {name}: expected bounded decimal string")


def _timestamp_text(value: object, name: str) -> None:
    if (
        not isinstance(value, str)
        or not 0 < len(value) <= MAX_NUMERIC_TEXT_LENGTH
        or _INTEGER_TEXT.fullmatch(value) is None
        or int(value) > 2**63 - 1
    ):
        raise ValueError(f"invalid {name}: expected bounded millisecond string")


@dataclass(frozen=True)
class SpotFillFact:
    """Raw provider strings plus a spot symbol, without any float conversion.

    ``fill_time`` is exclusively OKX ``fillTime``; ``record_time`` is ``ts``.
    Fee sign follows OKX: a negative fee is a charge and a positive fee a rebate.
    Optional fee data remains explicitly unknown when absent, null, or empty.
    """

    bill_id: str
    inst_id: str
    trade_id: str
    ord_id: str
    side: str
    fill_sz: str
    fill_px: str
    fill_time: str
    record_time: str
    cl_ord_id: OptionalRawText = MISSING
    fee: OptionalRawText = MISSING
    fee_ccy: OptionalRawText = MISSING
    symbol: str = field(init=False)

    def __post_init__(self) -> None:
        for name in ("bill_id", "trade_id", "ord_id"):
            validate_raw_text(getattr(self, name), name)
        object.__setattr__(self, "symbol", canonical_spot_symbol(self.inst_id))
        if not isinstance(self.side, str) or self.side not in {"buy", "sell"}:
            raise ValueError("invalid side")
        _decimal_text(self.fill_sz, "fillSz", positive=True)
        _decimal_text(self.fill_px, "fillPx", positive=True)
        _timestamp_text(self.fill_time, "fillTime")
        _timestamp_text(self.record_time, "ts")
        for name in ("cl_ord_id", "fee_ccy"):
            value = getattr(self, name)
            if value is not MISSING and value is not None:
                validate_raw_text(value, name, allow_empty=True)
        if self.fee is not MISSING and self.fee is not None and self.fee != "":
            _decimal_text(self.fee, "fee")

    @property
    def fill_time_ms(self) -> int:
        return int(self.fill_time)

    @property
    def record_time_ms(self) -> int:
        return int(self.record_time)


@dataclass(frozen=True)
class SpotFillPage:
    """One raw page, not an assertion that a time window has been exhausted.

    COMPLETE is reserved for a future provider contract with explicit coverage
    proof. OKX fills-history supplies none; its parser only emits PARTIAL or
    UNKNOWN, including on empty and short pages. ``issues`` signal duplicates
    without discarding identical observations. Keep prior pages within one
    account, environment, endpoint, and instrument traversal.
    """

    fills: tuple[SpotFillFact, ...]
    next_after: str | None
    coverage: FactCoverage
    requested_after: str | None = None
    requested_before: str | None = None
    inst_id: str | None = None
    issues: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return self.coverage is FactCoverage.COMPLETE
