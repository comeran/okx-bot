from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from src.data.repository import Repository

router = APIRouter()

PAPER_ACCOUNT_KEYS = (
    "cash_balance",
    "equity",
    "realized_pnl",
    "unrealized_pnl",
    "daily_pnl",
    "fees_paid",
)
ZERO_PAPER_ACCOUNT = {key: 0.0 for key in PAPER_ACCOUNT_KEYS}


def record_value(record: object, key: str, default: float = 0.0) -> float:
    if isinstance(record, dict):
        value = record.get(key, default)
    elif hasattr(record, key):
        value = getattr(record, key)
    elif hasattr(record, "model_dump"):
        value = record.model_dump().get(key, default)
    else:
        value = default
    return value if isinstance(value, int | float) else default


def record_timestamp(record: object) -> int:
    return int(record_value(record, "timestamp", 0.0))


def serialize_record(record: object) -> dict[str, Any]:
    if hasattr(record, "model_dump"):
        return record.model_dump()
    if isinstance(record, dict):
        return record
    return vars(record)


def serialize_records(records: list[object]) -> list[dict[str, Any]]:
    return [serialize_record(record) for record in records]


def account_mapping(account: object) -> dict[str, Any]:
    if hasattr(account, "model_dump"):
        return account.model_dump()
    if isinstance(account, dict):
        return account
    return {key: getattr(account, key) for key in PAPER_ACCOUNT_KEYS if hasattr(account, key)}


def serialize_account(account: object | None) -> dict[str, float]:
    if account is None:
        return dict(ZERO_PAPER_ACCOUNT)

    values = account_mapping(account)
    return {
        key: float(values.get(key, 0.0)) if isinstance(values.get(key, 0.0), int | float) else 0.0
        for key in PAPER_ACCOUNT_KEYS
    }


@router.get("/positions")
async def get_positions(strategy: str | None = None) -> list[dict[str, Any]]:
    repository = Repository()
    if hasattr(repository, "get_open_positions"):
        positions = repository.get_open_positions(strategy)
    else:
        positions = [
            position
            for position in repository.get_positions(strategy)
            if record_value(position, "amount") != 0
        ]

    positions = sorted(positions, key=record_timestamp, reverse=True)
    return serialize_records(positions)


@router.get("/orders")
async def get_orders() -> list[dict[str, Any]]:
    orders = sorted(
        Repository().get_orders(),
        key=record_timestamp,
        reverse=True,
    )
    return serialize_records(orders)


@router.get("/trades")
async def get_trades(strategy: str | None = None) -> list[dict[str, Any]]:
    trades = sorted(
        Repository().get_trades(strategy),
        key=record_timestamp,
        reverse=True,
    )
    return serialize_records(trades)


@router.get("/account")
async def get_account() -> dict[str, float]:
    repository = Repository()
    if hasattr(repository, "get_account"):
        return serialize_account(repository.get_account())
    return dict(ZERO_PAPER_ACCOUNT)
