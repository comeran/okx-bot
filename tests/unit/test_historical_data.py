import pytest
from sqlmodel import SQLModel, create_engine

from src.backtest.historical_data import ensure_historical_bars, timeframe_to_ms
from src.core.types import Bar
from src.data.models import KlineCache
from src.data.repository import Repository


@pytest.fixture
def repo() -> Repository:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return Repository(engine=engine)


class FakeAdapter:
    def __init__(self, bars: list[Bar]):
        self.bars = bars
        self.calls: list[dict[str, int | str]] = []
        self.closed = False

    async def fetch_ohlcv(self, symbol, timeframe, limit=100, since=None):
        self.calls.append(
            {"symbol": symbol, "timeframe": timeframe, "limit": limit, "since": since}
        )
        return [bar for bar in self.bars if bar.timestamp >= since][:limit]

    async def close(self):
        self.closed = True


def save_cached(repo: Repository, timestamp: int) -> None:
    repo.save_kline(
        KlineCache(
            symbol="BTC-USDT",
            timeframe="1m",
            timestamp=timestamp,
            open=float(timestamp),
            high=float(timestamp + 1),
            low=float(timestamp - 1),
            close=float(timestamp),
            volume=1.0,
        )
    )


def bar(timestamp: int) -> Bar:
    return Bar(
        timestamp=timestamp,
        open=float(timestamp),
        high=float(timestamp + 1),
        low=float(timestamp - 1),
        close=float(timestamp),
        volume=1.0,
    )


@pytest.mark.parametrize(
    ("timeframe", "expected"),
    [
        ("1m", 60_000),
        ("5m", 300_000),
        ("15m", 900_000),
        ("1h", 3_600_000),
        ("4h", 14_400_000),
        ("1d", 86_400_000),
    ],
)
def test_timeframe_to_ms_supports_backtest_timeframes(timeframe, expected):
    assert timeframe_to_ms(timeframe) == expected


def test_timeframe_to_ms_rejects_unsupported_timeframes():
    with pytest.raises(ValueError, match="unsupported timeframe"):
        timeframe_to_ms("2h")


@pytest.mark.asyncio
async def test_ensure_historical_bars_fetches_missing_range_and_persists(repo: Repository):
    save_cached(repo, 0)
    save_cached(repo, 180_000)
    adapter = FakeAdapter([bar(60_000), bar(120_000)])

    bars = await ensure_historical_bars(
        repo=repo,
        symbol="BTC-USDT",
        timeframe="1m",
        start=0,
        end=180_000,
        adapter_factory=lambda: adapter,
    )

    assert [item.timestamp for item in bars] == [0, 60_000, 120_000, 180_000]
    assert adapter.calls == [{"symbol": "BTC-USDT", "timeframe": "1m", "limit": 2, "since": 60_000}]
    assert adapter.closed is True
    assert [kline.timestamp for kline in repo.get_klines("BTC-USDT", "1m", 0, 180_000)] == [
        0,
        60_000,
        120_000,
        180_000,
    ]


@pytest.mark.asyncio
async def test_ensure_historical_bars_paginates_missing_ranges(repo: Repository):
    adapter = FakeAdapter([bar(timestamp) for timestamp in range(0, 360_000, 60_000)])

    bars = await ensure_historical_bars(
        repo=repo,
        symbol="BTC-USDT",
        timeframe="1m",
        start=0,
        end=300_000,
        adapter_factory=lambda: adapter,
        page_limit=2,
    )

    assert [item.timestamp for item in bars] == [0, 60_000, 120_000, 180_000, 240_000, 300_000]
    assert adapter.calls == [
        {"symbol": "BTC-USDT", "timeframe": "1m", "limit": 2, "since": 0},
        {"symbol": "BTC-USDT", "timeframe": "1m", "limit": 2, "since": 120_000},
        {"symbol": "BTC-USDT", "timeframe": "1m", "limit": 2, "since": 240_000},
    ]


@pytest.mark.asyncio
async def test_ensure_historical_bars_does_not_fetch_when_cache_is_complete(repo: Repository):
    save_cached(repo, 0)
    save_cached(repo, 60_000)
    adapter = FakeAdapter([])

    bars = await ensure_historical_bars(
        repo=repo,
        symbol="BTC-USDT",
        timeframe="1m",
        start=0,
        end=60_000,
        adapter_factory=lambda: adapter,
    )

    assert [item.timestamp for item in bars] == [0, 60_000]
    assert adapter.calls == []
    assert adapter.closed is False
