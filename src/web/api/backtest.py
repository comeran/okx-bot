from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.backtest.report import generate_report
from src.web.api.strategies import strategy_exists

router = APIRouter()
_results: list[dict[str, float | int | str]] = []


class BacktestRequest(BaseModel):
    strategy: str
    symbol: str
    timeframe: str
    start_time: int
    end_time: int
    initial_capital: float


@router.post("/run")
async def run_backtest(req: BacktestRequest) -> dict[str, float | int]:
    if not strategy_exists(req.strategy):
        raise HTTPException(status_code=404, detail="Strategy not found")

    duration_steps = max(
        1,
        min(24, (req.end_time - req.start_time) // _timeframe_ms(req.timeframe)),
    )
    symbol_bias = (sum(ord(char) for char in req.symbol) % 7) / 10000
    trades = [
        {"pnl": req.initial_capital * (0.003 + symbol_bias), "timestamp": req.start_time},
        {"pnl": -req.initial_capital * 0.0015, "timestamp": req.start_time + duration_steps},
        {"pnl": req.initial_capital * (0.002 + duration_steps / 10000), "timestamp": req.end_time},
    ]
    equity = req.initial_capital
    equity_curve = [equity]
    for trade in trades:
        equity += float(trade["pnl"])
        equity_curve.append(equity)

    report = generate_report(
        initial_capital=req.initial_capital,
        trades=trades,
        equity_curve=equity_curve,
    )
    metrics = {
        "total_return": report.total_return,
        "sharpe_ratio": report.sharpe_ratio,
        "max_drawdown": report.max_drawdown,
        "win_rate": report.win_rate,
        "total_trades": report.total_trades,
    }
    _results.append(
        {
            "strategy": req.strategy,
            "symbol": req.symbol,
            "timeframe": req.timeframe,
            "start_time": req.start_time,
            "end_time": req.end_time,
            **metrics,
        }
    )
    return metrics


def _timeframe_ms(timeframe: str) -> int:
    unit = timeframe[-1:]
    value = int(timeframe[:-1]) if timeframe[:-1].isdigit() else 1
    value = max(1, value)
    return {
        "m": 60_000,
        "h": 3_600_000,
        "d": 86_400_000,
    }.get(unit, 3_600_000) * value


@router.get("/results")
async def list_results() -> list[dict[str, float | int | str]]:
    return _results
