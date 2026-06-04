from collections.abc import Callable
from typing import Protocol

from src.backtest.datasource import BacktestDataSource
from src.core.types import Bar
from src.data.repository import Repository

TIMEFRAME_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}
DEFAULT_PAGE_LIMIT = 100
MAX_PAGE_LIMIT = 300


class HistoricalDataAdapter(Protocol):
    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = DEFAULT_PAGE_LIMIT,
        since: int | None = None,
    ) -> list[Bar]: ...

    async def close(self) -> None: ...


def timeframe_to_ms(timeframe: str) -> int:
    if timeframe not in TIMEFRAME_MS:
        raise ValueError("unsupported timeframe")
    return TIMEFRAME_MS[timeframe]


async def ensure_historical_bars(
    *,
    repo: Repository,
    symbol: str,
    timeframe: str,
    start: int,
    end: int,
    adapter_factory: Callable[[], HistoricalDataAdapter],
    page_limit: int = DEFAULT_PAGE_LIMIT,
) -> list[Bar]:
    interval = timeframe_to_ms(timeframe)
    datasource = BacktestDataSource(repo, symbol=symbol, timeframe=timeframe)
    cached_bars = datasource.get_cached_bars(start, end)
    expected = _expected_timestamps(start, end, interval)
    cached_by_timestamp = {bar.timestamp: bar for bar in cached_bars}
    missing = [timestamp for timestamp in expected if timestamp not in cached_by_timestamp]
    if not missing:
        return _sorted_bars(cached_by_timestamp)

    fetched_by_timestamp: dict[int, Bar] = {}
    adapter = adapter_factory()
    try:
        for range_start, range_end in _contiguous_ranges(missing, interval):
            await _fetch_missing_range(
                adapter=adapter,
                symbol=symbol,
                timeframe=timeframe,
                range_start=range_start,
                range_end=range_end,
                interval=interval,
                page_limit=max(1, min(MAX_PAGE_LIMIT, page_limit)),
                fetched_by_timestamp=fetched_by_timestamp,
            )
    finally:
        await adapter.close()

    new_bars = [
        bar
        for timestamp, bar in fetched_by_timestamp.items()
        if timestamp not in cached_by_timestamp and start <= timestamp <= end
    ]
    datasource.save_bars_to_cache(new_bars)
    return _sorted_bars(cached_by_timestamp | fetched_by_timestamp)


def _expected_timestamps(start: int, end: int, interval: int) -> list[int]:
    first = start if start % interval == 0 else start + interval - (start % interval)
    return list(range(first, end + 1, interval))


def _contiguous_ranges(timestamps: list[int], interval: int) -> list[tuple[int, int]]:
    if not timestamps:
        return []
    ranges = []
    range_start = timestamps[0]
    previous = timestamps[0]
    for timestamp in timestamps[1:]:
        if timestamp != previous + interval:
            ranges.append((range_start, previous))
            range_start = timestamp
        previous = timestamp
    ranges.append((range_start, previous))
    return ranges


async def _fetch_missing_range(
    *,
    adapter: HistoricalDataAdapter,
    symbol: str,
    timeframe: str,
    range_start: int,
    range_end: int,
    interval: int,
    page_limit: int,
    fetched_by_timestamp: dict[int, Bar],
) -> None:
    since = range_start
    while since <= range_end:
        expected_count = ((range_end - since) // interval) + 1
        limit = min(page_limit, expected_count)
        rows = await adapter.fetch_ohlcv(symbol, timeframe, limit=limit, since=since)
        in_range_rows = [bar for bar in rows if range_start <= bar.timestamp <= range_end]
        for bar in in_range_rows:
            fetched_by_timestamp[bar.timestamp] = bar
        if not in_range_rows:
            return
        next_since = max(bar.timestamp for bar in in_range_rows) + interval
        if next_since <= since:
            return
        since = next_since


def _sorted_bars(bars: dict[int, Bar]) -> list[Bar]:
    return [bars[timestamp] for timestamp in sorted(bars)]
