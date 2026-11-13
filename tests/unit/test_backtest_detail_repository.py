import pytest
from sqlmodel import SQLModel, create_engine

from src.data.models import (
    BacktestEquityPointRecord,
    BacktestRejectedOrderRecord,
    BacktestResultRecord,
)
from src.data.repository import Repository


@pytest.fixture
def repo() -> Repository:
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    return Repository(engine)


def test_backtest_details_round_trip(repo: Repository) -> None:
    repo.save_backtest_result(
        BacktestResultRecord(
            id="bt-details",
            strategy="ma_cross",
            symbol="BTC-USDT",
            timeframe="1h",
            start_time=1,
            end_time=2,
            initial_capital=100,
            total_return=0,
            sharpe_ratio=0,
            max_drawdown=0.2,
            win_rate=0,
            total_trades=0,
            created_at=3,
        )
    )
    repo.save_backtest_details(
        "bt-details",
        [
            BacktestEquityPointRecord(
                result_id="bt-details", seq=0, timestamp=0, equity=100, drawdown=0
            ),
            BacktestEquityPointRecord(
                result_id="bt-details", seq=1, timestamp=1, equity=80, drawdown=0.2
            ),
        ],
        [
            BacktestRejectedOrderRecord(
                result_id="bt-details",
                symbol="BTC-USDT",
                side="buy",
                timestamp=1,
                amount=2,
                price=None,
                reason="Order exceeds maximum position size",
            )
        ],
    )

    points = repo.get_backtest_equity_points("bt-details")
    rejected = repo.get_backtest_rejected_orders("bt-details")

    assert [point.equity for point in points] == [100, 80]
    assert [point.drawdown for point in points] == [0, 0.2]
    assert rejected[0].reason == "Order exceeds maximum position size"
    assert rejected[0].price is None
