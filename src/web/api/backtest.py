from __future__ import annotations

import time
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.backtest.datasource import BacktestDataSource
from src.backtest.engine import BacktestEngine
from src.backtest.matcher import OrderMatcher
from src.core.config import BacktestConfig, load_config
from src.data.models import BacktestResultRecord
from src.data.repository import Repository
from src.web.api.strategies import create_strategy_registry

router = APIRouter()


class BacktestRequest(BaseModel):
    strategy: str
    symbol: str
    timeframe: str
    start_time: int
    end_time: int
    initial_capital: float


@router.post("/run")
async def run_backtest(req: BacktestRequest) -> dict[str, float | int]:
    if req.end_time <= req.start_time:
        raise HTTPException(status_code=422, detail="end_time must be after start_time")
    if req.initial_capital <= 0:
        raise HTTPException(status_code=422, detail="initial_capital must be greater than 0")

    registry = create_strategy_registry()
    if req.strategy not in registry.list_strategies():
        raise HTTPException(status_code=404, detail="Strategy not found")

    repository = Repository()
    bars = BacktestDataSource(repository, req.symbol, req.timeframe).get_cached_bars(
        req.start_time,
        req.end_time,
    )
    if len(bars) < 2:
        raise HTTPException(
            status_code=422,
            detail="insufficient historical data for requested backtest range",
        )

    strategy = registry.create(req.strategy)
    if hasattr(strategy, "symbol"):
        strategy.symbol = req.symbol

    backtest_config = load_backtest_config()
    report = await BacktestEngine(
        initial_capital=req.initial_capital,
        matcher=OrderMatcher(
            slippage=backtest_config.slippage,
            fee_rate=backtest_config.fee_rate,
        ),
    ).run(strategy, bars)
    metrics = {
        "total_return": report.total_return,
        "sharpe_ratio": report.sharpe_ratio,
        "max_drawdown": report.max_drawdown,
        "win_rate": report.win_rate,
        "total_trades": report.total_trades,
    }
    created_at = current_timestamp_ms()
    repository.save_backtest_result(
        BacktestResultRecord(
            id=f"bt_{created_at}_{uuid4().hex[:8]}",
            strategy=req.strategy,
            symbol=req.symbol,
            timeframe=req.timeframe,
            start_time=req.start_time,
            end_time=req.end_time,
            initial_capital=req.initial_capital,
            created_at=created_at,
            **metrics,
        )
    )
    return metrics


@router.get("/results")
async def list_results() -> list[dict[str, float | int | str]]:
    return [result.model_dump() for result in Repository().get_backtest_results()]


def current_timestamp_ms() -> int:
    return int(time.time() * 1000)


def load_backtest_config() -> BacktestConfig:
    try:
        return load_config("config/settings.yaml").backtest
    except FileNotFoundError:
        return BacktestConfig()
