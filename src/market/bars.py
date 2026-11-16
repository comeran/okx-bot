"""研究和实盘共用的 K 线解析与收盘定义。"""

from __future__ import annotations

from src.core.types import Bar
from src.exchange.base import safe_ohlcv_bar


def parse_exchange_bar(row: object) -> Bar | None:
    return safe_ohlcv_bar(row)


def bar_from_prices(
    timestamp: object, open_price: object, high: object, low: object, close: object, volume: object,
) -> Bar:
    bar = parse_exchange_bar([timestamp, open_price, high, low, close, volume])
    if bar is None:
        raise ValueError("invalid OHLCV bar")
    return bar


def is_closed_bar(timestamp_ms: int, timeframe_ms: int, now_ms: int) -> bool:
    return timestamp_ms + timeframe_ms <= now_ms


def confirmed_candle_row(row: object) -> list[float] | None:
    """OKX K 线最后一列 confirm 为 1 才收盘。没有该列时沿用已收盘的 OHLCV。"""
    if isinstance(row, (list, tuple)) and len(row) >= 9 and str(row[8]) not in {"1", "1.0"}:
        return None
    bar = parse_exchange_bar(row)
    if bar is None:
        return None
    return [bar.timestamp, bar.open, bar.high, bar.low, bar.close, bar.volume]
