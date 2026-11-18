from __future__ import annotations

from types import SimpleNamespace

import pytest
from ccxt.base.errors import BadRequest, NetworkError

from src.core.types import (
    AccountSnapshot,
    PositionSide,
    PositionSnapshot,
    PositionSnapshotResult,
)
from src.exchange.factory import create_okx_adapter
from src.exchange.okx_account import OKXAccountAdapter


class FakeAdapter:
    created: list[FakeAdapter] = []
    market_type = "spot"

    def __init__(self, api_key, secret, passphrase, demo=True):
        self.args = (api_key, secret, passphrase, demo)
        self._default_type = self.market_type
        self.calls: list[tuple] = []
        self.account_calls = 0
        self.created.append(self)

    async def fetch_account_snapshot(self):
        self.account_calls += 1
        return AccountSnapshot(equity=10, updated_at=100)

    async def fetch_position_snapshots(self, symbols=None):
        self.calls.append(("positions", symbols))
        symbol = {
            "spot": "BTC-USDT",
            "swap": "BTC-USDT-SWAP",
            "future": "BTC-USDT-260627",
            "option": "BTC-USDT-260627-100000-C",
        }[self.market_type]
        return PositionSnapshotResult(
            [PositionSnapshot(symbol, PositionSide.LONG, 1, 10, timestamp=100)],
            complete_snapshot=True,
            snapshot_timestamp=100,
        )

    async def fetch_open_order_snapshots(self, symbols=None):
        self.calls.append(("orders", symbols))
        return []

    async def fetch_recent_trade_snapshots(self, symbols=None, since=None, limit=100):
        self.calls.append(("trades", symbols, since, limit))
        return []

    async def fetch_ohlcv(self, symbol, timeframe, limit=100, since=None):
        self.calls.append(("ohlcv", symbol, timeframe))
        return []

    async def fetch_tickers(self, symbols=None):
        self.calls.append(("tickers", symbols))
        return []

    async def submit(self, order):
        self.calls.append(("submit", order.symbol))
        return order

    async def cancel(self, order_id, symbol=None):
        self.calls.append(("cancel", order_id, symbol))
        return {"id": order_id}

    async def amend(self, order_id, symbol, price):
        self.calls.append(("amend", order_id, symbol, price))
        return {"id": order_id, "price": price}

    async def close(self):
        self.calls.append(("close",))


class SpotAdapter(FakeAdapter):
    market_type = "spot"


class SwapAdapter(FakeAdapter):
    market_type = "swap"


class FutureAdapter(FakeAdapter):
    market_type = "future"


class OptionAdapter(FakeAdapter):
    market_type = "option"


def config(market_type="spot"):
    return SimpleNamespace(
        api_key="k", secret="s", passphrase="p", demo=True, market_type=market_type
    )


def test_factory_explicit_market_uses_selected_adapter():
    adapter = create_okx_adapter(config("spot"), {"spot": SpotAdapter}, market_type="spot")
    assert isinstance(adapter, SpotAdapter)


def test_factory_without_market_returns_account_adapter_and_ignores_legacy_market():
    adapter = create_okx_adapter(
        config("spot"),
        {
            "spot": SpotAdapter,
            "swap": SwapAdapter,
            "future": FutureAdapter,
            "option": OptionAdapter,
        },
    )
    assert isinstance(adapter, OKXAccountAdapter)
    assert adapter._default_type == "auto"


@pytest.mark.asyncio
async def test_account_adapter_fetches_balance_once_and_aggregates_positions():
    FakeAdapter.created.clear()
    adapter = create_okx_adapter(
        config("spot"),
        {
            "spot": SpotAdapter,
            "swap": SwapAdapter,
            "future": FutureAdapter,
            "option": OptionAdapter,
        },
    )
    snapshot = await adapter.fetch_account_snapshot()
    assert snapshot.equity == 10
    assert sum(item.account_calls for item in FakeAdapter.created) == 1

    result = await adapter.fetch_position_snapshots()
    assert isinstance(result, PositionSnapshotResult)
    assert result.complete_snapshot is True
    assert {row.symbol for row in result.positions} == {
        "BTC-USDT-SWAP",
        "BTC-USDT-260627",
        "BTC-USDT-260627-100000-C",
    }


@pytest.mark.asyncio
async def test_account_adapter_groups_symbol_operations_and_routes_order():
    FakeAdapter.created.clear()
    adapter = create_okx_adapter(
        config(),
        {
            "spot": SpotAdapter,
            "swap": SwapAdapter,
            "future": FutureAdapter,
            "option": OptionAdapter,
        },
    )
    symbols = ["BTC-USDT", "BTC-USDT-SWAP", "BTC-USDT-260627", "BTC-USDT-260627-100000-C"]
    result = await adapter.fetch_open_order_snapshots(symbols)
    assert result == []
    await adapter.fetch_recent_trade_snapshots(symbols, since=123, limit=7)
    order = SimpleNamespace(symbol="BTC-USDT-SWAP")
    await adapter.submit(order)
    await adapter.cancel("ord", "BTC-USDT-260627")
    await adapter.amend("ord", "BTC-USDT-260627-100000-C", 12.3)
    assert any(call[0] == "submit" for item in FakeAdapter.created for call in item.calls)
    assert any(call[0] == "cancel" for item in FakeAdapter.created for call in item.calls)
    assert any(call[0] == "amend" for item in FakeAdapter.created for call in item.calls)


@pytest.mark.asyncio
async def test_account_queries_scope_real_ccxt_clients_before_parsing(monkeypatch):
    from src.exchange import base

    requests = []

    class Client:
        markets = {"synthetic-loaded-market": {}}

        def milliseconds(self):
            return 100

        async def fetch_positions(self, symbols, params):
            requests.append(("positions", symbols, params))
            return []

        async def fetch_open_orders(self, symbol=None, *, params):
            requests.append(("orders", symbol, params))
            return []

        async def close(self):
            pass

    monkeypatch.setattr(base, "create_okx_client", lambda **kwargs: Client())
    adapter = create_okx_adapter(config())
    result = await adapter.fetch_position_snapshots()
    await adapter.fetch_open_order_snapshots()
    await adapter.close()
    assert result.complete_snapshot is True
    assert [row[2] for row in requests if row[0] == "positions"] == [
        {"instType": "SWAP"},
        {"instType": "FUTURES"},
        {"instType": "OPTION"},
    ]
    assert [row[2] for row in requests if row[0] == "orders"] == [
        {"instType": "SPOT"},
        {"trigger": True, "instType": "SPOT"},
        {"instType": "SWAP"},
        {"trigger": True, "instType": "SWAP"},
        {"instType": "FUTURES"},
        {"trigger": True, "instType": "FUTURES"},
        {"instType": "OPTION"},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("market_type", ["option", None])
@pytest.mark.parametrize("symbols", [None, ["BTC-USD-271231-100000-C"]])
async def test_option_open_orders_use_only_supported_pending_endpoint(
    monkeypatch, market_type, symbols,
):
    from src.exchange import base
    from src.exchange.okx_client import create_okx_client

    requests = []

    def client_factory(**kwargs):
        client = create_okx_client(**kwargs)
        option = {
            "id": "BTC-USD-271231-100000-C", "symbol": "BTC/USD:BTC-271231-100000-C",
            "type": "option", "spot": False, "swap": False, "future": False, "option": True,
        }
        client.markets = {option["symbol"]: option}
        client.markets_by_id = {option["id"]: [option]}

        async def pending(params):
            requests.append(("ordinary", kwargs["default_type"], params))
            return {"code": "0", "data": []}

        async def algo_pending(params):
            requests.append(("trigger", kwargs["default_type"], params))
            if kwargs["default_type"] == "option":
                raise BadRequest('okx {"code":"51000","msg":"Parameter instType error"}')
            return {"code": "0", "data": []}

        client.privateGetTradeOrdersPending = pending
        client.privateGetTradeOrdersAlgoPending = algo_pending
        return client

    monkeypatch.setattr(base, "create_okx_client", client_factory)
    adapter = create_okx_adapter(config(), market_type=market_type)
    try:
        assert await adapter.fetch_open_order_snapshots(symbols) == []
    finally:
        await adapter.close()

    option_requests = [
        (endpoint, params) for endpoint, market, params in requests if market == "option"
    ]
    expected_params = {} if market_type == "option" else {"instType": "OPTION"}
    if symbols is not None:
        expected_params["instId"] = "BTC-USD-271231-100000-C"
    assert option_requests == [("ordinary", expected_params)]
    if market_type is None and symbols is None:
        assert {
            params["instType"] for endpoint, _, params in requests if endpoint == "trigger"
        } == {
            "SPOT", "SWAP", "FUTURES",
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("market_type", ["option", None])
@pytest.mark.parametrize(
    "failure", [BadRequest("ordinary rejected"), NetworkError("ordinary offline")],
)
async def test_option_ordinary_open_order_errors_propagate(monkeypatch, market_type, failure):
    from src.exchange import base
    from src.exchange.okx_client import create_okx_client

    def client_factory(**kwargs):
        client = create_okx_client(**kwargs)
        option = {
            "id": "BTC-USD-271231-100000-C", "symbol": "BTC/USD:BTC-271231-100000-C",
            "type": "option", "spot": False, "swap": False, "future": False, "option": True,
        }
        client.markets = {option["symbol"]: option}
        client.markets_by_id = {option["id"]: [option]}

        async def pending(params):
            raise failure

        client.privateGetTradeOrdersPending = pending
        return client

    monkeypatch.setattr(base, "create_okx_client", client_factory)
    adapter = create_okx_adapter(config(), market_type=market_type)
    try:
        with pytest.raises(type(failure), match=str(failure)):
            await adapter.fetch_open_order_snapshots(
                None if market_type == "option" else ["BTC-USD-271231-100000-C"],
            )
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_trade_history_retains_each_market_fill_when_page_is_full():
    symbols = ["BTC-USDT", "BTC-USDT-SWAP", "BTC-USDT-271231", "BTC-USDT-271231-100000-C"]

    class TradeAdapter(FakeAdapter):
        async def fetch_recent_trade_snapshots(self, symbols=None, since=None, limit=100):
            assert limit == 1
            symbol = symbols[0]
            return [SimpleNamespace(symbol=symbol, timestamp=10 + symbols_by_market.index(symbol))]

    symbols_by_market = symbols
    adapter = create_okx_adapter(
        config(),
        {market: TradeAdapter for market in ("spot", "swap", "future", "option")},
    )
    page = await adapter.fetch_recent_trade_snapshots(symbols, since=1, limit=1)

    # A fill already fetched for one market must remain available to reconcile
    # its pending order even when a busier market fills its own provider page.
    assert {row.symbol for row in page} == set(symbols)
    assert [row.timestamp for row in page] == [13, 12, 11, 10]
    assert page.complete is False
    assert page.since == 1
