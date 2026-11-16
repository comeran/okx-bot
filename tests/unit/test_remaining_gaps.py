"""剩下五项缺口：同一状态机、成交模型、复式账本、公有行情、可单独重启的边界。"""

from decimal import Decimal

import pytest

from src.backtest.engine import BacktestEngine
from src.backtest.fill_model import BookFillModel, ContractSpec, PriceLevel
from src.backtest.matcher import OrderMatcher
from src.core.types import Bar, Order, OrderSide, OrderStatus, OrderType
from src.data.journal import Journal
from src.data.repository import Repository
from src.market.bars import bar_from_prices, confirmed_candle_row, is_closed_bar
from src.market.service import MarketDataService
from src.order.lifecycle import explain, record_transition
from src.order.manager import RiskGateResult, UnifiedOrderManager
from src.order.router import OrderRouter
from src.runtime.boundaries import Boundary, DeskRuntime
from src.web.api.strategies import LocalPaperOrderHandler


def _order() -> Order:
    return Order(
        id="order-1",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=1,
    )


class _Handler:
    async def submit(self, order: Order) -> Order:
        return order

    async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
        return True


def test_research_fill_and_live_reject_share_one_transition_table() -> None:
    research = _order()
    record_transition(
        research, OrderStatus.FILLED,
        cause="research_match", clock_ms=1_700_000_000_000, source="research_matcher",
    )
    live = _order()
    record_transition(
        live, OrderStatus.REJECTED,
        cause="venue_reject", clock_ms=20, source="venue",
    )
    record_transition(
        live, OrderStatus.PENDING,
        cause="provider_status_conflict", clock_ms=21, source="venue",
    )

    assert research.status is OrderStatus.FILLED
    assert live.status is OrderStatus.PENDING
    assert explain(research) == [
        "pending->filled research_match research_matcher 1700000000000"
    ]
    assert explain(live)[1] == "rejected->pending provider_status_conflict venue 21"
    with pytest.raises(ValueError, match="illegal order transition"):
        record_transition(
            research, OrderStatus.PENDING,
            cause="research_match", clock_ms=2, source="research_matcher",
        )


async def test_live_risk_reject_uses_the_same_machine() -> None:
    manager = UnifiedOrderManager(OrderRouter(_Handler()), timestamp_ms=lambda: 42)
    order = _order()

    rejected = await manager._reject_order(order, "alpha", RiskGateResult(False, "halt"))

    assert rejected.status is OrderStatus.REJECTED
    assert explain(order) == ["pending->rejected risk_reject venue 42"]


class _Buy:
    name = "buy"

    def __init__(self) -> None:
        self.symbol = "BTC-USDT"

    async def on_init(self) -> None:
        return None

    async def on_bar(self, bar: Bar) -> Order:
        return Order(
            id=f"{self.symbol}-{bar.timestamp}",
            symbol=self.symbol,
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            amount=1,
        )

    async def on_order(self, order: Order) -> None:
        return None


def _bar(timestamp: int, price: float, volume: float = 10) -> Bar:
    return Bar(timestamp, price, price + 1, price - 1, price, volume)


class _Remember(_Buy):
    def __init__(self) -> None:
        super().__init__()
        self.fills: list[Order] = []

    async def on_order(self, order: Order) -> None:
        self.fills.append(order)


async def test_single_symbol_backtest_explains_each_fill() -> None:
    strategy = _Remember()
    report = await BacktestEngine(1000, OrderMatcher(slippage=0, fee_rate=0)).run(
        strategy, [_bar(1, 10), _bar(2, 10)]
    )

    assert len(report.trades) == 1
    assert explain(strategy.fills[-1]) == ["pending->filled research_match research_matcher 2"]


def test_spread_and_levels_change_the_fill() -> None:
    touched = BookFillModel(slippage=0, fee_rate=0, spread=0.02).match(
        _order(), _bar(1, 100, volume=5)
    )
    walked = BookFillModel(
        slippage=0,
        fee_rate=0,
        levels=(PriceLevel(100, 0.25), PriceLevel(110, 1)),
    ).match(_order(), _bar(1, 100, volume=5))

    assert touched.fill_price == pytest.approx(101)
    assert walked.status is OrderStatus.FILLED
    assert walked.filled_amount == 1
    assert walked.fill_price == pytest.approx((0.25 * 100 + 0.75 * 110) / 1)


def test_volume_cap_partially_fills() -> None:
    order = _order()
    order.amount = 2
    match = BookFillModel(slippage=0, fee_rate=0, use_volume=True).match(
        order, _bar(1, 100, volume=0.5)
    )

    assert match.status is OrderStatus.PARTIALLY_FILLED
    assert match.filled_amount == 0.5
    assert match.fill_price == pytest.approx(100)


def test_contract_spec_adds_funding_cost() -> None:
    charge = BookFillModel(
        contract=ContractSpec(contract_size=3, funding_rate=0.01),
    ).funding_charge(quantity=2, mark=10)

    assert charge == pytest.approx(0.6)


async def test_portfolio_shares_cash_and_charges_funding() -> None:
    class Both:
        name = "both"

        def __init__(self) -> None:
            self.symbol = ""
            self.seen: list[str] = []

        async def on_init(self) -> None:
            return None

        async def on_bar(self, bar: Bar) -> Order | None:
            self.seen.append(self.symbol)
            if bar.timestamp != 1:
                return None
            return Order(
                id=f"{self.symbol}-{bar.timestamp}",
                symbol=self.symbol,
                side=OrderSide.BUY,
                type=OrderType.MARKET,
                amount=1,
            )

        async def on_order(self, order: Order) -> None:
            return None

    engine = BacktestEngine(15, OrderMatcher(slippage=0, fee_rate=0))
    report = await engine.run_portfolio(
        Both(),
        {
            "BTC-USDT": [_bar(1, 10), _bar(2, 10)],
            "ETH-USDT": [_bar(1, 10), _bar(2, 10)],
        },
        contracts={"BTC-USDT": ContractSpec(contract_size=1, funding_rate=0.1)},
    )

    assert report.total_trades == 1
    assert report.final_equity == pytest.approx(14)


def test_journal_nav_change_equals_the_sum_of_flows() -> None:
    book = Journal("sandbox")
    book.open_cash(Decimal("100"), "USDT", timestamp=1)
    book.post_trade(
        side="buy", quantity=Decimal("1"), price=Decimal("10"),
        currency="USDT", symbol="BTC-USDT", ref="buy-1", timestamp=2,
    )
    book.post_fee(Decimal("1"), "USDT", ref="fee-1", timestamp=2)
    book.post_trade(
        side="sell", quantity=Decimal("1"), price=Decimal("12"),
        currency="USDT", symbol="BTC-USDT", ref="sell-1", timestamp=3,
    )
    book.post_funding(Decimal("0.5"), "USDT", ref="funding-1", timestamp=4)
    book.post_transfer(Decimal("5"), "USDT", ref="transfer-1", timestamp=5, direction="in")
    book.post_settlement(Decimal("0.25"), "USDT", ref="settle-1", timestamp=6, direction="pay")

    assert book.nav_change() == book.flows()
    assert book.nav_change() == Decimal("5.25")


def test_live_journal_rows_stay_out_of_the_sandbox_book(tmp_path) -> None:
    repository = Repository(db_path=str(tmp_path / "journal.db"))
    live = Journal("live")
    sandbox = Journal("sandbox")
    live.open_cash(Decimal("10"), "USDT", timestamp=1)
    sandbox.open_cash(Decimal("4"), "USDT", timestamp=1)
    repository.replace_journal("live", live.entries)
    repository.replace_journal("sandbox", sandbox.entries)

    restored = repository.load_journal("live")

    assert restored.nav() == Decimal("10")
    assert restored.environment == "live"
    assert repository.load_journal("sandbox").nav() == Decimal("4")


def test_research_and_live_parse_the_same_closed_bar() -> None:
    row = [1_700_000_000_000, "100", "110", "90", "105", "3", "0", "0", "1"]
    confirmed = confirmed_candle_row(row)
    research = bar_from_prices(1_700_000_000_000, 100, 110, 90, 105, 3)

    assert confirmed == [
        research.timestamp, research.open, research.high,
        research.low, research.close, research.volume,
    ]
    assert confirmed_candle_row([1_700_000_000_000, 100, 110, 90, 105, 3, 0, 0, "0"]) is None
    assert is_closed_bar(1_700_000_000_000, 60_000, 1_700_000_060_000) is True
    assert is_closed_bar(1_700_000_000_000, 60_000, 1_700_000_059_999) is False


async def test_public_stream_is_read_before_rest_polling() -> None:
    class Stream:
        def __init__(self) -> None:
            self.calls = 0

        async def watch_ohlcv(self, symbol: str, timeframe: str) -> list[list[float]]:
            self.calls += 1
            return [[1_700_000_000_000, 100, 110, 90, 105, 3]]

    class Exchange:
        def __init__(self) -> None:
            self.watched = 0

        async def watch_ohlcv(self, symbol: str, timeframe: str):
            self.watched += 1
            raise AssertionError("公有流可用时不应该再轮询 watch_ohlcv")

        async def fetch_ohlcv(self, *args, **kwargs):
            raise AssertionError("没有缺口时不应该用 REST 补齐")

        async def close(self) -> None:
            return None

    stream = Stream()
    exchange = Exchange()
    service = MarketDataService(
        "key", "secret", "pass", public_stream=stream, exchange=exchange,
    )
    service._current_time = lambda: 1_700_000_120
    service.subscribe("BTC-USDT", "1m", _accept)

    await service._poll_once("BTC-USDT", "1m")

    assert stream.calls == 1
    assert exchange.watched == 0
    assert [bar.close for bar in service.get_recent_bars("BTC-USDT", "1m")] == [105]


async def _accept(_bar: Bar) -> None:
    return None


async def test_gap_backfill_continues_until_the_hole_closes() -> None:
    class Exchange:
        def __init__(self) -> None:
            self.fetches = 0

        async def watch_ohlcv(self, symbol: str, timeframe: str):
            return [[600_000, 1, 2, 1, 1, 1]]

        async def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
            self.fetches += 1
            return [[since, 1, 2, 1, 1, 1]]

        async def close(self) -> None:
            return None

    exchange = Exchange()
    service = MarketDataService("key", "secret", "pass", exchange=exchange)
    service._current_time = lambda: 1_000
    service._last_bar_timestamps["BTC-USDT:1m"] = 60_000
    service.subscribe("BTC-USDT", "1m", _accept)

    await service._poll_once("BTC-USDT", "1m")

    timestamps = [bar.timestamp for bar in service.get_recent_bars("BTC-USDT", "1m")]
    assert 120_000 in timestamps
    assert 540_000 in timestamps
    assert exchange.fetches > 4


async def test_restarting_the_market_boundary_leaves_the_ledger_and_risk_up() -> None:
    ledger_runs = {"start": 0, "stop": 0}
    risk_runs = {"start": 0, "stop": 0}
    market_runs = {"start": 0, "stop": 0}

    async def start_ledger() -> None:
        ledger_runs["start"] += 1

    async def stop_ledger() -> None:
        ledger_runs["stop"] += 1

    async def start_risk() -> None:
        risk_runs["start"] += 1

    async def stop_risk() -> None:
        risk_runs["stop"] += 1

    async def start_market() -> None:
        market_runs["start"] += 1

    async def stop_market() -> None:
        market_runs["stop"] += 1

    desk = DeskRuntime([
        Boundary("ledger", start_ledger, stop_ledger),
        Boundary("risk", start_risk, stop_risk),
        Boundary("orders", start_ledger, stop_ledger),
        Boundary("market", start_market, stop_market),
    ])
    await desk.start()
    await desk.restart("market")

    assert market_runs == {"start": 2, "stop": 1}
    assert ledger_runs["stop"] == 0
    assert risk_runs["stop"] == 0
    assert desk.running("ledger") is True
    assert desk.running("risk") is True
    assert desk.running("market") is True


async def test_local_paper_fill_uses_the_shared_machine() -> None:
    handler = LocalPaperOrderHandler()
    order = Order(
        id="paper-1",
        symbol="BTC-USDT",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=1,
        price=10,
    )

    filled = await handler.submit(order)

    assert filled.status is OrderStatus.FILLED
    assert explain(filled)[0].startswith("pending->filled")
