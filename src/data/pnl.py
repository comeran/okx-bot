"""用已保存的现货成交计算策略已实现盈亏。"""

from __future__ import annotations


def strategy_realized_pnl(repository, strategy: str) -> float:
    getter = getattr(repository, "get_trades", None)
    if not callable(getter):
        return 0.0
    try:
        trades = list(getter(strategy))
    except Exception:
        return 0.0
    inventory = 0.0
    average = 0.0
    realized = 0.0
    for trade in trades:
        side = str(getattr(trade, "side", "")).lower()
        amount = float(getattr(trade, "amount", 0) or 0)
        price = float(getattr(trade, "price", 0) or 0)
        fee = float(getattr(trade, "fee", 0) or 0)
        if amount <= 0:
            continue
        if side == "buy":
            total = inventory + amount
            average = ((inventory * average) + (amount * price)) / total
            inventory = total
            realized -= fee
        elif side == "sell":
            sold = min(amount, inventory)
            realized += (price - average) * sold - fee
            inventory = max(0.0, inventory - sold)
            if inventory == 0:
                average = 0.0
    return realized
