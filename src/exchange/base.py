from __future__ import annotations

from abc import ABC, abstractmethod

import ccxt.async_support as ccxt

from src.core.types import Bar, Order, OrderStatus
from src.order.router import OrderHandler


class ExchangeAdapter(OrderHandler, ABC):
    @abstractmethod
    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[Bar]:
        pass

    @abstractmethod
    async def close(self) -> None:
        pass


class OKXBaseAdapter(ExchangeAdapter):
    def __init__(self, api_key: str, secret: str, passphrase: str, default_type: str) -> None:
        self._exchange = ccxt.okx(
            {
                "apiKey": api_key,
                "secret": secret,
                "password": passphrase,
                "options": {"defaultType": default_type},
            }
        )

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[Bar]:
        rows = await self._exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        return [
            Bar(
                timestamp=int(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
            for row in rows
        ]

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
