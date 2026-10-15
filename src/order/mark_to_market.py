from __future__ import annotations

import copy
import logging
import math
from dataclasses import dataclass
from typing import Any

from src.data.models import AccountRecord
from src.order.value import inverse_quote_pnl, signed_position_cost_basis

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MarkToMarketUpdate:
    positions: list[Any]
    account: AccountRecord


class PaperMarkToMarketService:
    def __init__(
        self,
        repository: Any,
        initial_equity: float = 100000.0,
        runtime_mode: str = "paper",
    ) -> None:
        self.repository = repository
        self.initial_equity = initial_equity
        self.runtime_mode = "paper" if runtime_mode == "demo" else runtime_mode
        self.source = "backtest" if self.runtime_mode == "backtest" else "paper"

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
        try:
            normalized_mark_price = float(mark_price)
        except (TypeError, ValueError, OverflowError):
            normalized_mark_price = 0.0
        if not math.isfinite(normalized_mark_price) or normalized_mark_price <= 0.0:
            logger.warning(
                "Ignoring invalid mark price for %s/%s: %r",
                strategy_name,
                symbol,
                mark_price,
            )
            return None

        if hasattr(self.repository, "atomic_accounting"):
            return self.repository.atomic_accounting(
                lambda transaction: self._mark_update(
                    transaction,
                    strategy_name,
                    symbol,
                    normalized_mark_price,
                    timestamp,
                )
            )
        return self._mark_update(
            self.repository,
            strategy_name,
            symbol,
            normalized_mark_price,
            timestamp,
        )

    def _mark_update(
        self,
        repository: Any,
        strategy_name: str,
        symbol: str,
        mark_price: float,
        timestamp: int,
    ) -> MarkToMarketUpdate | None:
        positions = _positions_for_symbol(
            repository,
            strategy_name,
            symbol,
            source=self.source,
            runtime_mode=self.runtime_mode,
        )
        open_symbol_positions = [
            position for position in positions if _float_attr(position, "amount") != 0.0
        ]
        if not open_symbol_positions:
            return None

        for position in open_symbol_positions:
            if timestamp < int(getattr(position, "timestamp", 0) or 0):
                continue
            updated_position = copy.copy(position)
            updated_position.mark_price = mark_price
            updated_position.unrealized_pnl = _unrealized_pnl(updated_position, mark_price)
            updated_position.timestamp = timestamp
            updated_position.source = self.source
            updated_position.runtime_mode = self.runtime_mode
            repository.upsert_position(updated_position)

        try:
            open_positions = repository.get_open_positions(
                strategy_name, source=self.source, runtime_mode=self.runtime_mode
            )
        except TypeError:
            open_positions = [
                position for position in repository.get_open_positions(strategy_name)
                if _has_domain(position, self.source, self.runtime_mode)
            ]
        account = self._get_or_create_account(repository, strategy_name, timestamp)
        account.unrealized_pnl = sum(
            _float_attr(open_position, "unrealized_pnl") for open_position in open_positions
        )
        account.equity = (
            account.cash_balance
            + self._open_position_cost_basis(open_positions)
            + account.unrealized_pnl
        )
        account.updated_at = timestamp
        account = repository.upsert_account(account)
        return MarkToMarketUpdate(positions=open_positions, account=account)

    def _get_or_create_account(
        self, repository: Any, strategy_name: str, timestamp: int
    ) -> AccountRecord:
        try:
            account = repository.get_account(
                strategy_name, source=self.source, runtime_mode=self.runtime_mode
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

    def _open_position_cost_basis(self, positions: list[Any]) -> float:
        return sum(signed_position_cost_basis(position) for position in positions)


def _positions_for_symbol(
    repository: Any,
    strategy_name: str,
    symbol: str,
    *,
    source: str,
    runtime_mode: str,
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


def _unrealized_pnl(position: Any, mark_price: float) -> float:
    if getattr(position, "is_inverse", False):
        return inverse_quote_pnl(position, mark_price)
    amount = abs(_float_attr(position, "amount"))
    entry_price = _float_attr(position, "entry_price")
    if getattr(position, "side") == "short":
        return (entry_price - mark_price) * amount
    return (mark_price - entry_price) * amount


def _float_attr(obj: Any, name: str) -> float:
    return float(getattr(obj, name, 0.0) or 0.0)
