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


def generate_report(
    initial_capital: float,
    trades: list[dict[str, Any]],
    equity_curve: list[float],
) -> BacktestReport:
    final_equity = equity_curve[-1] if equity_curve else initial_capital
    total_return = (final_equity - initial_capital) / initial_capital if initial_capital else 0

    pnl_values = [float(trade.get("pnl", 0)) for trade in trades]
    wins = [pnl for pnl in pnl_values if pnl > 0]
    losses = [pnl for pnl in pnl_values if pnl < 0]
    total_trades = len(trades)
    win_rate = len(wins) / total_trades if total_trades else 0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss else 0

    max_drawdown = _max_drawdown(equity_curve)
    sharpe_ratio = _sharpe_ratio(equity_curve)

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


def _max_drawdown(equity_curve: list[float]) -> float:
    peak = 0.0
    max_drawdown = 0.0

    for equity in equity_curve:
        peak = max(peak, equity)
        if peak:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)

    return max_drawdown


def _sharpe_ratio(equity_curve: list[float]) -> float:
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

    return mean(returns) / volatility * sqrt(252)
