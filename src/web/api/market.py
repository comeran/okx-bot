from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from src.exchange.okx_spot import OKXSpotAdapter

router = APIRouter()

_MARKET_FETCH_ERROR_DETAIL = "failed to fetch market data"
_MARKET_SYMBOLS = ["BTC-USDT", "ETH-USDT", "OKB-USDT", "SOL-USDT"]


@router.get("/klines")
async def get_klines(
    symbol: str = "BTC-USDT",
    timeframe: str = "1h",
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, float | int | str]]:
    adapter = OKXSpotAdapter(api_key="", secret="", passphrase="")
    try:
        try:
            bars = await adapter.fetch_ohlcv(symbol, timeframe, limit=limit)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=_MARKET_FETCH_ERROR_DETAIL) from exc
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
        try:
            return await adapter.fetch_tickers(_MARKET_SYMBOLS)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=_MARKET_FETCH_ERROR_DETAIL) from exc
    finally:
        await adapter.close()
