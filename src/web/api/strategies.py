from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.core.engine import BotEngine
from src.strategy.builtin.ma_cross import register_ma_cross
from src.strategy.registry import StrategyRegistry


def create_strategy_registry() -> StrategyRegistry:
    registry = StrategyRegistry()
    register_ma_cross(registry)
    return registry


def strategy_exists(name: str) -> bool:
    return name in create_strategy_registry().list_strategies()


def create_router() -> APIRouter:
    router = APIRouter()
    registry = create_strategy_registry()
    strategy_status: dict[str, str] = {name: "stopped" for name in registry.list_strategies()}
    engines: dict[str, BotEngine] = {}

    def strategy_exists(name: str) -> bool:
        return name in strategy_status

    @router.get("")
    async def list_strategies() -> list[dict[str, str]]:
        return [
            {"name": name, "status": strategy_status[name]} for name in registry.list_strategies()
        ]

    @router.post("/{name}/start")
    async def start_strategy(name: str) -> dict[str, str]:
        if not strategy_exists(name):
            raise HTTPException(status_code=404, detail="Strategy not found")
        if name not in engines:
            engine = BotEngine(strategies=[registry.create(name)])
            await engine.start()
            engines[name] = engine
        strategy_status[name] = "running"
        return {"status": "started", "strategy": name}

    @router.post("/{name}/stop")
    async def stop_strategy(name: str) -> dict[str, str]:
        if not strategy_exists(name):
            raise HTTPException(status_code=404, detail="Strategy not found")
        engine = engines.pop(name, None)
        if engine is not None:
            await engine.stop()
        strategy_status[name] = "stopped"
        return {"status": "stopped", "strategy": name}

    return router


router = create_router()
