from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass
from types import SimpleNamespace

from src.data.models import AccountRecord, OrderRecord, PositionRecord, TradeRecord
from src.exchange.base import canonical_symbol
from src.order.value import (
    inverse_quote_pnl,
)
from src.order.value import (
    is_inverse as parse_is_inverse,
)
from src.order.value import (
    position_notional as shared_position_notional,
)


@dataclass(frozen=True)
class StrategyPerformance:
    strategy: str
    initial_equity: float | None
    equity: float | None
    return_pct: float | None
    realized_pnl: float | None
    unrealized_pnl: float | None
    fees_paid: float | None
    position_notional: float | None
    open_positions: int
    order_count: int
    filled_order_count: int
    trade_count: int
    closed_trade_count: int
    winning_trade_count: int
    losing_trade_count: int
    win_rate: float | None
    last_order_at: int | None
    positions: list[dict[str, object]]
    recent_orders: list[dict[str, object]]


@dataclass
class _Lot:
    quantity: float
    price: float
    entry_fee: float
    is_inverse: bool = False
    contracts: float | None = None
    contract_size: float | None = None
    quantity_unit: str = "base"


@dataclass
class _ClosedTradeResult:
    pnl: float


def build_strategy_performance(
    accounts: list[AccountRecord],
    positions: list[PositionRecord],
    orders: list[OrderRecord],
    trades: list[TradeRecord],
    *,
    runtime_modes: set[str] | None = None,
    source: str | None = None,
) -> list[StrategyPerformance]:
    """Build a performance view for a single explicit provenance scope.

    Callers that need current runtime data must pass ``runtime_modes``. Legacy rows
    are intentionally excluded by that filter rather than inferred from a strategy
    name or an order/trade identity.
    """
    normalized_modes = (
        {mode.strip().lower() for mode in runtime_modes} if runtime_modes is not None else None
    )
    normalized_source = source.strip().lower() if source is not None else None
    if normalized_modes is not None and len(normalized_modes) > 1:
        raise ValueError("strategy performance requires a single runtime_mode")

    def in_scope(record: object) -> bool:
        record_runtime = str(
            getattr(record, "runtime_mode", "unknown") or "unknown"
        ).strip().lower()
        record_source = str(
            getattr(record, "source", "unknown") or "unknown"
        ).strip().lower()
        if normalized_modes is not None:
            expected_modes = {
                "demo": "paper",
                "paper": "paper",
                "live": "live",
                "backtest": "backtest",
            }
            expected_sources = {
                "demo": "paper",
                "paper": "paper",
                "live": "live_exchange",
                "backtest": "backtest",
            }
            if not any(
                record_runtime == expected_modes.get(mode)
                and record_source == expected_sources.get(mode)
                for mode in normalized_modes
            ):
                return False
        return normalized_source is None or record_source == normalized_source

    accounts = [
        record
        for record in accounts
        if in_scope(record) and getattr(record, "active", None) is not False
    ]
    positions = [
        record
        for record in positions
        if in_scope(record) and getattr(record, "active", None) is not False
    ]
    orders = [
        record for record in orders
        if in_scope(record) and getattr(record, "active", None) is not False
    ]
    trades = [
        record for record in trades
        if in_scope(record) and getattr(record, "active", None) is not False
    ]
    strategies = sorted(
        {
            record.strategy
            for record in (*accounts, *positions, *orders, *trades)
            if _is_strategy_in_scope(record.strategy)
        }
    )

    latest_accounts = _latest_accounts_by_strategy(accounts)
    performances: list[StrategyPerformance] = []

    for strategy in strategies:
        account = latest_accounts.get(strategy)
        strategy_positions = [position for position in positions if position.strategy == strategy]
        strategy_orders = [order for order in orders if order.strategy == strategy]
        strategy_trades = [trade for trade in trades if trade.strategy == strategy]
        closed_trades = _build_closed_trade_results(strategy_trades)

        initial_equity = account.initial_equity if account is not None else None
        equity = account.equity if account is not None else None
        realized_pnl = account.realized_pnl if account is not None else None
        unrealized_pnl = account.unrealized_pnl if account is not None else None
        fees_paid = account.fees_paid if account is not None else None

        return_pct = None
        if account is not None and initial_equity != 0:
            return_pct = (equity - initial_equity) / initial_equity

        open_positions = sum(1 for position in strategy_positions if position.amount != 0)
        position_values = [
            _position_notional(position)
            for position in strategy_positions
            if position.amount != 0
        ]
        position_notional = (
            None if any(value is None for value in position_values)
            else sum(value for value in position_values if value is not None)
        )
        order_count = len(strategy_orders)
        filled_order_count = sum(1 for order in strategy_orders if order.status == "filled")
        trade_count = len(strategy_trades)
        last_order_at = max((order.timestamp for order in strategy_orders), default=None)

        closed_trade_count = len(closed_trades)
        winning_trade_count = sum(1 for trade in closed_trades if trade.pnl > 0)
        losing_trade_count = sum(1 for trade in closed_trades if trade.pnl < 0)
        win_rate = None
        if closed_trade_count:
            win_rate = winning_trade_count / closed_trade_count

        performances.append(
            StrategyPerformance(
                strategy=strategy,
                initial_equity=initial_equity,
                equity=equity,
                return_pct=return_pct,
                realized_pnl=realized_pnl,
                unrealized_pnl=unrealized_pnl,
                fees_paid=fees_paid,
                position_notional=position_notional,
                open_positions=open_positions,
                order_count=order_count,
                filled_order_count=filled_order_count,
                trade_count=trade_count,
                closed_trade_count=closed_trade_count,
                winning_trade_count=winning_trade_count,
                losing_trade_count=losing_trade_count,
                win_rate=win_rate,
                last_order_at=last_order_at,
                positions=[_public_position(position) for position in strategy_positions],
                recent_orders=[
                    _public_order(order)
                    for order in sorted(
                        strategy_orders,
                        key=lambda order: (order.timestamp, order.id or 0),
                        reverse=True,
                    )[:20]
                ],
            )
        )

    return performances


def _public_position(position: PositionRecord) -> dict[str, object]:
    values = position.model_dump()
    if (
        getattr(position, "notional_value", None) is not None
        or getattr(position, "contracts", None) is not None
        or getattr(position, "is_inverse", False)
    ):
        for field in (
            "contracts", "quantity", "quantity_unit", "notional_value",
            "is_inverse", "contract_size",
        ):
            if hasattr(position, field):
                values[field] = getattr(position, field)
        if getattr(position, "is_inverse", False) and values.get("notional_value") is None:
            values["notional_value"] = _position_notional(position)
    return values


def _public_order(order: OrderRecord) -> dict[str, object]:
    values = order.model_dump()
    return {
        field: values.get(field, getattr(order, field, 0.0))
        for field in (
            "id",
            "order_id",
            "strategy",
            "symbol",
            "side",
            "type",
            "amount",
            "price",
            "status",
            "fill_price",
            "timestamp",
            "filled_amount",
            "remaining_amount",
        )
    }


def _is_strategy_in_scope(strategy: str) -> bool:
    return bool(strategy) and strategy != "__exchange__"


def _latest_accounts_by_strategy(accounts: list[AccountRecord]) -> dict[str, AccountRecord]:
    latest: dict[str, AccountRecord] = {}
    for account in accounts:
        if (
            getattr(account, "active", None) is False
            or not _is_strategy_in_scope(account.strategy)
        ):
            continue
        current = latest.get(account.strategy)
        if current is None or (
            account.updated_at,
            account.id or -1,
        ) >= (
            current.updated_at,
            current.id or -1,
        ):
            latest[account.strategy] = account
    return latest


def _position_notional(position: PositionRecord) -> float | None:
    try:
        return shared_position_notional(position)
    except ValueError:
        return None


def _build_closed_trade_results(trades: list[TradeRecord]) -> list[_ClosedTradeResult]:
    inventory: dict[tuple[str, str], dict[str, deque[_Lot]]] = defaultdict(
        lambda: {"long": deque(), "short": deque()}
    )
    closed_results: list[_ClosedTradeResult] = []

    for trade in sorted(
        trades,
        key=lambda trade: (
            trade.timestamp,
            trade.exchange_trade_id,
            trade.order_id,
        ),
    ):
        quantity = _trade_quantity(trade)
        if quantity is None:
            continue

        side = trade.side.lower()
        key = (
            trade.strategy,
            canonical_symbol(trade.symbol, market_type="auto"),
        )
        side_inventory = inventory[key]
        if side == "buy":
            closed_results.extend(
                _apply_trade(
                    closing_lots=side_inventory["short"],
                    opening_lots=side_inventory["long"],
                    amount=quantity,
                    price=trade.price,
                    fee=trade.fee,
                    trade=trade,
                    closing_short=True,
                )
            )
        elif side == "sell":
            closed_results.extend(
                _apply_trade(
                    closing_lots=side_inventory["long"],
                    opening_lots=side_inventory["short"],
                    amount=quantity,
                    price=trade.price,
                    fee=trade.fee,
                    trade=trade,
                    closing_short=False,
                )
            )

    return closed_results


def _trade_quantity(trade: object) -> float | None:
    try:
        amount = float(getattr(trade, "amount", 0.0))
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(amount) or amount <= 0.0:
        return None
    if not parse_is_inverse(trade):
        return amount
    unit = str(getattr(trade, "quantity_unit", "") or "").strip().lower()
    try:
        contracts = float(getattr(trade, "contracts", 0.0))
        size = float(getattr(trade, "contract_size", 0.0))
    except (TypeError, ValueError, OverflowError):
        return None
    if (
        unit != "contracts"
        or not math.isfinite(contracts)
        or contracts <= 0.0
        or not math.isfinite(size)
        or size <= 0.0
    ):
        return None
    return contracts


def _compatible_trade_metadata(lot: _Lot, trade: object) -> bool:
    inverse = parse_is_inverse(trade)
    if lot.is_inverse != inverse:
        return False
    if not inverse:
        return True
    try:
        size = float(getattr(trade, "contract_size"))
    except (TypeError, ValueError, OverflowError):
        return False
    return lot.quantity_unit == "contracts" and math.isclose(
        lot.contract_size or 0.0, size, rel_tol=0.0, abs_tol=1e-12
    )


def _inverse_lot_position(
    lot: _Lot,
    closing_short: bool,
    *,
    contracts: float | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        side="short" if closing_short else "long",
        contracts=(
            contracts
            if contracts is not None
            else lot.contracts if lot.contracts is not None else lot.quantity
        ),
        contract_size=lot.contract_size,
        is_inverse=True,
    )


def _apply_trade(
    *,
    closing_lots: deque[_Lot],
    opening_lots: deque[_Lot],
    amount: float,
    price: float,
    fee: float,
    trade: object,
    closing_short: bool,
) -> list[_ClosedTradeResult]:
    closed_results: list[_ClosedTradeResult] = []
    remaining = amount
    matched_fragments: list[tuple[_Lot, float, float, float]] = []

    while remaining > 0 and closing_lots:
        lot = closing_lots[0]
        if not _compatible_trade_metadata(lot, trade):
            break
        original_quantity = lot.quantity
        original_entry_fee = lot.entry_fee
        matched = min(remaining, original_quantity)
        matched_fragments.append((lot, original_quantity, original_entry_fee, matched))
        remaining -= matched

        if matched == original_quantity:
            closing_lots.popleft()
        else:
            remaining_quantity = original_quantity - matched
            lot.quantity = remaining_quantity
            if lot.is_inverse:
                lot.contracts = remaining_quantity
            lot.entry_fee = original_entry_fee * (remaining_quantity / original_quantity)

    closed_quantity = amount - remaining
    if closed_quantity > 0:
        closed_fee_share = fee * (closed_quantity / amount)
        pnl = 0.0
        for lot, original_quantity, original_entry_fee, matched in matched_fragments:
            entry_fee_alloc = original_entry_fee * (matched / original_quantity)
            exit_fee_alloc = closed_fee_share * (matched / closed_quantity)
            if lot.is_inverse:
                gross = inverse_quote_pnl(
                    _inverse_lot_position(
                        lot, closing_short, contracts=matched
                    ),
                    price,
                    entry_price=lot.price,
                )
            else:
                gross = (
                    (lot.price - price) * matched
                    if closing_short
                    else (price - lot.price) * matched
                )
            pnl += gross - entry_fee_alloc - exit_fee_alloc
        closed_results.append(_ClosedTradeResult(pnl=pnl))

    if remaining > 0:
        open_fee_share = fee - (fee * (closed_quantity / amount)) if amount else 0.0
        opening_lots.append(
            _Lot(
                quantity=remaining,
                price=price,
                entry_fee=open_fee_share,
                is_inverse=parse_is_inverse(trade),
                contracts=remaining if parse_is_inverse(trade) else None,
                contract_size=getattr(trade, "contract_size", None),
                quantity_unit=str(getattr(trade, "quantity_unit", "base") or "base"),
            )
        )

    return closed_results
