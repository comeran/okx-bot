from __future__ import annotations

import math
from typing import Any

from src.core.types import strict_is_inverse


def _finite(value: object, default: float = 0.0) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return normalized if math.isfinite(normalized) else default


def _metadata(value: Any, name: str) -> Any:
    direct = getattr(value, name, None)
    if direct is not None:
        return direct
    params = getattr(value, "params", None)
    return params.get(name) if isinstance(params, dict) else None


def _parse_bool_metadata(raw: Any) -> bool:
    try:
        return strict_is_inverse(raw)
    except ValueError:
        return False


def is_inverse(value: Any) -> bool:
    return _parse_bool_metadata(_metadata(value, "is_inverse"))


def _normalize_contract_size(raw: Any) -> float:
    if isinstance(raw, bool):
        raise ValueError(f"invalid contract_size metadata: {raw!r}")
    try:
        normalized = float(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"invalid contract_size metadata: {raw!r}") from exc
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise ValueError(f"invalid contract_size metadata: {raw!r}")
    return normalized


def contract_size(value: Any, default: float | None = 1.0) -> float:
    raw = _metadata(value, "contract_size")
    if raw is None:
        if default is None:
            raise ValueError("missing contract_size metadata")
        return default
    return _normalize_contract_size(raw)


def position_quantity(position: Any) -> float:
    if not is_inverse(position) and str(
        _metadata(position, "quantity_unit") or "base"
    ).strip().lower() != "contracts":
        amount = getattr(position, "amount", None)
        if amount is not None:
            return abs(_finite(amount))

    contracts = getattr(position, "contracts", None)
    if contracts is not None:
        return abs(_finite(contracts))
    quantity = getattr(position, "quantity", None)
    if quantity is not None:
        return abs(_finite(quantity))
    return abs(_finite(getattr(position, "amount", 0.0)))


def position_order_quantity(position: Any) -> float:
    contracts = getattr(position, "contracts", None)
    if contracts is not None:
        return abs(_finite(contracts))
    return position_quantity(position)


def position_notional(position: Any) -> float:
    if is_inverse(position):
        contracts = _finite(getattr(position, "contracts", None), -1.0)
        if contracts <= 0.0:
            raise ValueError("inverse position requires valid contracts")
        return contracts * contract_size(position, default=None)
    price = _finite(
        getattr(position, "mark_price", None)
        or getattr(position, "entry_price", 0.0)
    )
    return abs(_finite(getattr(position, "amount", 0.0))) * price


def order_notional(
    order: Any,
    price: float,
    *,
    amount: float | None = None,
    is_inverse_instrument: bool | None = None,
    contract_size_value: float | None = None,
) -> float:
    inverse = (
        is_inverse(order)
        if is_inverse_instrument is None
        else _parse_bool_metadata(is_inverse_instrument)
    )
    amount = abs(_finite(getattr(order, "amount", 0.0) if amount is None else amount))
    if not inverse:
        raw_size = _metadata(order, "contract_size")
        market_type = str(_metadata(order, "market_type") or "").strip().lower()
        if raw_size is not None and market_type not in {"spot"}:
            return amount * contract_size(order, default=None) * _finite(price)
        return amount * _finite(price)
    size = (
        contract_size(order, default=None)
        if contract_size_value is None
        else contract_size_value
    )
    if contract_size_value is not None:
        size = _normalize_contract_size(contract_size_value)
    return amount * size


def inverse_quote_pnl(
    position: Any,
    mark_price: float,
    *,
    entry_price: float | None = None,
) -> float:
    """Mark inverse contracts in quote currency using contract notional."""
    entry = _finite(
        entry_price if entry_price is not None else getattr(position, "entry_price", 0.0)
    )
    mark = _finite(mark_price)
    if entry <= 0.0 or mark <= 0.0:
        return 0.0
    change = position_notional(position) * (mark - entry) / entry
    return -change if str(getattr(position, "side", "long")).lower() == "short" else change


def signed_position_cost_basis(position: Any) -> float:
    if is_inverse(position):
        value = position_notional(position)
    else:
        value = abs(_finite(getattr(position, "amount", 0.0))) * _finite(
            getattr(position, "entry_price", 0.0)
        )
    return -value if str(getattr(position, "side", "long")).lower() == "short" else value
