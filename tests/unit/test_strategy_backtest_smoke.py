import pytest

from src.backtest.engine import BacktestEngine
from src.backtest.matcher import OrderMatcher
from src.core.types import Bar
from src.strategy.builtin import register_builtin_strategies
from src.strategy.registry import StrategyRegistry


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "strategy_type",
    [
        "ma_cross",
        "rsi_mean_reversion",
        "bollinger_mean_reversion",
        "donchian_breakout",
    ],
)
async def test_registered_builtin_strategy_can_run_through_backtest_engine(
    strategy_type: str,
) -> None:
    registry = StrategyRegistry()
    register_builtin_strategies(registry)
    strategy = registry.create_instance(
        name=f"{strategy_type}_btc",
        strategy_type=strategy_type,
        symbol="BTC-USDT",
        timeframe="1h",
        params={},
    )
    assert strategy.name == f"{strategy_type}_btc"
    assert strategy.symbol == "BTC-USDT"
    assert strategy.timeframe == "1h"
    bars = [
        Bar(timestamp=index, open=100, high=101, low=99, close=100, volume=1)
        for index in range(40)
    ]

    report = await BacktestEngine(
        initial_capital=100000,
        matcher=OrderMatcher(slippage=0, fee_rate=0.001),
    ).run(strategy, bars)

    assert report.total_trades == 0
    assert len(registry.list_strategies()) == 4
