from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal, InvalidOperation
from inspect import isawaitable

import ccxt.async_support as ccxt

from src.core.types import (
    AccountSnapshot,
    AssetBalance,
    Bar,
    ExchangeOrderSnapshot,
    ExchangeTradeSnapshot,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    PositionSnapshot,
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
    async def fetch_account_snapshot(self) -> AccountSnapshot:
        pass

    @abstractmethod
    async def fetch_position_snapshots(
        self, symbols: list[str] | None = None
    ) -> list[PositionSnapshot]:
        pass

    @abstractmethod
    async def fetch_open_order_snapshots(
        self,
        symbols: list[str] | None = None,
    ) -> list[ExchangeOrderSnapshot]:
        pass

    @abstractmethod
    async def fetch_recent_trade_snapshots(
        self,
        symbols: list[str] | None = None,
        since: int | None = None,
        limit: int = 100,
    ) -> list[ExchangeTradeSnapshot]:
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

    def __init__(
        self,
        api_key: str,
        secret: str,
        passphrase: str,
        default_type: str,
        demo: bool = True,
    ) -> None:
        config = {"options": {"defaultType": default_type}}
        if api_key:
            config["apiKey"] = api_key
        if secret:
            config["secret"] = secret
        if passphrase:
            config["password"] = passphrase
        self._default_type = default_type
        self._exchange = ccxt.okx(config)
        if demo and hasattr(self._exchange, "set_sandbox_mode"):
            result = self._exchange.set_sandbox_mode(True)
            if isawaitable(result):
                result.close()

    def _to_ccxt_symbol(self, symbol: str) -> str:
        if self._default_type != "spot":
            return symbol
        base_quote = symbol.split("-")
        if len(base_quote) != 2:
            return symbol
        return "/".join(base_quote)

    def _safe_float(self, value: object, default: float = 0.0) -> float:
        if value is None or value == "":
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _safe_int(self, value: object, default: int = 0) -> int:
        if value is None or value == "":
            return default
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    def _balance_value(self, balance: dict[str, object], currency: str, field_name: str) -> object:
        row = balance.get(currency)
        if isinstance(row, dict) and row.get(field_name) is not None:
            return row.get(field_name)
        field = balance.get(field_name)
        if isinstance(field, dict):
            return field.get(currency)
        return None

    def _balance_currency(self, balance: dict[str, object]) -> str:
        total = balance.get("total")
        if isinstance(total, dict) and total:
            return str(next(iter(total)))
        free = balance.get("free")
        if isinstance(free, dict) and free:
            return str(next(iter(free)))
        return next(
            (key for key, value in balance.items() if isinstance(value, dict) and key != "info"), ""
        )

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        balance = await self._exchange.fetch_balance()
        info = balance.get("info") if isinstance(balance.get("info"), dict) else {}
        data = info.get("data") or []
        account_row = next((row for row in data if isinstance(row, dict)), {})
        details = [
            detail
            for account in data
            if isinstance(account, dict)
            for detail in account.get("details") or []
            if isinstance(detail, dict)
        ]
        assets = [
            AssetBalance(
                ccy=str(detail.get("ccy") or ""),
                cash_bal=self._safe_float(detail.get("cashBal")),
                eq=self._safe_float(detail.get("eq")),
                eq_utd=self._safe_float(detail.get("eqUtd"), self._safe_float(detail.get("eqUsd"))),
                avail_bal=self._safe_float(detail.get("availBal")),
                upl=self._safe_float(detail.get("upl")),
            )
            for detail in details
            if detail.get("ccy")
        ]

        currency = "USDT"
        row = next((detail for detail in details if detail.get("ccy") == currency), None)
        fallback_total = self._safe_float(self._balance_value(balance, currency, "total"))
        fallback_free = self._safe_float(self._balance_value(balance, currency, "free"))
        assets_equity = sum(asset.eq_utd for asset in assets)
        updated_at = self._safe_int(
            row.get("uTime") if row is not None else None,
            self._safe_int(
                account_row.get("uTime"),
                self._safe_int(info.get("uTime"), self._safe_int(info.get("ts"))),
            ),
        )
        return AccountSnapshot(
            currency=currency,
            equity=self._safe_float(
                account_row.get("totalEq"),
                assets_equity if assets_equity else fallback_total,
            ),
            cash_balance=self._safe_float(
                row.get("cashBal") if row is not None else None,
                fallback_free,
            ),
            available_balance=self._safe_float(
                row.get("availBal") if row is not None else None,
                self._safe_float(account_row.get("availEq"), fallback_free),
            ),
            unrealized_pnl=self._safe_float(
                account_row.get("upl"),
                sum(asset.upl for asset in assets),
            ),
            realized_pnl=sum(self._safe_float(detail.get("realizedPnl")) for detail in details),
            updated_at=updated_at,
            assets=assets,
        )

    async def fetch_position_snapshots(
        self, symbols: list[str] | None = None
    ) -> list[PositionSnapshot]:
        rows = await self._exchange.fetch_positions(symbols)
        return [self._parse_position_snapshot(row) for row in rows]

    def _parse_position_snapshot(self, row: dict[str, object]) -> PositionSnapshot:
        info = row.get("info") if isinstance(row.get("info"), dict) else {}
        amount = self._safe_float(
            row.get("contracts"),
            self._safe_float(
                row.get("contractSize"),
                self._safe_float(row.get("amount"), self._safe_float(info.get("pos"))),
            ),
        )
        mark_price = (
            row.get("markPrice") if row.get("markPrice") is not None else info.get("markPx")
        )
        return PositionSnapshot(
            symbol=str(info.get("instId") or row.get("symbol") or ""),
            side=self._position_side(row, info, amount),
            amount=abs(amount),
            entry_price=self._safe_float(
                row.get("entryPrice"), self._safe_float(info.get("avgPx"))
            ),
            mark_price=None
            if mark_price is None or mark_price == ""
            else self._safe_float(mark_price),
            unrealized_pnl=self._safe_float(
                row.get("unrealizedPnl"), self._safe_float(info.get("upl"))
            ),
            realized_pnl=self._safe_float(
                row.get("realizedPnl"), self._safe_float(info.get("realizedPnl"))
            ),
            leverage=self._safe_int(row.get("leverage"), self._safe_int(info.get("lever"), 1)),
            updated_at=self._safe_int(row.get("timestamp"), self._safe_int(info.get("uTime"))),
        )

    def _position_side(
        self, row: dict[str, object], info: dict[str, object], amount: float
    ) -> PositionSide:
        for value in (row.get("side"), info.get("posSide")):
            side = str(value or "").lower()
            if side in {"long", "buy"}:
                return PositionSide.LONG
            if side in {"short", "sell"}:
                return PositionSide.SHORT
        if amount < 0:
            return PositionSide.SHORT
        return PositionSide.LONG

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
        order_type, params = self._okx_order_type_and_params(order)
        await self._validate_order_against_market(order)
        response = await self._exchange.create_order(
            order.symbol,
            order_type,
            order.side.value,
            order.amount,
            order.price,
            params,
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

    def _okx_order_type_and_params(self, order: Order) -> tuple[str, dict[str, object]]:
        params = dict(order.params)
        if self._default_type != "spot":
            params.setdefault("tdMode", "cross")
        order_type = order.type.value
        if order.type == OrderType.STOP or order.type.value == "stop":
            if order.trigger_price is None:
                raise ValueError("Stop orders require trigger_price")
            order_type = "market" if order.price is None else "limit"
            params["triggerPrice"] = order.trigger_price
        if order.stop_loss is not None:
            params["stopLoss"] = {"triggerPrice": order.stop_loss}
        if order.take_profit is not None:
            params["takeProfit"] = {"triggerPrice": order.take_profit}
        return order_type, params

    async def _validate_order_against_market(self, order: Order) -> None:
        markets = getattr(self._exchange, "markets", None)
        if not markets and hasattr(self._exchange, "load_markets"):
            markets = await self._exchange.load_markets()
        markets = markets or {}
        market = markets.get(order.symbol) or markets.get(self._to_ccxt_symbol(order.symbol))
        if not market:
            market_lookup = getattr(self._exchange, "market", None)
            if callable(market_lookup):
                try:
                    candidate = market_lookup(order.symbol)
                except Exception:
                    candidate = None
                if isinstance(candidate, dict):
                    market = candidate
        if not market:
            raise ValueError(f"Exchange market metadata unavailable for {order.symbol}")

        limits = market.get("limits") or {}
        amount_min = (limits.get("amount") or {}).get("min")
        if amount_min is not None and order.amount < float(amount_min):
            raise ValueError("amount below exchange minimum")

        if order.price is not None:
            price_min = (limits.get("price") or {}).get("min")
            if price_min is not None and order.price < float(price_min):
                raise ValueError("price below exchange minimum")
            cost_min = (limits.get("cost") or {}).get("min")
            if cost_min is not None and order.amount * order.price < float(cost_min):
                raise ValueError("cost below exchange minimum")

        precision = market.get("precision") or {}
        self._validate_decimal_precision(order.amount, precision.get("amount"), "amount")
        if order.price is not None:
            self._validate_decimal_precision(order.price, precision.get("price"), "price")

    def _validate_decimal_precision(
        self,
        value: float,
        precision: object,
        field_name: str,
    ) -> None:
        if precision is None or not isinstance(precision, int):
            return
        try:
            decimal_value = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError(f"invalid {field_name}") from exc
        if decimal_value.as_tuple().exponent < -precision:
            raise ValueError(f"{field_name} precision exceeds exchange precision")

    async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
        if symbol is None:
            raise ValueError("OKX cancel requires symbol")
        await self._exchange.cancel_order(order_id, symbol)
        return True

    async def close(self) -> None:
        await self._exchange.close()

    def _map_order(self, row: dict[str, object]) -> ExchangeOrderSnapshot:
        timestamp = self._safe_int(row.get("timestamp"))
        return ExchangeOrderSnapshot(
            exchange_order_id=str(row.get("id") or ""),
            client_order_id=str(row.get("clientOrderId") or row.get("clientOid") or ""),
            symbol=str(row.get("symbol") or ""),
            side=OrderSide(str(row.get("side") or "buy").lower()),
            type=OrderType(str(row.get("type") or "limit").lower()),
            amount=self._safe_float(row.get("amount")),
            price=self._safe_float(row.get("price")),
            status=self._map_status(row.get("status")),
            fill_price=self._safe_float(row.get("average")),
            timestamp=timestamp,
            updated_at=self._safe_int(
                row.get("lastTradeTimestamp"),
                self._safe_int(row.get("updated"), timestamp),
            ),
        )

    def _map_trade(self, row: dict[str, object]) -> ExchangeTradeSnapshot:
        fee = row.get("fee") or {}
        return ExchangeTradeSnapshot(
            exchange_trade_id=str(row.get("id") or ""),
            exchange_order_id=str(row.get("order") or row.get("orderId") or ""),
            client_order_id=str(row.get("clientOrderId") or row.get("clientOid") or ""),
            symbol=str(row.get("symbol") or ""),
            side=OrderSide(str(row.get("side") or "buy").lower()),
            amount=self._safe_float(row.get("amount")),
            price=self._safe_float(row.get("price")),
            fee=self._safe_float(fee.get("cost")) if isinstance(fee, dict) else 0.0,
            timestamp=self._safe_int(row.get("timestamp")),
        )

    def _map_status(self, status: object) -> OrderStatus:
        return {
            "open": OrderStatus.PENDING,
            "closed": OrderStatus.FILLED,
            "filled": OrderStatus.FILLED,
            "canceled": OrderStatus.CANCELLED,
            "cancelled": OrderStatus.CANCELLED,
            "rejected": OrderStatus.REJECTED,
        }.get(str(status or "").lower(), OrderStatus.PENDING)
