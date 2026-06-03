from __future__ import annotations

from fastapi import APIRouter, Query

from src.exchange.okx_spot import OKXSpotAdapter

router = APIRouter()

_MARKET_SYMBOLS = ["BTC-USDT", "ETH-USDT", "OKB-USDT", "SOL-USDT"]


@router.get("/klines")
async def get_klines(
    symbol: str = "BTC-USDT",
    timeframe: str = "1h",
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, float | int | str]]:
    adapter = OKXSpotAdapter(api_key="", secret="", passphrase="")
    try:
        bars = await adapter.fetch_ohlcv(symbol, timeframe, limit=limit)
        return [
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "timestamp": bar.timestamp,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            }
            for bar in bars
        ]
    finally:
        await adapter.close()


@router.get("/tickers")
async def get_tickers() -> list[dict[str, float | str]]:
    adapter = OKXSpotAdapter(api_key="", secret="", passphrase="")
    try:
        return await adapter.fetch_tickers(_MARKET_SYMBOLS)
    finally:
        await adapter.close()
