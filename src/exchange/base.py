from __future__ import annotations

from abc import ABC, abstractmethod

import ccxt.async_support as ccxt

from src.core.types import Bar, Order, OrderStatus
from src.order.router import OrderHandler


class ExchangeAdapter(OrderHandler, ABC):
    @abstractmethod
    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100,
        since: int | None = None,
    ) -> list[Bar]:
        pass

    @abstractmethod
    async def fetch_tickers(self, symbols: list[str]) -> list[dict[str, float | str]]:
        pass

    @abstractmethod
    async def close(self) -> None:
        pass


class OKXBaseAdapter(ExchangeAdapter):
    _SPOT_OHLCV_TIMEFRAMES = {
        "1m": ("1m", 60_000),
        "5m": ("5m", 300_000),
        "15m": ("15m", 900_000),
        "1h": ("1H", 3_600_000),
        "4h": ("4H", 14_400_000),
        "1d": ("1Dutc", 86_400_000),
    }

    def __init__(self, api_key: str, secret: str, passphrase: str, default_type: str) -> None:
        config = {"options": {"defaultType": default_type}}
        if api_key:
            config["apiKey"] = api_key
        if secret:
            config["secret"] = secret
        if passphrase:
            config["password"] = passphrase
        self._default_type = default_type
        self._exchange = ccxt.okx(config)

    def _to_ccxt_symbol(self, symbol: str) -> str:
        if self._default_type != "spot":
            return symbol
        base_quote = symbol.split("-")
        if len(base_quote) != 2:
            return symbol
        return "/".join(base_quote)

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100,
        since: int | None = None,
    ) -> list[Bar]:
        if self._default_type == "spot":
            okx_bar, duration_ms = self._SPOT_OHLCV_TIMEFRAMES[timeframe]
            params = {"instId": symbol, "bar": okx_bar, "limit": limit}
            if since is not None:
                params["before"] = max(since - 1, 0)
                params["after"] = since + duration_ms * limit
                response = await self._exchange.public_get_market_history_candles(params)
            else:
                response = await self._exchange.public_get_market_candles(params)
            rows = response.get("data", [])
        else:
            rows = await self._exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)

        return [
            Bar(
                timestamp=int(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
            for row in sorted(rows, key=lambda row: int(row[0]))
        ]

    async def fetch_tickers(self, symbols: list[str]) -> list[dict[str, float | str]]:
        if self._default_type == "spot":
            response = await self._exchange.public_get_market_tickers({"instType": "SPOT"})
            rows = {row.get("instId"): row for row in response.get("data", [])}
            tickers = []
            for symbol in symbols:
                row = rows.get(symbol)
                if row is None:
                    continue
                tickers.append(
                    {
                        "symbol": symbol,
                        "last": float(row.get("last") or 0),
                        "bidPx": float(row.get("bidPx") or 0),
                        "askPx": float(row.get("askPx") or 0),
                        "vol24h": float(row.get("vol24h") or 0),
                    }
                )
            return tickers

        rows = await self._exchange.fetch_tickers(symbols)
        tickers = []
        for symbol in symbols:
            row = rows.get(symbol) or rows.get(symbol.replace("-", "/"))
            if row is None:
                continue
            tickers.append(
                {
                    "symbol": symbol,
                    "last": float(row.get("last") or 0),
                    "bidPx": float(row.get("bid") or 0),
                    "askPx": float(row.get("ask") or 0),
                    "vol24h": float(row.get("baseVolume") or 0),
                }
            )
        return tickers

    async def submit(self, order: Order) -> Order:
        if order.type.value == "stop":
            raise ValueError("Stop orders require OKX trigger parameters")
        if order.stop_loss is not None or order.take_profit is not None:
            raise ValueError("OKX stop_loss and take_profit are not supported")
        response = await self._exchange.create_order(
            order.symbol,
            order.type.value,
            order.side.value,
            order.amount,
            order.price,
            {},
        )
        order.id = str(response.get("id", order.id))
        order.status = self._map_status(response.get("status"))
        if order.status == OrderStatus.FILLED:
            fill_price = response.get("average")
            if fill_price is not None:
                order.fill_price = float(fill_price)
            timestamp = response.get("timestamp")
            if timestamp is not None:
                order.fill_time = int(timestamp)
        return order

    async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
        if symbol is None:
            raise ValueError("OKX cancel requires symbol")
        await self._exchange.cancel_order(order_id, symbol)
        return True

    async def close(self) -> None:
        await self._exchange.close()

    def _map_status(self, status: str | None) -> OrderStatus:
        return {
            "closed": OrderStatus.FILLED,
            "canceled": OrderStatus.CANCELLED,
            "cancelled": OrderStatus.CANCELLED,
            "rejected": OrderStatus.REJECTED,
        }.get(status or "", OrderStatus.PENDING)
