from __future__ import annotations

import logging
import math
from typing import Any

from src.data.models import AccountRecord, CashLedgerRecord, PositionRecord, TradeRecord
from src.order.value import (
    contract_size,
    inverse_quote_pnl,
    is_inverse,
    order_notional,
    position_order_quantity,
    signed_position_cost_basis,
)

logger = logging.getLogger(__name__)


class PaperAccountingService:
    def __init__(
        self,
        repository: Any,
        initial_equity: float = 100000.0,
        fee_rate: float = 0.0,
        runtime_mode: str = "paper",
    ) -> None:
        self.repository = repository
        self.initial_equity = initial_equity
        self.fee_rate = fee_rate
        self.runtime_mode = "paper" if runtime_mode == "demo" else runtime_mode
        self.source = "backtest" if self.runtime_mode == "backtest" else "paper"

    def process_filled_order(
        self,
        order: Any,
        strategy_name: str,
        timestamp: int,
        fee: float | None = None,
        transaction: Any | None = None,
    ) -> None:
        if transaction is not None:
            self._process_filled_order(transaction, order, strategy_name, timestamp, fee)
            return
        atomic_accounting = getattr(self.repository, "atomic_accounting", None)
        if callable(atomic_accounting):
            atomic_accounting(
                lambda transaction: self._process_filled_order(
                    transaction, order, strategy_name, timestamp, fee
                )
            )
            return
        self._process_filled_order(self.repository, order, strategy_name, timestamp, fee)

    def _process_filled_order(
        self,
        repository: Any,
        order: Any,
        strategy_name: str,
        timestamp: int,
        fee: float | None,
    ) -> None:
        status = _value(order.status)
        if status not in {"filled", "partially_filled"}:
            return

        side = _value(order.side)
        if side not in {"buy", "sell"}:
            raise ValueError(f"unsupported order side: {side}")
        requested_amount = _positive_finite(order.amount, "order amount")
        filled_amount = _nonnegative_finite(
            getattr(order, "filled_amount", 0.0), "filled amount"
        )
        if status == "partially_filled" and filled_amount <= 0.0:
            return
        total_filled_amount = filled_amount if filled_amount > 0.0 else requested_amount
        if total_filled_amount > requested_amount:
            raise ValueError("filled order amount exceeds requested amount")
        order_id = str(order.id)
        accounted_trades = self._accounted_trades(
            repository, strategy_name, order_id, source=self.source, runtime_mode=self.runtime_mode
        )
        amount_already_accounted = sum(_float_attr(trade, "amount") for trade in accounted_trades)
        amount = max(0.0, total_filled_amount - amount_already_accounted)
        if amount <= 0.0:
            return
        cumulative_price = _fill_price(order)
        prior_price_cost = sum(
            _float_attr(trade, "amount") * _float_attr(trade, "price")
            for trade in accounted_trades
        )
        total_price_cost = total_filled_amount * cumulative_price
        prior_notional = sum(
            order_notional(order, _float_attr(trade, "price"), amount=_float_attr(trade, "amount"))
            for trade in accounted_trades
        )
        total_notional = order_notional(
            order, cumulative_price, amount=total_filled_amount
        )
        if not all(
            math.isfinite(value)
            for value in (prior_price_cost, total_price_cost, prior_notional, total_notional)
        ):
            raise ValueError("filled order requires finite notional")
        price = (total_price_cost - prior_price_cost) / amount
        price = _positive_finite(price, "incremental fill price")
        symbol = str(order.symbol)
        if not symbol.strip():
            raise ValueError("filled order requires a symbol")
        notional = total_notional - prior_notional
        if not math.isfinite(notional) or notional <= 0.0:
            raise ValueError("filled order requires a positive finite notional")
        actual_fee = _nonnegative_finite(
            fee if fee is not None else notional * self.fee_rate,
            "fill fee",
        )

        account = self._get_or_create_account(repository, strategy_name, timestamp)
        if side == "buy" and account.cash_balance < notional + actual_fee:
            raise ValueError("insufficient cash for filled buy")
        positions = _positions_for_symbol(
            repository,
            strategy_name,
            symbol,
            source=self.source,
            runtime_mode=self.runtime_mode,
        )
        reducing_side = "short" if side == "buy" else "long"
        opening_side = "long" if side == "buy" else "short"
        reducing_position = next(
            (position for position in positions if getattr(position, "side", "") == reducing_side),
            None,
        )
        opening_position = next(
            (position for position in positions if getattr(position, "side", "") == opening_side),
            None,
        )
        realized_delta = 0.0
        residual_amount = amount

        if reducing_position is not None and position_order_quantity(reducing_position) > 0.0:
            existing_quantity = position_order_quantity(reducing_position)
            closed_amount = min(existing_quantity, amount)
            entry_price = _float_attr(reducing_position, "entry_price")
            if getattr(reducing_position, "is_inverse", False):
                full_pnl = inverse_quote_pnl(
                    reducing_position, price, entry_price=entry_price
                )
                realized_delta = full_pnl * (closed_amount / existing_quantity)
            else:
                base_closed_amount = closed_amount * (
                    contract_size(reducing_position, default=None)
                    if getattr(reducing_position, "contracts", None) is not None
                    else 1.0
                )
                realized_delta = (
                    (entry_price - price) * base_closed_amount
                    if reducing_side == "short"
                    else (price - entry_price) * base_closed_amount
                )
            residual_amount = amount - closed_amount
            remaining_amount = existing_quantity - closed_amount
            if remaining_amount > 0.0:
                updated = _position(
                    strategy_name, symbol, reducing_side, remaining_amount,
                    entry_price, timestamp, metadata=reducing_position,
                )
                updated.realized_pnl = (
                    _float_attr(reducing_position, "realized_pnl") + realized_delta
                )
                _apply_existing_mark(reducing_position, updated)
                self._set_provenance(updated)
                repository.upsert_position(updated)
            else:
                _delete_or_flatten_side(
                    repository,
                    strategy_name,
                    symbol,
                    reducing_side,
                    timestamp,
                    source=self.source,
                    runtime_mode=self.runtime_mode,
                )

        if residual_amount > 0.0:
            if opening_position is None:
                new_position = _position(
                    strategy_name, symbol, opening_side, residual_amount, price, timestamp,
                    metadata=order,
                )
            else:
                existing_amount = position_order_quantity(opening_position)
                new_position = _position(
                    strategy_name, symbol, opening_side,
                    existing_amount + residual_amount,
                    _weighted_entry(
                        existing_amount,
                        _float_attr(opening_position, "entry_price"),
                        residual_amount,
                        price,
                    ),
                    timestamp,
                    metadata=opening_position,
                )
                new_position.realized_pnl = _float_attr(opening_position, "realized_pnl")
                _apply_existing_mark(opening_position, new_position)
            self._set_provenance(new_position)
            repository.upsert_position(new_position)

        cash_delta = notional if side == "sell" else -notional
        cash_delta -= actual_fee
        account.cash_balance += cash_delta
        account.realized_pnl += realized_delta
        account.daily_pnl += realized_delta
        account.fees_paid += actual_fee
        account.updated_at = timestamp

        account.unrealized_pnl = self._open_position_unrealized_pnl(repository, strategy_name)
        account.equity = (
            account.cash_balance
            + self._open_position_cost_basis(repository, strategy_name)
            + account.unrealized_pnl
        )
        derivative_size = (
            contract_size(order, default=None)
            if is_inverse(order) or (
                isinstance(order.params, dict)
                and order.params.get("contract_size") is not None
                and order.params.get("market_type") != "spot"
            )
            else None
        )
        repository.save_trade(
            TradeRecord(
                order_id=order_id,
                strategy=strategy_name,
                source=self.source,
                runtime_mode=self.runtime_mode,
                symbol=symbol,
                side=side,
                amount=amount,
                price=price,
                fee=actual_fee,
                timestamp=timestamp,
                contracts=amount if derivative_size is not None else None,
                quantity=amount if is_inverse(order) else amount * (derivative_size or 1.0),
                quantity_unit="contracts" if is_inverse(order) else "base",
                is_inverse=is_inverse(order),
                contract_size=derivative_size,
            )
        )
        if hasattr(repository, "save_cash_ledger"):
            repository.save_cash_ledger(
                CashLedgerRecord(
                    source=self.source,
                    runtime_mode=self.runtime_mode,
                    strategy=strategy_name,
                    symbol=symbol,
                    order_id=order_id,
                    event_type="fill",
                    amount=cash_delta,
                    balance_after=account.cash_balance,
                    timestamp=timestamp,
                )
            )
        repository.upsert_account(account)

    def _set_provenance(self, record: Any) -> None:
        record.source = self.source
        record.runtime_mode = self.runtime_mode

    def _accounted_trades(
        self,
        repository: Any,
        strategy_name: str,
        order_id: str,
        *,
        source: str,
        runtime_mode: str,
    ) -> list[Any]:
        if not order_id or not hasattr(repository, "get_trades"):
            return []
        try:
            return list(repository.get_trades(
                strategy=strategy_name,
                order_id=order_id,
                source=source,
                runtime_mode=runtime_mode,
            ))
        except TypeError:
            try:
                trades = repository.get_trades(strategy_name)
            except TypeError:
                return []
            return [
                trade for trade in trades
                if getattr(trade, "order_id", "") == order_id
                and _has_domain(trade, source, runtime_mode)
            ]

    def _get_or_create_account(
        self, repository: Any, strategy_name: str, timestamp: int
    ) -> AccountRecord:
        try:
            account = repository.get_account(
                strategy_name,
                source=self.source,
                runtime_mode=self.runtime_mode,
            )
        except TypeError:
            account = repository.get_account(strategy_name)
            if account is not None and not _has_domain(account, self.source, self.runtime_mode):
                account = None
        if account is not None:
            return account
        return AccountRecord(
            strategy=strategy_name,
            source=self.source,
            runtime_mode=self.runtime_mode,
            initial_equity=self.initial_equity,
            cash_balance=self.initial_equity,
            equity=self.initial_equity,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            daily_pnl=0.0,
            fees_paid=0.0,
            updated_at=timestamp,
        )

    def _delete_or_flatten_position(self, strategy_name: str, symbol: str, timestamp: int) -> None:
        if hasattr(self.repository, "delete_position"):
            try:
                self.repository.delete_position(
                    strategy_name,
                    symbol,
                    source=self.source,
                    runtime_mode=self.runtime_mode,
                )
            except TypeError as exc:
                if "unexpected keyword argument" not in str(exc):
                    raise
                if _can_safely_delete_legacy_position(
                    self.repository, strategy_name, symbol, self.source, self.runtime_mode
                ):
                    self.repository.delete_position(strategy_name, symbol)
                else:
                    logger.warning(
                        "Repository lacks scoped position archive; preserving legacy position "
                        "rows for %s/%s",
                        strategy_name, symbol,
                    )
            return
        self.repository.upsert_position(
            PositionRecord(
                strategy=strategy_name,
                source=self.source,
                runtime_mode=self.runtime_mode,
                symbol=symbol,
                side="long",
                amount=0.0,
                entry_price=0.0,
                mark_price=None,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
                leverage=1,
                timestamp=timestamp,
            )
        )

    def _open_position_cost_basis(self, repository: Any, strategy_name: str) -> float:
        if not hasattr(repository, "get_open_positions"):
            return 0.0
        total = 0.0
        for position in _open_positions_for_runtime(
            repository, strategy_name, self.source, self.runtime_mode
        ):
            total += signed_position_cost_basis(position)
        return total

    def _open_position_unrealized_pnl(self, repository: Any, strategy_name: str) -> float:
        if not hasattr(repository, "get_open_positions"):
            return 0.0
        return sum(
            _float_attr(position, "unrealized_pnl")
            for position in _open_positions_for_runtime(
                repository, strategy_name, self.source, self.runtime_mode
            )
        )


def _positions_for_symbol(
    repository: Any,
    strategy_name: str,
    symbol: str,
    *,
    source: str = "unknown",
    runtime_mode: str = "unknown",
) -> list[Any]:
    if hasattr(repository, "get_positions"):
        try:
            filtered = list(repository.get_positions(
                strategy_name,
                symbol,
                source=source,
                runtime_mode=runtime_mode,
            ))
            if filtered:
                return filtered
            legacy = list(repository.get_positions(strategy_name, symbol))
        except TypeError:
            legacy = list(repository.get_positions(strategy_name, symbol))
        return [
            position for position in legacy
            if _has_domain(position, source, runtime_mode)
        ]
    try:
        position = repository.get_position(
            strategy_name,
            symbol,
            source=source,
            runtime_mode=runtime_mode,
        )
    except TypeError:
        position = repository.get_position(strategy_name, symbol)
    return (
        []
        if position is None or not _has_domain(position, source, runtime_mode)
        else [position]
    )


def _open_positions_for_runtime(
    repository: Any,
    strategy_name: str,
    source: str,
    runtime_mode: str,
) -> list[Any]:
    try:
        positions = repository.get_open_positions(
            strategy_name, source=source, runtime_mode=runtime_mode
        )
    except TypeError:
        positions = repository.get_open_positions(strategy_name)
    return [
        position for position in positions
        if _has_domain(position, source, runtime_mode)
    ]


def _has_domain(record: Any, source: str, runtime_mode: str) -> bool:
    return (
        str(getattr(record, "source", "unknown") or "unknown").strip().lower() == source
        and str(getattr(record, "runtime_mode", "unknown") or "unknown").strip().lower()
        == runtime_mode
    )


def _is_legacy_record(record: Any) -> bool:
    return (
        str(getattr(record, "source", "unknown") or "unknown").strip().lower()
        in {"", "unknown", "legacy"}
        and str(getattr(record, "runtime_mode", "unknown") or "unknown")
        .strip()
        .lower()
        in {"", "unknown", "legacy"}
    )


def _can_safely_delete_legacy_position(
    repository: Any,
    strategy_name: str,
    symbol: str,
    source: str,
    runtime_mode: str,
    *,
    side: str | None = None,
) -> bool:
    get_positions = getattr(repository, "get_positions", None)
    if callable(get_positions):
        try:
            rows = list(get_positions(strategy_name, symbol))
        except TypeError:
            return False
    else:
        get_position = getattr(repository, "get_position", None)
        if not callable(get_position):
            return False
        try:
            row = get_position(strategy_name, symbol, side=side)
        except TypeError:
            try:
                row = get_position(strategy_name, symbol)
            except TypeError:
                return False
        rows = [] if row is None else [row]
    matching = [
        row for row in rows
        if side is None or getattr(row, "side", None) == side
    ]
    return bool(matching) and all(_has_domain(row, source, runtime_mode) for row in matching)


def _delete_or_flatten_side(
    repository: Any,
    strategy_name: str,
    symbol: str,
    side: str,
    timestamp: int,
    *,
    source: str = "unknown",
    runtime_mode: str = "unknown",
) -> None:
    if hasattr(repository, "delete_position"):
        try:
            repository.delete_position(
                strategy_name,
                symbol,
                side=side,
                source=source,
                runtime_mode=runtime_mode,
            )
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc):
                raise
            # Compatibility for test/dry-run repositories predating provenance.
            try:
                if _can_safely_delete_legacy_position(
                    repository, strategy_name, symbol, source, runtime_mode, side=side
                ):
                    repository.delete_position(strategy_name, symbol, side=side)
                else:
                    logger.warning(
                        "Repository lacks scoped position archive; preserving legacy position "
                        "rows for %s/%s/%s",
                        strategy_name, symbol, side,
                    )
                return
            except TypeError as side_error:
                if "unexpected keyword argument" not in str(side_error):
                    raise
                repository.delete_position(strategy_name, symbol)
        return
    repository.upsert_position(
        PositionRecord(
            strategy=strategy_name,
            source=source,
            runtime_mode=runtime_mode,
            symbol=symbol,
            side=side,
            amount=0.0,
            entry_price=0.0,
            mark_price=None,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            leverage=1,
            timestamp=timestamp,
        )
    )


def _net_position(
    *,
    strategy_name: str,
    symbol: str,
    side: str,
    amount: float,
    price: float,
    timestamp: int,
    existing: Any,
) -> tuple[float, PositionRecord | None]:
    if amount <= 0:
        raise ValueError("filled order requires a positive amount")
    if side not in {"buy", "sell"}:
        raise ValueError(f"unsupported order side: {side}")

    existing_amount = _float_attr(existing, "amount")
    if existing is None or existing_amount == 0:
        position_side = "long" if side == "buy" else "short"
        return 0.0, _position(strategy_name, symbol, position_side, amount, price, timestamp)

    existing_side = str(existing.side)
    existing_entry = _float_attr(existing, "entry_price")

    if side == "buy" and existing_side == "long":
        new_amount = existing_amount + amount
        return 0.0, _position(
            strategy_name,
            symbol,
            "long",
            new_amount,
            _weighted_entry(existing_amount, existing_entry, amount, price),
            timestamp,
        )

    if side == "sell" and existing_side == "short":
        new_amount = existing_amount + amount
        return 0.0, _position(
            strategy_name,
            symbol,
            "short",
            new_amount,
            _weighted_entry(existing_amount, existing_entry, amount, price),
            timestamp,
        )

    if existing_side == "long" and side == "sell":
        closed_amount = min(existing_amount, amount)
        realized = (price - existing_entry) * closed_amount
        remaining = existing_amount - amount
        if remaining > 0:
            return realized, _position(
                strategy_name, symbol, "long", remaining, existing_entry, timestamp
            )
        if remaining < 0:
            return realized, _position(
                strategy_name, symbol, "short", abs(remaining), price, timestamp
            )
        return realized, None

    if existing_side == "short" and side == "buy":
        closed_amount = min(existing_amount, amount)
        realized = (existing_entry - price) * closed_amount
        remaining = existing_amount - amount
        if remaining > 0:
            return realized, _position(
                strategy_name, symbol, "short", remaining, existing_entry, timestamp
            )
        if remaining < 0:
            return realized, _position(
                strategy_name, symbol, "long", abs(remaining), price, timestamp
            )
        return realized, None

    raise ValueError(f"unsupported position side: {existing_side}")


def _position(
    strategy_name: str,
    symbol: str,
    side: str,
    amount: float,
    entry_price: float,
    timestamp: int,
    *,
    metadata: Any | None = None,
) -> PositionRecord:
    inverse = is_inverse(metadata)
    params = getattr(metadata, "params", None)
    linear = not inverse and (
        getattr(metadata, "contracts", None) is not None
        or isinstance(params, dict)
        and params.get("contract_size") is not None
        and params.get("market_type") != "spot"
    )
    size = contract_size(metadata, default=None) if inverse or linear else None
    contracts = amount if inverse or linear else None
    notional = contracts * size if inverse and size else None
    return PositionRecord(
        strategy=strategy_name,
        source="paper",
        runtime_mode="paper",
        symbol=symbol,
        side=side,
        amount=(
            notional if inverse and notional is not None
            else amount * size if linear and size is not None
            else amount
        ),
        entry_price=entry_price,
        mark_price=None,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        leverage=1,
        timestamp=timestamp,
        contracts=contracts,
        quantity=contracts if inverse else amount * (size or 1.0),
        quantity_unit="contracts" if inverse else "base",
        notional_value=notional,
        is_inverse=inverse,
        contract_size=size,
    )


def _apply_existing_mark(existing: Any, position: PositionRecord) -> None:
    if existing is None or getattr(existing, "side", None) != position.side:
        return
    mark_price = getattr(existing, "mark_price", None)
    if mark_price is None:
        return
    position.mark_price = float(mark_price)
    position.unrealized_pnl = _marked_unrealized_pnl(position, position.mark_price)


def _marked_unrealized_pnl(position: PositionRecord, mark_price: float) -> float:
    if getattr(position, "is_inverse", False):
        return inverse_quote_pnl(position, mark_price)
    amount = abs(_float_attr(position, "amount"))
    entry_price = _float_attr(position, "entry_price")
    if position.side == "short":
        return (entry_price - mark_price) * amount
    return (mark_price - entry_price) * amount


def _nonnegative_finite(value: Any, name: str) -> float:
    if value is None:
        return 0.0
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"filled order requires a finite {name}") from exc
    if not math.isfinite(normalized) or normalized < 0.0:
        raise ValueError(f"filled order requires a nonnegative finite {name}")
    return normalized


def _positive_finite(value: Any, name: str) -> float:
    normalized = _nonnegative_finite(value, name)
    if normalized <= 0.0:
        raise ValueError(f"filled order requires a positive {name}")
    return normalized


def _fill_price(order: Any) -> float:
    price = order.fill_price if order.fill_price is not None else order.price
    try:
        normalized_price = float(price)
    except (TypeError, ValueError, OverflowError):
        normalized_price = 0.0
    if not math.isfinite(normalized_price) or normalized_price <= 0.0:
        raise ValueError("filled order requires a positive fill price")
    return normalized_price


def _weighted_entry(
    existing_amount: float,
    existing_entry: float,
    amount: float,
    price: float,
) -> float:
    new_amount = existing_amount + amount
    return ((existing_amount * existing_entry) + (amount * price)) / new_amount


def _float_attr(obj: Any, name: str) -> float:
    if obj is None:
        return 0.0
    return float(getattr(obj, name, 0.0) or 0.0)


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))
