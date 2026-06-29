from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from collections.abc import Awaitable, Callable

import ccxt.async_support as ccxt
from ccxt.base.errors import NotSupported

from src.core.types import Bar

BarCallback = Callable[[Bar], Awaitable[None]]


class MarketDataService:
    def __init__(self, api_key: str, secret: str, passphrase: str):
        self._exchange_config = {"apiKey": api_key, "secret": secret, "password": passphrase}
        self._exchange = self._create_exchange()
        self._exchange_closed = False
        self._subscriptions: dict[str, list[BarCallback]] = {}
        self._buffers: dict[str, deque[Bar]] = {}
        self._last_bar_timestamps: dict[str, int] = {}
        self._running = False

    def _create_exchange(self):
        return ccxt.okx(dict(self._exchange_config))

    def _ensure_exchange_open(self) -> None:
        if self._exchange_closed:
            self._exchange = self._create_exchange()
            self._exchange_closed = False

    def subscribe(self, symbol: str, timeframe: str, callback: BarCallback) -> None:
        key = f"{symbol}:{timeframe}"
        callbacks = self._subscriptions.setdefault(key, [])
        if callback not in callbacks:
            callbacks.append(callback)
        self._buffers.setdefault(key, deque(maxlen=1000))

    def unsubscribe(self, symbol: str, timeframe: str, callback: BarCallback) -> None:
        key = f"{symbol}:{timeframe}"
        callbacks = self._subscriptions.get(key)
        if callbacks is None:
            return
        self._subscriptions[key] = [existing for existing in callbacks if existing is not callback]
        if not self._subscriptions[key]:
            self._subscriptions.pop(key)

    def get_recent_bars(self, symbol: str, timeframe: str, count: int = 100) -> list[Bar]:
        key = f"{symbol}:{timeframe}"
        return list(self._buffers.get(key, deque()))[-count:]

    async def _poll_once(self, symbol: str, timeframe: str) -> None:
        self._ensure_exchange_open()
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
            timestamp = int(row[0])
            if timestamp <= self._last_bar_timestamps.get(key, 0):
                continue
            bar = Bar(
                timestamp=timestamp,
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
            self._last_bar_timestamps[key] = timestamp
            self._buffers.setdefault(key, deque(maxlen=1000)).append(bar)
            for callback in list(self._subscriptions.get(key, [])):
                if any(existing is callback for existing in self._subscriptions.get(key, [])):
                    await callback(bar)

    async def start(self) -> None:
        self._running = True
        while self._running:
            for key in list(self._subscriptions):
                symbol, timeframe = key.split(":", 1)
                with contextlib.suppress(Exception):
                    await self._poll_once(symbol, timeframe)
            if self._running:
                await asyncio.sleep(1)

    async def stop(self) -> None:
        self._running = False
        if self._exchange_closed:
            return
        try:
            await self._exchange.close()
        finally:
            self._exchange_closed = True
