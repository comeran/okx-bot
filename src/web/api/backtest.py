from __future__ import annotations

import math
import time
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.backtest.engine import BacktestEngine, spot_backtest_symbol
from src.backtest.historical_data import (
    MAX_PAGE_LIMIT,
    InsufficientHistoricalDataError,
    UnsupportedTimeframeError,
    ensure_historical_bars,
)
from src.backtest.matcher import OrderMatcher
from src.backtest.report import drawdown_series
from src.core.config import BacktestConfig
from src.core.runtime_settings import load_runtime_settings
from src.data.models import (
    BacktestEquityPointRecord,
    BacktestRejectedOrderRecord,
    BacktestResultRecord,
    BacktestTradeRecord,
)
from src.data.repository import Repository
from src.exchange.okx_client import OKX_RUNTIME_TIMEFRAME_MILLISECONDS
from src.exchange.okx_spot import OKXSpotAdapter
from src.risk.manager import RiskManager
from src.web.api.strategies import create_strategy_registry

router = APIRouter()

MAX_BACKTEST_BARS = 200_000


class BacktestRequest(BaseModel):
    strategy: str
    symbol: str
    timeframe: str
    start_time: int
    end_time: int
    initial_capital: float
    market_type: Literal["spot"] = "spot"


@router.post("/run")
async def run_backtest(req: BacktestRequest) -> dict[str, float | int]:
    if req.end_time <= req.start_time:
        raise HTTPException(status_code=422, detail="end_time must be after start_time")
    if not math.isfinite(req.initial_capital) or req.initial_capital <= 0:
        raise HTTPException(status_code=422, detail="initial_capital must be greater than 0")
    try:
        symbol = spot_backtest_symbol(req.symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    timeframe_ms = OKX_RUNTIME_TIMEFRAME_MILLISECONDS.get(req.timeframe)
    if timeframe_ms:
        requested_bars = (req.end_time - req.start_time) // timeframe_ms + 1
        if requested_bars > MAX_BACKTEST_BARS:
            raise HTTPException(
                status_code=422,
                detail=f"backtest range exceeds {MAX_BACKTEST_BARS} bars",
            )

    registry = create_strategy_registry()
    repository = Repository()
    strategy_config = None
    get_strategy_config = getattr(repository, "get_strategy_config", None)
    if get_strategy_config is not None:
        strategy_config = get_strategy_config(req.strategy)

    if strategy_config is not None:
        if strategy_config.strategy_type not in registry.list_strategies():
            raise HTTPException(status_code=404, detail="Strategy not found")
        strategy = registry.create_instance(
            strategy_config.name,
            strategy_config.strategy_type,
            strategy_config.symbol,
            strategy_config.timeframe,
            strategy_config.params,
        )
    elif req.strategy in registry.list_strategies():
        strategy = registry.create(req.strategy)
    else:
        raise HTTPException(status_code=404, detail="Strategy not found")

    # Research requests may override a saved strategy's instrument and timeframe.
    # Apply both before loading data so the strategy and account share one context.
    strategy.symbol = symbol
    strategy.timeframe = req.timeframe

    try:
        bars = await ensure_historical_bars(
            repo=repository,
            symbol=symbol,
            timeframe=req.timeframe,
            start=req.start_time,
            end=req.end_time,
            adapter_factory=lambda: OKXSpotAdapter(
                api_key="",
                secret="",
                passphrase="",
            ),
            page_limit=MAX_PAGE_LIMIT,
        )
    except UnsupportedTimeframeError as exc:
        raise HTTPException(
            status_code=422,
            detail="unsupported timeframe for historical backtest data",
        ) from exc
    except InsufficientHistoricalDataError as exc:
        raise HTTPException(
            status_code=422,
            detail="insufficient historical data for requested backtest range",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="failed to fetch historical market data",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="failed to fetch historical market data",
        ) from exc
    if len(bars) < 2:
        raise HTTPException(
            status_code=422,
            detail="insufficient historical data for requested backtest range",
        )

    backtest_config = load_backtest_config()
    report = await BacktestEngine(
        initial_capital=req.initial_capital,
        matcher=OrderMatcher(
            slippage=backtest_config.slippage,
            fee_rate=backtest_config.fee_rate,
        ),
        market_type=req.market_type,
        symbol=symbol,
        risk_manager=research_risk_manager(),
    ).run(strategy, bars)
    metrics = {
        "total_return": report.total_return,
        "sharpe_ratio": report.sharpe_ratio,
        "max_drawdown": report.max_drawdown,
        "win_rate": report.win_rate,
        "total_trades": report.total_trades,
    }
    created_at = current_timestamp_ms()
    result_id = f"bt_{created_at}_{uuid4().hex[:8]}"
    result = BacktestResultRecord(
        id=result_id,
        strategy=req.strategy,
        symbol=symbol,
        timeframe=req.timeframe,
        start_time=req.start_time,
        end_time=req.end_time,
        initial_capital=req.initial_capital,
        created_at=created_at,
        **metrics,
    )
    backtest_trades = [
        BacktestTradeRecord(
            result_id=result_id,
            symbol=trade["symbol"],
            side=trade["side"],
            timestamp=trade["timestamp"],
            price=trade["price"],
            amount=trade["amount"],
            fee=trade["fee"],
            pnl=trade["pnl"],
        )
        for trade in report.trades
    ]
    repository.save_backtest_result_with_trades(result, backtest_trades)
    save_details = getattr(repository, "save_backtest_details", None)
    if callable(save_details):
        timestamps = report.equity_timestamps
        if len(timestamps) != len(report.equity_curve):
            timestamps = list(range(len(report.equity_curve)))
        drawdowns = drawdown_series(report.equity_curve)
        save_details(
            result_id,
            [
                BacktestEquityPointRecord(
                    result_id=result_id,
                    seq=index,
                    timestamp=int(timestamp),
                    equity=float(equity),
                    drawdown=float(drawdown),
                )
                for index, (timestamp, equity, drawdown) in enumerate(
                    zip(timestamps, report.equity_curve, drawdowns, strict=True)
                )
            ],
            [
                BacktestRejectedOrderRecord(
                    result_id=result_id,
                    symbol=str(rejected["symbol"]),
                    side=str(rejected["side"]),
                    timestamp=int(rejected["timestamp"]),
                    amount=float(rejected["amount"]),
                    price=None if rejected["price"] is None else float(rejected["price"]),
                    reason=str(rejected["reason"]),
                )
                for rejected in report.rejected_orders
            ],
        )
    return metrics


@router.get("/results")
async def list_results() -> list[dict[str, float | int | str]]:
    return [result.model_dump() for result in Repository().get_backtest_results()]


@router.get("/results/{result_id}")
async def get_result_detail(result_id: str) -> dict[str, list[dict] | dict]:
    repository = Repository()
    result = repository.get_backtest_result(result_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Backtest result not found")

    try:
        klines = repository.get_klines(
            symbol=result.symbol,
            timeframe=result.timeframe,
            start=result.start_time,
            end=result.end_time,
            source="backtest",
            runtime_mode="backtest",
        )
    except TypeError as exc:
        if "unexpected keyword argument" not in str(exc):
            raise
        raise HTTPException(
            status_code=422,
            detail="repository does not support scoped backtest kline queries",
        ) from exc
    klines = [
        kline
        for kline in klines
        if getattr(kline, "source", "unknown") == "backtest"
        and getattr(kline, "runtime_mode", "unknown") == "backtest"
    ]
    markers = repository.get_backtest_trades(result_id)
    equity_getter = getattr(repository, "get_backtest_equity_points", None)
    rejected_getter = getattr(repository, "get_backtest_rejected_orders", None)
    equity_curve = equity_getter(result_id) if callable(equity_getter) else []
    rejected_orders = rejected_getter(result_id) if callable(rejected_getter) else []
    return {
        "result": result.model_dump(),
        "klines": [
            {
                **{
                    field: getattr(kline, field)
                    for field in (
                        "id", "symbol", "timeframe", "timestamp", "open", "high",
                        "low", "close", "volume",
                    )
                },
                "source": "backtest",
                "runtime_mode": "backtest",
            }
            for kline in klines
        ],
        "markers": [marker.model_dump() for marker in markers],
        "equity_curve": [point.model_dump() for point in equity_curve],
        "rejected_orders": [order.model_dump() for order in rejected_orders],
    }


def current_timestamp_ms() -> int:
    return int(time.time() * 1000)


def load_backtest_config() -> BacktestConfig:
    return load_runtime_settings().backtest


def research_risk_manager() -> RiskManager:
    """回测使用和实盘相同的下单前比例规则，包含日亏和回撤。"""
    risk = load_runtime_settings().risk
    return RiskManager(
        max_position_pct=risk.max_total_position_pct,
        max_daily_loss_pct=risk.max_daily_loss_pct,
        max_drawdown_pct=risk.max_drawdown_pct,
        enforce_daily_loss=True,
        enforce_drawdown=True,
    )
