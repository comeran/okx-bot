"""研究撮合和交易所回报共用的订单状态机。

研究路径只提供撮合结果和 K 线时钟。交易所路径使用同一张转移表，
多出来的重开只能带着 provider_status_conflict 原因留下。
"""

from __future__ import annotations

from src.core.types import OrderStatus

_RECONCILE = "provider_status_conflict"
_RECORD_UNCHANGED = {"venue_unknown"}
_ALLOWED = {
    OrderStatus.PENDING: {
        OrderStatus.PENDING,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
    },
    OrderStatus.PARTIALLY_FILLED: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
    },
    OrderStatus.FILLED: {OrderStatus.FILLED},
    OrderStatus.CANCELLED: {OrderStatus.CANCELLED},
    OrderStatus.REJECTED: {OrderStatus.REJECTED},
}


def _status(value: OrderStatus | str) -> OrderStatus:
    if isinstance(value, OrderStatus):
        return value
    return OrderStatus(str(value))


def move_status(
    current: OrderStatus | str, target: OrderStatus | str, *, cause: str,
) -> OrderStatus:
    current_status = _status(current)
    target_status = _status(target)
    if current_status is OrderStatus.REJECTED and cause == _RECONCILE:
        if target_status in {
            OrderStatus.PENDING,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
        }:
            return target_status
        raise ValueError(
            f"illegal order transition {current_status.value} -> {target_status.value}"
        )
    if target_status not in _ALLOWED[current_status]:
        raise ValueError(
            f"illegal order transition {current_status.value} -> {target_status.value}"
        )
    return target_status


def _trail(order: object) -> list[dict[str, object]]:
    params = getattr(order, "params", None)
    if isinstance(params, dict):
        trail = params.get("lifecycle")
        if not isinstance(trail, list):
            trail = []
            params["lifecycle"] = trail
        return trail
    trail = getattr(order, "_lifecycle", None)
    if not isinstance(trail, list):
        trail = []
        object.__setattr__(order, "_lifecycle", trail)
    return trail


def record_transition(
    order: object,
    target: OrderStatus | str,
    *,
    cause: str,
    clock_ms: int,
    source: str,
) -> OrderStatus:
    current = order.status
    new = move_status(current, target, cause=cause)
    changed = _status(current) is not new
    # StrEnum 也是 str。只有本来就是普通字符串的记录才继续存字符串。
    order.status = new.value if type(current) is str else new
    if changed or cause in _RECORD_UNCHANGED:
        _trail(order).append({
            "from": _status(current).value,
            "to": new.value,
            "cause": cause,
            "clock_ms": int(clock_ms),
            "source": source,
        })
    return new


def explain(order: object) -> list[str]:
    params = getattr(order, "params", None)
    if isinstance(params, dict):
        trail = params.get("lifecycle")
    else:
        trail = getattr(order, "_lifecycle", None)
    if not isinstance(trail, list):
        return []
    return [
        f"{item['from']}->{item['to']} {item['cause']} {item['source']} {item['clock_ms']}"
        for item in trail
    ]
