"""Exact raw OKX account configuration, kept separate from activation policy.

No network access or unified CCXT projections are used. A configuration snapshot
proves neither absence of liabilities nor that a strategy may safely trade.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from src.core.private_facts import MISSING, OptionalRawText, validate_raw_text
from src.exchange.errors import MalformedProviderData

ACCOUNT_CONFIG_FIELDS = {
    "acct_lv": "acctLv",
    "acct_stp_mode": "acctStpMode",
    "pos_mode": "posMode",
    "auto_loan": "autoLoan",
    "enable_spot_borrow": "enableSpotBorrow",
    "fee_type": "feeType",
    "settle_ccy": "settleCcy",
    "main_uid": "mainUid",
}


@dataclass(frozen=True)
class AccountConfigFact:
    """Provider strings with distinct missing, null and empty observations."""

    uid: str
    acct_lv: OptionalRawText = MISSING
    acct_stp_mode: OptionalRawText = MISSING
    pos_mode: OptionalRawText = MISSING
    auto_loan: OptionalRawText = MISSING
    enable_spot_borrow: OptionalRawText = MISSING
    fee_type: OptionalRawText = MISSING
    settle_ccy: OptionalRawText = MISSING
    main_uid: OptionalRawText = MISSING

    def __post_init__(self) -> None:
        validate_raw_text(self.uid, "uid")
        for name in ACCOUNT_CONFIG_FIELDS:
            value = getattr(self, name)
            if value is not MISSING and value is not None:
                validate_raw_text(value, name, allow_empty=True)

    @property
    def auto_loan_enabled(self) -> bool | None:
        return {"0": False, "1": True}.get(self.auto_loan)

    @property
    def spot_borrow_enabled(self) -> bool | None:
        return {"0": False, "1": True}.get(self.enable_spot_borrow)

    @property
    def borrow_status(self) -> Literal["enabled", "disabled", "unknown"]:
        """Only report these two flags, never infer the absence of borrowing."""
        states = (self.auto_loan_enabled, self.spot_borrow_enabled)
        if True in states:
            return "enabled"
        if states == (False, False):
            return "disabled"
        return "unknown"

    @property
    def is_unleveraged(self) -> None:
        """Configuration alone does not prove a liability-free account."""
        return None


def parse_okx_account_config(payload: object) -> AccountConfigFact:
    """Require one successful raw row and bind identity exclusively to uid."""
    if (
        not isinstance(payload, Mapping)
        or payload.get("code") != "0"
        or not isinstance(payload.get("data"), list)
        or len(payload["data"]) != 1
        or not isinstance(payload["data"][0], Mapping)
    ):
        raise MalformedProviderData("invalid OKX account config success envelope")
    row = payload["data"][0]
    try:
        return AccountConfigFact(
            uid=row.get("uid"),
            **{
                name: row.get(raw_name, MISSING)
                for name, raw_name in ACCOUNT_CONFIG_FIELDS.items()
            },
        )
    except ValueError as exc:
        raise MalformedProviderData(str(exc)) from exc
