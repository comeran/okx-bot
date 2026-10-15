from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Protocol

from ccxt.base.errors import (
    AuthenticationError,
    DDoSProtection,
    ExchangeNotAvailable,
    NetworkError,
    PermissionDenied,
    RateLimitExceeded,
    RequestTimeout,
)

from src.core.types import (
    AccountSnapshot,
    ExchangeOrderSnapshot,
    ExchangeTradeSnapshot,
    OrderSide,
    OrderStatus,
    execution_type_family,
    has_trusted_account_snapshot_provenance,
    stable_blank_trade_fingerprint,
)
from src.data.models import (
    EXCHANGE_STRATEGY,
    AccountRecord,
    OrderRecord,
    TradeRecord,
)
from src.exchange.base import canonical_symbol, is_supported_canonical_symbol
from src.exchange.errors import MalformedProviderData

PRIVATE_SYNC_DIVERGENCE = "private_sync_divergence"
logger = logging.getLogger(__name__)

RiskEventNotifier = Callable[[dict[str, object]], Awaitable[None]]


class PrivateSyncAdapter(Protocol):
    async def fetch_account_snapshot(self) -> AccountSnapshot: ...

    async def fetch_open_order_snapshots(
        self,
        symbols: list[str] | None = None,
    ) -> list[ExchangeOrderSnapshot]: ...

    async def fetch_recent_trade_snapshots(
        self,
        symbols: list[str] | None = None,
        since: int | None = None,
        limit: int = 100,
    ) -> list[ExchangeTradeSnapshot]: ...


@dataclass(frozen=True)
class PrivateSyncResult:
    account_upserted: int
    orders_upserted: int
    trades_upserted: int
    trades_status: str
    risk_events_saved: int
    kill_switch_engaged: bool
    divergences: list[dict[str, object]]

    def as_response(self) -> dict[str, object]:
        return asdict(self)


def current_timestamp_ms() -> int:
    return int(time.time() * 1000)


def _adapter_market_type(adapter: object | None) -> str:
    market_type = getattr(adapter, "_default_type", "auto")
    normalized = str(market_type or "auto").strip().lower()
    return normalized or "auto"


def _canonical_symbol_for_adapter(adapter, symbol: object) -> str:
    market_type = _adapter_market_type(adapter)
    mapper = getattr(adapter, "canonical_symbol", None)
    if callable(mapper):
        return str(mapper(symbol) or "").strip().upper()
    return canonical_symbol(symbol, market_type=market_type)


def _normalize_symbols(
    symbols: list[str] | None, adapter: PrivateSyncAdapter | None = None
) -> list[str] | None:
    if symbols is None:
        return None
    normalized: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        market_type = _adapter_market_type(adapter)
        value = (
            _canonical_symbol_for_adapter(adapter, symbol)
            if adapter is not None
            else canonical_symbol(symbol, market_type="auto")
        )
        if not value or not is_supported_canonical_symbol(
            value, market_type=market_type
        ):
            raise MalformedProviderData("Malformed request: invalid symbol scope")
        if value not in seen:
            normalized.append(value)
            seen.add(value)
    return normalized


def _canonical_symbol_for_comparison(symbol: object) -> str:
    return canonical_symbol(symbol, market_type="auto")


def _symbols_match(left: object, right: object) -> bool:
    left_value = _canonical_symbol_for_comparison(left)
    right_value = _canonical_symbol_for_comparison(right)
    return bool(left_value and left_value == right_value)


def _in_scope(symbol: object, target_symbols: list[str] | None) -> bool:
    return target_symbols is None or any(
        _symbols_match(symbol, target) for target in target_symbols
    )


def _strict_live_orders(repository) -> list[OrderRecord]:
    """Reload only explicitly live orders; legacy rows remain audit-only."""
    try:
        orders = repository.get_orders(source="live_exchange", runtime_mode="live")
    except TypeError:
        orders = repository.get_orders()
    return [order for order in orders if _is_live_owned(order)]


async def sync_private_state(
    repository,
    adapter: PrivateSyncAdapter,
    *,
    symbols: list[str] | None = None,
    since: int | None = None,
    limit: int = 100,
    timestamp_ms: Callable[[], int] = current_timestamp_ms,
    risk_event_notifier: RiskEventNotifier | None = None,
) -> PrivateSyncResult:
    target_symbols = _normalize_symbols(symbols, adapter)
    if target_symbols == []:
        return PrivateSyncResult(
            account_upserted=0,
            orders_upserted=0,
            trades_upserted=0,
            trades_status="available",
            risk_events_saved=0,
            kill_switch_engaged=_kill_switch_is_engaged(repository),
            divergences=[],
        )

    account = await adapter.fetch_account_snapshot()
    exchange_orders = await adapter.fetch_open_order_snapshots(target_symbols)
    _validate_order_snapshots(exchange_orders)
    trades_available = True
    complete_trade_page = False
    complete_trade_since = None
    try:
        exchange_trades = await adapter.fetch_recent_trade_snapshots(
            target_symbols, since=since, limit=limit
        )
        if not isinstance(exchange_trades, (list, tuple)):
            raise MalformedProviderData("Malformed provider recent-trade response")
        _validate_trade_snapshots(exchange_trades)
        _validate_trade_identities(exchange_trades)
        complete_trade_page = bool(
            getattr(exchange_trades, "complete", False)
            and getattr(exchange_trades, "mode", None) in {"since", "cumulative"}
        )
        complete_trade_since = (
            since
            if complete_trade_page
            and since is not None
            and getattr(exchange_trades, "mode", None) == "since"
            and getattr(exchange_trades, "since", None) == since
            else None
        )
    except MalformedProviderData:
        logger.error(
            "Recent trades provider data is malformed; failing closed", exc_info=True
        )
        raise
    except (AuthenticationError, PermissionDenied):
        logger.error(
            "Recent trades provider credentials were rejected; failing closed",
            exc_info=True,
        )
        raise
    except (
        NetworkError,
        RequestTimeout,
        ExchangeNotAvailable,
        DDoSProtection,
        RateLimitExceeded,
        TimeoutError,
        ConnectionError,
    ):
        logger.warning(
            "trades_unavailable: recent trades provider failed; preserving safe "
            "account/open-order state and skipping trade-dependent reconciliation",
            exc_info=True,
        )
        trades_available = False
        exchange_trades = []
        complete_trade_page = False
        complete_trade_since = None

    def persist_consistently(transaction):
        try:
            local_orders = list(
                transaction.get_orders(source="live_exchange", runtime_mode="live")
            )
        except TypeError:
            local_orders = list(transaction.get_orders())
        local_orders = [order for order in local_orders if _is_live_owned(order)]
        if target_symbols is not None:
            local_orders = [
                order for order in local_orders if _in_scope(order.symbol, target_symbols)
            ]
            scoped_exchange_orders = [
                snapshot
                for snapshot in exchange_orders
                if _in_scope(snapshot.symbol, target_symbols)
            ]
            scoped_exchange_trades = [
                snapshot
                for snapshot in exchange_trades
                if _in_scope(snapshot.symbol, target_symbols)
            ]
        else:
            scoped_exchange_orders = exchange_orders
            scoped_exchange_trades = exchange_trades
        local_by_exchange_id, local_by_client_id = _local_order_indexes(local_orders)
        return _persist_private_state(
            transaction,
            account,
            scoped_exchange_orders,
            scoped_exchange_trades,
            local_orders,
            local_by_exchange_id,
            local_by_client_id,
            timestamp_ms,
            trades_available=trades_available,
            target_symbols=target_symbols,
            complete_trade_page=complete_trade_page,
            complete_trade_since=complete_trade_since,
        )

    atomic_sync = getattr(repository, "atomic_private_sync", None)
    if callable(atomic_sync):
        result, notified_divergences = atomic_sync(persist_consistently)
    else:
        result, notified_divergences = persist_consistently(repository)

    if risk_event_notifier is not None:
        for divergence in notified_divergences:
            if divergence.get("severity") != "high":
                continue
            try:
                await risk_event_notifier(divergence)
            except Exception:
                pass
    return result


def _kill_switch_is_engaged(repository) -> bool:
    getter = getattr(repository, "get_kill_switch", None)
    if callable(getter):
        return bool(getattr(getter(), "engaged", False))
    return bool(getattr(repository, "kill_switch", False))


def _persist_private_state(
    repository,
    account: AccountSnapshot,
    exchange_orders: list[ExchangeOrderSnapshot],
    exchange_trades: list[ExchangeTradeSnapshot],
    local_orders: list[OrderRecord],
    local_by_exchange_id: dict[str, OrderRecord],
    local_by_client_id: dict[str, OrderRecord],
    timestamp_ms: Callable[[], int],
    trade_conflicts: list[tuple[ExchangeTradeSnapshot, TradeRecord, list[str]]] | None = None,
    trades_available: bool = True,
    target_symbols: list[str] | None = None,
    complete_trade_page: bool = False,
    complete_trade_since: int | None = None,
) -> tuple[PrivateSyncResult, list[dict[str, object]]]:
    if target_symbols is not None:
        exchange_orders = [
            snapshot for snapshot in exchange_orders if _in_scope(snapshot.symbol, target_symbols)
        ]
        exchange_trades = [
            snapshot for snapshot in exchange_trades if _in_scope(snapshot.symbol, target_symbols)
        ]
    # Persist matched open-order snapshots first. This completes a local
    # order's external identity before exchange-only trade rows are matched;
    # unmatched provider orders remain available for divergence detection.
    orders_upserted = 0
    created_snapshot_order_ids: set[str] = set()
    unmatched_exchange_snapshot_ids: set[int] = set()
    source_identity_conflicts: list[tuple[str, str, str]] = []
    trade_source_identity_conflicts: list[ExchangeTradeSnapshot] = []
    source_identity_conflict_snapshot_ids: set[int] = set()
    for snapshot in exchange_orders:
        if _non_live_order_identity_conflict(snapshot, local_orders):
            source_identity_conflicts.append(
                (
                    snapshot.symbol,
                    snapshot.exchange_order_id,
                    snapshot.client_order_id,
                )
            )
            unmatched_exchange_snapshot_ids.add(id(snapshot))
            source_identity_conflict_snapshot_ids.add(id(snapshot))
            logger.error(
                "Rejecting live order snapshot whose external identity belongs to a "
                "non-live record: "
                "exchange_order_id=%s client_order_id=%s",
                snapshot.exchange_order_id,
                snapshot.client_order_id,
            )
            continue
        local = _match_order(snapshot, local_by_exchange_id, local_by_client_id)
        if local is None:
            unmatched_exchange_snapshot_ids.add(id(snapshot))
        if local is None:
            if _order_identity_conflicts(
                snapshot, local_by_exchange_id, local_by_client_id
            ):
                continue
            # Establish the provider identity before trades are persisted. Otherwise
            # a same-round client-only trade is forced into an exchange placeholder
            # and cannot be rebound to the newly discovered open order safely.
            created_order = _order_record(snapshot, None)
            repository.upsert_order(created_order)
            created_snapshot_order_ids.add(created_order.order_id)
            orders_upserted += 1
            continue
        repository.upsert_order(
            reconcile_order(
                local,
                snapshot,
                _same_round_trade_records(
                    snapshot,
                    local,
                    exchange_trades if trades_available else [],
                    local_by_exchange_id,
                    local_by_client_id,
                ),
                complete_trade_page=trades_available and complete_trade_page,
            )
        )
        orders_upserted += 1

    local_orders = _strict_live_orders(repository)
    if target_symbols is not None:
        local_orders = [
            order for order in local_orders if _in_scope(order.symbol, target_symbols)
        ]
    local_by_exchange_id, local_by_client_id = _local_order_indexes(local_orders)
    if trades_available and hasattr(repository, "rebind_trades_to_orders"):
        repository.rebind_trades_to_orders(local_orders)

    trade_conflicts: list[tuple[ExchangeTradeSnapshot, TradeRecord, list[str]]] = []
    if trades_available:
        exchange_trades = _deduplicate_trade_snapshots(
            repository,
            exchange_trades,
            local_by_exchange_id,
            local_by_client_id,
            trade_conflicts,
            target_symbols=target_symbols,
        )

    persisted_trade_count = 0
    divergences: list[dict[str, object]] = []
    divergence_keys: set[str] = set()
    for snapshot in exchange_trades:
        if _non_live_order_identity_conflict(snapshot, local_orders):
            trade_source_identity_conflicts.append(snapshot)
            logger.error(
                "Rejecting live trade snapshot whose order identity belongs to a non-live record: "
                "exchange_order_id=%s client_order_id=%s",
                snapshot.exchange_order_id,
                snapshot.client_order_id,
            )
            continue
        local = _match_trade(snapshot, local_by_exchange_id, local_by_client_id)
        if local is None and _trade_identity_conflicts(
            snapshot, local_by_exchange_id, local_by_client_id
        ):
            logger.warning(
                "Skipping trade snapshot with unsafe external identity; no ownership assigned: "
                "exchange_order_id=%s, client_order_id=%s",
                snapshot.exchange_order_id,
                snapshot.client_order_id,
            )
            continue
        repository.upsert_trade(_trade_record(snapshot, local))
        persisted_trade_count += 1

    # Collect both order and trade identity conflicts before generating events so
    # their persistence, kill-switch decision, and notifications share one commit.
    for symbol, exchange_order_id, client_order_id in source_identity_conflicts:
        _append_divergence(
            divergences,
            divergence_keys,
            timestamp_ms,
            strategy=EXCHANGE_STRATEGY,
            divergence_type="live_source_identity_conflict",
            reason=(
                "Live exchange order identity collides with a non-live local record; "
                "reconciliation was rejected"
            ),
            symbol=_canonical_symbol_for_comparison(symbol),
            order_id=_external_order_id(exchange_order_id, client_order_id),
            exchange_order_id=exchange_order_id,
            client_order_id=client_order_id,
        )
    for snapshot, persisted, conflicting_fields in (
        trade_conflicts if trades_available else []
    ):
        divergence_type = (
            "live_source_identity_conflict"
            if "source" in conflicting_fields
            else "conflicting_exchange_trade_identity"
            if any(
                field in {"symbol", "side", "exchange_order_id", "client_order_id", "order_id"}
                for field in conflicting_fields
            )
            else "conflicting_exchange_trade_facts"
        )
        _append_divergence(
            divergences,
            divergence_keys,
            timestamp_ms,
            strategy=persisted.strategy,
            divergence_type=divergence_type,
            reason=(
                "OKX replay for an existing exchange trade ID conflicts with persisted "
                f"{', '.join(conflicting_fields)}"
            ),
            symbol=_canonical_symbol_for_comparison(snapshot.symbol),
            order_id=persisted.order_id,
            exchange_order_id=snapshot.exchange_order_id,
            client_order_id=snapshot.client_order_id,
            exchange_trade_id=snapshot.exchange_trade_id,
            event_identity=f"exchange_trade_id:{snapshot.exchange_trade_id}",
        )
    for snapshot in trade_source_identity_conflicts:
        _append_divergence(
            divergences,
            divergence_keys,
            timestamp_ms,
            strategy=EXCHANGE_STRATEGY,
            divergence_type="live_source_identity_conflict",
            reason=(
                "Live exchange trade identity collides with a non-live local record; "
                "reconciliation was rejected"
            ),
            symbol=_canonical_symbol_for_comparison(snapshot.symbol),
            order_id=_external_order_id(
                snapshot.exchange_order_id, snapshot.client_order_id
            ),
            exchange_order_id=snapshot.exchange_order_id,
            client_order_id=snapshot.client_order_id,
            exchange_trade_id=snapshot.exchange_trade_id,
            event_identity=f"exchange_trade_id:{snapshot.exchange_trade_id}",
        )

    persisted_trades = _get_trades(repository) if trades_available else []
    if target_symbols is not None:
        persisted_trades = [
            trade for trade in persisted_trades if _in_scope(trade.symbol, target_symbols)
        ]

    if trades_available:
        divergences.extend(_find_divergences(
            local_orders,
            exchange_orders,
            exchange_trades,
            persisted_trades,
            local_by_exchange_id,
            local_by_client_id,
            timestamp_ms,
            target_symbols,
            unmatched_exchange_snapshot_ids,
        ))
    high_risk_divergences = [
        divergence for divergence in divergences if divergence.get("severity") == "high"
    ]
    if high_risk_divergences:
        repository.set_kill_switch(
            True, "Private sync detected high-risk divergence", timestamp_ms()
        )
    risk_events_saved = 0
    newly_saved_divergences: list[dict[str, object]] = []
    save_with_created = getattr(repository, "save_risk_event_with_created", None)
    for divergence in divergences:
        if callable(save_with_created):
            try:
                _, created = save_with_created(
                    divergence,
                    source="live_exchange",
                    runtime_mode="live",
                )
            except TypeError:
                _, created = save_with_created(divergence)
        else:
            try:
                repository.save_risk_event(
                    divergence,
                    source="live_exchange",
                    runtime_mode="live",
                )
            except TypeError:
                repository.save_risk_event(divergence)
            created = True
        divergence.pop("_event_key_domain_scoped", None)
        if created:
            risk_events_saved += 1
            newly_saved_divergences.append(divergence)

    account_upserted = 0
    if has_trusted_account_snapshot_provenance(account):
        repository.upsert_account(_account_record(account))
        account_upserted = 1
    else:
        get_account = getattr(repository, "get_account", None)
        existing_account = get_account(EXCHANGE_STRATEGY) if callable(get_account) else None
        logger.warning(
            "Skipping account update for %s without trusted timestamp; "
            "account state cannot be used as current live risk state",
            EXCHANGE_STRATEGY,
        )
        if existing_account is not None:
            logger.info("Preserving existing account state for %s", EXCHANGE_STRATEGY)
    for snapshot in exchange_orders:
        if id(snapshot) in source_identity_conflict_snapshot_ids:
            continue
        local = _match_order(snapshot, local_by_exchange_id, local_by_client_id)
        if local is not None:
            order = reconcile_order(
                local, snapshot, _trades_for_order(persisted_trades, local),
                complete_trade_page=complete_trade_page,
            )
        else:
            new_order = _order_record(snapshot, None)
            order = reconcile_order(
                new_order, None, _trades_for_snapshot(persisted_trades, snapshot),
                complete_trade_page=complete_trade_page,
            )
        if local is None:
            repository.upsert_order(order)
            orders_upserted += 1
        elif _order_state_changed(local, order):
            if local.order_id in created_snapshot_order_ids:
                for field, value in order.model_dump(exclude={"id"}).items():
                    setattr(local, field, value)
                for field in (
                    "filled_amount",
                    "remaining_amount",
                    "snapshot_filled_amount",
                    "snapshot_updated_at",
                ):
                    setattr(local, field, getattr(order, field))
            else:
                repository.upsert_order(order)
    for local in local_orders:
        if any(
            _match_order(snapshot, local_by_exchange_id, local_by_client_id) is local
            for snapshot in exchange_orders
        ):
            continue
        trades = _trades_for_order(persisted_trades, local)
        if trades:
            reconciled = reconcile_order(
                local, None, trades,
                complete_trade_page=complete_trade_page,
                complete_trade_since=complete_trade_since,
            )
            if _order_state_changed(local, reconciled):
                repository.upsert_order(reconciled)
                orders_upserted += 1

    result = PrivateSyncResult(
        account_upserted=account_upserted,
        orders_upserted=orders_upserted,
        trades_upserted=persisted_trade_count,
        trades_status="available" if trades_available else "unavailable",
        risk_events_saved=risk_events_saved,
        kill_switch_engaged=_kill_switch_is_engaged(repository),
        divergences=divergences,
    )
    return result, newly_saved_divergences


def _validate_order_snapshots(snapshots: object) -> None:
    if not isinstance(snapshots, list):
        raise MalformedProviderData("Malformed provider open-order response")
    for snapshot in snapshots:
        if not isinstance(snapshot, ExchangeOrderSnapshot):
            raise MalformedProviderData(
                "Malformed provider open-order response: rows must be order snapshots"
            )


def _validate_trade_snapshots(snapshots: list[ExchangeTradeSnapshot]) -> None:
    for snapshot in snapshots:
        if not isinstance(snapshot, ExchangeTradeSnapshot):
            raise MalformedProviderData(
                "Malformed provider recent-trade response: rows must be trade snapshots"
            )
        if (
            not isinstance(snapshot.symbol, str)
            or not snapshot.symbol.strip()
            or not _strictly_positive_finite(snapshot.amount)
            or not _strictly_positive_finite(snapshot.price)
            or not isinstance(snapshot.side, OrderSide)
            or not _safe_trade_timestamp(snapshot.timestamp)
        ):
            raise MalformedProviderData(
                f"Malformed provider recent-trade row (invalid critical fields): {snapshot!r}"
            )


def _strictly_positive_finite(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value > 0
    )


def _safe_trade_timestamp(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 2**63 - 1


def _validate_trade_identities(snapshots: list[ExchangeTradeSnapshot]) -> None:
    for snapshot in snapshots:
        if any(
            isinstance(identifier, str) and identifier.strip()
            for identifier in (
                snapshot.exchange_trade_id,
                snapshot.exchange_order_id,
                snapshot.client_order_id,
            )
        ):
            continue
        logger.error(
            "Malformed unmatched exchange trade has no external identity; failing closed: %s",
            snapshot,
        )
        raise MalformedProviderData(
            f"Malformed provider recent-trade row (missing external identity): {snapshot!r}"
        )


def _is_live_owned(record: object) -> bool:
    """Return whether a persisted record can be matched to live exchange state.

    Live ownership requires an explicit, non-conflicting provenance pair.
    Unknown and legacy rows remain audit-only history and cannot be rebound to
    current live exchange state.
    """
    source = str(getattr(record, "source", "") or "").strip().lower()
    runtime_mode = str(getattr(record, "runtime_mode", "") or "").strip().lower()
    strategy = str(getattr(record, "strategy", "") or "").strip()
    if source == "live_exchange" and runtime_mode == "live":
        return True

    logger.warning(
        "Rejecting live ownership for record with non-matching provenance: "
        "strategy=%s source=%s runtime_mode=%s",
        strategy,
        source or "unknown",
        runtime_mode or "unknown",
    )
    return False


def _local_order_indexes(
    orders: list[OrderRecord],
) -> tuple[dict[str, OrderRecord], dict[str, OrderRecord]]:
    live_orders = [order for order in orders if _is_live_owned(order)]
    return (
        {
            order.exchange_order_id: order
            for order in live_orders
            if getattr(order, "exchange_order_id", "")
        },
        {
            order.client_order_id: order
            for order in live_orders
            if getattr(order, "client_order_id", "")
        },
    )


def _non_live_order_identity_conflict(
    snapshot: ExchangeOrderSnapshot, orders: list[OrderRecord]
) -> bool:
    return any(
        not _is_live_owned(order)
        and (
            snapshot.exchange_order_id
            and snapshot.exchange_order_id == getattr(order, "exchange_order_id", "")
            or snapshot.client_order_id
            and snapshot.client_order_id == getattr(order, "client_order_id", "")
        )
        for order in orders
    )


def _get_trades(repository) -> list[TradeRecord]:
    trades = list(repository.get_trades()) if hasattr(repository, "get_trades") else []
    return [trade for trade in trades if _is_live_owned(trade)]


def _trades_for_order(trades: list[TradeRecord], order: OrderRecord | None) -> list[TradeRecord]:
    if order is None:
        return []
    return [trade for trade in trades if trade.order_id == order.order_id]


def _trades_for_snapshot(
    trades: list[TradeRecord], snapshot: ExchangeOrderSnapshot
) -> list[TradeRecord]:
    return [
        trade
        for trade in trades
        if (
            snapshot.exchange_order_id
            and trade.exchange_order_id == snapshot.exchange_order_id
        )
        or (
            snapshot.client_order_id
            and trade.client_order_id == snapshot.client_order_id
        )
    ]


def _same_round_trade_records(
    snapshot: ExchangeOrderSnapshot,
    local: OrderRecord,
    exchange_trades: list[ExchangeTradeSnapshot],
    local_by_exchange_id: dict[str, OrderRecord],
    local_by_client_id: dict[str, OrderRecord],
) -> list[TradeRecord]:
    snapshot_by_exchange_id = dict(local_by_exchange_id)
    snapshot_by_client_id = dict(local_by_client_id)
    if snapshot.exchange_order_id:
        snapshot_by_exchange_id[snapshot.exchange_order_id] = local
    if snapshot.client_order_id:
        snapshot_by_client_id[snapshot.client_order_id] = local
    return _unique_trade_records([
        _trade_record(trade, local)
        for trade in exchange_trades
        if _match_trade(trade, snapshot_by_exchange_id, snapshot_by_client_id) is local
    ])


def _order_state_changed(previous: OrderRecord, current: OrderRecord) -> bool:
    fields = (
        "exchange_order_id", "client_order_id", "status", "fill_price", "timestamp",
        "amount", "updated_at", "filled_amount", "remaining_amount",
        "snapshot_filled_amount", "snapshot_updated_at", "execution_type_family",
    )
    return any(getattr(previous, field) != getattr(current, field) for field in fields)


def _trade_fingerprint(
    snapshot: ExchangeTradeSnapshot,
    *,
    order_id: str = "",
    strategy: str = "",
) -> str | None:
    del order_id, strategy
    return stable_blank_trade_fingerprint(
        exchange_order_id=snapshot.exchange_order_id,
        client_order_id=snapshot.client_order_id,
        symbol=_canonical_symbol_for_comparison(snapshot.symbol),
        side=snapshot.side,
        amount=snapshot.amount,
        price=snapshot.price,
        fee=snapshot.fee,
        timestamp=snapshot.timestamp,
    )


def _trade_record_fingerprint(trade: TradeRecord) -> str | None:
    if not trade.exchange_order_id and not trade.client_order_id:
        return None
    return getattr(trade, "blank_trade_fingerprint", "") or stable_blank_trade_fingerprint(
        exchange_order_id=trade.exchange_order_id,
        client_order_id=trade.client_order_id,
        symbol=_canonical_symbol_for_comparison(trade.symbol),
        side=trade.side,
        amount=trade.amount,
        price=trade.price,
        fee=trade.fee,
        timestamp=trade.timestamp,
    )


def _trade_fingerprint_aliases(snapshot: ExchangeTradeSnapshot) -> set[str]:
    if not snapshot.exchange_order_id and not snapshot.client_order_id:
        return set()
    fields = {
        "symbol": _canonical_symbol_for_comparison(snapshot.symbol),
        "side": snapshot.side,
        "amount": snapshot.amount,
        "price": snapshot.price,
        "fee": snapshot.fee,
        "timestamp": snapshot.timestamp,
    }
    fingerprints = {
        _trade_fingerprint(snapshot),
        stable_blank_trade_fingerprint(
            exchange_order_id=snapshot.exchange_order_id,
            client_order_id="",
            **fields,
        ),
        stable_blank_trade_fingerprint(
            exchange_order_id="",
            client_order_id=snapshot.client_order_id,
            **fields,
        ),
    }
    return {fingerprint for fingerprint in fingerprints if fingerprint is not None}


def _trade_record_fingerprint_aliases(trade: TradeRecord) -> set[str]:
    if not trade.exchange_order_id and not trade.client_order_id:
        return set()
    fields = {
        "symbol": _canonical_symbol_for_comparison(trade.symbol),
        "side": trade.side,
        "amount": trade.amount,
        "price": trade.price,
        "fee": trade.fee,
        "timestamp": trade.timestamp,
    }
    fingerprints = {
        _trade_record_fingerprint(trade),
        stable_blank_trade_fingerprint(
            exchange_order_id=trade.exchange_order_id,
            client_order_id="",
            **fields,
        ),
        stable_blank_trade_fingerprint(
            exchange_order_id="",
            client_order_id=trade.client_order_id,
            **fields,
        ),
    }
    return {fingerprint for fingerprint in fingerprints if fingerprint is not None}


def _trade_external_ids_are_compatible(
    snapshot: ExchangeTradeSnapshot,
    trade: TradeRecord,
) -> bool:
    return all(
        not getattr(snapshot, field)
        or not getattr(trade, field)
        or getattr(snapshot, field) == getattr(trade, field)
        for field in ("exchange_order_id", "client_order_id")
    )


def _trade_snapshot_conflicts(
    snapshot: ExchangeTradeSnapshot,
    persisted: TradeRecord,
    local_order: OrderRecord | None,
) -> list[str]:
    conflicts = [
        field
        for field in ("side", "amount", "price", "fee", "timestamp")
        if getattr(snapshot, field) != getattr(persisted, field)
    ]
    if not _symbols_match(snapshot.symbol, persisted.symbol):
        conflicts.append("symbol")
    for field in ("exchange_order_id", "client_order_id"):
        snapshot_id = getattr(snapshot, field)
        persisted_id = getattr(persisted, field)
        if snapshot_id and persisted_id and snapshot_id != persisted_id:
            conflicts.append(field)
    if local_order is not None and persisted.order_id != local_order.order_id:
        conflicts.append("order_id")
    return conflicts


def _deduplicate_trade_snapshots(
    repository,
    snapshots: list[ExchangeTradeSnapshot],
    local_by_exchange_id: dict[str, OrderRecord] | None = None,
    local_by_client_id: dict[str, OrderRecord] | None = None,
    conflicts: list[tuple[ExchangeTradeSnapshot, TradeRecord, list[str]]] | None = None,
    target_symbols: list[str] | None = None,
) -> list[ExchangeTradeSnapshot]:
    if hasattr(repository, "get_trades"):
        try:
            all_persisted_trades = list(repository.get_trades(include_inactive=True))
        except TypeError:
            # Compatibility for small legacy repositories and test doubles. The
            # strict provenance checks below still prevent cross-domain matching.
            all_persisted_trades = list(repository.get_trades())
    else:
        all_persisted_trades = []
    persisted_trades = [trade for trade in all_persisted_trades if _is_live_owned(trade)]
    if target_symbols is not None:
        persisted_trades = [
            trade for trade in persisted_trades if _in_scope(trade.symbol, target_symbols)
        ]
    persisted_by_id = {
        trade.exchange_trade_id.strip(): trade
        for trade in persisted_trades
        if isinstance(trade.exchange_trade_id, str) and trade.exchange_trade_id.strip()
    }
    globally_persisted_by_id = {
        trade.exchange_trade_id.strip(): trade
        for trade in all_persisted_trades
        if _is_live_owned(trade)
        and isinstance(trade.exchange_trade_id, str)
        and trade.exchange_trade_id.strip()
    }
    globally_non_live_by_id = {
        trade.exchange_trade_id.strip(): trade
        for trade in all_persisted_trades
        if not _is_live_owned(trade)
        and isinstance(trade.exchange_trade_id, str)
        and trade.exchange_trade_id.strip()
    }
    persisted_ids = set(persisted_by_id)
    persisted_by_fingerprint: dict[str, list[TradeRecord]] = {}
    for trade in persisted_trades:
        if not _is_live_owned(trade):
            continue
        for fingerprint in _trade_record_fingerprint_aliases(trade):
            persisted_by_fingerprint.setdefault(fingerprint, []).append(trade)

    def matching_persisted_trades(snapshot: ExchangeTradeSnapshot) -> list[TradeRecord]:
        matches: list[TradeRecord] = []
        seen: set[int] = set()
        for fingerprint in _trade_fingerprint_aliases(snapshot):
            for trade in persisted_by_fingerprint.get(fingerprint, []):
                marker = id(trade)
                if marker not in seen and _trade_external_ids_are_compatible(snapshot, trade):
                    seen.add(marker)
                    matches.append(trade)
        return matches

    seen_ids = set(persisted_ids)
    response_trades_by_id: dict[str, ExchangeTradeSnapshot] = {}
    response_fingerprint_indexes: dict[tuple[str, str | None], int] = {}
    seen_response_fingerprints: set[tuple[str, tuple[object, ...]]] = set()
    deduplicated: list[ExchangeTradeSnapshot] = []
    for snapshot in snapshots:
        trade_id = (
            snapshot.exchange_trade_id.strip()
            if isinstance(snapshot.exchange_trade_id, str)
            else ""
        )
        fingerprints = _trade_fingerprint_aliases(snapshot)
        matched_order = _match_trade(snapshot, local_by_exchange_id, local_by_client_id)
        response_context = (
            matched_order.order_id
            if matched_order is not None and not snapshot.exchange_order_id
            and not snapshot.client_order_id
            else None
        )
        fingerprint_keys = {(fingerprint, response_context) for fingerprint in fingerprints}
        if trade_id and trade_id in globally_non_live_by_id:
            persisted = globally_non_live_by_id[trade_id]
            if conflicts is not None:
                conflicts.append((snapshot, persisted, ["source"]))
            # Keep the incoming snapshot in the persistence stream. Repository
            # upsert must isolate the strict live provenance pair rather than
            # upgrading the non-live row.
        if trade_id and trade_id in globally_persisted_by_id:
            persisted = globally_persisted_by_id[trade_id]
            conflicting_fields = _trade_snapshot_conflicts(snapshot, persisted, matched_order)
            if conflicting_fields and conflicts is not None:
                conflicts.append((snapshot, persisted, conflicting_fields))
            continue
        if trade_id and trade_id in response_trades_by_id:
            prior_snapshot = response_trades_by_id[trade_id]
            if prior_snapshot != snapshot and conflicts is not None:
                prior_record = TradeRecord(
                    source="live_exchange",
                    runtime_mode="live",
                    exchange_trade_id=trade_id,
                    strategy=EXCHANGE_STRATEGY,
                    symbol=prior_snapshot.symbol,
                    side=prior_snapshot.side.value,
                    amount=prior_snapshot.amount,
                    price=prior_snapshot.price,
                    fee=prior_snapshot.fee,
                    timestamp=prior_snapshot.timestamp,
                    contracts=prior_snapshot.contracts,
                    quantity=prior_snapshot.quantity,
                    quantity_unit=prior_snapshot.quantity_unit,
                    is_inverse=prior_snapshot.is_inverse,
                    contract_size=prior_snapshot.contract_size,
                    exchange_order_id=prior_snapshot.exchange_order_id,
                    client_order_id=prior_snapshot.client_order_id,
                )
                conflicts.append((
                    snapshot,
                    prior_record,
                    _trade_snapshot_conflicts(snapshot, prior_record, matched_order),
                ))
            continue

        response_indexes = [
            response_fingerprint_indexes[key]
            for key in fingerprint_keys
            if key in response_fingerprint_indexes
        ]
        if response_indexes:
            index = response_indexes[0]
            prior = deduplicated[index]
            prior_id = str(prior.exchange_trade_id or "").strip()
            if trade_id and not prior_id:
                deduplicated[index] = snapshot
                seen_ids.add(trade_id)
            elif not trade_id and prior_id:
                continue
            elif not trade_id:
                continue
            else:
                # Distinct reliable trade IDs are distinct fills even when the
                # exchange-only fields happen to be identical.
                for key in fingerprint_keys:
                    response_fingerprint_indexes.pop(key, None)
            if any(key in response_fingerprint_indexes for key in fingerprint_keys):
                continue

        if (
            not trade_id
            and (snapshot.exchange_order_id or snapshot.client_order_id)
            and matching_persisted_trades(snapshot)
        ):
            # A blank replay cannot supersede either a placeholder or an
            # identified persisted fill with the same exchange fingerprint.
            continue
        if trade_id:
            seen_ids.add(trade_id)
            response_trades_by_id[trade_id] = snapshot
        elif not fingerprints:
            response_fingerprint = (
                _canonical_symbol_for_comparison(snapshot.symbol),
                snapshot.side.value,
                snapshot.amount,
                snapshot.price,
                snapshot.fee,
                snapshot.timestamp,
            )
            if response_context is not None:
                response_key = (response_context, response_fingerprint)
                if response_key in seen_response_fingerprints:
                    continue
                seen_response_fingerprints.add(response_key)
        for key in fingerprint_keys:
            response_fingerprint_indexes.setdefault(key, len(deduplicated))
        deduplicated.append(snapshot)
    return deduplicated


def _has_trusted_snapshot_provenance(snapshot: ExchangeOrderSnapshot | None) -> bool:
    """Zero is reserved for unknown snapshot provenance, never an authoritative time."""
    return snapshot is not None and snapshot.updated_at > 0


def _snapshot_has_missing_terminal_fill_fields(snapshot: ExchangeOrderSnapshot) -> bool:
    return (
        snapshot.status in {OrderStatus.FILLED, OrderStatus.CANCELLED}
        and not snapshot._filled_amount_present
        and not snapshot._remaining_amount_present
        and not snapshot._fill_price_present
    )


def _snapshot_has_contradictory_zero_fill(snapshot: ExchangeOrderSnapshot) -> bool:
    return (
        snapshot.status in {OrderStatus.FILLED, OrderStatus.CANCELLED}
        and snapshot._filled_amount_present
        and snapshot.filled_amount == 0.0
        and (
            snapshot.status is OrderStatus.FILLED
            or not snapshot._remaining_amount_present
        )
    )


def _snapshot_needs_trade_fill_completion(snapshot: ExchangeOrderSnapshot) -> bool:
    """Allow same-round trades to complete an open snapshot with no fill data."""
    return snapshot.filled_amount == 0.0 and (
        not snapshot._filled_amount_present
        or not snapshot._remaining_amount_present
        or not snapshot._fill_price_present
        or snapshot.fill_price == 0.0
    )


def _complete_since_fill(
    local: OrderRecord,
    trades: list[TradeRecord],
    since: int | None,
) -> tuple[float, float] | None:
    snapshot_time = local.snapshot_updated_at
    if since is None or snapshot_time is None or since > snapshot_time:
        return None
    baseline = local.snapshot_filled_amount
    already_counted = local.filled_amount - baseline
    if baseline < 0.0 or already_counted < -1e-12:
        return None
    if baseline > 0.0 and local.fill_price <= 0.0:
        return None
    late_trades = sorted(
        (trade for trade in trades if trade.timestamp > snapshot_time),
        key=lambda trade: (trade.timestamp, trade.exchange_trade_id or ""),
    )
    total_late = sum(_trade_order_amount(trade) for trade in late_trades)
    total = baseline + total_late
    if total > local.amount + 1e-12 or total < local.filled_amount - 1e-12:
        return None
    counted_amount = 0.0
    counted_cost = 0.0
    for trade in late_trades:
        if counted_amount >= already_counted - 1e-12:
            break
        counted_amount += _trade_order_amount(trade)
        counted_cost += _trade_order_amount(trade) * trade.price
    if not math.isclose(counted_amount, already_counted, rel_tol=0.0, abs_tol=1e-12):
        return None
    if total <= 0.0:
        return None
    baseline_cost = local.filled_amount * local.fill_price - counted_cost
    if baseline_cost < -1e-12:
        return None
    total_cost = baseline_cost + sum(
        _trade_order_amount(trade) * trade.price for trade in late_trades
    )
    return min(local.amount, total), total_cost / total


def reconcile_order(
    local: OrderRecord,
    snapshot: ExchangeOrderSnapshot | None,
    trades: list[TradeRecord],
    *,
    complete_trade_page: bool = True,
    complete_trade_since: int | None = None,
) -> OrderRecord:
    """Reconcile one order from one authoritative snapshot or all persisted trades."""
    current_snapshot = (
        snapshot
        if _has_trusted_snapshot_provenance(snapshot)
        and (
            local.snapshot_updated_at is None
            or snapshot.updated_at >= local.snapshot_updated_at
        )
        else None
    )
    unique_trades = _unique_trade_records(trades)
    if current_snapshot is not None:
        snapshot_regresses_known_quantity = (
            current_snapshot.amount < local.amount
            or current_snapshot.filled_amount < local.filled_amount
        )
        snapshot_downgrades_terminal = _is_terminal(local.status) and not _is_terminal(
            current_snapshot.status.value
        )
        if snapshot_regresses_known_quantity or snapshot_downgrades_terminal:
            amount = (
                max(local.amount, current_snapshot.amount)
                if local.snapshot_updated_at is None
                else current_snapshot.amount
                if current_snapshot.amount >= local.filled_amount
                else local.amount
            )
            filled = local.filled_amount
            remaining = max(0.0, amount - filled)
            fill_price = local.fill_price
            status = (
                local.status if _is_terminal(local.status)
                else current_snapshot.status.value
                if _is_terminal(current_snapshot.status.value)
                else _derived_status(filled, amount)
            )
            baseline = max(local.snapshot_filled_amount, current_snapshot.filled_amount)
        else:
            amount = current_snapshot.amount
            status = current_snapshot.status.value
            filled = current_snapshot.filled_amount
            fill_price = (
                current_snapshot.fill_price
                if current_snapshot._fill_price_present
                else local.fill_price
            )
            remaining = current_snapshot.remaining_amount
            baseline = current_snapshot.filled_amount
            missing_terminal_fields = _snapshot_has_missing_terminal_fill_fields(
                current_snapshot
            )
            contradictory_zero_fill = _snapshot_has_contradictory_zero_fill(
                current_snapshot
            )
            needs_trade_fill_completion = (
                missing_terminal_fields
                or contradictory_zero_fill
                or _snapshot_needs_trade_fill_completion(current_snapshot)
            )
            if needs_trade_fill_completion:
                trade_total = sum(_trade_order_amount(trade) for trade in unique_trades)
                if trade_total > 0.0 and trade_total <= amount:
                    if trade_total >= local.filled_amount:
                        filled = trade_total
                        fill_price = _weighted_average(unique_trades)
                    else:
                        filled = local.filled_amount
                        fill_price = local.fill_price
                    remaining = max(0.0, amount - filled)
                elif trade_total > amount:
                    logger.warning(
                        "Rejecting excessive trade evidence for order %s: total=%s amount=%s",
                        local.order_id,
                        trade_total,
                        amount,
                    )
                    # Do not clamp bad evidence: a weighted average over more
                    # quantity than the order represents is not an executable fill.
                    filled = max(local.filled_amount, current_snapshot.filled_amount)
                    remaining = max(0.0, amount - filled)
                    fill_price = local.fill_price
                elif (
                    missing_terminal_fields
                    or contradictory_zero_fill
                    or local.filled_amount > 0.0
                ):
                    # Terminal status and missing/contradictory quantity data do
                    # not prove that a previously confirmed fill disappeared.
                    filled = local.filled_amount
                    remaining = max(0.0, amount - filled)
                    fill_price = local.fill_price
            elif (
                filled > 0.0
                and (not current_snapshot._fill_price_present or fill_price == 0.0)
                and unique_trades
                and math.isclose(
                    sum(_trade_order_amount(trade) for trade in unique_trades),
                    filled,
                    rel_tol=0.0,
                    abs_tol=1e-12,
                )
            ):
                # The snapshot quantity remains authoritative; same-round trades
                # may only supply an omitted average for that exact quantity.
                fill_price = _weighted_average(unique_trades)
        updated_at = max(local.updated_at, current_snapshot.updated_at)
    else:
        amount = local.amount
        trade_total = (
            sum(_trade_order_amount(trade) for trade in unique_trades)
            if complete_trade_page else 0.0
        )
        filled = min(amount, max(local.filled_amount, trade_total))
        if trade_total > 0.0:
            if trade_total < local.filled_amount:
                fill_price = local.fill_price
            else:
                fill_price = _weighted_average(unique_trades)
        else:
            fill_price = local.fill_price
        complete_fill = _complete_since_fill(local, unique_trades, complete_trade_since)
        if complete_fill is not None and complete_fill[0] > filled:
            filled, fill_price = complete_fill
        status = local.status if _is_terminal(local.status) else _derived_status(filled, amount)
        remaining = max(0.0, amount - filled)
        updated_at = max([local.updated_at, *(trade.timestamp for trade in unique_trades)])
        baseline = local.snapshot_filled_amount

    reconciled = OrderRecord(
        source="live_exchange",
        runtime_mode="live",
        order_id=local.order_id,
        exchange_order_id=(
            local.exchange_order_id
            or (snapshot.exchange_order_id if snapshot is not None else "")
            or next(
                (trade.exchange_order_id for trade in unique_trades if trade.exchange_order_id),
                "",
            )
        ),
        client_order_id=(
            local.client_order_id
            or (snapshot.client_order_id if snapshot is not None else "")
        ),
        strategy=local.strategy,
        symbol=local.symbol,
        side=local.side,
        type=local.type,
        amount=amount,
        price=local.price,
        status=status,
        fill_price=fill_price,
        timestamp=max(local.timestamp, current_snapshot.timestamp if current_snapshot else 0),
        updated_at=updated_at,
        filled_amount=filled,
        remaining_amount=remaining,
        snapshot_filled_amount=baseline,
        snapshot_updated_at=(
            current_snapshot.updated_at
            if current_snapshot is not None
            else local.snapshot_updated_at
        ),
        execution_type_family=(
            current_snapshot.execution_type_family
            if current_snapshot is not None
            else local.execution_type_family
        ),
    )
    if current_snapshot is not None and (
        _snapshot_has_missing_terminal_fill_fields(current_snapshot)
        or _snapshot_has_contradictory_zero_fill(current_snapshot)
    ):
        reconciled.status = (
            _derived_status(reconciled.filled_amount, reconciled.amount)
            if current_snapshot.status is OrderStatus.FILLED
            else current_snapshot.status.value
        )
    reconciled._snapshot_authoritative = current_snapshot is not None
    return reconciled


def _unique_trade_records(trades: list[TradeRecord]) -> list[TradeRecord]:
    seen_ids: set[str] = set()
    fingerprint_indexes: dict[str, list[int]] = {}
    local_context_indexes: dict[tuple[str, tuple[object, ...]], int] = {}
    unique: list[TradeRecord] = []
    for trade in trades:
        trade_id = (
            trade.exchange_trade_id.strip()
            if isinstance(trade.exchange_trade_id, str)
            else ""
        )
        if trade_id and trade_id in seen_ids:
            continue

        fingerprint = _trade_record_fingerprint(trade)
        if fingerprint is None and trade.order_id:
            local_context_key = (
                trade.order_id,
                (
                    _canonical_symbol_for_comparison(trade.symbol),
                    trade.side,
                    trade.amount,
                    trade.price,
                    trade.fee,
                    trade.timestamp,
                ),
            )
            if local_context_key in local_context_indexes:
                continue
        else:
            local_context_key = None
        prior_indexes = fingerprint_indexes.get(fingerprint, []) if fingerprint else []
        if prior_indexes:
            if not trade_id:
                if any(
                    unique[index].order_id
                    and unique[index].order_id == trade.order_id
                    for index in prior_indexes
                ):
                    # A blank replay is deduplicated only within an explicit order context.
                    continue
            blank_index = next(
                (
                    index
                    for index in prior_indexes
                    if not unique[index].exchange_trade_id
                ),
                None,
            )
            if (
                blank_index is not None
                and unique[blank_index].order_id
                and unique[blank_index].order_id == trade.order_id
            ):
                # Prefer the reliable exchange trade ID when it upgrades a blank.
                unique[blank_index] = trade
                seen_ids.add(trade_id)
                continue
            # Distinct reliable IDs remain distinct fills.

        if trade_id:
            seen_ids.add(trade_id)
        unique.append(trade)
        if fingerprint:
            fingerprint_indexes.setdefault(fingerprint, []).append(len(unique) - 1)
        elif local_context_key is not None:
            local_context_indexes[local_context_key] = len(unique) - 1
    return unique


def _trade_order_amount(trade: TradeRecord | ExchangeTradeSnapshot) -> float:
    contracts = getattr(trade, "contracts", None)
    return contracts if contracts is not None else trade.amount


def _weighted_average(trades: list[TradeRecord]) -> float:
    total = sum(_trade_order_amount(trade) for trade in trades)
    return (
        sum(_trade_order_amount(trade) * trade.price for trade in trades) / total
        if total else 0.0
    )


def _is_terminal(status: str) -> bool:
    return status in {
        OrderStatus.FILLED.value,
        OrderStatus.CANCELLED.value,
        "canceled",
        "cancel",
        OrderStatus.REJECTED.value,
    }


def _derived_status(filled: float, amount: float) -> str:
    if filled <= 0.0:
        return OrderStatus.PENDING.value
    if filled >= amount:
        return OrderStatus.FILLED.value
    return OrderStatus.PARTIALLY_FILLED.value


def _trade_record_matches_order(trade: TradeRecord, order: OrderRecord) -> bool:
    if not _symbols_match(trade.symbol, order.symbol):
        return False
    if str(trade.side or "").strip().lower() != str(order.side or "").strip().lower():
        return False
    for field in ("exchange_order_id", "client_order_id"):
        trade_value = str(getattr(trade, field, "") or "").strip()
        order_value = str(getattr(order, field, "") or "").strip()
        if trade_value and order_value and trade_value != order_value:
            return False
    if trade.order_id and order.order_id and trade.order_id == order.order_id:
        return True
    return any(
        getattr(trade, field, "")
        and getattr(order, field, "")
        and getattr(trade, field) == getattr(order, field)
        for field in ("exchange_order_id", "client_order_id")
    )


def _find_divergences(
    local_orders: list[OrderRecord],
    exchange_orders: list[ExchangeOrderSnapshot],
    exchange_trades: list[ExchangeTradeSnapshot],
    persisted_trades: list[TradeRecord],
    local_by_exchange_id: dict[str, OrderRecord],
    local_by_client_id: dict[str, OrderRecord],
    timestamp_ms: Callable[[], int],
    target_symbols: list[str] | None = None,
    unmatched_exchange_snapshot_ids: set[int] | None = None,
) -> list[dict[str, object]]:
    divergences: list[dict[str, object]] = []
    seen: set[str] = set()
    scoped_local_orders = (
        local_orders
        if target_symbols is None
        else [order for order in local_orders if _in_scope(order.symbol, target_symbols)]
    )
    traded_amount_by_local_order: dict[int, float] = {}
    unique_persisted_trades = _unique_trade_records(persisted_trades)
    for trade in unique_persisted_trades:
        matching_orders = [
            order for order in scoped_local_orders
            if _trade_record_matches_order(trade, order)
        ]
        if len(matching_orders) == 1:
            order = matching_orders[0]
            traded_amount_by_local_order[id(order)] = (
                traded_amount_by_local_order.get(id(order), 0.0)
                + _trade_order_amount(trade)
            )

    for order in scoped_local_orders:
        traded_amount = traded_amount_by_local_order.get(id(order), 0.0)
        if traded_amount > order.amount:
            _append_divergence(
                divergences,
                seen,
                timestamp_ms,
                strategy=order.strategy,
                divergence_type="order_overfilled",
                reason=(
                    "Persisted exchange trade evidence exceeds the local order amount; "
                    "reconciliation is required"
                ),
                symbol=order.symbol,
                order_id=order.order_id,
                exchange_order_id=order.exchange_order_id,
                client_order_id=order.client_order_id,
            )

        if order.status not in {
            OrderStatus.PENDING.value,
            OrderStatus.PARTIALLY_FILLED.value,
        }:
            continue
        if not order.exchange_order_id and not order.client_order_id:
            continue
        # External IDs alone do not establish ownership: an ID collision on a
        # different dated contract must still produce a missing-order divergence.
        if any(
            _match_order(snapshot, local_by_exchange_id, local_by_client_id) is order
            for snapshot in exchange_orders
        ):
            continue
        traded_amount = traded_amount_by_local_order.get(id(order), 0.0)
        if traded_amount >= order.amount:
            continue
        _append_divergence(
            divergences,
            seen,
            timestamp_ms,
            strategy=order.strategy,
            divergence_type=(
                "partially_filled_order_missing"
                if order.status == OrderStatus.PARTIALLY_FILLED.value
                else "orphaned_order_partial"
                if traded_amount > 0.0
                else "missing_exchange_order"
            ),
            reason=(
                "Local partially filled order is missing from OKX open orders"
                if order.status == OrderStatus.PARTIALLY_FILLED.value
                else (
                    "Local pending order is missing from OKX open orders despite "
                    "partial matching trades"
                )
                if traded_amount > 0.0
                else "Local pending order is missing from OKX open orders"
            ),
            symbol=order.symbol,
            order_id=order.order_id,
            exchange_order_id=order.exchange_order_id,
            client_order_id=order.client_order_id,
        )

    unmatched_exchange_snapshot_ids = unmatched_exchange_snapshot_ids or set()
    for snapshot in exchange_orders:
        if (
            id(snapshot) not in unmatched_exchange_snapshot_ids
            and _match_order(snapshot, local_by_exchange_id, local_by_client_id) is not None
        ):
            continue
        _append_divergence(
            divergences,
            seen,
            timestamp_ms,
            strategy=EXCHANGE_STRATEGY,
            divergence_type="unmatched_exchange_order",
            reason="OKX open order has no matching local order",
            symbol=_canonical_symbol_for_comparison(snapshot.symbol),
            order_id=_external_order_id(snapshot.exchange_order_id, snapshot.client_order_id),
            exchange_order_id=snapshot.exchange_order_id,
            client_order_id=snapshot.client_order_id,
        )

    for snapshot in exchange_trades:
        if _match_trade(snapshot, local_by_exchange_id, local_by_client_id) is not None:
            continue
        _append_divergence(
            divergences,
            seen,
            timestamp_ms,
            strategy=EXCHANGE_STRATEGY,
            divergence_type="unmatched_exchange_trade",
            reason="OKX trade has no matching local order",
            symbol=_canonical_symbol_for_comparison(snapshot.symbol),
            order_id=_external_order_id(snapshot.exchange_order_id, snapshot.client_order_id),
            exchange_order_id=snapshot.exchange_order_id,
            client_order_id=snapshot.client_order_id,
            exchange_trade_id=snapshot.exchange_trade_id,
            trade_fingerprint=_unmatched_trade_fingerprint(snapshot),
        )

    return divergences


def _unmatched_trade_fingerprint(snapshot: ExchangeTradeSnapshot) -> str:
    payload = (
        _canonical_symbol_for_comparison(snapshot.symbol),
        snapshot.side.value,
        snapshot.amount,
        snapshot.price,
        snapshot.fee,
        snapshot.timestamp,
    )
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def _append_divergence(
    divergences: list[dict[str, object]],
    seen: set[str],
    timestamp_ms: Callable[[], int],
    *,
    strategy: str,
    divergence_type: str,
    reason: str,
    symbol: str,
    order_id: str | None,
    exchange_order_id: str,
    client_order_id: str,
    exchange_trade_id: str = "",
    trade_fingerprint: str = "",
    event_identity: str = "",
    source: str = "live_exchange",
    runtime_mode: str = "live",
) -> None:
    event_key_payload = {
        "client_order_id": client_order_id,
        "source": source,
        "runtime_mode": runtime_mode,
        "divergence_type": divergence_type,
        "exchange_order_id": exchange_order_id,
        "exchange_trade_id": exchange_trade_id,
        "order_id": order_id or "",
        "symbol": symbol,
    }
    if event_identity:
        event_key_payload = {
            "canonical_identity": event_identity,
            "divergence_type": divergence_type,
            "source": source,
            "runtime_mode": runtime_mode,
        }
    if divergence_type == "unmatched_exchange_trade":
        event_key_payload["trade_fingerprint"] = trade_fingerprint
    event_key = hashlib.sha256(
        json.dumps(
            event_key_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    if event_key in seen:
        return
    seen.add(event_key)
    divergences.append(
        {
            "type": "risk_event",
            "strategy": strategy,
            "reason_code": PRIVATE_SYNC_DIVERGENCE,
            "reason": reason,
            "severity": "high",
            "divergence_type": divergence_type,
            "event_key": event_key,
            "source": source,
            "runtime_mode": runtime_mode,
            "_event_key_domain_scoped": True,
            "symbol": symbol,
            "order_id": order_id or "",
            "exchange_order_id": exchange_order_id,
            "client_order_id": client_order_id,
            "exchange_trade_id": exchange_trade_id,
            "timestamp": timestamp_ms(),
        }
    )


def _order_identity_conflicts(
    snapshot: ExchangeOrderSnapshot,
    local_by_exchange_id: dict[str, OrderRecord],
    local_by_client_id: dict[str, OrderRecord],
) -> bool:
    has_identity_match = (
        bool(snapshot.exchange_order_id and snapshot.exchange_order_id in local_by_exchange_id)
        or bool(snapshot.client_order_id and snapshot.client_order_id in local_by_client_id)
    )
    return has_identity_match and _match_order(
        snapshot, local_by_exchange_id, local_by_client_id
    ) is None


def _match_order(
    snapshot: ExchangeOrderSnapshot,
    local_by_exchange_id: dict[str, OrderRecord],
    local_by_client_id: dict[str, OrderRecord],
) -> OrderRecord | None:
    exchange_match = (
        local_by_exchange_id.get(snapshot.exchange_order_id)
        if snapshot.exchange_order_id
        else None
    )
    client_match = (
        local_by_client_id.get(snapshot.client_order_id)
        if snapshot.client_order_id
        else None
    )
    if (
        exchange_match is not None
        and client_match is not None
        and exchange_match is not client_match
    ):
        logger.warning(
            "Rejecting order snapshot with conflicting external IDs: exchange_order_id=%s, "
            "client_order_id=%s",
            snapshot.exchange_order_id,
            snapshot.client_order_id,
        )
        return None
    match = exchange_match or client_match
    if match is not None and (
        (
            snapshot.exchange_order_id
            and match.exchange_order_id
            and snapshot.exchange_order_id != match.exchange_order_id
        )
        or (
            snapshot.client_order_id
            and match.client_order_id
            and snapshot.client_order_id != match.client_order_id
        )
    ):
        logger.warning(
            "Rejecting order snapshot with conflicting external IDs: exchange_order_id=%s, "
            "client_order_id=%s",
            snapshot.exchange_order_id,
            snapshot.client_order_id,
        )
        return None
    if match is not None and not _execution_identity_matches(
        match,
        snapshot,
        ("symbol", "side", "type"),
    ):
        logger.warning(
            "Rejecting order snapshot with execution identity conflict for order_id=%s",
            match.order_id,
        )
        return None
    return match


def _trade_identity_conflicts(
    snapshot: ExchangeTradeSnapshot,
    local_by_exchange_id: dict[str, OrderRecord],
    local_by_client_id: dict[str, OrderRecord],
) -> bool:
    exchange_match = (
        local_by_exchange_id.get(snapshot.exchange_order_id)
        if snapshot.exchange_order_id
        else None
    )
    client_match = (
        local_by_client_id.get(snapshot.client_order_id)
        if snapshot.client_order_id
        else None
    )
    if (
        exchange_match is not None
        and client_match is not None
        and exchange_match is not client_match
    ):
        return True
    match = exchange_match or client_match
    if match is None:
        return False
    return (
        (snapshot.exchange_order_id and match.exchange_order_id
         and snapshot.exchange_order_id != match.exchange_order_id)
        or (snapshot.client_order_id and match.client_order_id
            and snapshot.client_order_id != match.client_order_id)
        or not _execution_identity_matches(match, snapshot, ("symbol", "side"))
    )


def _match_trade(
    snapshot: ExchangeTradeSnapshot,
    local_by_exchange_id: dict[str, OrderRecord],
    local_by_client_id: dict[str, OrderRecord],
) -> OrderRecord | None:
    exchange_match = (
        local_by_exchange_id.get(snapshot.exchange_order_id)
        if snapshot.exchange_order_id
        else None
    )
    client_match = (
        local_by_client_id.get(snapshot.client_order_id)
        if snapshot.client_order_id
        else None
    )
    if (
        exchange_match is not None
        and client_match is not None
        and exchange_match is not client_match
    ):
        logger.warning(
            "Rejecting trade snapshot with conflicting external IDs: exchange_order_id=%s, "
            "client_order_id=%s",
            snapshot.exchange_order_id,
            snapshot.client_order_id,
        )
        return None
    match = exchange_match or client_match
    if match is not None and (
        (
            snapshot.exchange_order_id
            and match.exchange_order_id
            and snapshot.exchange_order_id != match.exchange_order_id
        )
        or (
            snapshot.client_order_id
            and match.client_order_id
            and snapshot.client_order_id != match.client_order_id
        )
    ):
        logger.warning(
            "Rejecting trade snapshot with conflicting external IDs: exchange_order_id=%s, "
            "client_order_id=%s",
            snapshot.exchange_order_id,
            snapshot.client_order_id,
        )
        return None
    if match is not None and not _execution_identity_matches(
        match,
        snapshot,
        ("symbol", "side"),
    ):
        logger.warning(
            "Rejecting trade snapshot with execution identity conflict for order_id=%s",
            match.order_id,
        )
        return None
    return match


def _execution_identity_matches(
    local: OrderRecord,
    snapshot: ExchangeOrderSnapshot | ExchangeTradeSnapshot,
    fields: tuple[str, ...],
) -> bool:
    for field in fields:
        local_value = str(getattr(local, field, "") or "").strip().lower()
        snapshot_raw = getattr(snapshot, field, "")
        snapshot_value = str(getattr(snapshot_raw, "value", snapshot_raw) or "").strip().lower()
        if field == "symbol":
            if not _symbols_match(local_value, snapshot_value):
                return False
        elif field == "type":
            snapshot_family = getattr(snapshot, "execution_type_family", "")
            local_family = execution_type_family(
                local_value,
                provider_type=getattr(local, "execution_type_family", "") or None,
            )
            snapshot_family = execution_type_family(
                snapshot_value,
                provider_type=snapshot_family or None,
            )
            if local_family != snapshot_family:
                return False
        elif local_value != snapshot_value:
            return False
    return True


def _account_record(snapshot: AccountSnapshot) -> AccountRecord:
    return AccountRecord(
        strategy=EXCHANGE_STRATEGY,
        source="live_exchange",
        runtime_mode="live",
        initial_equity=snapshot.initial_equity,
        cash_balance=snapshot.cash_balance,
        available_balance=snapshot.available_balance,
        equity=snapshot.equity,
        realized_pnl=snapshot.realized_pnl,
        unrealized_pnl=snapshot.unrealized_pnl,
        daily_pnl=snapshot.daily_pnl,
        fees_paid=snapshot.fees_paid,
        updated_at=snapshot.updated_at or snapshot.timestamp,
        assets=[asdict(asset) for asset in snapshot.assets],
    )


def _order_record(snapshot: ExchangeOrderSnapshot, local: OrderRecord | None) -> OrderRecord:
    record = OrderRecord(
        source="live_exchange",
        runtime_mode="live",
        order_id=(
            local.order_id
            if local is not None
            else _external_order_id(snapshot.exchange_order_id, snapshot.client_order_id)
        ),
        exchange_order_id=snapshot.exchange_order_id,
        client_order_id=snapshot.client_order_id,
        strategy=local.strategy if local is not None else EXCHANGE_STRATEGY,
        symbol=_canonical_symbol_for_comparison(snapshot.symbol),
        side=snapshot.side.value,
        type=snapshot.type.value,
        amount=snapshot.amount,
        price=snapshot.price,
        status=snapshot.status.value,
        fill_price=snapshot.fill_price,
        timestamp=snapshot.timestamp,
        updated_at=snapshot.updated_at,
        filled_amount=snapshot.filled_amount,
        remaining_amount=snapshot.remaining_amount,
        snapshot_filled_amount=snapshot.filled_amount,
        snapshot_updated_at=(snapshot.updated_at if snapshot.updated_at > 0 else None),
        execution_type_family=snapshot.execution_type_family,
    )
    record._snapshot_authoritative = _has_trusted_snapshot_provenance(snapshot)
    return record


def _trade_record(snapshot: ExchangeTradeSnapshot, local: OrderRecord | None) -> TradeRecord:
    return TradeRecord(
        source="live_exchange",
        runtime_mode="live",
        exchange_trade_id=snapshot.exchange_trade_id,
        exchange_order_id=snapshot.exchange_order_id,
        client_order_id=snapshot.client_order_id,
        order_id=(
            local.order_id
            if local is not None
            else _external_order_id(snapshot.exchange_order_id, snapshot.client_order_id)
        ),
        strategy=local.strategy if local is not None else EXCHANGE_STRATEGY,
        symbol=_canonical_symbol_for_comparison(snapshot.symbol),
        side=snapshot.side.value,
        amount=snapshot.amount,
        price=snapshot.price,
        fee=snapshot.fee,
        timestamp=snapshot.timestamp,
        contracts=snapshot.contracts,
        quantity=snapshot.quantity,
        quantity_unit=snapshot.quantity_unit,
        is_inverse=snapshot.is_inverse,
        contract_size=snapshot.contract_size,
        blank_trade_fingerprint=(
            _trade_fingerprint(snapshot) or ""
            if snapshot.exchange_order_id or snapshot.client_order_id
            else ""
        ),
    )


def _external_order_id(exchange_order_id: str, client_order_id: str = "") -> str:
    if exchange_order_id:
        return f"okx:{exchange_order_id}"
    if client_order_id:
        return f"okx-client:{client_order_id}"
    return "okx:unknown"
