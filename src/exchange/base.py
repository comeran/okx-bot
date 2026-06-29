from __future__ import annotations

from abc import ABC, abstractmethod
from inspect import isawaitable

import ccxt.async_support as ccxt

from src.core.types import (
    AccountSnapshot,
    Bar,
    ExchangeOrderSnapshot,
    ExchangeTradeSnapshot,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
)
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
    def __init__(
        self,
        api_key: str,
        secret: str,
        passphrase: str,
        default_type: str,
        demo: bool = True,
    ) -> None:
        self._exchange = ccxt.okx(
            {
                "apiKey": api_key,
                "secret": secret,
                "password": passphrase,
                "options": {"defaultType": default_type},
            }
        )
        if demo and hasattr(self._exchange, "set_sandbox_mode"):
            result = self._exchange.set_sandbox_mode(True)
            if isawaitable(result):
                result.close()

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100,
        since: int | None = None,
    ) -> list[Bar]:
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
            for row in rows
        ]

    async def fetch_tickers(self, symbols: list[str]) -> list[dict[str, float | str]]:
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

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        row = await self._exchange.fetch_balance()
        equity = self._sum_balances(row.get("total"))
        return AccountSnapshot(
            initial_equity=equity,
            cash_balance=self._sum_balances(row.get("free")),
            equity=equity,
            realized_pnl=float(row.get("realizedPnl") or row.get("realized_pnl") or 0.0),
            unrealized_pnl=float(row.get("unrealizedPnl") or row.get("unrealized_pnl") or 0.0),
            daily_pnl=float(row.get("dailyPnl") or row.get("daily_pnl") or 0.0),
            fees_paid=float(row.get("feesPaid") or row.get("fees_paid") or 0.0),
            timestamp=self._extract_timestamp(row),
        )

    async def fetch_open_order_snapshots(
        self,
        symbols: list[str] | None = None,
    ) -> list[ExchangeOrderSnapshot]:
        orders: list[ExchangeOrderSnapshot] = []
        for symbol in symbols or [None]:
            rows = (
                await self._exchange.fetch_open_orders(symbol)
                if symbol is not None
                else await self._exchange.fetch_open_orders()
            )
            orders.extend(self._map_order(row) for row in rows)
        return orders

    async def fetch_recent_trade_snapshots(
        self,
        symbols: list[str] | None = None,
        since: int | None = None,
        limit: int = 100,
    ) -> list[ExchangeTradeSnapshot]:
        trades: list[ExchangeTradeSnapshot] = []
        for symbol in symbols or [None]:
            rows = await self._exchange.fetch_my_trades(symbol, since=since, limit=limit)
            trades.extend(self._map_trade(row) for row in rows)
        return trades

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

    def _map_order(self, row: dict) -> ExchangeOrderSnapshot:
        timestamp = int(row.get("timestamp") or 0)
        return ExchangeOrderSnapshot(
            exchange_order_id=str(row.get("id") or ""),
            client_order_id=str(row.get("clientOrderId") or row.get("clientOid") or ""),
            symbol=str(row.get("symbol") or ""),
            side=OrderSide(str(row.get("side") or "buy")),
            type=OrderType(str(row.get("type") or "limit")),
            amount=float(row.get("amount") or 0.0),
            price=float(row.get("price") or 0.0),
            status=self._map_status(row.get("status")),
            fill_price=float(row.get("average") or 0.0),
            timestamp=timestamp,
            updated_at=int(row.get("lastTradeTimestamp") or row.get("updated") or timestamp),
        )

    def _map_trade(self, row: dict) -> ExchangeTradeSnapshot:
        fee = row.get("fee") or {}
        return ExchangeTradeSnapshot(
            exchange_trade_id=str(row.get("id") or ""),
            exchange_order_id=str(row.get("order") or row.get("orderId") or ""),
            client_order_id=str(row.get("clientOrderId") or row.get("clientOid") or ""),
            symbol=str(row.get("symbol") or ""),
            side=OrderSide(str(row.get("side") or "buy")),
            amount=float(row.get("amount") or 0.0),
            price=float(row.get("price") or 0.0),
            fee=float(fee.get("cost") or 0.0) if isinstance(fee, dict) else 0.0,
            timestamp=int(row.get("timestamp") or 0),
        )

    @staticmethod
    def _extract_timestamp(row: dict) -> int:
        info = row.get("info") if isinstance(row.get("info"), dict) else {}
        return int(row.get("timestamp") or info.get("uTime") or info.get("ts") or 0)

    @staticmethod
    def _sum_balances(balances: object) -> float:
        if not isinstance(balances, dict):
            return 0.0
        return sum(float(value or 0.0) for value in balances.values())

    def _map_status(self, status: str | None) -> OrderStatus:
        return {
            "open": OrderStatus.PENDING,
            "closed": OrderStatus.FILLED,
            "filled": OrderStatus.FILLED,
            "canceled": OrderStatus.CANCELLED,
            "cancelled": OrderStatus.CANCELLED,
            "rejected": OrderStatus.REJECTED,
        }.get(status or "", OrderStatus.PENDING)
