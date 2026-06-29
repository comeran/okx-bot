from collections.abc import Callable
from dataclasses import dataclass

from src.core.types import AccountSnapshot, PositionSnapshot
from src.data.models import AccountRecord, PositionRecord
from src.exchange.factory import create_okx_adapter


@dataclass(frozen=True)
class LiveStateSyncResult:
    account: AccountRecord
    positions: list[PositionRecord]


class LiveStateSyncService:
    def __init__(self, adapter, repository, timestamp_ms: Callable[[], int]) -> None:
        self.adapter = adapter
        self.repository = repository
        self.timestamp_ms = timestamp_ms

    async def refresh(
        self,
        strategy: str,
        symbols: list[str] | None = None,
    ) -> LiveStateSyncResult:
        account_snapshot = await self.adapter.fetch_account_snapshot()
        position_snapshots = await self.adapter.fetch_position_snapshots(symbols)
        account = self._persist_account(strategy, account_snapshot)
        positions = [self._persist_position(strategy, snapshot) for snapshot in position_snapshots]
        self._delete_stale_positions(strategy, symbols, {position.symbol for position in positions})
        return LiveStateSyncResult(account=account, positions=positions)

    def _persist_account(self, strategy: str, snapshot: AccountSnapshot) -> AccountRecord:
        existing = self.repository.get_account(strategy)
        account = AccountRecord(
            strategy=strategy,
            initial_equity=existing.initial_equity if existing is not None else snapshot.equity,
            cash_balance=snapshot.cash_balance,
            equity=snapshot.equity,
            realized_pnl=snapshot.realized_pnl,
            unrealized_pnl=snapshot.unrealized_pnl,
            daily_pnl=existing.daily_pnl if existing is not None else 0.0,
            fees_paid=existing.fees_paid if existing is not None else 0.0,
            updated_at=snapshot.updated_at or self.timestamp_ms(),
        )
        return self.repository.upsert_account(account)

    def _persist_position(self, strategy: str, snapshot: PositionSnapshot) -> PositionRecord:
        position = PositionRecord(
            strategy=strategy,
            symbol=snapshot.symbol,
            side=snapshot.side.value,
            amount=snapshot.amount,
            entry_price=snapshot.entry_price,
            leverage=snapshot.leverage,
            timestamp=snapshot.updated_at or self.timestamp_ms(),
            mark_price=snapshot.mark_price,
            realized_pnl=snapshot.realized_pnl,
            unrealized_pnl=snapshot.unrealized_pnl,
        )
        return self.repository.upsert_position(position)

    def _delete_stale_positions(
        self,
        strategy: str,
        symbols: list[str] | None,
        synced_symbols: set[str],
    ) -> None:
        requested_symbols = set(symbols) if symbols is not None else None
        for position in self.repository.get_open_positions(strategy):
            if requested_symbols is not None and position.symbol not in requested_symbols:
                continue
            if position.symbol not in synced_symbols:
                self.repository.delete_position(strategy, position.symbol)


async def refresh_okx_live_state(
    exchange,
    repository,
    strategy: str,
    symbols: list[str] | None,
    timestamp_ms: Callable[[], int],
) -> LiveStateSyncResult:
    adapter = create_okx_adapter(exchange)
    try:
        return await LiveStateSyncService(adapter, repository, timestamp_ms).refresh(
            strategy,
            symbols,
        )
    finally:
        await adapter.close()
