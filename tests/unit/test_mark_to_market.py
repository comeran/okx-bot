from types import SimpleNamespace

from src.data.models import AccountRecord, PositionRecord
from src.data.repository import Repository
from src.order.mark_to_market import PaperMarkToMarketService
from src.order.value import position_quantity


def test_delayed_mark_does_not_overwrite_newer_position_or_account_state(tmp_path):
    repo = Repository(db_path=str(tmp_path / "delayed-mark.db"))
    repo.upsert_account(AccountRecord(
        strategy="ma_cross", source="paper", runtime_mode="paper",
        initial_equity=100000.0, cash_balance=99900.0,
        equity=100100.0, realized_pnl=0.0, unrealized_pnl=100.0, daily_pnl=0.0,
        fees_paid=0.0, updated_at=200,
    ))
    repo.upsert_position(PositionRecord(
        strategy="ma_cross", source="paper", runtime_mode="paper",
        symbol="BTC-USDT", side="long", amount=1.0,
        entry_price=100.0, leverage=1, mark_price=200.0, unrealized_pnl=100.0,
        timestamp=200,
    ))

    assert PaperMarkToMarketService(repo).mark("ma_cross", "BTC-USDT", 150.0, 100) is True

    position = repo.get_position("ma_cross", "BTC-USDT")
    account = repo.get_account("ma_cross")
    assert position is not None
    assert position.timestamp == 200
    assert position.mark_price == 200.0
    assert position.unrealized_pnl == 100.0
    assert account is not None
    assert account.updated_at == 200
    assert account.unrealized_pnl == 100.0
    assert account.equity == 100100.0


def test_linear_derivative_position_quantity_uses_base_amount():
    position = SimpleNamespace(
        contracts=100.0,
        amount=1.0,
        quantity=1.0,
        quantity_unit="base",
        is_inverse=False,
        contract_size=0.01,
    )

    assert position_quantity(position) == 1.0
