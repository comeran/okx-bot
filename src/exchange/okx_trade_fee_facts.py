"""Exact request-bound SPOT fee observations, separate from cost/coverage policy.

OKX maker/taker text is signed: positive denotes rebate, negative commission.
Neither raw rates nor fee groups alone prove a currency-aware fee reserve.
The response does not prove UID or echo the requested instrument; the caller
owns transport identity and persistence retains the explicit request context.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from src.core.private_facts import (
    MISSING,
    MissingField,
    OptionalRawText,
    canonical_spot_symbol,
    validate_raw_text,
)
from src.exchange.errors import MalformedProviderData

SPOT_TRADE_FEE_FIELDS = {
    "level": "level", "maker": "maker", "taker": "taker",
    "maker_usdc": "makerUSDC", "taker_usdc": "takerUSDC", "ts": "ts",
    "rule_type": "ruleType", "category": "category",
}
_GROUP_FIELDS = {"maker": "maker", "taker": "taker", "rpi_maker": "rpiMaker"}


def _validate_optional_text(fact: object, names: Mapping[str, str]) -> None:
    for name in names:
        value = getattr(fact, name)
        if value is not MISSING and value is not None:
            validate_raw_text(value, name, allow_empty=True)


@dataclass(frozen=True)
class SpotFeeGroupFact:
    group_id: str
    maker: OptionalRawText = MISSING
    taker: OptionalRawText = MISSING
    rpi_maker: OptionalRawText = MISSING

    def __post_init__(self) -> None:
        validate_raw_text(self.group_id, "groupId")
        _validate_optional_text(self, _GROUP_FIELDS)


@dataclass(frozen=True)
class SpotTradeFeeFact:
    requested_inst_id: str
    inst_type: str = "SPOT"
    level: OptionalRawText = MISSING
    maker: OptionalRawText = MISSING
    taker: OptionalRawText = MISSING
    maker_usdc: OptionalRawText = MISSING
    taker_usdc: OptionalRawText = MISSING
    ts: OptionalRawText = MISSING
    rule_type: OptionalRawText = MISSING
    category: OptionalRawText = MISSING
    fee_group: tuple[SpotFeeGroupFact, ...] | None | MissingField = MISSING

    def __post_init__(self) -> None:
        canonical_spot_symbol(self.requested_inst_id)
        if self.inst_type != "SPOT":
            raise ValueError("Expected raw SPOT trade fee instrument type")
        _validate_optional_text(self, SPOT_TRADE_FEE_FIELDS)
        if self.fee_group is MISSING or self.fee_group is None:
            return
        if not isinstance(self.fee_group, tuple) or any(
            not isinstance(group, SpotFeeGroupFact) for group in self.fee_group
        ):
            raise ValueError("Expected immutable fee group facts")
        if len({group.group_id for group in self.fee_group}) != len(self.fee_group):
            raise ValueError("Duplicate trade fee group IDs")


def parse_okx_spot_trade_fee(payload: object, *, inst_id: str) -> SpotTradeFeeFact:
    """Retain one success row under its explicit native-symbol request context."""
    canonical_spot_symbol(inst_id)
    if (
        not isinstance(payload, Mapping) or payload.get("code") != "0"
        or not isinstance(payload.get("data"), list) or len(payload["data"]) != 1
        or not isinstance(payload["data"][0], Mapping)
    ):
        raise MalformedProviderData("Invalid OKX spot trade fee success envelope")
    row = payload["data"][0]
    if "instId" in row and row["instId"] != inst_id:
        raise MalformedProviderData("Trade fee response instrument contradicts request")
    try:
        groups = row.get("feeGroup", MISSING)
        if groups is not MISSING and groups is not None:
            if not isinstance(groups, list) or any(not isinstance(g, Mapping) for g in groups):
                raise ValueError("Invalid trade fee group list")
            groups = tuple(SpotFeeGroupFact(
                group_id=group.get("groupId"),
                **{name: group.get(raw_name, MISSING) for name, raw_name in _GROUP_FIELDS.items()},
            ) for group in groups)
        return SpotTradeFeeFact(
            requested_inst_id=inst_id, inst_type=row.get("instType"), fee_group=groups,
            **{name: row.get(raw_name, MISSING)
               for name, raw_name in SPOT_TRADE_FEE_FIELDS.items()},
        )
    except ValueError as exc:
        raise MalformedProviderData(str(exc)) from exc


class RawOKXTradeFeeExchange(Protocol):
    async def private_get_account_trade_fee(self, params: dict[str, str]) -> object: ...


class OKXSpotTradeFeeFactsAdapter:
    def __init__(self, exchange: RawOKXTradeFeeExchange) -> None:
        self._exchange = exchange

    async def fetch(self, inst_id: str) -> SpotTradeFeeFact:
        canonical_spot_symbol(inst_id)
        payload = await self._exchange.private_get_account_trade_fee({
            "instType": "SPOT", "instId": inst_id,
        })
        return parse_okx_spot_trade_fee(payload, inst_id=inst_id)
