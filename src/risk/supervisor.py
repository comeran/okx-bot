"""独立的开仓监督。没有行情观测时不暂停。"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from src.data.repository import Repository
from src.risk.daily_ledger import roll_daily_pnl

logger = logging.getLogger(__name__)


def supervision_reason(
    *,
    daily_pnl: float,
    equity: float,
    peak_equity: float,
    price_age_ms: int | None,
    max_daily_loss_pct: float,
    max_drawdown_pct: float,
    max_price_age_ms: int,
) -> str | None:
    if equity > 0 and daily_pnl <= -(equity * max_daily_loss_pct):
        return "daily_loss"
    if peak_equity > 0 and (peak_equity - equity) / peak_equity > max_drawdown_pct:
        return "drawdown"
    if (
        price_age_ms is not None
        and max_price_age_ms > 0
        and price_age_ms > max_price_age_ms
    ):
        return "stale_price"
    return None


def evaluate(
    repository,
    *,
    now_ms: int | None = None,
    price_ages: list[int] | None = None,
) -> str | None:
    now = int(time.time() * 1000) if now_ms is None else now_ms
    try:
        roll_daily_pnl(repository, now)
    except Exception:
        logger.exception("滚动日盈亏失败")
    reason = _account_reason(repository, price_ages)
    if reason is not None:
        repository.set_opening_pause(True, reason, now)
    return reason


def _account_reason(repository, price_ages: list[int] | None) -> str | None:
    limits = _limits()
    accounts = []
    getter = getattr(repository, "get_accounts", None)
    if callable(getter):
        try:
            accounts = list(getter())
        except Exception:
            logger.exception("读取账户失败")
            return None
    for account in accounts:
        equity = float(getattr(account, "equity", 0) or 0)
        peak = float(getattr(account, "peak_equity", None) or equity)
        reason = supervision_reason(
            daily_pnl=float(getattr(account, "daily_pnl", 0) or 0),
            equity=equity,
            peak_equity=peak,
            price_age_ms=None,
            **limits,
        )
        if reason is not None:
            return reason
    if not price_ages:
        return None
    for age in price_ages:
        reason = supervision_reason(
            daily_pnl=0,
            equity=1,
            peak_equity=1,
            price_age_ms=age,
            **limits,
        )
        if reason == "stale_price":
            return reason
    return None


def _limits() -> dict[str, float | int]:
    try:
        from src.core.runtime_settings import load_runtime_settings

        risk = load_runtime_settings().risk
        return {
            "max_daily_loss_pct": float(risk.max_daily_loss_pct),
            "max_drawdown_pct": float(risk.max_drawdown_pct),
            "max_price_age_ms": int(risk.max_price_age_ms),
        }
    except Exception:
        return {
            "max_daily_loss_pct": 0.05,
            "max_drawdown_pct": 0.15,
            "max_price_age_ms": 15000,
        }


async def supervise_forever(interval_s: float | None = None) -> None:
    if interval_s is None:
        interval = float(os.environ.get("OKX_BOT_SUPERVISOR_SECONDS", "30"))
    else:
        interval = interval_s
    await asyncio.sleep(interval)
    while True:
        try:
            evaluate(Repository())
        except Exception:
            logger.exception("开仓监督失败")
        await asyncio.sleep(interval)


def main() -> None:
    asyncio.run(supervise_forever())


if __name__ == "__main__":
    main()
