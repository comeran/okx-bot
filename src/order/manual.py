"""手工委托：提交、撤销和改价都走现有订单通道。"""

from __future__ import annotations

_TERMINAL = {"filled", "cancelled", "canceled", "rejected"}


def _status(order: object) -> str:
    status = getattr(order, "status", "open")
    return str(getattr(status, "value", status))


async def submit_manual_order(
    manager,
    *,
    symbol: str,
    side: str,
    order_type: str,
    amount: float,
    price: float | None,
) -> dict[str, object]:
    """提交一笔手工单。数量和限价不合法时直接拒绝。"""
    from src.core.types import OrderSide, OrderType

    if not str(symbol).strip():
        raise ValueError("symbol")
    if amount is None or float(amount) <= 0:
        raise ValueError("amount")
    if order_type == "limit" and (price is None or float(price) <= 0):
        raise ValueError("price")
    order = await manager.submit(
        symbol=symbol,
        side=OrderSide(side),
        order_type=OrderType(order_type),
        amount=float(amount),
        price=None if price is None else float(price),
        strategy_name="manual",
    )
    status = _status(order)
    if status == "rejected":
        raise RuntimeError("rejected")
    return {
        "id": str(getattr(order, "id", "")),
        "status": status,
        "symbol": str(getattr(order, "symbol", symbol)),
        "amount": float(getattr(order, "amount", amount)),
        "price": getattr(order, "price", price),
    }


async def cancel_one_order(manager, order_id: str, symbol: str) -> dict[str, object]:
    """只撤销指定的一笔委托。"""
    cancelled = await manager.cancel(order_id, symbol)
    if not cancelled:
        raise RuntimeError("cancel_failed")
    return {"id": order_id, "symbol": symbol, "cancelled": True}


async def amend_resting_order(repository, router, order_id: str, symbol: str, price: float):
    """改未完成委托的价格。已结束的委托保持原样。"""
    if price is None or float(price) <= 0:
        raise ValueError("price")
    matches = [
        order for order in repository.get_orders()
        if order_id in {
            str(getattr(order, "order_id", "") or ""),
            str(getattr(order, "client_order_id", "") or ""),
            str(getattr(order, "exchange_order_id", "") or ""),
        }
    ]
    if len(matches) != 1 or _status(matches[0]) in _TERMINAL:
        raise ValueError("order_not_open")
    await router.amend(order_id, symbol, float(price))
    order = matches[0]
    order.price = float(price)
    save = getattr(repository, "save_order", None) or repository.upsert_order
    save(order)
    return {"id": order_id, "symbol": symbol, "price": float(price), "status": _status(order)}
