from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/positions")
async def get_positions() -> list[dict[str, str]]:
    return []


@router.get("/orders")
async def get_orders() -> list[dict[str, str]]:
    return []


@router.get("/account")
async def get_account() -> dict[str, float]:
    return {"equity": 0.0, "daily_pnl": 0.0}
