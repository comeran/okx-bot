from src.core.types import Bar
from src.data.models import KlineCache
from src.data.repository import Repository


class BacktestDataSource:
    def __init__(self, repo: Repository, symbol: str, timeframe: str):
        self.repo = repo
        self.symbol = symbol
        self.timeframe = timeframe

    def get_cached_bars(self, start: int, end: int) -> list[Bar]:
        klines = self.repo.get_klines(self.symbol, self.timeframe, start, end)
        return [
            Bar(
                timestamp=kline.timestamp,
                open=kline.open,
                high=kline.high,
                low=kline.low,
                close=kline.close,
                volume=kline.volume,
            )
            for kline in klines
        ]

    def save_bars_to_cache(self, bars: list[Bar]) -> None:
        for bar in bars:
            self.repo.save_kline(
                KlineCache(
                    symbol=self.symbol,
                    timeframe=self.timeframe,
                    timestamp=bar.timestamp,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                )
            )
