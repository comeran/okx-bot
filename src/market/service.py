from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable

import ccxt.async_support as ccxt
from ccxt.base.errors import NotSupported

from src.core.types import Bar

BarCallback = Callable[[Bar], Awaitable[None]]


class MarketDataService:
    def __init__(self, api_key: str, secret: str, passphrase: str):
        self._exchange = ccxt.okx({"apiKey": api_key, "secret": secret, "password": passphrase})
        self._subscriptions: dict[str, list[BarCallback]] = {}
        self._buffers: dict[str, deque[Bar]] = {}
        self._running = False

    def subscribe(self, symbol: str, timeframe: str, callback: BarCallback) -> None:
        key = f"{symbol}:{timeframe}"
        self._subscriptions.setdefault(key, []).append(callback)
        self._buffers.setdefault(key, deque(maxlen=1000))

    def get_recent_bars(self, symbol: str, timeframe: str, count: int = 100) -> list[Bar]:
        key = f"{symbol}:{timeframe}"
        return list(self._buffers.get(key, deque()))[-count:]

    async def _poll_once(self, symbol: str, timeframe: str) -> None:
        key = f"{symbol}:{timeframe}"
        watch_ohlcv = getattr(self._exchange, "watch_ohlcv", None)
        fetch_ohlcv = getattr(self._exchange, "fetch_ohlcv", None)
        if callable(watch_ohlcv):
            try:
                rows = await watch_ohlcv(symbol, timeframe)
            except NotSupported:
                if not callable(fetch_ohlcv):
                    raise RuntimeError("Exchange does not support OHLCV data") from None
                rows = await fetch_ohlcv(symbol, timeframe)
        elif callable(fetch_ohlcv):
            rows = await fetch_ohlcv(symbol, timeframe)
        else:
            raise RuntimeError("Exchange does not support OHLCV data")

        for row in rows:
            bar = Bar(
                timestamp=int(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
            self._buffers.setdefault(key, deque(maxlen=1000)).append(bar)
            for callback in self._subscriptions.get(key, []):
                await callback(bar)

    async def start(self) -> None:
        self._running = True
        while self._running:
            for key in list(self._subscriptions):
                symbol, timeframe = key.split(":", 1)
                try:
                    await self._poll_once(symbol, timeframe)
                except Exception:
                    await asyncio.sleep(1)

    async def stop(self) -> None:
        self._running = False
        await self._exchange.close()
