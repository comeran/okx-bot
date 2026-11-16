"""OKX 公有 K 线流。只向下发已收盘的 K 线，连接失败时由行情服务改走 REST。"""

from __future__ import annotations

import json
from typing import Any

from src.market.bars import confirmed_candle_row

_LIVE_URL = "wss://ws.okx.com:8443/ws/v5/public"
_DEMO_URL = "wss://wspap.okx.com:8443/ws/v5/public"
_CHANNELS = {
    "1m": "candle1m",
    "3m": "candle3m",
    "5m": "candle5m",
    "15m": "candle15m",
    "30m": "candle30m",
    "1h": "candle1H",
    "2h": "candle2H",
    "4h": "candle4H",
    "1d": "candle1D",
}


class OkxPublicCandleStream:
    def __init__(self, *, demo: bool, connect: Any | None = None) -> None:
        self._url = _DEMO_URL if demo else _LIVE_URL
        self._connect = connect
        self._socket: Any | None = None

    async def watch_ohlcv(self, symbol: str, timeframe: str) -> list[list[float]]:
        channel = _CHANNELS.get(timeframe)
        if channel is None:
            raise ValueError("不支持的 K 线周期")
        socket = await self._ensure_socket()
        await socket.send(json.dumps({
            "op": "subscribe",
            "args": [{"channel": channel, "instId": symbol}],
        }))
        message = json.loads(await socket.recv())
        rows: list[list[float]] = []
        for row in message.get("data") or []:
            confirmed = confirmed_candle_row(row)
            if confirmed is not None:
                rows.append(confirmed)
        return rows

    async def _ensure_socket(self) -> Any:
        if self._socket is not None:
            return self._socket
        connect = self._connect or _open_socket
        self._socket = await connect(self._url)
        return self._socket

    async def close(self) -> None:
        socket = self._socket
        self._socket = None
        if socket is not None:
            await socket.close()


async def _open_socket(url: str) -> Any:
    import websockets

    return await websockets.connect(url)
