"""撤销未完成委托，并只发送能把持仓收平的市价单。"""

from __future__ import annotations

import logging

from src.core.types import OrderSide, OrderType
from src.order.manager import position_quantity

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {"filled", "cancelled", "canceled", "rejected"}


def _status_value(record: object) -> str:
    status = getattr(record, "status", "")
    return str(getattr(status, "value", status) or "").strip().lower()


def _open_orders(manager) -> list[object]:
    repository = getattr(manager, "repository", None)
    getter = getattr(repository, "get_orders", None)
    if not callable(getter):
        return []
    orders = []
    for order in getter():
        if getattr(order, "active", True) is False:
            continue
        if _status_value(order) in _TERMINAL_STATUSES:
            continue
        orders.append(order)
    return orders


def _open_positions(manager) -> list[object]:
    repository = getattr(manager, "repository", None)
    getter = getattr(repository, "get_open_positions", None)
    if not callable(getter):
        return []
    try:
        return [position for position in getter() if getattr(position, "active", True) is not False]
    except TypeError:
        return [
            position for position in getter(None)
            if getattr(position, "active", True) is not False
        ]


async def cancel_open_orders(manager) -> dict[str, list]:
    cancelled: list[str] = []
    failed: list[dict[str, object]] = []
    for order in _open_orders(manager):
        order_id = str(
            getattr(order, "exchange_order_id", "") or getattr(order, "order_id", "")
        )
        symbol = getattr(order, "symbol", None)
        try:
            succeeded = bool(await manager.cancel(order_id, symbol))
        except Exception:
            logger.exception("撤销委托失败")
            failed.append({"order_id": order_id, "symbol": symbol, "reason": "cancel_failed"})
            continue
        if succeeded:
            cancelled.append(order_id)
        else:
            failed.append({"order_id": order_id, "symbol": symbol, "reason": "cancel_rejected"})
    return {"cancelled": cancelled, "failed": failed}


async def flatten_exposure(manager) -> dict[str, list]:
    result = await cancel_open_orders(manager)
    closed: list[dict[str, object]] = []
    close_failed: list[dict[str, object]] = []
    checker = getattr(manager, "_unresolved_live_submission", None)
    unresolved = False
    if callable(checker):
        try:
            unresolved = bool(checker())
        except Exception:
            logger.exception("读取未决实盘订单失败")
            unresolved = True
    if unresolved:
        close_failed.append({"reason": "unresolved_live_order"})
        return {**result, "closed": closed, "close_failed": close_failed}

    for position in _open_positions(manager):
        try:
            amount = float(position_quantity(position))
        except (TypeError, ValueError):
            amount = 0.0
        symbol = str(getattr(position, "symbol", "") or "")
        strategy = str(getattr(position, "strategy", "") or "")
        if amount <= 0 or not symbol:
            continue
        side_name = str(getattr(position, "side", "") or "").strip().lower()
        if side_name == "long":
            side = OrderSide.SELL
        elif side_name == "short":
            side = OrderSide.BUY
        else:
            close_failed.append({
                "strategy": strategy,
                "symbol": symbol,
                "reason": "unknown_position_side",
            })
            continue
        order = await manager.submit(
            symbol,
            side,
            OrderType.MARKET,
            amount,
            strategy_name=strategy,
            protective_reduce=True,
        )
        status = _status_value(order)
        payload = {
            "strategy": strategy,
            "symbol": symbol,
            "side": side.value,
            "amount": amount,
            "status": status,
        }
        if status == "filled":
            closed.append(payload)
        else:
            close_failed.append(payload)
    return {**result, "closed": closed, "close_failed": close_failed}
