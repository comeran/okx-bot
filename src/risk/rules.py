from src.core.types import Order


class MaxPositionRule:
    def __init__(self, max_position_pct: float) -> None:
        self.max_position_pct = max_position_pct

    def check(
        self,
        current_position_value: float,
        total_equity: float,
        order_value: float,
    ) -> bool:
        if total_equity <= 0:
            return False

        return current_position_value + order_value <= total_equity * self.max_position_pct


class MaxDailyLossRule:
    def __init__(self, max_loss_pct: float) -> None:
        self.max_loss_pct = max_loss_pct

    def check(self, daily_pnl: float, total_equity: float) -> bool:
        if total_equity <= 0:
            return False

        max_loss = total_equity * self.max_loss_pct
        return daily_pnl >= -max_loss


class MaxDrawdownRule:
    def __init__(self, max_drawdown_pct: float) -> None:
        self.max_drawdown_pct = max_drawdown_pct

    def check(self, peak_equity: float, current_equity: float) -> bool:
        if peak_equity <= 0:
            return False

        drawdown = (peak_equity - current_equity) / peak_equity
        return drawdown <= self.max_drawdown_pct


class StopLossRequiredRule:
    def check(self, order: Order) -> bool:
        return order.stop_loss is not None
