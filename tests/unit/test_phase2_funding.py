"""第二阶段剩余三条：独占现货资金、可信预检原子拨款、运行时强制执行。"""

from decimal import Decimal

import pytest

from src.core.config import AppConfig, ExchangeConfig
from src.core.runtime_settings import save_runtime_settings
from src.core.types import OrderSide, OrderStatus, OrderType
from src.data.funded_allocation import (
    FundingError,
    TrustedActivationFacts,
    activate_funded_allocation,
    assert_funded_draft_editable,
    strategy_book,
)
from src.data.repository import Repository
from src.order.manager import UnifiedOrderManager
from src.order.router import OrderHandler, OrderRouter
from src.risk.manager import RiskManager
from src.web.api.strategies import create_order_manager, has_live_strategy_ownership_mapping


def _facts(
    available: str,
    observed_at: int,
    scope: str,
    *,
    passed: bool = True,
    market_type: str = "spot",
) -> TrustedActivationFacts:
    return TrustedActivationFacts(
        available_quote=Decimal(available),
        observed_at=observed_at,
        preflight_passed=passed,
        market_type=market_type,
        account_scope_id=scope,
    )


def _activate(repository, **overrides):
    arguments = dict(
        scope_id="scope-live",
        strategy="alpha",
        symbol="BTC-USDT",
        budget=Decimal("60"),
        environment="live",
        revision=1,
        facts=_facts("100", 1_000_000, "scope-live"),
        now_ms=1_000_000,
        market_type="spot",
    )
    arguments.update(overrides)
    return activate_funded_allocation(repository, **arguments)


def test_exclusive_books_fund_from_fresh_preflight_and_keep_closes_local():
    repository = Repository()
    _activate(repository)
    _activate(
        repository,
        strategy="beta",
        symbol="ETH-USDT",
        budget=Decimal("40"),
        revision=2,
    )
    with pytest.raises(FundingError) as overlap:
        _activate(
            repository,
            strategy="gamma",
            symbol="SOL-USDT",
            budget=Decimal("1"),
            revision=1,
        )
    assert overlap.value.code == "budget_overlap"
    with pytest.raises(FundingError) as same_position:
        _activate(
            repository,
            strategy="beta-btc",
            symbol="BTC-USDT",
            budget=Decimal("1"),
            facts=_facts("200", 1_000_000, "scope-live"),
        )
    assert same_position.value.code == "position_overlap"

    alpha = strategy_book(repository, "scope-live", "alpha", "live")
    beta = strategy_book(repository, "scope-live", "beta", "live")
    assert alpha.cash == Decimal("60")
    assert alpha.base_position == Decimal("0")
    assert alpha.realized_pnl == Decimal("0")
    assert beta.cash == Decimal("40")
    assert strategy_book(repository, "scope-live", "alpha", "sandbox") is None

    _activate(
        repository,
        scope_id="scope-sandbox",
        environment="sandbox",
        budget=Decimal("10"),
        facts=_facts("10", 1_000_000, "scope-sandbox"),
    )
    assert strategy_book(repository, "scope-live", "alpha", "live").cash == Decimal("60")
    assert has_live_strategy_ownership_mapping(repository, "solo", "BTC-USDT") is False
    _activate(
        repository,
        scope_id="scope-solo",
        strategy="solo",
        environment="sandbox",
        budget=Decimal("5"),
        facts=_facts("5", 1_000_000, "scope-solo"),
    )
    assert has_live_strategy_ownership_mapping(repository, "solo", "BTC-USDT") is False
    assert has_live_strategy_ownership_mapping(repository, "alpha", "BTC-USDT") is True

    from src.data.funded_allocation import reserve_funded_order, settle_funded_order

    assert reserve_funded_order(
        repository,
        scope_id="scope-live",
        strategy="beta",
        environment="live",
        symbol="ETH-USDT",
        order_id="beta-too-big",
        side="buy",
        amount=Decimal("6"),
        price=Decimal("10"),
    ) == "Funded quote budget exceeded"
    assert reserve_funded_order(
        repository,
        scope_id="scope-live",
        strategy="alpha",
        environment="live",
        symbol="BTC-USDT",
        order_id="alpha-buy",
        side="buy",
        amount=Decimal("0.001"),
        price=Decimal("10000"),
    ) == "reserved"
    assert strategy_book(repository, "scope-live", "alpha", "live").reserved_quote == Decimal("10")
    settle_funded_order(
        repository,
        "alpha-buy",
        status="filled",
        filled_amount=Decimal("0.001"),
        fill_price=Decimal("10000"),
    )
    held = strategy_book(repository, "scope-live", "alpha", "live")
    assert held.cash == Decimal("50")
    assert held.base_position == Decimal("0.001")
    assert held.realized_pnl == Decimal("0")
    assert held.reserved_quote == Decimal("0")
    assert strategy_book(repository, "scope-live", "beta", "live").cash == Decimal("40")

    assert reserve_funded_order(
        repository,
        scope_id="scope-live",
        strategy="beta",
        environment="live",
        symbol="ETH-USDT",
        order_id="beta-sell",
        side="sell",
        amount=Decimal("0.001"),
        price=Decimal("12000"),
    ) == "Funded base budget exceeded"
    assert reserve_funded_order(
        repository,
        scope_id="scope-live",
        strategy="alpha",
        environment="live",
        symbol="BTC-USDT",
        order_id="alpha-sell",
        side="sell",
        amount=Decimal("0.001"),
        price=Decimal("12000"),
    ) == "reserved"
    settle_funded_order(
        repository,
        "alpha-sell",
        status="filled",
        filled_amount=Decimal("0.001"),
        fill_price=Decimal("12000"),
    )
    closed = strategy_book(repository, "scope-live", "alpha", "live")
    assert closed.cash == Decimal("62")
    assert closed.base_position == Decimal("0")
    assert closed.realized_pnl == Decimal("2")
    assert strategy_book(repository, "scope-sandbox", "alpha", "sandbox").cash == Decimal("10")
    assert strategy_book(repository, "scope-live", "beta", "live").cash == Decimal("40")


def test_activation_refuses_untrusted_stale_derivative_and_unsafe_edits():
    repository = Repository()
    with pytest.raises(FundingError) as untrusted:
        _activate(repository, facts=_facts("100", 1_000_000, "scope-live", passed=False))
    assert untrusted.value.code == "preflight_not_trusted"
    with pytest.raises(FundingError) as stale:
        _activate(repository, facts=_facts("100", 1_000_000 - 30_001, "scope-live"))
    assert stale.value.code == "stale_exchange_facts"
    with pytest.raises(FundingError) as derivative:
        _activate(repository, market_type="swap", symbol="BTC-USDT")
    assert derivative.value.code == "derivative_activation_closed"
    assert strategy_book(repository, "scope-live", "alpha", "live") is None

    _activate(repository)
    _activate(repository)
    assert strategy_book(repository, "scope-live", "alpha", "live").cash == Decimal("60")
    with pytest.raises(FundingError) as locked:
        _activate(repository, budget=Decimal("70"))
    assert locked.value.code == "funded_allocation_locked"
    assert_funded_draft_editable(
        repository,
        scope_id="scope-live",
        strategy="alpha",
        environment="live",
        symbol="BTC-USDT",
        budget=Decimal("60"),
    )
    with pytest.raises(FundingError) as edit:
        assert_funded_draft_editable(
            repository,
            scope_id="scope-live",
            strategy="alpha",
            environment="live",
            symbol="ETH-USDT",
            budget=Decimal("60"),
        )
    assert edit.value.code == "funded_allocation_locked"
    with pytest.raises(FundingError) as deleting:
        assert_funded_draft_editable(
            repository,
            scope_id="scope-live",
            strategy="alpha",
            environment="live",
            symbol="BTC-USDT",
            budget=Decimal("60"),
            deleting=True,
        )
    assert deleting.value.code == "funded_allocation_locked"


class _FillHandler(OrderHandler):
    def __init__(self) -> None:
        self.submitted: list[object] = []

    async def submit(self, order):
        self.submitted.append(order)
        order.status = OrderStatus.FILLED
        order.filled_amount = order.amount
        order.remaining_amount = 0
        order.fill_price = order.price
        return order

    async def cancel(self, order_id: str, symbol: str | None = None):
        return {"status": "canceled"}


class _RefuseHandler(OrderHandler):
    async def submit(self, order):
        raise AssertionError("over-budget order reached the router")

    async def cancel(self, order_id: str, symbol: str | None = None):
        return None


def _manager(
    repository, handler, *, environment: str, scope: str, allow_open: bool, safeguards: bool,
):
    reasons: list[str] = []

    async def remember(event: dict[str, object]) -> None:
        reasons.append(str(event.get("reason", "")))

    manager = UnifiedOrderManager(
        router=OrderRouter(backtest=None, live=handler, mode="live"),
        repository=repository,
        risk_manager=RiskManager(max_position_pct=1.0),
        live_safeguards=safeguards,
        live_market_type="spot",
        allow_live_open_orders=allow_open,
        require_position_ownership=safeguards,
        funding_environment=environment,
        funding_scope_id=lambda: scope,
        initial_equity=100000.0,
        on_risk_event=remember,
    )
    manager.risk_reasons = reasons
    return manager


@pytest.mark.asyncio
async def test_runtime_orders_consume_only_the_strategy_book():
    repository = Repository()
    _activate(repository, environment="sandbox", scope_id="scope-sandbox", facts=_facts(
        "100", 1_000_000, "scope-sandbox",
    ))
    _activate(
        repository,
        environment="sandbox",
        scope_id="scope-sandbox",
        strategy="beta",
        symbol="ETH-USDT",
        budget=Decimal("40"),
        facts=_facts("100", 1_000_000, "scope-sandbox"),
    )
    refused = _manager(
        repository, _RefuseHandler(), environment="sandbox", scope="scope-sandbox",
        allow_open=True, safeguards=False,
    )
    blocked = await refused.submit(
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=1,
        price=100,
        strategy_name="alpha",
    )
    assert blocked.status is OrderStatus.REJECTED
    assert refused.risk_reasons == ["Funded quote budget exceeded"]
    book = strategy_book(repository, "scope-sandbox", "alpha", "sandbox")
    assert book.cash == Decimal("60")
    assert book.reserved_quote == Decimal("0")

    handler = _FillHandler()
    manager = _manager(
        repository, handler, environment="sandbox", scope="scope-sandbox",
        allow_open=True, safeguards=False,
    )
    bought = await manager.submit(
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.001,
        price=10000,
        strategy_name="alpha",
    )
    assert bought.status is OrderStatus.FILLED
    assert strategy_book(repository, "scope-sandbox", "alpha", "sandbox").cash == Decimal("50")
    sold = await manager.submit(
        symbol="BTC/USDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        amount=0.001,
        price=12000,
        strategy_name="alpha",
    )
    assert sold.status is OrderStatus.FILLED
    closed = strategy_book(repository, "scope-sandbox", "alpha", "sandbox")
    assert closed.cash == Decimal("62")
    assert closed.base_position == Decimal("0")
    assert closed.realized_pnl == Decimal("2")
    assert strategy_book(repository, "scope-sandbox", "beta", "sandbox").cash == Decimal("40")
    assert len(handler.submitted) == 2


@pytest.mark.asyncio
async def test_funded_live_book_does_not_open_live_orders():
    repository = Repository()
    _activate(repository)
    handler = _FillHandler()
    manager = _manager(
        repository, handler, environment="live", scope="scope-live",
        allow_open=False, safeguards=True,
    )
    rejected = await manager.submit(
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        amount=0.001,
        price=10000,
        strategy_name="alpha",
    )
    assert rejected.status is OrderStatus.REJECTED
    assert manager.risk_reasons == ["Live position ownership is unavailable"]
    assert handler.submitted == []
    assert strategy_book(repository, "scope-live", "alpha", "live").cash == Decimal("60")
    assert manager.allow_live_open_orders is False


def test_order_factory_keeps_the_live_open_gate_closed():
    save_runtime_settings(AppConfig(
        mode="live",
        exchange=ExchangeConfig(
            api_key="test-key", secret="test-secret", passphrase="test-passphrase", demo=False,
        ),
    ))
    live = create_order_manager(repository=Repository())
    assert live.allow_live_open_orders is False
    assert live.funding_environment == "live"

    save_runtime_settings(AppConfig(
        mode="demo",
        exchange=ExchangeConfig(
            api_key="sandbox-key", secret="test-secret", passphrase="test-passphrase", demo=True,
        ),
    ))
    sandbox = create_order_manager(repository=Repository())
    assert sandbox.funding_environment == "sandbox"
    assert sandbox.allow_live_open_orders is True
