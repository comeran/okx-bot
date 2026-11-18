"""Account-wide reads with exact per-instrument routing for OKX operations."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from src.core.types import PositionSnapshotResult
from src.exchange.base import ExchangeAdapter, RecentTradePage, canonical_symbol
from src.exchange.errors import MalformedProviderData
from src.exchange.markets import market_type_for_symbol

_INSTRUMENT_TYPES = {"spot": "SPOT", "swap": "SWAP", "future": "FUTURES", "option": "OPTION"}


class _MarketClient:
    """Scope responses before the existing market-specific parsers consume them."""

    def __init__(self, client, market: str):
        self._client = client
        self._instrument_type = _INSTRUMENT_TYPES[market]

    def __getattr__(self, name):
        return getattr(self._client, name)

    async def fetch_positions(self, symbols=None, params=None):
        return await self._client.fetch_positions(
            symbols, {**(params or {}), "instType": self._instrument_type},
        )

    async def fetch_open_orders(self, symbol=None, *, params=None):
        return await self._client.fetch_open_orders(
            symbol, params={**(params or {}), "instType": self._instrument_type},
        )


class OKXAccountAdapter(ExchangeAdapter):
    _default_type = "auto"

    def __init__(self, api_key, secret, passphrase, *, demo=True, adapter_classes: Mapping):
        self._credentials = (api_key, secret, passphrase)
        self._demo = demo
        self._classes = adapter_classes
        self._adapters = {}
        self._account_timestamp = 0

    def _adapter(self, market):
        if market not in self._adapters:
            adapter = self._classes[market](*self._credentials, demo=self._demo)
            client = getattr(adapter, "_exchange", None)
            if client is not None:
                # defaultType alone does not scope account positions/open orders.
                adapter._exchange = _MarketClient(client, market)
            self._adapters[market] = adapter
        return self._adapters[market]

    def canonical_symbol(self, symbol):
        market = market_type_for_symbol(symbol)
        return canonical_symbol(symbol, market_type=market)

    def _groups(self, symbols):
        if symbols is None:
            return {market: None for market in _INSTRUMENT_TYPES}
        groups = {}
        for symbol in symbols:
            market = market_type_for_symbol(symbol)
            normalized = canonical_symbol(symbol, market_type=market)
            group = groups.setdefault(market, [])
            if normalized not in group:
                group.append(normalized)
        return groups

    def _validate_rows(self, rows, market, symbols):
        if not isinstance(rows, (list, tuple)):
            raise MalformedProviderData("Invalid market snapshot rows")
        for row in rows:
            try:
                symbol = row.symbol
                normalized = self.canonical_symbol(symbol)
                if market_type_for_symbol(symbol) != market:
                    raise ValueError("foreign market")
                if symbols is not None and normalized not in symbols:
                    raise ValueError("foreign instrument")
            except (AttributeError, ValueError) as exc:
                raise MalformedProviderData("Snapshot contains an out-of-scope instrument") from exc
        return rows

    async def fetch_account_snapshot(self):
        snapshot = await self._adapter("spot").fetch_account_snapshot()
        self._account_timestamp = snapshot.updated_at
        return snapshot

    async def fetch_position_snapshots(self, symbols=None):
        groups = self._groups(symbols)
        positions = []
        timestamps = []
        complete = True
        for market, scoped_symbols in groups.items():
            # Cash spot holdings are balance assets, not /account/positions rows.
            if market == "spot":
                continue
            response = await self._adapter(market).fetch_position_snapshots(scoped_symbols)
            if isinstance(response, PositionSnapshotResult):
                rows = response.positions
                complete = complete and response.complete_snapshot
                timestamps.append(response.snapshot_timestamp)
            else:
                rows = response
                complete = False
            positions.extend(self._validate_rows(rows, market, scoped_symbols))
        if not timestamps:
            timestamps = [self._account_timestamp]
        timestamp = min(timestamps)
        return PositionSnapshotResult(
            positions, complete_snapshot=bool(groups) and complete and timestamp > 0,
            snapshot_timestamp=timestamp,
        )

    async def fetch_open_order_snapshots(self, symbols=None):
        orders = []
        for market, scoped_symbols in self._groups(symbols).items():
            rows = await self._adapter(market).fetch_open_order_snapshots(scoped_symbols)
            orders.extend(self._validate_rows(rows, market, scoped_symbols))
        return orders

    async def fetch_recent_trade_snapshots(self, symbols=None, since=None, limit=100):
        trades = []
        for market, scoped_symbols in self._groups(symbols).items():
            rows = await self._adapter(market).fetch_recent_trade_snapshots(
                scoped_symbols, since=since, limit=limit,
            )
            trades.extend(self._validate_rows(rows, market, scoped_symbols))
        # Each provider page can be capped/filtered; no combined history coverage
        # can be inferred from a short result, even when every market responded.
        trades.sort(key=lambda row: row.timestamp, reverse=True)
        # Keep every fetched fill for pending-order reconciliation. A global
        # cap could discard another market's already observed order evidence.
        return RecentTradePage(trades, since=since, complete=False)

    async def fetch_ohlcv(self, symbol, timeframe, limit=100, since=None):
        return await self._adapter(market_type_for_symbol(symbol)).fetch_ohlcv(
            symbol, timeframe, limit=limit, since=since,
        )

    async def fetch_tickers(self, symbols=None):
        tickers = []
        for market, scoped_symbols in self._groups(symbols).items():
            tickers.extend(await self._adapter(market).fetch_tickers(scoped_symbols))
        return tickers

    async def submit(self, order):
        return await self._adapter(market_type_for_symbol(order.symbol)).submit(order)

    async def cancel(self, order_id, symbol=None):
        return await self._adapter(market_type_for_symbol(symbol)).cancel(order_id, symbol)

    async def amend(self, order_id, symbol, price):
        return await self._adapter(market_type_for_symbol(symbol)).amend(order_id, symbol, price)

    async def close(self):
        results = await asyncio.gather(
            *(adapter.close() for adapter in self._adapters.values()), return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result
