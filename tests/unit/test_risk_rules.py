from src.core.types import Order, OrderSide, OrderType
from src.risk.manager import RiskManager
from src.risk.rules import (
    MaxDailyLossRule,
    MaxDrawdownRule,
    MaxPositionRule,
    StopLossRequiredRule,
)


def make_order(stop_loss: float | None = 90.0) -> Order:
    return Order(
        id="order-1",
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        amount=1.0,
        price=100.0,
        stop_loss=stop_loss,
    )


def test_max_position_rule_passes_when_exposure_at_limit() -> None:
    rule = MaxPositionRule(max_position_pct=0.1)

    assert rule.check(
        current_position_value=5_000,
        total_equity=100_000,
        order_value=5_000,
    )


def test_max_position_rule_fails_when_exposure_exceeds_limit() -> None:
    rule = MaxPositionRule(max_position_pct=0.1)

    assert not rule.check(
        current_position_value=8_000,
        total_equity=100_000,
        order_value=5_000,
    )


def test_max_daily_loss_rule_passes_when_loss_within_limit() -> None:
    rule = MaxDailyLossRule(max_loss_pct=0.05)

    assert rule.check(daily_pnl=-2_000, total_equity=100_000)


def test_max_daily_loss_rule_fails_when_loss_exceeds_limit() -> None:
    rule = MaxDailyLossRule(max_loss_pct=0.05)

    assert not rule.check(daily_pnl=-6_000, total_equity=100_000)


def test_max_drawdown_rule_passes_when_drawdown_within_limit() -> None:
    rule = MaxDrawdownRule(max_drawdown_pct=0.15)

    assert rule.check(peak_equity=100_000, current_equity=90_000)


def test_max_drawdown_rule_fails_when_drawdown_exceeds_limit() -> None:
    rule = MaxDrawdownRule(max_drawdown_pct=0.15)

    assert not rule.check(peak_equity=100_000, current_equity=80_000)


def test_stop_loss_required_rule_passes_when_order_has_stop_loss() -> None:
    rule = StopLossRequiredRule()

    assert rule.check(make_order(stop_loss=90.0))


def test_stop_loss_required_rule_fails_when_order_missing_stop_loss() -> None:
    rule = StopLossRequiredRule()

    assert not rule.check(make_order(stop_loss=None))


def test_risk_manager_passes_when_all_rules_pass_and_stop_loss_required() -> None:
    manager = RiskManager(require_stop_loss=True)

    result = manager.check_order(
        order=make_order(stop_loss=90.0),
        current_position_value=5_000,
        total_equity=100_000,
        order_value=5_000,
        daily_pnl=-2_000,
        peak_equity=100_000,
        current_equity=90_000,
    )

    assert result.passed
    assert result.reason == ""


def test_risk_manager_rejects_missing_stop_loss() -> None:
    manager = RiskManager(require_stop_loss=True)

    result = manager.check_order(
        order=make_order(stop_loss=None),
        current_position_value=5_000,
        total_equity=100_000,
        order_value=5_000,
        daily_pnl=-2_000,
        peak_equity=100_000,
        current_equity=90_000,
    )

    assert not result.passed
    assert "stop" in result.reason.lower()


def test_risk_manager_can_run_max_position_only() -> None:
    manager = RiskManager(
        max_position_pct=0.8,
        enforce_daily_loss=False,
        enforce_drawdown=False,
    )

    result = manager.check_order(
        order=make_order(stop_loss=None),
        current_position_value=5_000,
        total_equity=100_000,
        order_value=5_000,
        daily_pnl=-99_000,
        peak_equity=100_000,
        current_equity=1_000,
    )

    assert result.passed
