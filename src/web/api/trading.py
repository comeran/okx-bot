from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter

from src.data.repository import Repository

router = APIRouter()

DAY_MS = 24 * 60 * 60 * 1000


def current_timestamp_ms() -> int:
    return int(time.time() * 1000)


def record_value(record: object, key: str, default: float = 0.0) -> float:
    value = getattr(record, key, default)
    return value if isinstance(value, int | float) else default


def serialize_records(records: list[object]) -> list[dict[str, Any]]:
    return [record.model_dump() for record in records if hasattr(record, "model_dump")]


def trade_cashflow(trade: object) -> float:
    notional = record_value(trade, "amount") * record_value(trade, "price")
    fee = record_value(trade, "fee")
    side = getattr(trade, "side", "")
    if side == "sell":
        return notional - fee
    if side == "buy":
        return -notional - fee
    return -fee


@router.get("/positions")
async def get_positions(strategy: str | None = None) -> list[dict[str, Any]]:
    positions = sorted(
        Repository().get_positions(strategy),
        key=lambda position: getattr(position, "timestamp", 0),
        reverse=True,
    )
    return serialize_records(positions)


@router.get("/orders")
async def get_orders() -> list[dict[str, Any]]:
    orders = sorted(
        Repository().get_orders(),
        key=lambda order: getattr(order, "timestamp", 0),
        reverse=True,
    )
    return serialize_records(orders)


@router.get("/trades")
async def get_trades(strategy: str | None = None) -> list[dict[str, object]]:
    trades = sorted(
        Repository().get_trades(strategy),
        key=lambda trade: trade.timestamp,
        reverse=True,
    )
    return [trade.model_dump() for trade in trades]


@router.get("/account")
async def get_account() -> dict[str, float]:
    repository = Repository()
    positions = repository.get_positions()
    trades = repository.get_trades()
    cutoff = current_timestamp_ms() - DAY_MS

    equity = sum(
        abs(record_value(position, "amount")) * record_value(position, "entry_price")
        for position in positions
    )
    daily_pnl = sum(
        trade_cashflow(trade)
        for trade in trades
        if getattr(trade, "timestamp", 0) >= cutoff
    )

    return {
        "equity": equity,
        "daily_pnl": daily_pnl,
    }
