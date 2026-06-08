from dataclasses import dataclass

from src.core.types import Order
from src.risk.rules import (
    MaxDailyLossRule,
    MaxDrawdownRule,
    MaxPositionRule,
    StopLossRequiredRule,
)


@dataclass
class RiskCheckResult:
    passed: bool
    reason: str = ""


class RiskManager:
    def __init__(
        self,
        max_position_pct: float = 0.8,
        max_daily_loss_pct: float = 0.05,
        max_drawdown_pct: float = 0.15,
        require_stop_loss: bool = False,
        enforce_daily_loss: bool = True,
        enforce_drawdown: bool = True,
    ) -> None:
        self.position_rule = MaxPositionRule(max_position_pct)
        self.daily_loss_rule = MaxDailyLossRule(max_daily_loss_pct)
        self.drawdown_rule = MaxDrawdownRule(max_drawdown_pct)
        self.stop_loss_rule = StopLossRequiredRule()
        self.require_stop_loss = require_stop_loss
        self.enforce_daily_loss = enforce_daily_loss
        self.enforce_drawdown = enforce_drawdown

    def check_order(
        self,
        order: Order,
        current_position_value: float,
        total_equity: float,
        order_value: float,
        daily_pnl: float,
        peak_equity: float,
        current_equity: float,
    ) -> RiskCheckResult:
        if not self.position_rule.check(
            current_position_value=current_position_value,
            total_equity=total_equity,
            order_value=order_value,
        ):
            return RiskCheckResult(False, "Order exceeds maximum position size")

        if self.enforce_daily_loss and not self.daily_loss_rule.check(
            daily_pnl=daily_pnl,
            total_equity=total_equity,
        ):
            return RiskCheckResult(False, "Daily loss exceeds maximum allowed loss")

        if self.enforce_drawdown and not self.drawdown_rule.check(
            peak_equity=peak_equity,
            current_equity=current_equity,
        ):
            return RiskCheckResult(False, "Drawdown exceeds maximum allowed drawdown")

        if self.require_stop_loss and not self.stop_loss_rule.check(order):
            return RiskCheckResult(False, "Order requires a stop loss")

        return RiskCheckResult(True)
