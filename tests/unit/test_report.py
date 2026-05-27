import pytest

from src.backtest.report import generate_report


def test_report_calculates_metrics() -> None:
    report = generate_report(
        initial_capital=10000,
        trades=[{"pnl": 100}, {"pnl": -50}, {"pnl": 200}, {"pnl": -30}],
        equity_curve=[10000, 10100, 10050, 10250, 10220],
    )

    assert report.total_return == pytest.approx(0.022)
    assert report.total_trades == 4
    assert report.win_rate == pytest.approx(0.5)
    assert report.max_drawdown >= 0


def test_report_empty_trades() -> None:
    report = generate_report(initial_capital=10000, trades=[], equity_curve=[10000])

    assert report.total_return == 0
    assert report.total_trades == 0
    assert report.win_rate == 0
