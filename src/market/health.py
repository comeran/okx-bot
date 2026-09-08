from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

MarketFeedStatus = Literal["pending", "ready", "degraded", "unavailable"]


@dataclass(frozen=True, slots=True)
class MarketFeedHealth:
    key: str
    symbol: str
    timeframe: str
    status: MarketFeedStatus
    buffered_bars: int
    consecutive_failures: int
    total_failures: int
    last_success_at: float | None
    last_failure_at: float | None
    last_bar_timestamp: int | None
    error_code: str | None
    public_message: str | None
    generation: int
    success_generation: int | None = None


HealthListener = Callable[[MarketFeedHealth], None]

MARKET_FEED_ERROR_CODE = "market_feed_poll_failed"
MARKET_FEED_PUBLIC_MESSAGE = "Market data feed is temporarily unavailable. Please retry shortly."
