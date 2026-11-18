"""手工单、行情板、规则策略、合约拨款和实盘同步。"""

from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.config import AppConfig, ExchangeConfig
from src.core.execution_scope import build_execution_context
from src.core.types import Bar
from src.data.funded_allocation import TrustedActivationFacts, activate_funded_allocation
from src.data.repository import Repository
from src.market.board import load_market_board
from src.order.manual import amend_resting_order, cancel_one_order, submit_manual_order
from src.order.router import OrderRouter
from src.strategy.rule import RuleStrategy
from src.web.api import market as market_api
from src.web.api import trading as trading_api
from src.web.api.ops import private_sync_allowed
from src.web.api.strategies import create_strategy_registry


class _Order:
    def __init__(self, order_id, status="open", price=10):
        self.order_id = order_id
        self.client_order_id = order_id
        self.exchange_order_id = ""
        self.status = status
        self.price = price
        self.symbol = "BTC-USDT"


class _Repo:
    def __init__(self, order):
        self.order = order
        self.saved = None

    def get_orders(self):
        return [self.order]

    def save_order(self, order):
        self.saved = order
        return order


class _Router:
    def __init__(self):
        self.amended = None

    async def amend(self, order_id, symbol, price):
        self.amended = (order_id, symbol, price)
        return {"id": order_id, "price": price, "status": "open"}


class _Manager:
    def __init__(self):
        self.submitted = None
        self.cancelled = None

    async def submit(self, **kwargs):
        self.submitted = kwargs
        return type("Order", (), {
            "id": "manual-1", "symbol": kwargs["symbol"], "status": "open",
            "amount": kwargs["amount"], "price": kwargs["price"],
        })()

    async def cancel(self, order_id, symbol=None):
        self.cancelled = (order_id, symbol)
        return True


def test_rule_strategy_submits_the_configured_amount():
    registry = create_strategy_registry()
    strategy = registry.create_instance(
        "custom", "rule", "BTC-USDT", "1h",
        {"buy": "close > 10", "sell": "close < 0", "amount": 0.0001},
    )
    assert isinstance(strategy, RuleStrategy)
    seen = {}

    class Manager:
        async def submit(self, **kwargs):
            seen.update(kwargs)
            return type("Order", (), {"id": "1", "status": "open"})()

    strategy.set_order_manager(Manager())

    async def run():
        await strategy.on_bar(Bar(1, 11, 12, 10, 11, 1))

    import asyncio
    asyncio.run(run())
    assert seen["amount"] == 0.0001
    assert seen["side"].value == "buy"


def test_swap_future_and_option_accounts_can_be_addressed():
    for market in ("swap", "future", "option"):
        context, scope = build_execution_context(ExchangeConfig(
            api_key="synthetic-key", secret="synthetic-secret",
            passphrase="synthetic-passphrase", market_type=market, demo=True,
        ))
        assert context["available"] is True
        assert context["reason"] is None
        assert context["market_type"] == "all"
        assert market in context["market_types"]
        assert scope is not None


def test_swap_budget_activates_on_the_swap_instrument():
    repository = Repository()
    book = activate_funded_allocation(
        repository,
        scope_id="scope-live",
        strategy="alpha",
        symbol="BTC-USDT-SWAP",
        budget=Decimal("60"),
        environment="live",
        revision=1,
        facts=TrustedActivationFacts(
            available_quote=Decimal("100"),
            observed_at=1_000_000,
            preflight_passed=True,
            market_type="swap",
            account_scope_id="scope-live",
        ),
        now_ms=1_000_000,
        market_type="swap",
    )
    assert book.symbol == "BTC-USDT-SWAP"
    assert book.cash == Decimal("60")


@pytest.mark.asyncio
async def test_manual_order_cancel_and_amend_use_the_existing_route():
    manager = _Manager()
    placed = await submit_manual_order(
        manager, symbol="BTC-USDT", side="buy", order_type="limit",
        amount=0.0001, price=100,
    )
    assert placed["id"] == "manual-1"
    assert manager.submitted["strategy_name"] == "manual"
    with pytest.raises(ValueError):
        await submit_manual_order(
            manager, symbol="BTC-USDT", side="buy", order_type="limit",
            amount=0, price=100,
        )
    cancelled = await cancel_one_order(manager, "manual-1", "BTC-USDT")
    assert cancelled["cancelled"] is True
    assert manager.cancelled == ("manual-1", "BTC-USDT")
    router = _Router()
    repository = _Repo(_Order("manual-1"))
    amended = await amend_resting_order(
        repository, router, "manual-1", "BTC-USDT", 101,
    )
    assert amended["price"] == 101
    assert router.amended == ("manual-1", "BTC-USDT", 101)
    assert repository.saved.price == 101
    OrderRouter(backtest=None)


@pytest.mark.asyncio
async def test_market_board_skips_funding_on_spot_and_reads_it_for_swap():
    class Reader:
        def __init__(self):
            self.funding_reads = 0

        async def order_book(self, symbol, market_type):
            return {"bids": [[1, 2]], "asks": [[3, 4]]}

        async def trades(self, symbol, market_type):
            return [{"price": 2, "amount": 1, "side": "buy"}]

        async def funding(self, symbol, market_type):
            self.funding_reads += 1
            return {"available": True, "rate": 0.0001}

        async def open_interest(self, symbol, market_type):
            return {"available": True, "amount": 5}

    reader = Reader()
    spot = await load_market_board("BTC-USDT", "spot", reader)
    assert spot["funding_rate"]["reason"] == "not_applicable"
    assert spot["open_interest"]["reason"] == "not_applicable"
    assert reader.funding_reads == 0
    swap = await load_market_board("BTC-USDT-SWAP", "swap", reader)
    assert swap["funding_rate"]["rate"] == 0.0001
    assert swap["open_interest"]["amount"] == 5
    assert reader.funding_reads == 1


def test_market_board_and_manual_order_routes_do_not_call_the_exchange(monkeypatch):
    async def board(symbol, market_type, _reader=None):
        return {
            "symbol": symbol, "market_type": market_type,
            "order_book": {"bids": [[1, 1]], "asks": [[2, 1]]},
            "trades": [],
            "funding_rate": {"available": False, "reason": "not_applicable"},
            "open_interest": {"available": False, "reason": "not_applicable"},
        }

    async def place(body):
        return {
            "id": "m1", "status": "open", "symbol": body.symbol,
            "amount": body.amount, "price": body.price,
        }

    monkeypatch.setattr(market_api, "load_market_board", board)
    monkeypatch.setattr(trading_api, "submit_manual_order_request", place)
    app = FastAPI()
    app.include_router(market_api.router, prefix="/api/market")
    app.include_router(trading_api.router, prefix="/api/trading")
    client = TestClient(app)
    board_response = client.get(
        "/api/market/board", params={"symbol": "BTC-USDT", "market_type": "spot"},
    )
    order_response = client.post("/api/trading/orders", json={
        "symbol": "BTC-USDT", "side": "buy", "order_type": "limit", "amount": 0.0001, "price": 100,
    })
    assert board_response.status_code == 200
    assert board_response.json()["order_book"]["bids"] == [[1, 1]]
    assert order_response.status_code == 200
    assert order_response.json()["id"] == "m1"


def test_private_sync_allows_sandbox_and_a_configured_live_account():
    sandbox = AppConfig(mode="demo", exchange=ExchangeConfig(
        api_key="k", secret="s", passphrase="p", demo=True,
    ))
    live = AppConfig(mode="live", exchange=ExchangeConfig(
        api_key="k", secret="s", passphrase="p", demo=False,
    ))
    paper = AppConfig(mode="paper", exchange=ExchangeConfig(
        api_key="k", secret="s", passphrase="p", demo=False,
    ))
    assert private_sync_allowed(sandbox) is True
    assert private_sync_allowed(live) is True
    assert private_sync_allowed(paper) is False
