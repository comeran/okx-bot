from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from src.data.repository import Repository
from src.exchange.okx_futures import OKXFuturesAdapter
from src.exchange.okx_options import OKXOptionsAdapter
from src.exchange.okx_spot import OKXSpotAdapter
from src.exchange.okx_swap import OKXSwapAdapter

router = APIRouter()

_MARKET_FETCH_ERROR_DETAIL = "failed to fetch market data"
_MARKET_SYMBOLS_BY_TYPE = {
    "spot": ["BTC-USDT", "ETH-USDT", "OKB-USDT", "SOL-USDT"],
    "swap": ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"],
    "future": ["BTC-USDT-260626", "ETH-USDT-260626"],
    "option": [],
}


def normalize_market_type(market_type: str | None) -> str:
    normalized = (market_type or "spot").strip().lower()
    if normalized == "futures":
        return "future"
    if normalized == "options":
        return "option"
    if normalized not in _MARKET_SYMBOLS_BY_TYPE:
        raise HTTPException(status_code=400, detail=f"Unsupported market_type: {market_type}")
    return normalized


def create_public_market_adapter(market_type: str):
    adapter_cls = {
        "spot": OKXSpotAdapter,
        "swap": OKXSwapAdapter,
        "future": OKXFuturesAdapter,
        "option": OKXOptionsAdapter,
    }[market_type]
    return adapter_cls(api_key="", secret="", passphrase="")


def _serialize_kline(
    symbol: str,
    timeframe: str,
    item: Any,
    *,
    source: str | None = None,
    runtime_mode: str | None = None,
) -> dict[str, float | int | str]:
    payload: dict[str, float | int | str] = {
        "symbol": symbol,
        "timeframe": timeframe,
        "timestamp": item.timestamp,
        "open": item.open,
        "high": item.high,
        "low": item.low,
        "close": item.close,
        "volume": item.volume,
    }
    if source is not None and runtime_mode is not None:
        payload["source"] = source
        payload["runtime_mode"] = runtime_mode
    return payload


@router.get("/klines")
async def get_klines(
    symbol: str = "BTC-USDT",
    timeframe: str = "1h",
    limit: int = Query(default=100, ge=1, le=500),
    start_time: int | None = None,
    end_time: int | None = None,
    market_type: str | None = None,
    source: str | None = None,
    runtime_mode: str | None = None,
) -> list[dict[str, float | int | str]]:
    resolved_market_type = normalize_market_type(market_type)
    if (source is None) != (runtime_mode is None):
        raise HTTPException(
            status_code=422,
            detail="source and runtime_mode must be provided together",
        )
    if source is not None and (source, runtime_mode) != ("market", "market"):
        raise HTTPException(
            status_code=422,
            detail="market klines only support source=market and runtime_mode=market",
        )
    symbol = symbol.strip()
    if not symbol:
        raise HTTPException(status_code=422, detail="symbol must not be empty")

    if (start_time is None) != (end_time is None):
        raise HTTPException(
            status_code=422,
            detail="start_time and end_time must be provided together",
        )

    if source is not None and start_time is None and end_time is None:
        raise HTTPException(
            status_code=422,
            detail="scoped market klines require start_time and end_time",
        )

    if start_time is not None and end_time is not None:
        if end_time <= start_time:
            raise HTTPException(status_code=422, detail="end_time must be after start_time")

        repository = Repository()
        requested_source = source or "market"
        requested_runtime_mode = runtime_mode or "market"
        try:
            klines = repository.get_klines(
                symbol,
                timeframe,
                start_time,
                end_time,
                source=requested_source,
                runtime_mode=requested_runtime_mode,
            )
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc):
                raise
            raise HTTPException(
                status_code=422,
                detail="repository does not support scoped market kline queries",
            ) from exc
        klines = [
            kline
            for kline in klines
            if getattr(kline, "source", "unknown") == requested_source
            and getattr(kline, "runtime_mode", "unknown") == requested_runtime_mode
        ]
        return [
            _serialize_kline(
                kline.symbol,
                kline.timeframe,
                kline,
                source=source,
                runtime_mode=runtime_mode,
            )
            for kline in klines
        ]

    adapter = create_public_market_adapter(resolved_market_type)
    try:
        try:
            bars = await adapter.fetch_ohlcv(symbol, timeframe, limit=limit)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=_MARKET_FETCH_ERROR_DETAIL) from exc
        return [_serialize_kline(symbol, timeframe, bar) for bar in bars]
    finally:
        await adapter.close()


@router.get("/tickers")
async def get_tickers(
    market_type: str | None = None,
    symbols: list[str] | None = Query(default=None),
) -> list[dict[str, float | str]]:
    resolved_market_type = normalize_market_type(market_type)
    requested_symbols = symbols or _MARKET_SYMBOLS_BY_TYPE[resolved_market_type]
    if not requested_symbols:
        return []

    adapter = create_public_market_adapter(resolved_market_type)
    try:
        try:
            return await adapter.fetch_tickers(requested_symbols)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=_MARKET_FETCH_ERROR_DETAIL) from exc
    finally:
        await adapter.close()
