from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from src.data.models import AccountRecord, OrderRecord, PositionRecord, TradeRecord


@dataclass(frozen=True)
class StrategyPerformance:
    strategy: str
    initial_equity: float
    equity: float
    return_pct: float | None
    realized_pnl: float
    unrealized_pnl: float
    fees_paid: float
    position_notional: float
    open_positions: int
    order_count: int
    filled_order_count: int
    trade_count: int
    closed_trade_count: int
    winning_trade_count: int
    losing_trade_count: int
    win_rate: float | None
    last_order_at: int | None


@dataclass
class _Lot:
    quantity: float
    price: float
    entry_fee: float


@dataclass
class _ClosedTradeResult:
    pnl: float


def build_strategy_performance(
    accounts: list[AccountRecord],
    positions: list[PositionRecord],
    orders: list[OrderRecord],
    trades: list[TradeRecord],
) -> list[StrategyPerformance]:
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

        initial_equity = account.initial_equity if account is not None else 0.0
        equity = account.equity if account is not None else 0.0
        realized_pnl = account.realized_pnl if account is not None else 0.0
        unrealized_pnl = account.unrealized_pnl if account is not None else 0.0
        fees_paid = account.fees_paid if account is not None else 0.0

        return_pct = None
        if account is not None and initial_equity != 0:
            return_pct = (equity - initial_equity) / initial_equity

        open_positions = sum(1 for position in strategy_positions if position.amount != 0)
        position_notional = sum(
            _position_notional(position)
            for position in strategy_positions
            if position.amount != 0
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
            )
        )

    return performances


def _is_strategy_in_scope(strategy: str) -> bool:
    return bool(strategy) and strategy != "__exchange__"


def _latest_accounts_by_strategy(accounts: list[AccountRecord]) -> dict[str, AccountRecord]:
    latest: dict[str, AccountRecord] = {}
    for account in accounts:
        if not _is_strategy_in_scope(account.strategy):
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


def _position_notional(position: PositionRecord) -> float:
    mark_price = (
        position.mark_price
        if position.mark_price is not None and position.mark_price > 0
        else position.entry_price
    )
    return abs(position.amount) * mark_price


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
        if trade.amount <= 0:
            continue

        side = trade.side.lower()
        key = (trade.strategy, trade.symbol)
        side_inventory = inventory[key]
        if side == "buy":
            closed_results.extend(
                _apply_trade(
                    closing_lots=side_inventory["short"],
                    opening_lots=side_inventory["long"],
                    amount=trade.amount,
                    price=trade.price,
                    fee=trade.fee,
                    closing_short=True,
                )
            )
        elif side == "sell":
            closed_results.extend(
                _apply_trade(
                    closing_lots=side_inventory["long"],
                    opening_lots=side_inventory["short"],
                    amount=trade.amount,
                    price=trade.price,
                    fee=trade.fee,
                    closing_short=False,
                )
            )

    return closed_results


def _apply_trade(
    *,
    closing_lots: deque[_Lot],
    opening_lots: deque[_Lot],
    amount: float,
    price: float,
    fee: float,
    closing_short: bool,
) -> list[_ClosedTradeResult]:
    closed_results: list[_ClosedTradeResult] = []
    remaining = amount
    matched_fragments: list[tuple[float, float, float, float]] = []

    while remaining > 0 and closing_lots:
        lot = closing_lots[0]
        original_quantity = lot.quantity
        original_entry_fee = lot.entry_fee
        matched = min(remaining, original_quantity)
        matched_fragments.append((lot.price, original_quantity, original_entry_fee, matched))
        remaining -= matched

        if matched == original_quantity:
            closing_lots.popleft()
        else:
            remaining_quantity = original_quantity - matched
            lot.quantity = remaining_quantity
            lot.entry_fee = original_entry_fee * (remaining_quantity / original_quantity)

    closed_quantity = amount - remaining
    if closed_quantity > 0:
        closed_fee_share = fee * (closed_quantity / amount)
        pnl = 0.0
        for entry_price, original_quantity, original_entry_fee, matched in matched_fragments:
            entry_fee_alloc = original_entry_fee * (matched / original_quantity)
            exit_fee_alloc = closed_fee_share * (matched / closed_quantity)
            gross = (
                (entry_price - price) * matched
                if closing_short
                else (price - entry_price) * matched
            )
            pnl += gross - entry_fee_alloc - exit_fee_alloc
        closed_results.append(_ClosedTradeResult(pnl=pnl))

    if remaining > 0:
        open_fee_share = fee - (fee * (closed_quantity / amount)) if amount else 0.0
        opening_lots.append(_Lot(quantity=remaining, price=price, entry_fee=open_fee_share))

    return closed_results
