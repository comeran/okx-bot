"""按 UTC 日期滚动日盈亏。"""

from __future__ import annotations

from datetime import UTC, datetime


def utc_day(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).strftime("%Y-%m-%d")


def roll_daily_pnl(repository, now_ms: int) -> bool:
    day = utc_day(now_ms)
    previous = repository.get_daily_ledger_day()
    if previous == day:
        return False
    rolled = previous is not None
    if rolled:
        repository.reset_account_daily_pnl()
    repository.set_daily_ledger_day(day, now_ms)
    return rolled
