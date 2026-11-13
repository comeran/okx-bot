from dataclasses import dataclass, field
from math import sqrt
from statistics import mean, stdev
from typing import Any


@dataclass
class BacktestReport:
    initial_capital: float = 0
    final_equity: float = 0
    total_return: float = 0
    annualized_return: float = 0
    sharpe_ratio: float = 0
    max_drawdown: float = 0
    win_rate: float = 0
    profit_factor: float = 0
    total_trades: int = 0
    trades: list[dict[str, Any]] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    equity_timestamps: list[int] = field(default_factory=list)
    rejected_orders: list[dict[str, Any]] = field(default_factory=list)


def generate_report(
    initial_capital: float,
    trades: list[dict[str, Any]],
    equity_curve: list[float],
    round_trip_pnls: list[float] | None = None,
    bars_per_year: float | None = None,
) -> BacktestReport:
    final_equity = equity_curve[-1] if equity_curve else initial_capital
    total_return = (final_equity - initial_capital) / initial_capital if initial_capital else 0

    if round_trip_pnls is not None:
        pnl_values = [float(pnl) for pnl in round_trip_pnls]
    else:
        pnl_values = [float(trade.get("pnl", 0)) for trade in trades]
    wins = [pnl for pnl in pnl_values if pnl > 0]
    losses = [pnl for pnl in pnl_values if pnl < 0]
    total_trades = len(pnl_values)
    win_rate = len(wins) / total_trades if total_trades else 0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss else 0

    max_drawdown = _max_drawdown(equity_curve)
    sharpe_ratio = _sharpe_ratio(equity_curve, bars_per_year)

    return BacktestReport(
        initial_capital=initial_capital,
        final_equity=final_equity,
        total_return=total_return,
        sharpe_ratio=sharpe_ratio,
        max_drawdown=max_drawdown,
        win_rate=win_rate,
        profit_factor=profit_factor,
        total_trades=total_trades,
        trades=trades,
        equity_curve=equity_curve,
    )


def drawdown_series(equity_curve: list[float]) -> list[float]:
    peak = 0.0
    series: list[float] = []
    for equity in equity_curve:
        peak = max(peak, equity)
        series.append(0.0 if peak == 0 else (peak - equity) / peak)
    return series


def _max_drawdown(equity_curve: list[float]) -> float:
    peak = 0.0
    max_drawdown = 0.0

    for equity in equity_curve:
        peak = max(peak, equity)
        if peak:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)

    return max_drawdown


def _sharpe_ratio(equity_curve: list[float], bars_per_year: float | None = None) -> float:
    returns = [
        (current - previous) / previous
        for previous, current in zip(equity_curve, equity_curve[1:], strict=False)
        if previous
    ]
    if len(returns) < 2:
        return 0

    volatility = stdev(returns)
    if volatility == 0:
        return 0

    annualization = sqrt(bars_per_year) if bars_per_year and bars_per_year > 0 else sqrt(252)
    return mean(returns) / volatility * annualization
