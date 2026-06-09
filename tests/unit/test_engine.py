import asyncio
import contextlib

from src.core.engine import BotEngine
from src.core.types import Bar


class FakeMarketDataService:
    def __init__(self) -> None:
        self.subscriptions = {}
        self.start_count = 0
        self.stop_count = 0
        self._running = False

    def subscribe(self, symbol, timeframe, callback):
        self.subscriptions.setdefault((symbol, timeframe), []).append(callback)

    async def start(self):
        self.start_count += 1
        self._running = True

    async def stop(self):
        self.stop_count += 1
        self._running = False


class RecordingStrategy:
    def __init__(self, name, symbol="BTC-USDT", timeframe="1m") -> None:
        self.name = name
        self.symbol = symbol
        self.timeframe = timeframe
        self.bars = []
        self.shutdown_count = 0

    async def on_init(self):
        pass

    async def on_bar(self, bar):
        self.bars.append(bar)

    async def on_shutdown(self):
        self.shutdown_count += 1


class FailingStrategy(RecordingStrategy):
    async def on_bar(self, bar):
        raise RuntimeError("boom")


def make_bar() -> Bar:
    return Bar(
        timestamp=1700000000000,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1.0,
    )


async def test_engine_subscribes_strategies_by_symbol_timeframe_and_fans_out_bars():
    market_data = FakeMarketDataService()
    first = RecordingStrategy("first")
    second = RecordingStrategy("second")
    engine = BotEngine([first, second], market_data_service=market_data)

    await engine.start()
    for callback in market_data.subscriptions[("BTC-USDT", "1m")]:
        await callback(make_bar())

    assert market_data.start_count == 1
    assert len(market_data.subscriptions[("BTC-USDT", "1m")]) == 2
    assert len(first.bars) == 1
    assert len(second.bars) == 1


async def test_stopping_one_engine_keeps_shared_market_data_task_running():
    class BlockingMarketDataService(FakeMarketDataService):
        async def start(self):
            self.start_count += 1
            self._running = True
            try:
                await asyncio.Event().wait()
            finally:
                self._running = False

    market_data = BlockingMarketDataService()
    first_engine = BotEngine(
        [RecordingStrategy("first")],
        market_data_service=market_data,
        stop_market_data_on_stop=False,
    )
    second_engine = BotEngine(
        [RecordingStrategy("second")],
        market_data_service=market_data,
        stop_market_data_on_stop=False,
    )

    await first_engine.start()
    await second_engine.start()
    await first_engine.stop()

    assert market_data.start_count == 1
    assert first_engine._market_data_task is not None
    assert not first_engine._market_data_task.done()

    first_engine._market_data_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await first_engine._market_data_task


async def test_engine_waits_for_initial_callback_when_market_data_already_running():
    class RunningMarketDataService(FakeMarketDataService):
        def __init__(self) -> None:
            super().__init__()
            self._running = True
            self.tasks = []

        def subscribe(self, symbol, timeframe, callback):
            super().subscribe(symbol, timeframe, callback)
            self.tasks.append(asyncio.create_task(callback(make_bar())))

    class DelayedFailingStrategy(FailingStrategy):
        async def on_bar(self, bar):
            await asyncio.sleep(0)
            raise RuntimeError("boom")

    market_data = RunningMarketDataService()
    errors = []

    async def on_strategy_error(name, error):
        errors.append((name, str(error)))

    engine = BotEngine(
        [DelayedFailingStrategy("failing")],
        market_data_service=market_data,
        on_strategy_error=on_strategy_error,
        stop_market_data_on_stop=False,
    )

    await engine.start()

    assert errors == [("failing", "boom")]


async def test_engine_stops_only_failing_strategy_and_reports_error():
    market_data = FakeMarketDataService()
    errors = []
    failing = FailingStrategy("failing")
    healthy = RecordingStrategy("healthy")

    async def on_strategy_error(name, error):
        errors.append((name, str(error)))

    engine = BotEngine(
        [failing, healthy],
        market_data_service=market_data,
        on_strategy_error=on_strategy_error,
    )

    await engine.start()
    callbacks = market_data.subscriptions[("BTC-USDT", "1m")]
    await callbacks[0](make_bar())
    await callbacks[0](make_bar())
    await callbacks[1](make_bar())

    assert engine.running is True
    assert errors == [("failing", "boom")]
    assert failing.shutdown_count == 1
    assert len(healthy.bars) == 1
    assert healthy.shutdown_count == 0


async def test_engine_runs_before_strategy_bar_before_on_bar():
    class EventStrategy(RecordingStrategy):
        async def on_bar(self, bar):
            events.append(("on_bar", self.name, len(self.bars)))
            await super().on_bar(bar)

    market_data = FakeMarketDataService()
    events = []
    strategy = EventStrategy("ma_cross")

    async def before_strategy_bar(strategy, bar):
        events.append(("before", strategy.name, len(strategy.bars)))

    engine = BotEngine(
        [strategy],
        market_data_service=market_data,
        before_strategy_bar=before_strategy_bar,
    )

    await engine.start()
    await market_data.subscriptions[("BTC-USDT", "1m")][0](make_bar())

    assert events == [("before", "ma_cross", 0), ("on_bar", "ma_cross", 0)]
    assert len(strategy.bars) == 1


async def test_engine_before_strategy_bar_failure_uses_strategy_error_path():
    market_data = FakeMarketDataService()
    strategy = RecordingStrategy("ma_cross")
    errors = []

    async def before_strategy_bar(strategy, bar):
        raise RuntimeError("mark failed")

    async def on_strategy_error(name, error):
        errors.append((name, str(error)))

    engine = BotEngine(
        [strategy],
        market_data_service=market_data,
        before_strategy_bar=before_strategy_bar,
        on_strategy_error=on_strategy_error,
    )

    await engine.start()
    await market_data.subscriptions[("BTC-USDT", "1m")][0](make_bar())
    await market_data.subscriptions[("BTC-USDT", "1m")][0](make_bar())

    assert errors == [("ma_cross", "mark failed")]
    assert strategy.bars == []
    assert strategy.shutdown_count == 1
