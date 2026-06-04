from __future__ import annotations

from typing import Any

from src.data.models import AccountRecord, CashLedgerRecord, PositionRecord, TradeRecord


class PaperAccountingService:
    def __init__(
        self,
        repository: Any,
        initial_equity: float = 100000.0,
        fee_rate: float = 0.0,
    ) -> None:
        self.repository = repository
        self.initial_equity = initial_equity
        self.fee_rate = fee_rate

    def process_filled_order(
        self,
        order: Any,
        strategy_name: str,
        timestamp: int,
        fee: float | None = None,
    ) -> None:
        if _value(order.status) != "filled":
            return

        side = _value(order.side)
        amount = float(order.amount)
        price = _fill_price(order)
        symbol = str(order.symbol)
        order_id = str(order.id)
        notional = amount * price
        actual_fee = float(fee if fee is not None else notional * self.fee_rate)

        account = self._get_or_create_account(strategy_name, timestamp)
        existing = self.repository.get_position(strategy_name, symbol)
        realized_delta, new_position = _net_position(
            strategy_name=strategy_name,
            symbol=symbol,
            side=side,
            amount=amount,
            price=price,
            timestamp=timestamp,
            existing=existing,
        )

        cash_delta = notional if side == "sell" else -notional
        cash_delta -= actual_fee
        account.cash_balance += cash_delta
        account.realized_pnl += realized_delta
        account.daily_pnl += realized_delta
        account.fees_paid += actual_fee
        account.unrealized_pnl = 0.0
        account.updated_at = timestamp

        if new_position is None:
            self._delete_or_flatten_position(strategy_name, symbol, timestamp)
        else:
            new_position.realized_pnl = _float_attr(existing, "realized_pnl") + realized_delta
            self.repository.upsert_position(new_position)

        account.equity = account.cash_balance + self._open_position_cost_basis(strategy_name)
        self.repository.save_trade(
            TradeRecord(
                strategy=strategy_name,
                symbol=symbol,
                side=side,
                amount=amount,
                price=price,
                fee=actual_fee,
                timestamp=timestamp,
            )
        )
        if hasattr(self.repository, "save_cash_ledger"):
            self.repository.save_cash_ledger(
                CashLedgerRecord(
                    strategy=strategy_name,
                    symbol=symbol,
                    order_id=order_id,
                    event_type="fill",
                    amount=cash_delta,
                    balance_after=account.cash_balance,
                    timestamp=timestamp,
                )
            )
        self.repository.upsert_account(account)

    def _get_or_create_account(self, strategy_name: str, timestamp: int) -> AccountRecord:
        account = self.repository.get_account(strategy_name)
        if account is not None:
            return account
        return AccountRecord(
            strategy=strategy_name,
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
            self.repository.delete_position(strategy_name, symbol)
            return
        self.repository.upsert_position(
            PositionRecord(
                strategy=strategy_name,
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

    def _open_position_cost_basis(self, strategy_name: str) -> float:
        if not hasattr(self.repository, "get_open_positions"):
            return 0.0
        total = 0.0
        for position in self.repository.get_open_positions(strategy_name):
            value = abs(_float_attr(position, "amount")) * _float_attr(position, "entry_price")
            total += -value if getattr(position, "side") == "short" else value
        return total


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
) -> PositionRecord:
    return PositionRecord(
        strategy=strategy_name,
        symbol=symbol,
        side=side,
        amount=amount,
        entry_price=entry_price,
        mark_price=None,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        leverage=1,
        timestamp=timestamp,
    )


def _fill_price(order: Any) -> float:
    price = order.fill_price if order.fill_price is not None else order.price
    if price is None or price <= 0:
        raise ValueError("filled order requires a positive fill price")
    return float(price)


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
