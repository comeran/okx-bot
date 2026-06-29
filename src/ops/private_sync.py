from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Protocol

from src.core.types import AccountSnapshot, ExchangeOrderSnapshot, ExchangeTradeSnapshot, OrderStatus
from src.data.models import AccountRecord, OrderRecord, TradeRecord

EXCHANGE_STRATEGY = "__exchange__"
PRIVATE_SYNC_DIVERGENCE = "private_sync_divergence"

RiskEventNotifier = Callable[[dict[str, object]], Awaitable[None]]


class PrivateSyncAdapter(Protocol):
    async def fetch_account_snapshot(self) -> AccountSnapshot:
        ...

    async def fetch_open_order_snapshots(
        self,
        symbols: list[str] | None = None,
    ) -> list[ExchangeOrderSnapshot]:
        ...

    async def fetch_recent_trade_snapshots(
        self,
        symbols: list[str] | None = None,
        since: int | None = None,
        limit: int = 100,
    ) -> list[ExchangeTradeSnapshot]:
        ...


@dataclass(frozen=True)
class PrivateSyncResult:
    account_upserted: int
    orders_upserted: int
    trades_upserted: int
    risk_events_saved: int
    kill_switch_engaged: bool
    divergences: list[dict[str, object]]

    def as_response(self) -> dict[str, object]:
        return asdict(self)


def current_timestamp_ms() -> int:
    return int(time.time() * 1000)


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
    target_symbols = symbols or None
    account = await adapter.fetch_account_snapshot()
    exchange_orders = await adapter.fetch_open_order_snapshots(target_symbols)
    exchange_trades = await adapter.fetch_recent_trade_snapshots(
        target_symbols,
        since=since,
        limit=limit,
    )
    local_orders = list(repository.get_orders())
    local_by_exchange_id, local_by_client_id = _local_order_indexes(local_orders)
    divergences = _find_divergences(
        local_orders,
        exchange_orders,
        exchange_trades,
        local_by_exchange_id,
        local_by_client_id,
        timestamp_ms,
    )

    high_risk_divergences = [
        divergence for divergence in divergences if divergence.get("severity") == "high"
    ]
    kill_switch_engaged = False
    if high_risk_divergences:
        repository.set_kill_switch(
            True,
            "Private sync detected high-risk divergence",
            timestamp_ms(),
        )
        kill_switch_engaged = True

    for divergence in divergences:
        repository.save_risk_event(divergence)
        if divergence.get("severity") == "high" and risk_event_notifier is not None:
            try:
                await risk_event_notifier(divergence)
            except Exception:
                pass

    repository.upsert_account(_account_record(account))
    for snapshot in exchange_orders:
        local = _match_order(snapshot, local_by_exchange_id, local_by_client_id)
        repository.upsert_order(_order_record(snapshot, local))
    for snapshot in exchange_trades:
        local = _match_trade(snapshot, local_by_exchange_id, local_by_client_id)
        repository.upsert_trade(_trade_record(snapshot, local))

    return PrivateSyncResult(
        account_upserted=1,
        orders_upserted=len(exchange_orders),
        trades_upserted=len(exchange_trades),
        risk_events_saved=len(divergences),
        kill_switch_engaged=kill_switch_engaged,
        divergences=divergences,
    )


def _local_order_indexes(
    orders: list[OrderRecord],
) -> tuple[dict[str, OrderRecord], dict[str, OrderRecord]]:
    return (
        {
            order.exchange_order_id: order
            for order in orders
            if getattr(order, "exchange_order_id", "")
        },
        {
            order.client_order_id: order
            for order in orders
            if getattr(order, "client_order_id", "")
        },
    )


def _find_divergences(
    local_orders: list[OrderRecord],
    exchange_orders: list[ExchangeOrderSnapshot],
    exchange_trades: list[ExchangeTradeSnapshot],
    local_by_exchange_id: dict[str, OrderRecord],
    local_by_client_id: dict[str, OrderRecord],
    timestamp_ms: Callable[[], int],
) -> list[dict[str, object]]:
    divergences: list[dict[str, object]] = []
    seen: set[str] = set()
    exchange_order_ids = {
        order.exchange_order_id for order in exchange_orders if order.exchange_order_id
    }
    exchange_client_order_ids = {
        order.client_order_id for order in exchange_orders if order.client_order_id
    }

    for order in local_orders:
        if order.status != OrderStatus.PENDING.value:
            continue
        if not order.exchange_order_id and not order.client_order_id:
            continue
        if order.exchange_order_id and order.exchange_order_id in exchange_order_ids:
            continue
        if order.client_order_id and order.client_order_id in exchange_client_order_ids:
            continue
        _append_divergence(
            divergences,
            seen,
            timestamp_ms,
            strategy=order.strategy,
            divergence_type="missing_exchange_order",
            reason="Local pending order is missing from OKX open orders",
            symbol=order.symbol,
            order_id=order.order_id,
            exchange_order_id=order.exchange_order_id,
            client_order_id=order.client_order_id,
        )

    for snapshot in exchange_orders:
        if _match_order(snapshot, local_by_exchange_id, local_by_client_id) is not None:
            continue
        _append_divergence(
            divergences,
            seen,
            timestamp_ms,
            strategy=EXCHANGE_STRATEGY,
            divergence_type="unmatched_exchange_order",
            reason="OKX open order has no matching local order",
            symbol=snapshot.symbol,
            order_id=_external_order_id(snapshot.exchange_order_id),
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
            symbol=snapshot.symbol,
            order_id=_external_order_id(snapshot.exchange_order_id),
            exchange_order_id=snapshot.exchange_order_id,
            client_order_id=snapshot.client_order_id,
            exchange_trade_id=snapshot.exchange_trade_id,
        )

    return divergences


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
) -> None:
    event_key = ":".join(
        [
            divergence_type,
            symbol,
            order_id or "",
            exchange_order_id,
            client_order_id,
            exchange_trade_id,
        ]
    )
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
            "symbol": symbol,
            "order_id": order_id or "",
            "exchange_order_id": exchange_order_id,
            "client_order_id": client_order_id,
            "exchange_trade_id": exchange_trade_id,
            "timestamp": timestamp_ms(),
        }
    )


def _match_order(
    snapshot: ExchangeOrderSnapshot,
    local_by_exchange_id: dict[str, OrderRecord],
    local_by_client_id: dict[str, OrderRecord],
) -> OrderRecord | None:
    if snapshot.client_order_id and snapshot.client_order_id in local_by_client_id:
        return local_by_client_id[snapshot.client_order_id]
    if snapshot.exchange_order_id and snapshot.exchange_order_id in local_by_exchange_id:
        return local_by_exchange_id[snapshot.exchange_order_id]
    return None


def _match_trade(
    snapshot: ExchangeTradeSnapshot,
    local_by_exchange_id: dict[str, OrderRecord],
    local_by_client_id: dict[str, OrderRecord],
) -> OrderRecord | None:
    if snapshot.client_order_id and snapshot.client_order_id in local_by_client_id:
        return local_by_client_id[snapshot.client_order_id]
    if snapshot.exchange_order_id and snapshot.exchange_order_id in local_by_exchange_id:
        return local_by_exchange_id[snapshot.exchange_order_id]
    return None


def _account_record(snapshot: AccountSnapshot) -> AccountRecord:
    return AccountRecord(
        strategy=EXCHANGE_STRATEGY,
        initial_equity=snapshot.initial_equity,
        cash_balance=snapshot.cash_balance,
        equity=snapshot.equity,
        realized_pnl=snapshot.realized_pnl,
        unrealized_pnl=snapshot.unrealized_pnl,
        daily_pnl=snapshot.daily_pnl,
        fees_paid=snapshot.fees_paid,
        updated_at=snapshot.timestamp,
    )


def _order_record(snapshot: ExchangeOrderSnapshot, local: OrderRecord | None) -> OrderRecord:
    return OrderRecord(
        order_id=local.order_id if local is not None else _external_order_id(snapshot.exchange_order_id),
        exchange_order_id=snapshot.exchange_order_id,
        client_order_id=snapshot.client_order_id,
        strategy=local.strategy if local is not None else EXCHANGE_STRATEGY,
        symbol=snapshot.symbol,
        side=snapshot.side.value,
        type=snapshot.type.value,
        amount=snapshot.amount,
        price=snapshot.price,
        status=snapshot.status.value,
        fill_price=snapshot.fill_price,
        timestamp=snapshot.timestamp,
        updated_at=snapshot.updated_at,
    )


def _trade_record(snapshot: ExchangeTradeSnapshot, local: OrderRecord | None) -> TradeRecord:
    return TradeRecord(
        exchange_trade_id=snapshot.exchange_trade_id,
        order_id=local.order_id if local is not None else _external_order_id(snapshot.exchange_order_id),
        strategy=local.strategy if local is not None else EXCHANGE_STRATEGY,
        symbol=snapshot.symbol,
        side=snapshot.side.value,
        amount=snapshot.amount,
        price=snapshot.price,
        fee=snapshot.fee,
        timestamp=snapshot.timestamp,
    )


def _external_order_id(exchange_order_id: str) -> str:
    if exchange_order_id:
        return f"okx:{exchange_order_id}"
    return "okx:unknown"
