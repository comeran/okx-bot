import os

import pytest

from src.backtest.manifest import annualized_return, data_digest, split_oos
from src.core.config import AppConfig
from src.core.types import Bar
from src.data.live_ownership import OwnershipError, grant_ownership_from_balance
from src.data.models import AccountRecord, OrderRecord, TradeRecord
from src.data.pnl import strategy_realized_pnl
from src.data.repository import Repository
from src.ops.audit import record_operator_action
from src.ops.backup import backup_sqlite
from src.ops.lifecycle import graceful_shutdown, startup_reconcile
from src.risk.daily_ledger import roll_daily_pnl, utc_day
from src.risk.supervisor import supervision_reason
from src.web.api import strategies as strategy_api


def test_demo_orders_use_the_live_router_and_paper_stays_local(monkeypatch) -> None:
    monkeypatch.setattr(strategy_api, "load_runtime_settings", lambda: AppConfig(mode="demo"))
    assert strategy_api.current_order_router_mode() == "live"
    monkeypatch.setattr(strategy_api, "load_runtime_settings", lambda: AppConfig(mode="paper"))
    assert strategy_api.current_order_router_mode() == "demo"
    assert strategy_api.resolve_order_router_mode("demo") == "demo"
    assert strategy_api.resolve_order_router_mode("paper") == "demo"
    assert strategy_api.resolve_order_router_mode("live") == "live"


def test_oos_split_keeps_a_held_out_tail() -> None:
    insample, held = split_oos(list(range(10)), 0.2)
    assert insample == list(range(8))
    assert held == [8, 9]


def test_data_digest_changes_when_a_bar_changes() -> None:
    first = [Bar(1, 1, 2, 1, 1, 1)]
    second = [Bar(1, 1, 2, 1, 2, 1)]

    assert data_digest(first) == data_digest(first)
    assert data_digest(first) != data_digest(second)


def test_annualized_return_uses_elapsed_years() -> None:
    assert annualized_return(100, 121, 365 * 24 * 60 * 60 * 1000) == pytest.approx(0.21)


def test_live_ownership_cannot_share_one_balance(tmp_path) -> None:
    repository = Repository(db_path=str(tmp_path / "bot.db"))
    grant_ownership_from_balance(
        repository,
        scope_id="scope",
        strategy="alpha",
        symbol="BTC-USDT",
        budget=60,
        available=100,
        environment="live",
        timestamp=1,
    )
    with pytest.raises(OwnershipError) as error:
        grant_ownership_from_balance(
            repository,
            scope_id="scope",
            strategy="beta",
            symbol="ETH-USDT",
            budget=50,
            available=100,
            environment="live",
            timestamp=2,
        )

    assert error.value.code == "budget_overlap"


def test_daily_pnl_resets_when_the_utc_day_changes(tmp_path) -> None:
    repository = Repository(db_path=str(tmp_path / "bot.db"))
    repository.upsert_account(AccountRecord(
        strategy="alpha",
        initial_equity=1000,
        cash_balance=1000,
        equity=950,
        realized_pnl=-50,
        unrealized_pnl=0,
        daily_pnl=-50,
        fees_paid=0,
        updated_at=1,
    ))
    yesterday = 1_700_000_000_000
    today = yesterday + 86_400_000
    repository.set_daily_ledger_day(utc_day(yesterday), yesterday)

    assert roll_daily_pnl(repository, yesterday) is False
    assert repository.get_account("alpha").daily_pnl == pytest.approx(-50)
    assert roll_daily_pnl(repository, today) is True
    assert repository.get_account("alpha").daily_pnl == pytest.approx(0)


def test_supervisor_pauses_for_daily_loss_and_known_stale_prices() -> None:
    assert supervision_reason(
        daily_pnl=-6, equity=100, peak_equity=100, price_age_ms=None,
        max_daily_loss_pct=0.05, max_drawdown_pct=0.15, max_price_age_ms=15000,
    ) == "daily_loss"
    assert supervision_reason(
        daily_pnl=0, equity=100, peak_equity=100, price_age_ms=None,
        max_daily_loss_pct=0.05, max_drawdown_pct=0.15, max_price_age_ms=15000,
    ) is None
    assert supervision_reason(
        daily_pnl=0, equity=100, peak_equity=100, price_age_ms=20_000,
        max_daily_loss_pct=0.05, max_drawdown_pct=0.15, max_price_age_ms=15000,
    ) == "stale_price"


def test_startup_reconcile_pauses_until_an_unknown_order_is_terminal(tmp_path) -> None:
    repository = Repository(db_path=str(tmp_path / "bot.db"))
    repository.save_risk_event({
        "type": "risk_event",
        "strategy": "alpha",
        "reason_code": "live_order_submission_unknown",
        "reason": "unknown",
        "client_order_id": "cid-1",
        "timestamp": 1,
    })
    repository.upsert_order(OrderRecord(
        order_id="ord-1",
        client_order_id="cid-1",
        strategy="alpha",
        symbol="BTC-USDT",
        side="buy",
        type="market",
        amount=0.1,
        price=1,
        status="pending",
        fill_price=0,
        timestamp=1,
        updated_at=1,
        filled_amount=0,
        remaining_amount=0.1,
    ))

    result = startup_reconcile(repository)

    assert result["paused"] is True
    assert repository.get_opening_pause().paused is True
    repository.upsert_order(OrderRecord(
        order_id="ord-1",
        client_order_id="cid-1",
        strategy="alpha",
        symbol="BTC-USDT",
        side="buy",
        type="market",
        amount=0.1,
        price=1,
        status="cancelled",
        fill_price=0,
        timestamp=1,
        updated_at=2,
        filled_amount=0,
        remaining_amount=0.1,
    ))
    cleared = startup_reconcile(repository)
    assert cleared["paused"] is False


def test_graceful_shutdown_releases_only_this_process_lease(tmp_path) -> None:
    repository = Repository(db_path=str(tmp_path / "bot.db"))
    owner = str(os.getpid())
    assert repository.acquire_strategy_lease("alpha", owner) is True
    assert repository.acquire_strategy_lease("beta", "other") is True

    graceful_shutdown(repository, owner)

    alpha = repository.acquire_strategy_lease(
        "alpha", "replacement", alive=lambda _owner: False,
    )
    beta = repository.acquire_strategy_lease(
        "beta", "replacement", alive=lambda _owner: True,
    )
    assert alpha is True
    assert beta is False


def test_operator_action_is_saved_and_listed(tmp_path) -> None:
    repository = Repository(db_path=str(tmp_path / "bot.db"))
    record_operator_action(repository, action="flatten", result={"closed": 1}, timestamp=5)

    actions = repository.list_operator_actions()

    assert actions[0]["action"] == "flatten"
    assert actions[0]["result"]["closed"] == 1


def test_sqlite_backup_can_be_restored(tmp_path) -> None:
    source = tmp_path / "bot.db"
    repository = Repository(db_path=str(source))
    repository.set_opening_pause(True, "backup", 3)
    destination = tmp_path / "bot-backup.db"

    backup_sqlite(str(source), str(destination))

    restored = Repository(db_path=str(destination))
    assert restored.get_opening_pause().paused is True
    assert restored.get_schema_version() >= 2


def test_schema_version_is_recorded(tmp_path) -> None:
    repository = Repository(db_path=str(tmp_path / "bot.db"))
    assert repository.get_schema_version() >= 2


def test_strategy_realized_pnl_comes_from_stored_trades(tmp_path) -> None:
    repository = Repository(db_path=str(tmp_path / "bot.db"))
    repository.save_trade(TradeRecord(
        strategy="alpha", symbol="BTC-USDT", side="buy",
        amount=1, price=10, fee=0, timestamp=1,
    ))
    repository.save_trade(TradeRecord(
        strategy="alpha", symbol="BTC-USDT", side="sell",
        amount=1, price=12, fee=0, timestamp=2,
    ))

    assert strategy_realized_pnl(repository, "alpha") == pytest.approx(2)
