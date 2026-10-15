import logging
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass

from src.core.types import (
    AccountSnapshot,
    PositionSnapshot,
    has_trusted_account_snapshot_provenance,
)
from src.data.models import EXCHANGE_STRATEGY, AccountRecord, PositionRecord
from src.exchange.base import canonical_symbol, is_supported_canonical_symbol
from src.exchange.errors import LiveConfigurationError, MalformedProviderData
from src.exchange.factory import create_okx_adapter

logger = logging.getLogger(__name__)


def _has_live_provenance(record: object) -> bool:
    return (
        str(getattr(record, "source", "unknown") or "unknown").strip().lower()
        == "live_exchange"
        and str(getattr(record, "runtime_mode", "unknown") or "unknown").strip().lower()
        == "live"
    )


def _has_live_position_provenance(record: object) -> bool:
    return _has_live_provenance(record)


def _has_non_live_provenance(record: object) -> bool:
    source = str(getattr(record, "source", "unknown") or "unknown").strip().lower()
    runtime_mode = str(
        getattr(record, "runtime_mode", "unknown") or "unknown"
    ).strip().lower()
    return source in {"paper", "backtest"} or runtime_mode in {"paper", "backtest"}


def _strict_live_account(repository, strategy: str) -> AccountRecord | None:
    try:
        account = repository.get_account(
            strategy,
            source="live_exchange",
            runtime_mode="live",
            include_inactive=False,
        )
    except TypeError:
        account = repository.get_account(strategy)
    return (
        account
        if account is not None
        and getattr(account, "active", True) is not False
        and _has_live_provenance(account)
        else None
    )


def _adapter_market_type(adapter: object) -> str:
    market_type = getattr(adapter, "_default_type", "auto")
    normalized = str(market_type or "auto").strip().lower()
    return normalized or "auto"


def _canonical_symbol_for_adapter(adapter, symbol: object) -> str:
    market_type = _adapter_market_type(adapter)
    mapper = getattr(adapter, "canonical_symbol", None)
    if callable(mapper):
        return str(mapper(symbol) or "").strip().upper()
    return canonical_symbol(symbol, market_type=market_type)


def _normalize_symbols(adapter, symbols: list[str] | None) -> list[str] | None:
    if symbols is None:
        return None
    normalized: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        value = _canonical_symbol_for_adapter(adapter, symbol)
        if not value or not is_supported_canonical_symbol(
            value, market_type=_adapter_market_type(adapter)
        ):
            raise MalformedProviderData("Malformed request: invalid symbol scope")
        if value not in seen:
            normalized.append(value)
            seen.add(value)
    return normalized


@dataclass(frozen=True)
class PositionSnapshotResult:
    positions: list[PositionSnapshot]
    complete_snapshot: bool = False
    snapshot_timestamp: int = 0


@dataclass(frozen=True)
class LiveStateSyncResult:
    account: AccountRecord | None
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
        # Account and position snapshots are exchange-global facts. The caller's
        # strategy only scopes the provider request; it never establishes ownership.
        state_owner = EXCHANGE_STRATEGY
        target_symbols = _normalize_symbols(self.adapter, symbols)
        if target_symbols == []:
            return LiveStateSyncResult(
                account=_strict_live_account(self.repository, state_owner),
                positions=[],
            )

        account_snapshot = await self.adapter.fetch_account_snapshot()
        position_response = await self.adapter.fetch_position_snapshots(target_symbols)
        if isinstance(position_response, PositionSnapshotResult):
            position_snapshots = position_response.positions
            complete_position_snapshot = position_response.complete_snapshot
            position_snapshot_timestamp = position_response.snapshot_timestamp
        else:
            position_snapshots = position_response
            complete_position_snapshot = False
            position_snapshot_timestamp = 0
        if not isinstance(position_snapshots, (list, tuple)):
            raise MalformedProviderData(
                f"Malformed provider position response: {position_response!r}"
            )
        if any(not isinstance(snapshot, PositionSnapshot) for snapshot in position_snapshots):
            raise MalformedProviderData(
                f"Malformed provider position response: {position_snapshots!r}"
            )
        if target_symbols is not None:
            requested_symbols = set(target_symbols)
            position_snapshots = [
                snapshot
                for snapshot in position_snapshots
                if _canonical_symbol_for_adapter(self.adapter, snapshot.symbol)
                in requested_symbols
            ]

        def persist(repository) -> LiveStateSyncResult:
            account = self._persist_account(repository, state_owner, account_snapshot)
            positions = [
                position
                for snapshot in position_snapshots
                for position in [self._persist_position(repository, state_owner, snapshot)]
                if position is not None
            ]
            if complete_position_snapshot and position_snapshots:
                position_timestamps = [
                    max(snapshot.updated_at, snapshot.timestamp)
                    for snapshot in position_snapshots
                ]
                refresh_timestamp = (
                    max(position_timestamps)
                    if all(timestamp > 0 for timestamp in position_timestamps)
                    else 0
                )
            elif complete_position_snapshot and position_snapshot_timestamp > 0:
                refresh_timestamp = position_snapshot_timestamp
            else:
                refresh_timestamp = 0
                logger.warning(
                    "Retaining live positions because provider returned an empty "
                    "position list without a trusted complete snapshot marker"
                )
            self._delete_stale_positions(
                repository,
                state_owner,
                target_symbols,
                {
                    (_canonical_symbol_for_adapter(self.adapter, position.symbol), position.side)
                    for position in positions
                },
                refresh_timestamp,
            )
            return LiveStateSyncResult(account=account, positions=positions)

        atomic_sync = getattr(self.repository, "atomic_private_sync", None)
        if callable(atomic_sync):
            return atomic_sync(persist)
        return persist(self.repository)

    def _persist_account(
        self, repository, strategy: str, snapshot: AccountSnapshot
    ) -> AccountRecord | None:
        try:
            existing = repository.get_account(
                strategy,
                source="live_exchange",
                runtime_mode="live",
                include_inactive=True,
            )
        except TypeError:
            existing = repository.get_account(strategy)
            if existing is not None and not _has_live_provenance(existing):
                existing = None
        if not has_trusted_account_snapshot_provenance(snapshot):
            logger.warning(
                "Skipping account update for %s without trusted timestamp; "
                "account state cannot be used as current live risk state",
                strategy,
            )
            return existing
        if existing is None:
            logger.info(
                "No strict live account exists for %s; creating a new live row while "
                "retaining non-live rows for audit",
                strategy,
            )
        account = AccountRecord(
            strategy=strategy,
            source="live_exchange",
            runtime_mode="live",
            initial_equity=(
                existing.initial_equity
                if existing is not None and existing.initial_equity > 0.0
                else snapshot.equity
            ),
            cash_balance=snapshot.cash_balance,
            available_balance=snapshot.available_balance,
            equity=snapshot.equity,
            realized_pnl=snapshot.realized_pnl,
            unrealized_pnl=snapshot.unrealized_pnl,
            daily_pnl=existing.daily_pnl if existing is not None else 0.0,
            fees_paid=existing.fees_paid if existing is not None else 0.0,
            updated_at=snapshot.updated_at or snapshot.timestamp,
            assets=[asdict(asset) for asset in snapshot.assets],
        )
        return repository.upsert_account(account)

    def _persist_position(
        self, repository, strategy: str, snapshot: PositionSnapshot
    ) -> PositionRecord | None:
        symbol = _canonical_symbol_for_adapter(self.adapter, snapshot.symbol)
        side = snapshot.side.value
        snapshot_timestamp = max(snapshot.updated_at, snapshot.timestamp)
        if snapshot_timestamp <= 0:
            logger.warning(
                "Skipping position update for %s/%s without trusted timestamp",
                symbol,
                side,
            )
            try:
                return repository.get_position(
                    strategy,
                    symbol,
                    side,
                    source="live_exchange",
                    runtime_mode="live",
                    include_inactive=True,
                )
            except TypeError:
                existing = repository.get_position(strategy, symbol, side)
                return (
                    existing
                    if existing is not None and _has_live_position_provenance(existing)
                    else None
                )
        try:
            existing = repository.get_position(
                strategy,
                symbol,
                side,
                source="live_exchange",
                runtime_mode="live",
                include_inactive=True,
            )
        except TypeError:
            existing = repository.get_position(strategy, symbol, side)
            if existing is not None and not _has_live_position_provenance(existing):
                existing = None
        if existing is None:
            get_positions = getattr(repository, "get_positions", None)
            if callable(get_positions):
                try:
                    candidates = get_positions(strategy, include_inactive=True)
                except TypeError:
                    candidates = get_positions(strategy)
                existing = next(
                    (
                        position
                        for position in candidates
                        if _has_live_position_provenance(position)
                        and position.side == side
                        and _canonical_symbol_for_adapter(self.adapter, position.symbol)
                        == symbol
                    ),
                    None,
                )
        if snapshot_timestamp <= 0:
            logger.warning(
                "Skipping position update for %s/%s without trusted timestamp",
                symbol,
                side,
            )
            return existing
        position = PositionRecord(
            id=existing.id if existing is not None else None,
            strategy=strategy,
            source="live_exchange",
            runtime_mode="live",
            active=True,
            stale_at=None,
            # Preserve a pre-existing legacy alias; canonical symbols are only used
            # for matching so live sync never silently migrates or duplicates rows.
            symbol=existing.symbol if existing is not None else symbol,
            side=snapshot.side.value,
            amount=snapshot.amount,
            entry_price=snapshot.entry_price,
            leverage=snapshot.leverage,
            timestamp=snapshot_timestamp,
            mark_price=snapshot.mark_price,
            realized_pnl=snapshot.realized_pnl,
            unrealized_pnl=snapshot.unrealized_pnl,
            contracts=snapshot.contracts,
            quantity=snapshot.quantity,
            quantity_unit=snapshot.quantity_unit,
            notional_value=snapshot.notional_value,
            is_inverse=snapshot.is_inverse,
            contract_size=snapshot.contract_size,
        )
        return repository.upsert_position(position)

    def _delete_stale_positions(
        self,
        repository,
        strategy: str,
        symbols: list[str] | None,
        synced_positions: set[tuple[str, str]],
        refresh_timestamp: int,
    ) -> None:
        requested_symbols = (
            {
                _canonical_symbol_for_adapter(self.adapter, symbol)
                for symbol in symbols
            }
            if symbols is not None
            else None
        )
        try:
            open_positions = repository.get_open_positions(
                strategy, source="live_exchange", runtime_mode="live"
            )
        except TypeError:
            open_positions = repository.get_open_positions(strategy)
        for position in open_positions:
            if not _has_live_position_provenance(position) or not position.active:
                continue
            position_symbol = _canonical_symbol_for_adapter(self.adapter, position.symbol)
            if position.symbol != position_symbol:
                logger.info(
                    "Retaining legacy position alias %s/%s for read-only audit",
                    position.symbol,
                    position.side,
                )
                if (
                    requested_symbols is not None
                    and position_symbol in requested_symbols
                    and (position_symbol, position.side) not in synced_positions
                    and refresh_timestamp > 0
                    and refresh_timestamp >= position.timestamp
                ):
                    mark_stale = getattr(repository, "mark_position_stale", None)
                    if callable(mark_stale):
                        try:
                            mark_stale(
                                strategy,
                                position.symbol,
                                position.side,
                                refresh_timestamp,
                                source="live_exchange",
                                runtime_mode="live",
                            )
                        except TypeError:
                            mark_stale(
                                strategy,
                                position.symbol,
                                position.side,
                                refresh_timestamp,
                            )
                    else:
                        position.active = False
                        position.stale_at = refresh_timestamp
                        repository.upsert_position(position)
                continue
            if requested_symbols is not None and position_symbol not in requested_symbols:
                continue
            if (position_symbol, position.side) in synced_positions:
                continue
            if refresh_timestamp <= 0 or refresh_timestamp < position.timestamp:
                logger.warning(
                    "Retaining position %s/%s because refresh provenance %s does not "
                    "cover persisted timestamp %s",
                    position.symbol,
                    position.side,
                    refresh_timestamp,
                    position.timestamp,
                )
                continue
            mark_stale = getattr(repository, "mark_position_stale", None)
            if callable(mark_stale):
                try:
                    mark_stale(
                        strategy,
                        position.symbol,
                        position.side,
                        refresh_timestamp,
                        source="live_exchange",
                        runtime_mode="live",
                    )
                except TypeError:
                    mark_stale(
                        strategy,
                        position.symbol,
                        position.side,
                        refresh_timestamp,
                    )
            else:
                archive_position = getattr(repository, "upsert_position", None)
                if callable(archive_position):
                    position.active = False
                    position.stale_at = refresh_timestamp
                    archive_position(position)
                else:
                    logger.warning(
                        "Cannot archive stale position %s/%s: repository lacks "
                        "mark_position_stale and upsert_position; retaining it",
                        position.symbol,
                        position.side,
                    )


async def refresh_okx_live_state(
    exchange,
    repository,
    strategy: str,
    symbols: list[str] | None,
    timestamp_ms: Callable[[], int],
) -> LiveStateSyncResult:
    try:
        adapter = create_okx_adapter(exchange)
    except ValueError as exc:
        raise LiveConfigurationError(str(exc)) from exc
    try:
        return await LiveStateSyncService(adapter, repository, timestamp_ms).refresh(
            strategy,
            symbols,
        )
    finally:
        root_error_active = sys.exc_info()[0] is not None
        try:
            await adapter.close()
        except Exception:
            if root_error_active:
                logger.warning(
                    "Failed to close live-state adapter after provider error", exc_info=True
                )
            else:
                raise
