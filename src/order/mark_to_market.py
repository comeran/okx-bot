from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.data.models import AccountRecord


@dataclass(frozen=True)
class MarkToMarketUpdate:
    positions: list[Any]
    account: AccountRecord


class PaperMarkToMarketService:
    def __init__(self, repository: Any, initial_equity: float = 100000.0) -> None:
        self.repository = repository
        self.initial_equity = initial_equity

    def mark(
        self,
        strategy_name: str,
        symbol: str,
        mark_price: float,
        timestamp: int,
    ) -> bool:
        return self.mark_update(strategy_name, symbol, mark_price, timestamp) is not None

    def mark_update(
        self,
        strategy_name: str,
        symbol: str,
        mark_price: float,
        timestamp: int,
    ) -> MarkToMarketUpdate | None:
        position = self.repository.get_position(strategy_name, symbol)
        if position is None or _float_attr(position, "amount") == 0.0:
            return None

        position.mark_price = mark_price
        position.unrealized_pnl = _unrealized_pnl(position, mark_price)
        position.timestamp = timestamp
        self.repository.upsert_position(position)

        open_positions = self.repository.get_open_positions(strategy_name)
        account = self._get_or_create_account(strategy_name, timestamp)
        account.unrealized_pnl = sum(
            _float_attr(open_position, "unrealized_pnl") for open_position in open_positions
        )
        account.equity = (
            account.cash_balance
            + self._open_position_cost_basis(open_positions)
            + account.unrealized_pnl
        )
        account.updated_at = timestamp
        self.repository.upsert_account(account)
        return MarkToMarketUpdate(positions=open_positions, account=account)

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

    def _open_position_cost_basis(self, positions: list[Any]) -> float:
        total = 0.0
        for position in positions:
            value = abs(_float_attr(position, "amount")) * _float_attr(position, "entry_price")
            total += -value if getattr(position, "side") == "short" else value
        return total


def _unrealized_pnl(position: Any, mark_price: float) -> float:
    amount = abs(_float_attr(position, "amount"))
    entry_price = _float_attr(position, "entry_price")
    if getattr(position, "side") == "short":
        return (entry_price - mark_price) * amount
    return (mark_price - entry_price) * amount


def _float_attr(obj: Any, name: str) -> float:
    return float(getattr(obj, name, 0.0) or 0.0)
