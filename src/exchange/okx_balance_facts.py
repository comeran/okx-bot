"""Exact raw OKX balance evidence, separate from funds and activation policy.

``totalEq`` is account equity denominated in USD, never USDT cash. Per-currency
cash belongs exclusively to its own details row. Missing available balances do
not imply zero, available funds, absence of liabilities or unleveraged safety.
The balance endpoint supplies no UID; persistence requires an existing scoped
UID binding and caller-owned transport for that same account.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar, Protocol

from src.core.private_facts import MISSING, OptionalRawText, validate_raw_text
from src.exchange.errors import MalformedProviderData

ACCOUNT_BALANCE_FIELDS = {
    "total_eq": "totalEq", "adj_eq": "adjEq", "avail_eq": "availEq", "u_time": "uTime",
}
ASSET_BALANCE_FIELDS = {
    "cash_bal": "cashBal", "eq": "eq", "avail_bal": "availBal", "frozen_bal": "frozenBal",
    "upl": "upl", "u_time": "uTime", "liab": "liab", "interest": "interest",
    "eq_usd": "eqUsd", "ord_frozen": "ordFrozen",
    "cross_liab": "crossLiab", "iso_liab": "isoLiab", "upl_liab": "uplLiab",
}


def _validate_optional_fields(fact: object, fields: Mapping[str, str]) -> None:
    for name in fields:
        value = getattr(fact, name)
        if value is not MISSING and value is not None:
            validate_raw_text(value, name, allow_empty=True)


@dataclass(frozen=True)
class AssetBalanceFact:
    """One currency's provider text, without numeric or availability inference."""

    ccy: str
    cash_bal: OptionalRawText = MISSING
    eq: OptionalRawText = MISSING
    avail_bal: OptionalRawText = MISSING
    frozen_bal: OptionalRawText = MISSING
    upl: OptionalRawText = MISSING
    u_time: OptionalRawText = MISSING
    liab: OptionalRawText = MISSING
    interest: OptionalRawText = MISSING
    eq_usd: OptionalRawText = MISSING
    ord_frozen: OptionalRawText = MISSING
    cross_liab: OptionalRawText = MISSING
    iso_liab: OptionalRawText = MISSING
    upl_liab: OptionalRawText = MISSING

    def __post_init__(self) -> None:
        validate_raw_text(self.ccy, "ccy")
        _validate_optional_fields(self, ASSET_BALANCE_FIELDS)


@dataclass(frozen=True)
class AccountBalanceFact:
    """Identity-free response; USD account equity is never quote-currency cash."""

    details: tuple[AssetBalanceFact, ...]
    total_eq: OptionalRawText = MISSING
    adj_eq: OptionalRawText = MISSING
    avail_eq: OptionalRawText = MISSING
    u_time: OptionalRawText = MISSING
    equity_currency: ClassVar[str] = "USD"

    def __post_init__(self) -> None:
        _validate_optional_fields(self, ACCOUNT_BALANCE_FIELDS)
        if not isinstance(self.details, tuple) or any(
            not isinstance(row, AssetBalanceFact) for row in self.details
        ):
            raise ValueError("invalid balance details: expected immutable asset facts")
        if len({row.ccy for row in self.details}) != len(self.details):
            raise ValueError("duplicate balance currency rows")

    @property
    def is_unleveraged(self) -> None:
        """A balance response alone cannot prove a liability-free account."""
        return None


def parse_okx_account_balance(payload: object) -> AccountBalanceFact:
    """Require one success row and preserve exact optional provider text states."""
    if (
        not isinstance(payload, Mapping)
        or payload.get("code") != "0"
        or not isinstance(payload.get("data"), list)
        or len(payload["data"]) != 1
        or not isinstance(payload["data"][0], Mapping)
        or not isinstance(payload["data"][0].get("details"), list)
    ):
        raise MalformedProviderData("invalid OKX account balance success envelope")
    row = payload["data"][0]
    try:
        details = []
        for asset in row["details"]:
            if not isinstance(asset, Mapping):
                raise ValueError("invalid balance details row")
            details.append(AssetBalanceFact(
                ccy=asset.get("ccy"),
                **{name: asset.get(raw_name, MISSING)
                   for name, raw_name in ASSET_BALANCE_FIELDS.items()},
            ))
        return AccountBalanceFact(
            details=tuple(details),
            **{name: row.get(raw_name, MISSING)
               for name, raw_name in ACCOUNT_BALANCE_FIELDS.items()},
        )
    except ValueError as exc:
        raise MalformedProviderData(str(exc)) from exc


class RawOKXAccountBalanceExchange(Protocol):
    async def private_get_account_balance(self, params: dict[str, str]) -> object: ...


class OKXAccountBalanceFactsAdapter:
    """Fetch one raw balance response using caller-owned account transport."""

    def __init__(self, exchange: RawOKXAccountBalanceExchange) -> None:
        self._exchange = exchange

    async def fetch(self) -> AccountBalanceFact:
        payload = await self._exchange.private_get_account_balance({})
        return parse_okx_account_balance(payload)
