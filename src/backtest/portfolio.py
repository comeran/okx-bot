"""多标的现货共用一个现金账户。资金费按合约规格从现金扣除。"""

from __future__ import annotations

from typing import Any

from src.backtest.engine import BacktestOrderManager, _bars_per_year, spot_backtest_symbol
from src.backtest.fill_model import BookFillModel, ContractSpec
from src.backtest.manifest import annualized_return
from src.backtest.report import BacktestReport, generate_report
from src.core.types import Order, OrderSide, OrderStatus
from src.order.lifecycle import record_transition


def _leg(legs: dict[str, dict[str, float]], symbol: str) -> dict[str, float]:
    return legs.setdefault(symbol, {"quantity": 0.0, "average": 0.0, "fees": 0.0})


def _reject_reason(
    cash: float, legs: dict[str, dict[str, float]], order: Order, price: float, fee: float,
    filled: float,
) -> str | None:
    if filled <= 0 or price <= 0:
        return "invalid spot fill amount, price or fee"
    if order.side == OrderSide.BUY:
        if filled * price + fee > cash:
            return "insufficient spot cash including fees"
        return None
    if order.side == OrderSide.SELL:
        if filled > _leg(legs, order.symbol)["quantity"]:
            return "insufficient spot holdings"
        return None
    return "unsupported spot order side"


def _apply_fill(
    cash: float, legs: dict[str, dict[str, float]], order: Order, price: float, fee: float,
    filled: float,
) -> tuple[float, float]:
    leg = _leg(legs, order.symbol)
    if order.side == OrderSide.BUY:
        total = leg["quantity"] + filled
        leg["average"] = (leg["quantity"] * leg["average"] + filled * price) / total
        leg["quantity"] = total
        leg["fees"] += fee
        return cash - (filled * price + fee), 0.0
    entry_fee = leg["fees"] * (filled / leg["quantity"])
    realized = (price - leg["average"]) * filled - entry_fee - fee
    leg["quantity"] -= filled
    leg["fees"] -= entry_fee
    if leg["quantity"] <= 0:
        leg["quantity"] = 0.0
        leg["average"] = 0.0
        leg["fees"] = 0.0
    return cash + filled * price - fee, realized


async def run_portfolio(
    engine: Any,
    strategy: Any,
    bars_by_symbol: dict[str, list[Any]],
    *,
    contracts: dict[str, ContractSpec] | None = None,
) -> BacktestReport:
    normalized: dict[str, list[Any]] = {}
    for symbol, bars in bars_by_symbol.items():
        key = spot_backtest_symbol(symbol)
        normalized[key] = list(bars)
    if not normalized:
        raise ValueError("portfolio backtest requires symbols")
    specs = {
        spot_backtest_symbol(symbol): spec
        for symbol, spec in (contracts or {}).items()
    }
    events = [
        (bar.timestamp, symbol, bar)
        for symbol, bars in normalized.items()
        for bar in bars
    ]
    events.sort(key=lambda item: (item[0], item[1]))
    model = BookFillModel(
        slippage=engine.matcher.slippage, fee_rate=engine.matcher.fee_rate,
    )
    cash = float(engine.initial_capital)
    legs: dict[str, dict[str, float]] = {}
    marks = {
        symbol: bars[0].open for symbol, bars in normalized.items() if bars
    }
    pending: list[Order] = []
    trades: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    round_trips: list[float] = []
    equity_curve = [cash]
    equity_timestamps = [events[0][0] if events else 0]
    order_manager = BacktestOrderManager(
        strategy_name=getattr(strategy, "name", ""), allow_multiple=True,
    )
    if hasattr(strategy, "set_order_manager"):
        strategy.set_order_manager(order_manager)
    await strategy.on_init()

    for timestamp, symbol, bar in events:
        resting: list[Order] = []
        for order in pending:
            if order.symbol != symbol or order.status not in {
                OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED,
            }:
                resting.append(order)
                continue
            match = model.match(order, bar)
            if match.status is OrderStatus.PENDING or match.fill_price is None:
                resting.append(order)
                continue
            reason = _reject_reason(
                cash, legs, order, match.fill_price, match.fee, match.filled_amount,
            )
            if reason is not None:
                record_transition(
                    order, OrderStatus.REJECTED,
                    cause="research_reject", clock_ms=timestamp, source="research_matcher",
                )
                rejected.append({
                    "symbol": order.symbol, "side": order.side.value, "timestamp": timestamp,
                    "amount": order.amount, "price": order.price, "reason": reason,
                })
                await strategy.on_order(order)
                continue
            cash, realized = _apply_fill(
                cash, legs, order, match.fill_price, match.fee, match.filled_amount,
            )
            already = order.filled_amount or 0.0
            order.filled_amount = already + match.filled_amount
            order.remaining_amount = max(0.0, order.amount - order.filled_amount)
            target = (
                OrderStatus.FILLED
                if order.remaining_amount == 0
                else OrderStatus.PARTIALLY_FILLED
            )
            record_transition(
                order, target,
                cause="research_match", clock_ms=timestamp, source="research_matcher",
            )
            order.fill_price = match.fill_price
            order.fill_time = timestamp
            if realized:
                round_trips.append(realized)
            trades.append({
                "symbol": order.symbol, "side": order.side.value,
                "amount": match.filled_amount, "price": match.fill_price,
                "pnl": realized, "fee": match.fee, "timestamp": timestamp,
            })
            await strategy.on_order(order)
            if target is OrderStatus.PARTIALLY_FILLED:
                resting.append(order)
        pending = resting

        spec = specs.get(symbol)
        if spec is not None:
            charge = BookFillModel(contract=spec).funding_charge(
                quantity=_leg(legs, symbol)["quantity"], mark=bar.close,
            )
            cash -= charge
        marks[symbol] = bar.close
        equity_curve.append(cash + sum(
            item["quantity"] * marks.get(name, 0.0) for name, item in legs.items()
        ))
        equity_timestamps.append(timestamp)

        strategy.symbol = symbol
        returned = await strategy.on_bar(bar)
        submitted = order_manager.take_submitted()
        if isinstance(returned, Order) and all(returned is not item for item in submitted):
            submitted.append(returned)
        for order in submitted:
            if order.status is not OrderStatus.PENDING:
                continue
            order.symbol = spot_backtest_symbol(order.symbol)
            order_manager.track_order(order)
            pending.append(order)

    all_bars = [bar for _, _, bar in events]
    report = generate_report(
        initial_capital=engine.initial_capital,
        trades=trades,
        equity_curve=equity_curve,
        round_trip_pnls=round_trips or None,
        bars_per_year=_bars_per_year(all_bars),
    )
    elapsed = 0
    if len(equity_timestamps) > 1:
        elapsed = equity_timestamps[-1] - equity_timestamps[0]
    report.annualized_return = annualized_return(
        equity_curve[0], report.final_equity, elapsed,
    )
    report.equity_timestamps = equity_timestamps
    report.rejected_orders = rejected
    return report
