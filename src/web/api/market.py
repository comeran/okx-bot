from __future__ import annotations

from fastapi import APIRouter, Query

router = APIRouter()

_TIMEFRAME_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}

_SYMBOL_BASE_PRICE = {
    "BTC-USDT": 68_000.0,
    "ETH-USDT": 3_800.0,
    "OKB-USDT": 55.0,
    "SOL-USDT": 165.0,
}


@router.get("/klines")
async def get_klines(
    symbol: str = "BTC-USDT",
    timeframe: str = "1h",
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, float | int | str]]:
    step = _TIMEFRAME_MS.get(timeframe, _TIMEFRAME_MS["1h"])
    base = _SYMBOL_BASE_PRICE.get(symbol, 100.0)
    start = 1_700_000_000_000
    rows = []
    for index in range(limit):
        trend = index * base * 0.0005
        wave = ((index % 7) - 3) * base * 0.0002
        open_price = base + trend + wave
        close_price = open_price + (((index % 5) - 2) * base * 0.00015)
        high = max(open_price, close_price) + base * 0.001
        low = min(open_price, close_price) - base * 0.001
        rows.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "timestamp": start + index * step,
                "open": round(open_price, 4),
                "high": round(high, 4),
                "low": round(low, 4),
                "close": round(close_price, 4),
                "volume": round(100 + index * 1.5, 4),
            }
        )
    return rows


@router.get("/tickers")
async def get_tickers() -> list[dict[str, float | str]]:
    return [
        {
            "symbol": symbol,
            "last": price,
            "bidPx": round(price * 0.999, 4),
            "askPx": round(price * 1.001, 4),
            "vol24h": round(price * 12.5, 4),
        }
        for symbol, price in _SYMBOL_BASE_PRICE.items()
    ]
