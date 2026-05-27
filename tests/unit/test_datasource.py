import pytest
from sqlmodel import SQLModel, create_engine

from src.backtest.datasource import BacktestDataSource
from src.core.types import Bar
from src.data.models import KlineCache
from src.data.repository import Repository


@pytest.fixture
def repo() -> Repository:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return Repository(engine=engine)


def test_get_bars_from_cache(repo: Repository) -> None:
    start = 1_700_000_000
    end = start + 4 * 3_600
    for index in range(5):
        repo.save_kline(
            KlineCache(
                symbol="BTC-USDT",
                timeframe="1h",
                timestamp=start + index * 3_600,
                open=50_000 + index,
                high=51_000 + index,
                low=49_000 + index,
                close=50_500 + index,
                volume=100 + index,
            )
        )

    datasource = BacktestDataSource(repo, symbol="BTC-USDT", timeframe="1h")
    bars = datasource.get_cached_bars(start, end)

    assert len(bars) == 5
    assert isinstance(bars[0], Bar)
    assert bars[0].open == 50_000


def test_save_bars_to_cache(repo: Repository) -> None:
    start = 1_700_000_000
    bars = [
        Bar(
            timestamp=start,
            open=50_000,
            high=51_000,
            low=49_000,
            close=50_500,
            volume=100,
        ),
        Bar(
            timestamp=start + 3_600,
            open=50_100,
            high=51_100,
            low=49_100,
            close=50_600,
            volume=101,
        ),
    ]
    datasource = BacktestDataSource(repo, symbol="BTC-USDT", timeframe="1h")

    datasource.save_bars_to_cache(bars)

    cached = repo.get_klines("BTC-USDT", "1h", start, start + 3_600)
    assert len(cached) == 2
