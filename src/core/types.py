import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


def execution_type_family(
    order_type: object,
    *,
    provider_type: object | None = None,
) -> str:
    """Return the semantic execution family independent of provider aliases."""
    raw_value = provider_type if provider_type is not None else order_type
    value = str(getattr(raw_value, "value", raw_value) or "").strip().lower()
    if value in {
        "stop",
        "stop-market",
        "stop-limit",
        "stop_market",
        "stop_limit",
        "trigger",
        "trigger-market",
        "trigger-limit",
        "trigger_market",
        "trigger_limit",
    }:
        return "stop"
    if value == OrderType.MARKET.value:
        return "market"
    if value == OrderType.LIMIT.value:
        return "limit"
    return value


class OrderStatus(StrEnum):
    PENDING = "pending"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class PositionSide(StrEnum):
    LONG = "long"
    SHORT = "short"


def strict_is_inverse(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
    elif isinstance(value, (int, float)):
        try:
            normalized = float(value)
        except (TypeError, ValueError, OverflowError):
            normalized = math.nan
        if math.isfinite(normalized) and normalized in {0.0, 1.0}:
            return normalized == 1.0
    raise ValueError(f"invalid is_inverse: {value!r}")


def _finite_nonnegative(value: float) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return normalized if math.isfinite(normalized) and normalized >= 0.0 else 0.0


def _subtract_amounts(amount: float, value: float) -> float:
    try:
        candidate = float(Decimal(str(amount)) - Decimal(str(value)))
    except (ValueError, OverflowError):
        return 0.0
    if candidate + value == amount:
        return candidate

    candidates = [candidate]
    lower = candidate
    upper = candidate
    for _ in range(4):
        lower = math.nextafter(lower, 0.0)
        upper = math.nextafter(upper, math.inf)
        candidates.extend((lower, upper))
        for adjusted in (lower, upper):
            if adjusted >= 0.0 and adjusted + value == amount:
                return adjusted

    return min(
        (adjusted for adjusted in candidates if adjusted >= 0.0),
        key=lambda adjusted: abs((adjusted + value) - amount),
    )


_AMOUNT_RECONCILIATION_ABS_TOLERANCE = 1e-12


def _validate_amount_reconciliation(
    amount: float,
    filled_amount: float,
    remaining_amount: float,
    *,
    error_message: str = "inconsistent filled and remaining amounts",
) -> None:
    if not math.isclose(
        filled_amount + remaining_amount,
        amount,
        rel_tol=0.0,
        abs_tol=_AMOUNT_RECONCILIATION_ABS_TOLERANCE,
    ):
        raise ValueError(error_message)


def _safe_timestamp(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        normalized = value
    elif isinstance(value, str):
        try:
            normalized = int(value)
        except ValueError:
            return 0
    else:
        try:
            normalized_decimal = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return 0
        if not normalized_decimal.is_finite() or normalized_decimal != (
            normalized_decimal.to_integral_value()
        ):
            return 0
        normalized = int(normalized_decimal)
    return normalized if 0 <= normalized <= 2**63 - 1 else 0


def stable_blank_trade_fingerprint(
    *,
    exchange_order_id: object,
    client_order_id: object,
    symbol: object,
    side: object,
    amount: object,
    price: object,
    fee: object,
    timestamp: object,
) -> str | None:
    exchange_id = str(exchange_order_id or "").strip()
    client_id = str(client_order_id or "").strip()
    payload = (
        str(symbol or ""),
        str(getattr(side, "value", side) or ""),
        amount,
        price,
        fee,
        timestamp,
    )
    if exchange_id or client_id:
        payload = (
            "exchange_order_id" if exchange_id else "client_order_id",
            exchange_id or client_id,
            *payload,
        )
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def _finite_nonnegative_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return normalized if math.isfinite(normalized) and normalized >= 0.0 else None


def _finite_or_zero(value: object) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return normalized if math.isfinite(normalized) else 0.0


def _safe_leverage(value: object) -> int:
    if isinstance(value, bool):
        return 1
    try:
        normalized = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        return 1
    if (
        not normalized.is_finite()
        or normalized != normalized.to_integral_value()
        or normalized < 1
        or normalized > 2**63 - 1
    ):
        return 1
    return int(normalized)


@dataclass
class Bar:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        raw_timestamp = self.timestamp
        timestamp = _safe_timestamp(raw_timestamp)
        if timestamp == 0 and raw_timestamp not in (0, "0", 0.0):
            raise ValueError("invalid OHLCV bar")
        raw_values = (self.open, self.high, self.low, self.close, self.volume)
        if any(isinstance(value, bool) for value in raw_values):
            raise ValueError("invalid OHLCV bar")
        try:
            values = [float(value) for value in raw_values]
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("invalid OHLCV bar") from exc
        if not all(math.isfinite(value) and value >= 0.0 for value in values):
            raise ValueError("invalid OHLCV bar")
        open_price, high, low, close, volume = values
        if high < low or not low <= open_price <= high or not low <= close <= high:
            raise ValueError("invalid OHLCV bar")
        self.timestamp = timestamp
        self.open, self.high, self.low, self.close, self.volume = values


@dataclass
class Order:
    id: str
    symbol: str
    side: OrderSide
    type: OrderType
    amount: float
    price: float | None = None
    trigger_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    status: OrderStatus = OrderStatus.PENDING
    fill_price: float | None = None
    fill_time: int | None = None
    exchange_order_id: str = ""
    client_order_id: str = ""
    updated_at: int = 0
    filled_amount: float | None = None
    remaining_amount: float | None = None
    reduce_only: bool = False
    params: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.amount = _finite_nonnegative(self.amount)
        self.price = _finite_nonnegative_or_none(self.price)
        self.trigger_price = _finite_nonnegative_or_none(self.trigger_price)
        self.stop_loss = _finite_nonnegative_or_none(self.stop_loss)
        self.take_profit = _finite_nonnegative_or_none(self.take_profit)
        self.fill_price = _finite_nonnegative_or_none(self.fill_price)
        self.fill_time = (
            None if self.fill_time is None else _safe_timestamp(self.fill_time)
        )
        self.updated_at = _safe_timestamp(self.updated_at)
        raw_filled_amount = _finite_nonnegative_or_none(self.filled_amount)
        filled_amount = min(raw_filled_amount or 0.0, self.amount)
        remaining = _finite_nonnegative_or_none(self.remaining_amount)
        filled_is_authoritative = raw_filled_amount is not None and raw_filled_amount <= self.amount
        remaining_is_authoritative = remaining is not None and remaining <= self.amount
        if filled_is_authoritative and remaining_is_authoritative:
            _validate_amount_reconciliation(self.amount, filled_amount, remaining)
        if remaining_is_authoritative:
            filled_amount = _subtract_amounts(self.amount, remaining)
        elif self.status is OrderStatus.FILLED and self.amount > 0.0:
            # A filled order without quantity data represents a complete fill.
            filled_amount = self.amount
        self.filled_amount = filled_amount
        self.remaining_amount = (
            remaining
            if remaining_is_authoritative
            else _subtract_amounts(self.amount, filled_amount)
        )
        if self.status not in {OrderStatus.CANCELLED, OrderStatus.REJECTED}:
            self.status = (
                OrderStatus.PENDING
                if self.filled_amount == 0.0
                else OrderStatus.FILLED
                if self.filled_amount >= self.amount
                else OrderStatus.PARTIALLY_FILLED
            )


@dataclass
class Position:
    symbol: str
    side: PositionSide
    amount: float
    entry_price: float
    unrealized_pnl: float
    leverage: int = 1
    realized_pnl: float = 0.0
    mark_price: float | None = None
    timestamp: int = 0
    updated_at: int = 0
    contracts: float | None = None
    quantity: float | None = None
    quantity_unit: str = "base"
    notional_value: float | None = None
    is_inverse: bool = False
    contract_size: float | None = None

    def __post_init__(self) -> None:
        self.amount = abs(_finite_or_zero(self.amount))
        self.entry_price = _finite_nonnegative(self.entry_price)
        self.mark_price = _finite_nonnegative_or_none(self.mark_price)
        self.realized_pnl = _finite_or_zero(self.realized_pnl)
        self.unrealized_pnl = _finite_or_zero(self.unrealized_pnl)
        self.is_inverse = strict_is_inverse(self.is_inverse)
        self.leverage = _safe_leverage(self.leverage)
        self.timestamp = _safe_timestamp(self.timestamp)
        self.updated_at = _safe_timestamp(self.updated_at)
        if self.updated_at == 0 and self.timestamp != 0:
            self.updated_at = self.timestamp
        elif self.timestamp == 0 and self.updated_at != 0:
            self.timestamp = self.updated_at


class _ImmutableAsset(dict):
    def _immutable(self, *args, **kwargs):
        raise TypeError("account assets are immutable")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _immutable


class _ImmutableAssets(list):
    def _immutable(self, *args, **kwargs):
        raise TypeError("account assets are immutable")

    __setitem__ = __delitem__ = __iadd__ = __imul__ = _immutable
    append = clear = extend = insert = pop = remove = reverse = sort = _immutable


class _ImmutableAssetSet(tuple):
    def _immutable(self, *args, **kwargs):
        raise TypeError("account assets are immutable")

    add = clear = discard = pop = remove = update = _immutable


def _immutable_asset_copy(value: object) -> object:
    if isinstance(value, Mapping):
        return _ImmutableAsset({
            key: _immutable_asset_copy(item) for key, item in value.items()
        })
    if isinstance(value, list):
        return _ImmutableAssets(_immutable_asset_copy(item) for item in value)
    if isinstance(value, (set, frozenset)):
        # A tuple subclass retains JSON encodability while exposing blocked set APIs.
        return _ImmutableAssetSet(_immutable_asset_copy(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_immutable_asset_copy(item) for item in value)
    return value


def immutable_assets(assets: list[object]) -> list[object]:
    return _ImmutableAssets(_immutable_asset_copy(asset) for asset in assets)


@dataclass(frozen=True)
class AssetBalance:
    ccy: str
    cash_bal: float = 0.0
    eq: float = 0.0
    eq_utd: float = 0.0
    avail_bal: float = 0.0
    upl: float = 0.0

    def __post_init__(self) -> None:
        for field_name in ("cash_bal", "eq", "eq_utd", "avail_bal"):
            object.__setattr__(self, field_name, _finite_nonnegative(getattr(self, field_name)))
        object.__setattr__(self, "upl", _finite_or_zero(self.upl))


@dataclass(frozen=True)
class AccountSnapshot:
    equity: float = 0.0
    cash_balance: float = 0.0
    initial_equity: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    daily_pnl: float = 0.0
    fees_paid: float = 0.0
    timestamp: int = 0
    currency: str = ""
    available_balance: float | None = None
    updated_at: int = 0
    assets: list[AssetBalance] = field(default_factory=list)

    def __post_init__(self) -> None:
        equity = _finite_nonnegative(self.equity)
        initial_equity = _finite_nonnegative(self.initial_equity)
        cash_balance = _finite_nonnegative(self.cash_balance)
        available_balance = (
            cash_balance
            if self.available_balance is None
            else _finite_nonnegative(self.available_balance)
        )
        object.__setattr__(self, "equity", equity)
        object.__setattr__(
            self,
            "initial_equity",
            equity if initial_equity == 0.0 and equity != 0.0 else initial_equity,
        )
        object.__setattr__(self, "cash_balance", cash_balance)
        object.__setattr__(self, "available_balance", available_balance)
        for field_name in ("realized_pnl", "unrealized_pnl", "daily_pnl"):
            object.__setattr__(self, field_name, _finite_or_zero(getattr(self, field_name)))
        object.__setattr__(self, "fees_paid", _finite_nonnegative(self.fees_paid))
        assets: list[AssetBalance] = []
        for asset in self.assets:
            if isinstance(asset, AssetBalance):
                if isinstance(asset.ccy, str) and asset.ccy.strip():
                    assets.append(asset)
                continue
            if not isinstance(asset, dict):
                continue
            ccy = asset.get("ccy")
            if not isinstance(ccy, str) or not ccy.strip():
                continue
            try:
                assets.append(AssetBalance(**asset))
            except (TypeError, ValueError):
                continue
        object.__setattr__(self, "assets", immutable_assets(assets))
        updated_at = _safe_timestamp(self.updated_at)
        timestamp = _safe_timestamp(self.timestamp)
        if updated_at != 0:
            timestamp = updated_at
        elif timestamp != 0:
            updated_at = timestamp
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "updated_at", updated_at)


def has_trusted_account_snapshot_provenance(snapshot: AccountSnapshot) -> bool:
    """Return whether an account snapshot carries a provider timestamp."""
    return max(snapshot.timestamp, snapshot.updated_at) > 0


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    side: PositionSide
    amount: float
    entry_price: float
    mark_price: float | None = None
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    leverage: int = 1
    timestamp: int = 0
    updated_at: int = 0
    # ``amount`` remains the legacy public quantity. These fields make the
    # exchange quantity and risk notional explicit without changing that API.
    contracts: float | None = None
    quantity: float | None = None
    quantity_unit: str = "base"
    notional_value: float | None = None
    is_inverse: bool = False
    contract_size: float | None = None

    def __post_init__(self) -> None:
        amount = _finite_or_zero(self.amount)
        entry_price = _finite_nonnegative(self.entry_price)
        mark_price = _finite_nonnegative_or_none(self.mark_price)
        realized_pnl = _finite_or_zero(self.realized_pnl)
        unrealized_pnl = _finite_or_zero(self.unrealized_pnl)
        timestamp = _safe_timestamp(self.timestamp)
        updated_at = _safe_timestamp(self.updated_at)
        if updated_at == 0 and timestamp != 0:
            updated_at = timestamp
        if timestamp == 0 and updated_at != 0:
            timestamp = updated_at
        object.__setattr__(self, "amount", abs(amount))
        object.__setattr__(self, "entry_price", entry_price)
        object.__setattr__(self, "mark_price", mark_price)
        object.__setattr__(self, "realized_pnl", realized_pnl)
        object.__setattr__(self, "unrealized_pnl", unrealized_pnl)
        quantity_unit = str(self.quantity_unit or "base").strip().lower()
        if quantity_unit not in {"base", "contracts"}:
            quantity_unit = "base"
        quantity = _finite_nonnegative_or_none(self.quantity)
        contracts = _finite_nonnegative_or_none(self.contracts)
        contract_size = _finite_nonnegative_or_none(self.contract_size)
        notional_value = _finite_nonnegative_or_none(self.notional_value)
        object.__setattr__(self, "leverage", _safe_leverage(self.leverage))
        object.__setattr__(self, "quantity_unit", quantity_unit)
        object.__setattr__(self, "quantity", quantity if quantity is not None else self.amount)
        object.__setattr__(self, "contracts", contracts)
        object.__setattr__(self, "contract_size", contract_size)
        object.__setattr__(self, "notional_value", notional_value)
        object.__setattr__(self, "is_inverse", strict_is_inverse(self.is_inverse))
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "updated_at", updated_at)


@dataclass(frozen=True)
class ExchangeOrderSnapshot:
    exchange_order_id: str
    client_order_id: str
    symbol: str
    side: OrderSide
    type: OrderType
    amount: float
    price: float
    status: OrderStatus
    fill_price: float | None
    timestamp: int
    updated_at: int
    filled_amount: float | None = None
    remaining_amount: float | None = None
    execution_type_family: str = ""
    _filled_amount_present: bool = field(init=False, repr=False, compare=False)
    _remaining_amount_present: bool = field(init=False, repr=False, compare=False)
    _fill_price_present: bool = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        filled_amount_present = self.filled_amount is not None
        remaining_amount_present = self.remaining_amount is not None
        fill_price_present = self.fill_price is not None
        object.__setattr__(self, "_filled_amount_present", filled_amount_present)
        object.__setattr__(self, "_remaining_amount_present", remaining_amount_present)
        object.__setattr__(self, "_fill_price_present", fill_price_present)
        amount = _finite_nonnegative(self.amount)
        raw_filled_amount = _finite_nonnegative_or_none(self.filled_amount)
        filled_amount = min(raw_filled_amount or 0.0, amount)
        remaining_amount = _finite_nonnegative_or_none(self.remaining_amount)
        filled_is_valid = raw_filled_amount is not None and raw_filled_amount <= amount
        remaining_is_valid = (
            remaining_amount is not None and remaining_amount <= amount
        )
        if filled_is_valid and remaining_is_valid:
            _validate_amount_reconciliation(amount, filled_amount, remaining_amount)
        if remaining_is_valid:
            filled_amount = _subtract_amounts(amount, remaining_amount)
        else:
            remaining_amount = _subtract_amounts(amount, filled_amount)

        price = _finite_nonnegative(self.price)
        fill_price = _finite_nonnegative(self.fill_price)
        status = self.status
        if status not in {
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
        } and not (
            status is OrderStatus.FILLED and filled_amount == 0.0
            or status is OrderStatus.PARTIALLY_FILLED
            and not filled_amount_present and not remaining_amount_present
        ):
            status = (
                OrderStatus.PENDING
                if filled_amount == 0.0
                else OrderStatus.FILLED
                if filled_amount >= amount
                else OrderStatus.PARTIALLY_FILLED
            )
        object.__setattr__(self, "amount", amount)
        object.__setattr__(self, "price", price)
        object.__setattr__(
            self,
            "execution_type_family",
            execution_type_family(self.type, provider_type=self.execution_type_family or None),
        )
        object.__setattr__(self, "fill_price", fill_price)
        object.__setattr__(self, "timestamp", _safe_timestamp(self.timestamp))
        object.__setattr__(self, "updated_at", _safe_timestamp(self.updated_at))
        object.__setattr__(self, "filled_amount", filled_amount)
        object.__setattr__(self, "remaining_amount", remaining_amount)
        object.__setattr__(self, "status", status)


@dataclass(frozen=True)
class ExchangeTradeSnapshot:
    exchange_trade_id: str
    exchange_order_id: str
    client_order_id: str
    symbol: str
    side: OrderSide
    amount: float
    price: float
    fee: float
    timestamp: int
    filled_amount: float | None = None
    contracts: float | None = None
    quantity: float | None = None
    quantity_unit: str = "base"
    is_inverse: bool = False
    contract_size: float | None = None

    def __post_init__(self) -> None:
        amount = _finite_nonnegative(self.amount)
        price = _finite_nonnegative(self.price)
        fee = _finite_nonnegative(self.fee)
        filled_amount = (
            amount
            if self.filled_amount is None
            else _finite_nonnegative(self.filled_amount)
        )
        object.__setattr__(self, "amount", amount)
        object.__setattr__(self, "price", price)
        quantity_unit = str(self.quantity_unit or "base").strip().lower()
        if quantity_unit not in {"base", "contracts"}:
            quantity_unit = "base"
        object.__setattr__(self, "quantity_unit", quantity_unit)
        object.__setattr__(self, "contracts", _finite_nonnegative_or_none(self.contracts))
        object.__setattr__(self, "quantity", _finite_nonnegative_or_none(self.quantity))
        object.__setattr__(self, "contract_size", _finite_nonnegative_or_none(self.contract_size))
        object.__setattr__(self, "is_inverse", strict_is_inverse(self.is_inverse))
        object.__setattr__(self, "fee", fee)
        object.__setattr__(self, "timestamp", _safe_timestamp(self.timestamp))
        object.__setattr__(self, "filled_amount", filled_amount)
