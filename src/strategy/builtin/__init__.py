from src.strategy.builtin.bollinger_mean_reversion import register_bollinger_mean_reversion
from src.strategy.builtin.donchian_breakout import register_donchian_breakout
from src.strategy.builtin.ma_cross import register_ma_cross
from src.strategy.builtin.rsi_mean_reversion import register_rsi_mean_reversion
from src.strategy.registry import StrategyRegistry


def register_builtin_strategies(registry: StrategyRegistry) -> None:
    register_ma_cross(registry)
    register_rsi_mean_reversion(registry)
    register_bollinger_mean_reversion(registry)
    register_donchian_breakout(registry)
