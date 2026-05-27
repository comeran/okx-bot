from src.strategy.base import BaseStrategy


class StrategyRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, type[BaseStrategy]] = {}

    def register(self, name: str, cls: type[BaseStrategy]) -> None:
        self._strategies[name] = cls

    def create(self, name: str) -> BaseStrategy:
        strategy_cls = self._strategies[name]
        strategy = strategy_cls()
        strategy.name = name
        return strategy

    def list_strategies(self) -> list[str]:
        return list(self._strategies)
