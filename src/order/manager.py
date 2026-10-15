import inspect
import logging
import math
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import ccxt.async_support as ccxt

from src.core.types import (
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    _validate_amount_reconciliation,
)
from src.data.models import OrderRecord
from src.exchange.base import canonical_symbol
from src.exchange.errors import AcceptedUnknownOrderResultError
from src.order.accounting import PaperAccountingService
from src.order.router import OrderRouter
from src.order.value import (
    contract_size,
    is_inverse,
    position_order_quantity,
)
from src.order.value import (
    order_notional as shared_order_notional,
)
from src.order.value import (
    position_notional as shared_position_notional,
)

OrderUpdateCallback = Callable[[str], Awaitable[None] | None]
RiskEventCallback = Callable[[dict[str, object]], Awaitable[None] | None]
LiveStateRefresher = Callable[[str, str], Awaitable[None]]
PostLiveOrderSyncCallback = Callable[[str, str], Awaitable[None] | None]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RiskGateResult:
    passed: bool
    reason: str = ""
    order_value: float = 0.0
    effective_price: float | None = None


def risk_reason_code(reason: str) -> str:
    return {
        "Order exceeds maximum position size": "max_position_exceeded",
        "Daily loss exceeds maximum allowed loss": "daily_loss_exceeded",
        "Drawdown exceeds maximum allowed drawdown": "drawdown_exceeded",
        "Order requires a stop loss": "stop_loss_required",
        "Kill switch engaged": "kill_switch_engaged",
        "Live safeguards require a configured risk manager": "live_safeguard_unavailable",
        "Live safeguards require account state": "live_safeguard_unavailable",
        "Live safeguards require current position state": "live_safeguard_unavailable",
        "Live safeguards require a current price": "live_safeguard_unavailable",
        "Live orders must reduce existing exposure": "live_reduce_only_required",
        "Live opening orders are disabled": "live_opening_disabled",
        "Live order exceeds configured notional cap": "live_order_notional_exceeded",
        "Live spot sell requires existing position": "live_spot_position_required",
        "Live spot sell exceeds available position": "live_spot_position_exceeded",
        "Live position ownership is unavailable": "live_position_ownership_unavailable",
        "Live inverse order requires valid contract size": "live_contract_size_unavailable",
        "Live derivative order requires valid contract size": "live_contract_size_unavailable",
    }.get(reason, "risk_rejected")


def _response_value(response: Any, name: str) -> Any:
    if isinstance(response, dict):
        return response.get(name)
    return getattr(response, name, None)


def position_notional(position: Any) -> float:
    return shared_position_notional(position)


def position_quantity(position: Any) -> float:
    return position_order_quantity(position)


def order_notional(order: Any, price: float) -> float:
    return shared_order_notional(order, price)


def _position_has_explicit_ownership(repository: Any, position: Any, strategy: str) -> bool:
    for field in ("ownership", "owner_strategy", "allocation_strategy"):
        marker = getattr(position, field, None)
        if marker == strategy:
            return True
    checker = getattr(repository, "is_position_owned", None)
    if callable(checker):
        try:
            return bool(checker(strategy, position))
        except TypeError:
            return bool(checker(strategy, getattr(position, "symbol", "")))
    return False


class UnifiedOrderManager:
    def __init__(
        self,
        router: OrderRouter,
        repository=None,
        timestamp_ms: Callable[[], int] | None = None,
        initial_equity: float = 100000.0,
        fee_rate: float = 0.0,
        on_order_update: OrderUpdateCallback | None = None,
        on_risk_event: RiskEventCallback | None = None,
        risk_manager: Any | None = None,
        price_provider: Callable[[str], float | None] | None = None,
        contract_size_provider: Callable[[str], object | None] | None = None,
        kill_switch_checker: Callable[[], bool] | None = None,
        live_safeguards: bool = False,
        live_market_type: str = "",
        live_state_refresher: LiveStateRefresher | None = None,
        post_live_order_sync: PostLiveOrderSyncCallback | None = None,
        allow_live_open_orders: bool = False,
        live_max_order_notional: float = 0.0,
    ) -> None:
        self.router = router
        self.repository = repository
        self.timestamp_ms = timestamp_ms or self._current_timestamp_ms
        self.initial_equity = initial_equity
        self.fee_rate = fee_rate
        self.on_order_update = on_order_update
        self.on_risk_event = on_risk_event
        self.risk_manager = risk_manager
        self.price_provider = price_provider
        self.contract_size_provider = contract_size_provider
        self.kill_switch_checker = kill_switch_checker
        self.live_safeguards = live_safeguards
        self.live_market_type = live_market_type.strip().lower()
        self.live_state_refresher = live_state_refresher
        self.post_live_order_sync = post_live_order_sync
        self.allow_live_open_orders = allow_live_open_orders
        self.live_max_order_notional = live_max_order_notional
        self._balances: dict[str, float] = {}
        self._order_seq = 0
        self._live_lease_owner = f"{uuid.uuid4()}"

    async def submit(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        amount: float,
        price: float | None = None,
        trigger_price: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        strategy_name: str = "",
    ) -> Order:
        self._order_seq += 1
        order = Order(
            id=f"{strategy_name}-{symbol}-{id(self)}-{self._order_seq}",
            symbol=symbol,
            side=side,
            type=order_type,
            amount=amount,
            price=price,
            trigger_price=trigger_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        if self.router.mode == "live":
            order.symbol = self._canonical_state_symbol(symbol)
            self._enrich_live_order_metadata(order)
        if self.kill_switch_checker is not None and self.kill_switch_checker():
            risk_result = RiskGateResult(
                passed=False,
                reason="Kill switch engaged",
                effective_price=price,
            )
            return await self._reject_order(order, strategy_name, risk_result)

        if order.type is OrderType.LIMIT:
            try:
                valid_price = price is not None and math.isfinite(float(price)) and float(price) > 0
            except (TypeError, ValueError, OverflowError):
                valid_price = False
            if not valid_price:
                return await self._reject_order(
                    order,
                    strategy_name,
                    RiskGateResult(
                        False,
                        "Limit order requires a positive finite price",
                        effective_price=price,
                    ),
                )

        lease_acquired = await self._acquire_live_submission_lease()
        if self.router.mode == "live" and not lease_acquired:
            return await self._reject_order(
                order,
                strategy_name,
                RiskGateResult(False, "Live submission lock unavailable"),
            )
        try:
            owner = strategy_name
            if self.live_safeguards and self.live_state_refresher is not None:
                await self.live_state_refresher(owner, self._canonical_state_symbol(symbol))
            risk_result = self._check_risk_gate(order, strategy_name)
            if not risk_result.passed:
                return await self._reject_order(order, strategy_name, risk_result)

            if self.router.mode == "live":
                order.client_order_id = self._new_client_order_id()
                self._persist_order(order, strategy_name)

            try:
                submitted_order = await self.router.submit(order)
            except Exception as exc:
                await self._handle_submit_exception(order, strategy_name, exc)
                raise
            status_conflict = self._normalize_successful_submit_status(submitted_order)
            self._validate_paper_fill(submitted_order)
            try:
                self._persist_order(submitted_order, strategy_name)
            except Exception as exc:
                if self.router.mode != "live":
                    raise
                await self._record_post_submit_sync_failure(
                    submitted_order,
                    strategy_name,
                    exc,
                    reason_code="live_order_persistence_failed",
                    reason="Live order was accepted but local persistence failed",
                )
                accepted_id = (
                    submitted_order.exchange_order_id or submitted_order.client_order_id
                )
                raise AcceptedUnknownOrderResultError(
                    f"Exchange accepted order {accepted_id}; "
                    "local persistence failed and reconciliation is required"
                ) from exc
            if status_conflict:
                await self._record_provider_status_conflict(
                    submitted_order, strategy_name
                )
            try:
                if (
                    self.live_safeguards
                    and self.live_state_refresher is not None
                    and submitted_order.status == OrderStatus.FILLED
                ):
                    try:
                        await self.live_state_refresher(
                            owner, self._canonical_state_symbol(symbol)
                        )
                    except Exception as exc:
                        await self._record_post_submit_sync_failure(
                            submitted_order,
                            strategy_name,
                            exc,
                        )
                if self.router.mode == "live" and self.post_live_order_sync is not None:
                    try:
                        await self._run_callback(
                            self.post_live_order_sync,
                            strategy_name,
                            self._canonical_state_symbol(symbol),
                        )
                    except Exception as exc:
                        await self._record_post_submit_sync_failure(
                            submitted_order,
                            strategy_name,
                            exc,
                        )
            finally:
                await self._notify_order_update_safely(
                    strategy_name, submitted_order.id, "accepted submit"
                )
            return submitted_order
        finally:
            await self._release_live_submission_lease()

    @staticmethod
    def _cancel_response_succeeded(response: Any) -> bool:
        if response is None:
            # Backtest and compatibility handlers use None as a successful command
            # with no provider snapshot to return.
            return True
        if isinstance(response, bool):
            return response

        status = _response_value(response, "status")
        normalized_status = str(status or "").strip().lower()
        if normalized_status in {"cancelled", "canceled", "cancel", "closed", "filled"}:
            return UnifiedOrderManager._cancel_status_codes_succeeded(response)
        if normalized_status in {"open", "pending", "partially_filled", "rejected"}:
            return False

        has_status_code = any(
            _response_value(source, field) is not None
            for source in UnifiedOrderManager._cancel_response_sources(response)
            for field in ("code", "sCode")
        )
        has_nested_status_code = any(
            isinstance(_response_value(source, "data"), (list, tuple))
            and any(
                _response_value(item, "sCode") is not None
                for item in _response_value(source, "data")
            )
            for source in UnifiedOrderManager._cancel_response_sources(response)
        )
        if has_status_code or has_nested_status_code:
            return UnifiedOrderManager._cancel_status_codes_succeeded(response)
        raise ValueError(f"Unclear cancellation result; local order was not changed: {response!r}")

    @staticmethod
    def _cancel_response_sources(response: Any) -> tuple[Any, ...]:
        info = _response_value(response, "info")
        return (response, info) if isinstance(info, dict) else (response,)

    @staticmethod
    def _cancel_status_codes_succeeded(response: Any) -> bool:
        for source in UnifiedOrderManager._cancel_response_sources(response):
            for field in ("code", "sCode"):
                code = _response_value(source, field)
                if code is not None and str(code).strip() not in {"", "0"}:
                    return False
            data = _response_value(source, "data")
            if isinstance(data, (list, tuple)):
                for item in data:
                    item_code = _response_value(item, "sCode")
                    if item_code is not None and str(item_code).strip() not in {"", "0"}:
                        return False
        return True

    async def cancel(self, order_id: str, symbol: str | None = None) -> bool:
        cancel_response = await self.router.cancel(order_id, symbol)
        cancelled = self._cancel_response_succeeded(cancel_response)
        if not cancelled or self.repository is None or not hasattr(self.repository, "get_orders"):
            return cancelled

        candidates = self.repository.get_orders()
        local_order = next(
            (
                order
                for order in candidates
                if self._position_matches_runtime(order)
                and (
                    order.order_id == order_id
                    or order.exchange_order_id == order_id
                    or order.client_order_id == order_id
                )
            ),
            None,
        )
        if local_order is None:
            return cancelled
        if local_order.status in {
            OrderStatus.FILLED.value,
            OrderStatus.CANCELLED.value,
            OrderStatus.REJECTED.value,
            "canceled",
            "cancel",
        }:
            return cancelled

        response_filled_amount = _response_value(cancel_response, "filled_amount")
        if response_filled_amount is None:
            response_filled_amount = _response_value(cancel_response, "filled")
        response_remaining_amount = _response_value(cancel_response, "remaining_amount")
        if response_remaining_amount is None:
            response_remaining_amount = _response_value(cancel_response, "remaining")
        response_fill_price = _response_value(cancel_response, "fill_price")
        if response_fill_price is None:
            response_fill_price = _response_value(cancel_response, "average")
        response_status = str(_response_value(cancel_response, "status") or "").lower()
        has_response_quantity = (
            response_filled_amount is not None or response_remaining_amount is not None
        )
        reconciliation_reason = None
        if response_filled_amount is not None and response_remaining_amount is not None:
            try:
                filled_value = float(response_filled_amount)
                remaining_value = float(response_remaining_amount)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(
                    "invalid cancellation fill group; reconciliation required"
                ) from exc
            if (
                not math.isfinite(filled_value)
                or not 0.0 <= filled_value <= local_order.amount
                or not math.isfinite(remaining_value)
                or not 0.0 <= remaining_value <= local_order.amount
            ):
                raise ValueError("invalid cancellation fill group; reconciliation required")
            _validate_amount_reconciliation(
                local_order.amount,
                filled_value,
                remaining_value,
                error_message="inconsistent cancellation fill amounts; reconciliation required",
            )
        fill_order = None
        local_filled_amount = local_order.filled_amount
        filled_amount = local_filled_amount
        if response_filled_amount is not None:
            try:
                response_filled = float(response_filled_amount)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(
                    "invalid cancellation fill amount; reconciliation required"
                ) from exc
            if (
                not math.isfinite(response_filled)
                or not 0.0 <= response_filled <= local_order.amount
            ):
                raise ValueError("invalid cancellation fill amount; reconciliation required")
            filled_amount = response_filled
        elif response_remaining_amount is not None:
            try:
                remaining_amount = float(response_remaining_amount)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(
                    "invalid cancellation remaining amount; reconciliation required"
                ) from exc
            if (
                not math.isfinite(remaining_amount)
                or not 0.0 <= remaining_amount <= local_order.amount
            ):
                raise ValueError(
                    "invalid cancellation remaining amount; reconciliation required"
                )
            filled_amount = local_order.amount - remaining_amount
        if filled_amount < local_filled_amount:
            reconciliation_reason = "cancel_quantity_reconciliation_required"
            logger.warning(
                "Cancellation response regressed confirmed fill quantity; order_id=%s "
                "local_filled=%s response_filled=%s response_remaining=%s",
                local_order.order_id,
                local_filled_amount,
                response_filled_amount,
                response_remaining_amount,
            )
            filled_amount = local_filled_amount
        if (
            response_status in {"closed", "filled"}
            and not has_response_quantity
        ):
            if local_filled_amount >= local_order.amount:
                filled_amount = local_filled_amount
            else:
                reconciliation_reason = "cancel_outcome_reconciliation_required"
                logger.warning(
                    "Cancellation response claims terminal provider status without "
                    "quantity data; order_id=%s status=%s local_filled=%s amount=%s",
                    local_order.order_id,
                    response_status,
                    local_filled_amount,
                    local_order.amount,
                )
        if not math.isfinite(filled_amount) or not 0.0 <= filled_amount <= local_order.amount:
            raise ValueError("invalid cancellation fill amount; reconciliation required")
        if filled_amount > local_order.filled_amount:
            try:
                fill_price = float(response_fill_price)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("filled order requires a positive fill price") from exc
            if not math.isfinite(fill_price) or fill_price <= 0.0:
                raise ValueError("filled order requires a positive fill price")
            fill_status = (
                OrderStatus.FILLED
                if filled_amount >= local_order.amount
                else OrderStatus.PARTIALLY_FILLED
            )
            fill_order = Order(
                id=local_order.order_id,
                symbol=local_order.symbol,
                side=OrderSide(local_order.side),
                type=OrderType(local_order.type),
                amount=local_order.amount,
                price=local_order.price,
                status=fill_status,
                fill_price=fill_price,
                filled_amount=filled_amount,
                remaining_amount=local_order.amount - filled_amount,
            )
            local_order.filled_amount = filled_amount
            local_order.remaining_amount = local_order.amount - filled_amount
            local_order.fill_price = fill_price

        if filled_amount >= local_order.amount:
            local_order.status = OrderStatus.FILLED.value
        elif response_status in {"closed", "filled"} and not has_response_quantity:
            # A provider terminal status without quantities is not proof of a fill or
            # cancellation. Keep the locally confirmed pending/partial state.
            pass
        else:
            local_order.status = OrderStatus.CANCELLED.value
        if reconciliation_reason is not None:
            self._record_cancel_reconciliation_required(
                local_order, reconciliation_reason, cancel_response
            )
        local_order.updated_at = max(local_order.updated_at, self.timestamp_ms())
        order_record = OrderRecord(
            order_id=local_order.order_id,
            source=self._runtime_provenance()[0],
            runtime_mode=self._runtime_provenance()[1],
            exchange_order_id=local_order.exchange_order_id,
            client_order_id=local_order.client_order_id,
            strategy=local_order.strategy,
            symbol=local_order.symbol,
            side=local_order.side,
            type=local_order.type,
            amount=local_order.amount,
            price=local_order.price,
            status=local_order.status,
            fill_price=local_order.fill_price,
            timestamp=local_order.timestamp,
            updated_at=local_order.updated_at,
            filled_amount=local_order.filled_amount,
            remaining_amount=local_order.remaining_amount,
            snapshot_filled_amount=local_order.snapshot_filled_amount,
            snapshot_updated_at=local_order.snapshot_updated_at,
        )
        self._persist_order_record(
            order_record,
            local_order.strategy,
            order_record.updated_at,
            fill_order,
        )
        if self.on_order_update is not None:
            try:
                await self._run_callback(self.on_order_update, local_order.strategy)
            except BaseException:
                logger.exception(
                    "Order update callback failed after cancellation; order_id=%s",
                    local_order.order_id,
                )
        return cancelled

    def _record_cancel_reconciliation_required(
        self,
        order: OrderRecord,
        reason_code: str,
        response: Any,
    ) -> None:
        event = {
            "type": "risk_event",
            "strategy": order.strategy,
            "reason_code": reason_code,
            "reason": "Cancellation response requires order reconciliation",
            "severity": "high",
            "order_id": order.order_id,
            "symbol": order.symbol,
            "timestamp": self.timestamp_ms(),
            "event_key": f"cancel-reconciliation:{order.order_id}:{reason_code}",
            "response": repr(response),
            "local_filled_amount": order.filled_amount,
            "local_remaining_amount": order.remaining_amount,
        }
        save_risk_event = getattr(self.repository, "save_risk_event", None)
        if callable(save_risk_event):
            try:
                self._save_risk_event(event)
            except BaseException:
                logger.exception(
                    "Could not persist cancellation reconciliation event; order_id=%s",
                    order.order_id,
                )

    def _canonical_state_symbol(self, symbol: str) -> str:
        return canonical_symbol(
            symbol,
            market_type=self.live_market_type or "auto",
        )

    def get_position(
        self, strategy_name: str, symbol: str, side: str | None = None
    ) -> Any:
        positions = self.get_positions(strategy_name, symbol)
        if side is not None:
            normalized_side = str(getattr(side, "value", side)).lower()
            return next(
                (
                    position
                    for position in positions
                    if str(
                        getattr(
                            getattr(position, "side", ""),
                            "value",
                            getattr(position, "side", ""),
                        )
                    ).lower() == normalized_side
                ),
                None,
            )
        return positions[0] if positions else None

    def _runtime_provenance(self) -> tuple[str, str]:
        runtime_mode = str(getattr(self.router, "mode", "") or "").strip().lower()
        if runtime_mode == "live":
            return "live_exchange", "live"
        if runtime_mode == "demo":
            return "paper", "paper"
        if runtime_mode in {"paper", "backtest"}:
            return runtime_mode, runtime_mode
        return runtime_mode, runtime_mode

    def _position_matches_runtime(self, position: Any) -> bool:
        source, runtime_mode = self._runtime_provenance()
        actual_source = getattr(position, "source", None)
        actual_runtime = getattr(position, "runtime_mode", None)
        if actual_source == source and actual_runtime == runtime_mode:
            return True
        # Compatibility repositories may contain legacy non-live rows without
        # provenance. The real Repository query is always provenance-filtered.
        return runtime_mode != "live" and (
            actual_source in {None, "unknown"} or actual_runtime in {None, "unknown"}
        )

    def get_positions(self, strategy_name: str, symbol: str) -> list[Any]:
        symbol = self._canonical_state_symbol(symbol)
        if self.repository is None:
            return []
        source, runtime_mode = self._runtime_provenance()
        provenance = {"source": source, "runtime_mode": runtime_mode}
        if hasattr(self.repository, "get_positions"):
            try:
                positions = list(
                    self.repository.get_positions(strategy_name, symbol, **provenance)
                )
                if positions:
                    return [
                        position
                        for position in positions
                        if self._canonical_state_symbol(getattr(position, "symbol", "")) == symbol
                    ]
                positions = list(
                    self.repository.get_positions(strategy_name, None, **provenance)
                )
            except TypeError:
                try:
                    positions = list(self.repository.get_positions(strategy_name, symbol))
                    if positions:
                        return [
                            position
                            for position in positions
                            if self._canonical_state_symbol(
                                getattr(position, "symbol", "")
                            ) == symbol
                        ]
                    positions = list(self.repository.get_positions(strategy_name, None))
                except TypeError:
                    positions = list(self.repository.get_positions(strategy_name))
            return [
                position
                for position in positions
                if self._canonical_state_symbol(getattr(position, "symbol", "")) == symbol
                and getattr(position, "active", True)
                and self._position_matches_runtime(position)
            ]
        if hasattr(self.repository, "get_open_positions"):
            try:
                open_positions = self.repository.get_open_positions(strategy_name, **provenance)
            except TypeError:
                open_positions = self.repository.get_open_positions(strategy_name)
            return [
                position
                for position in open_positions
                if self._canonical_state_symbol(getattr(position, "symbol", "")) == symbol
                and getattr(position, "active", True)
                and self._position_matches_runtime(position)
            ]
        if hasattr(self.repository, "get_position"):
            try:
                position = self.repository.get_position(strategy_name, symbol, **provenance)
            except TypeError:
                position = self.repository.get_position(strategy_name, symbol)
            return [] if position is None else [position]
        return []

    def get_balance(self, strategy_name: str) -> float:
        if self.repository is not None and hasattr(self.repository, "get_account"):
            source, runtime_mode = self._runtime_provenance()
            try:
                account = self.repository.get_account(
                    strategy_name, source=source, runtime_mode=runtime_mode
                )
            except TypeError:
                account = self.repository.get_account(strategy_name)
            if account is not None and self._position_matches_runtime(account):
                return account.cash_balance
        return self._balances.get(strategy_name, 0.0)

    def set_balance(self, strategy_name: str, amount: float) -> None:
        self._balances[strategy_name] = amount

    async def _acquire_live_submission_lease(self) -> bool:
        if self.router.mode != "live" or self.repository is None:
            return True
        acquire = getattr(self.repository, "acquire_live_submission_lease", None)
        if not callable(acquire):
            logger.warning(
                "Live submission repository has no cross-process lock; using compatibility path"
            )
            return True
        try:
            result = acquire(self._live_lease_owner, timeout=1.0, lease_seconds=30.0)
            if inspect.isawaitable(result):
                result = await result
            return bool(result)
        except Exception:
            logger.exception("Could not acquire live submission lock")
            return False

    async def _release_live_submission_lease(self) -> None:
        if self.router.mode != "live" or self.repository is None:
            return
        release = getattr(self.repository, "release_live_submission_lease", None)
        if callable(release):
            try:
                result = release(self._live_lease_owner)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception("Could not release live submission lock")

    def _cached_market_metadata(self, symbol: str) -> Mapping[str, object] | None:
        handler = getattr(self.router, "live", None)
        exchange = getattr(handler, "_exchange", None)
        markets = getattr(exchange, "markets", None)
        if not isinstance(markets, Mapping):
            return None
        requested = self._canonical_state_symbol(symbol)
        for key, raw_market in markets.items():
            if not isinstance(raw_market, Mapping):
                continue
            candidates = (key, raw_market.get("id"), raw_market.get("symbol"))
            for candidate in candidates:
                if candidate is None:
                    continue
                try:
                    if self._canonical_state_symbol(str(candidate)) == requested:
                        return raw_market
                except (TypeError, ValueError):
                    continue
        return None

    def _enrich_live_order_metadata(self, order: Order) -> None:
        metadata: object | None = None
        if self.contract_size_provider is not None:
            try:
                metadata = self.contract_size_provider(order.symbol)
            except Exception:
                logger.exception(
                    "Could not resolve contract size metadata; symbol=%s",
                    order.symbol,
                )
        if metadata is None:
            metadata = self._cached_market_metadata(order.symbol)
        params = dict(order.params)
        if self.live_market_type:
            params["market_type"] = self.live_market_type
        if metadata is None:
            order.params = params
            return
        if isinstance(metadata, Mapping):
            info = metadata.get("info")
            sources = (metadata, info) if isinstance(info, Mapping) else (metadata,)
            for source in sources:
                if "is_inverse" in source:
                    params["is_inverse"] = source["is_inverse"]
                elif source.get("inverse") is True:
                    params["is_inverse"] = True
                if "contract_size" in source:
                    params["contract_size"] = source["contract_size"]
                elif "contractSize" in source:
                    params["contract_size"] = source["contractSize"]
                elif "ctVal" in source:
                    params["contract_size"] = source["ctVal"]
            settle = str(metadata.get("settle") or "").strip().upper()
            base = str(metadata.get("base") or "").strip().upper()
            if settle and base and settle == base:
                params["is_inverse"] = True
        else:
            params["is_inverse"] = True
            params["contract_size"] = metadata
        order.params = params

    def _order_value_for_risk(
        self, order: Order, price: float, positions: list[Any]
    ) -> float:
        inverse_position = next(
            (
                position for position in positions
                if getattr(position, "is_inverse", False)
                and getattr(position, "contract_size", None) is not None
            ),
            None,
        )
        inverse = is_inverse(order) or inverse_position is not None
        if not inverse:
            try:
                size = contract_size(order, default=None)
            except ValueError as exc:
                if "missing" not in str(exc):
                    raise
                linear_position = next(
                    (
                        position for position in positions
                        if not getattr(position, "is_inverse", False)
                    ),
                    None,
                )
                if linear_position is not None:
                    if getattr(linear_position, "contract_size", None) is None and getattr(
                        linear_position, "contracts", None
                    ) is None:
                        # Legacy live positions store base quantity directly; treat
                        # them as unit-sized linear quantities for reductions.
                        size = 1.0
                    else:
                        size = contract_size(linear_position, default=None)
                elif self.router.mode == "live" and self.live_market_type != "spot":
                    raise ValueError("missing contract_size metadata") from exc
                else:
                    return order_notional(order, price)
            return shared_order_notional(
                order,
                price,
                contract_size_value=size,
            )
        try:
            size = contract_size(order, default=None)
        except ValueError as exc:
            if "missing" not in str(exc):
                raise
            if inverse_position is None:
                raise ValueError("missing contract_size metadata") from exc
            size = contract_size(inverse_position, default=None)
        return shared_order_notional(
            order,
            price,
            is_inverse_instrument=True,
            contract_size_value=size,
        )

    def _check_risk_gate(self, order: Order, strategy_name: str) -> RiskGateResult:
        account = None
        positions: list[Any] = []
        state_owner = strategy_name
        state_symbol = self._canonical_state_symbol(order.symbol)

        if self.router.mode == "live" and strategy_name != "__exchange__":
            candidate_positions = self.get_positions("__exchange__", state_symbol)
            owned_positions = [
                position
                for position in candidate_positions
                if _position_has_explicit_ownership(
                    self.repository, position, strategy_name
                )
            ]
            if not owned_positions:
                return RiskGateResult(
                    False,
                    "Live position ownership is unavailable",
                )

        if self.risk_manager is None:
            if self.live_safeguards:
                return RiskGateResult(
                    passed=False,
                    reason="Live safeguards require a configured risk manager",
                )
            return RiskGateResult(passed=True)
        if self.repository is not None:
            if hasattr(self.repository, "get_account"):
                source, runtime_mode = self._runtime_provenance()
                if self.router.mode == "live":
                    state_owner = "__exchange__"
                try:
                    account = self.repository.get_account(
                        state_owner if self.router.mode == "live" else strategy_name,
                        source=source,
                        runtime_mode=runtime_mode,
                    )
                except TypeError:
                    account = self.repository.get_account(
                        state_owner if self.router.mode == "live" else strategy_name
                    )
                if account is not None and not self._position_matches_runtime(account):
                    account = None
            positions = self.get_positions(state_owner, state_symbol)

        order_price = order.price
        if order_price is None and self.price_provider is not None:
            order_price = self.price_provider(order.symbol)
        try:
            normalized_price = float(order_price)
        except (TypeError, ValueError, OverflowError):
            normalized_price = 0.0
        if not math.isfinite(normalized_price) or normalized_price <= 0:
            return RiskGateResult(
                passed=False,
                reason="Risk evaluation requires a current price",
            )
        order_price = normalized_price

        if self.live_safeguards:
            safety_positions = positions
            if self.router.mode == "live" and strategy_name != "__exchange__":
                candidate_positions = self.get_positions("__exchange__", state_symbol)
                owned_positions = [
                    position
                    for position in candidate_positions
                    if _position_has_explicit_ownership(
                        self.repository, position, strategy_name
                    )
                ]
                if not owned_positions:
                    return RiskGateResult(
                        False,
                        "Live position ownership is unavailable",
                        effective_price=order_price,
                    )
                safety_positions = owned_positions
            try:
                live_result = self._check_live_order_safety(
                    order, account, safety_positions, order_price
                )
            except ValueError as exc:
                if "contract_size" not in str(exc):
                    raise
                logger.warning(
                    "Rejecting live inverse order without valid contract size; symbol=%s reason=%s",
                    order.symbol,
                    exc,
                )
                return RiskGateResult(
                    False,
                    "Live inverse order requires valid contract size",
                    effective_price=order_price,
                )
            if not live_result.passed:
                return live_result

        try:
            current_position_value = sum(
                position_notional(position) for position in positions
            )
        except ValueError as exc:
            if "contract_size" not in str(exc) and "contracts" not in str(exc):
                raise
            return RiskGateResult(
                False,
                (
                    "Live inverse order requires valid contract size"
                    if self.router.mode == "live" and (
                        is_inverse(order)
                        or any(getattr(position, "is_inverse", False) for position in positions)
                    )
                    else "Live derivative order requires valid contract size"
                    if self.router.mode == "live"
                    else "Order requires valid contract size"
                ),
                effective_price=order_price,
            )
        reducing_side = "short" if order.side == OrderSide.BUY else "long"
        reducing_positions = [
            position for position in positions
            if str(getattr(position, "side", "")).lower() == reducing_side
        ]
        reducing_amount = sum(position_quantity(position) for position in reducing_positions)
        remaining_order_amount = max(0.0, order.amount - reducing_amount)
        reducing_value = 0.0
        remaining_to_reduce = min(order.amount, reducing_amount)
        for position in reducing_positions:
            available_quantity = position_quantity(position)
            matched_quantity = min(remaining_to_reduce, available_quantity)
            if available_quantity > 0.0:
                reducing_value += position_notional(position) * (
                    matched_quantity / available_quantity
                )
            remaining_to_reduce -= matched_quantity
            if remaining_to_reduce <= 0.0:
                break
        try:
            opening_order_value = self._order_value_for_risk(
                order, order_price, positions
            )
        except ValueError as exc:
            if "contract_size" not in str(exc):
                raise
            logger.warning(
                "Rejecting order risk evaluation without valid contract size; symbol=%s reason=%s",
                order.symbol,
                exc,
            )
            return RiskGateResult(
                False,
                (
                    "Live inverse order requires valid contract size"
                    if self.router.mode == "live" and (
                        is_inverse(order)
                        or any(getattr(position, "is_inverse", False) for position in positions)
                    )
                    else "Live derivative order requires valid contract size"
                    if self.router.mode == "live"
                    else "Order requires valid contract size"
                ),
                effective_price=order_price,
            )
        if order.amount > 0.0:
            opening_order_value *= remaining_order_amount / order.amount
        resulting_position_value = (
            max(0.0, current_position_value - reducing_value) + opening_order_value
        )
        try:
            current_other_position_value = self._other_position_notional(
                state_owner, state_symbol
            )
        except ValueError as exc:
            if "contract_size" not in str(exc) and "contracts" not in str(exc):
                raise
            return RiskGateResult(
                False,
                "Live inverse order requires valid contract size"
                if self.router.mode == "live"
                else "Order requires valid contract size",
                effective_price=order_price,
            )
        current_total_position_value = current_other_position_value + current_position_value
        resulting_total_position_value = current_other_position_value + resulting_position_value
        if resulting_total_position_value < current_total_position_value:
            risk_position_value = 0.0
            risk_order_value = 0.0
        else:
            risk_position_value = current_other_position_value
            risk_order_value = resulting_position_value

        total_equity = account.equity if account is not None else self.initial_equity
        daily_pnl = account.daily_pnl if account is not None else 0.0
        current_equity = account.equity if account is not None else self.initial_equity
        initial_equity = account.initial_equity if account is not None else self.initial_equity

        result = self.risk_manager.check_order(
            order=order,
            current_position_value=risk_position_value,
            total_equity=total_equity,
            order_value=risk_order_value,
            daily_pnl=daily_pnl,
            peak_equity=max(initial_equity, current_equity),
            current_equity=current_equity,
        )
        return RiskGateResult(
            passed=result.passed,
            reason=result.reason,
            order_value=resulting_position_value,
            effective_price=order_price,
        )

    def _check_live_order_safety(
        self,
        order: Order,
        account: Any,
        positions: list[Any],
        order_price: float | None,
    ) -> RiskGateResult:
        if account is None:
            return RiskGateResult(False, "Live safeguards require account state")
        if order_price is None or order_price <= 0:
            return RiskGateResult(False, "Live safeguards require a current price")

        current_amount_by_side = {
            "long": sum(
                position_quantity(position)
                for position in positions
                if str(getattr(position, "side", "")).lower() == "long"
            ),
            "short": sum(
                position_quantity(position)
                for position in positions
                if str(getattr(position, "side", "")).lower() == "short"
            ),
        }
        has_position = any(amount > 0 for amount in current_amount_by_side.values())
        reducing_side = "short" if order.side == OrderSide.BUY else "long"
        increasing_side = "long" if order.side == OrderSide.BUY else "short"
        if (
            self.live_market_type == "spot"
            and order.side == OrderSide.SELL
            and has_position
            and order.amount > current_amount_by_side["long"]
        ):
            return RiskGateResult(
                False,
                "Live spot sell exceeds available position",
                order_value=self._order_value_for_risk(order, order_price, positions),
                effective_price=order_price,
            )

        reduced_amount = min(order.amount, current_amount_by_side[reducing_side])
        resulting_amount = (
            current_amount_by_side[reducing_side] - reduced_amount,
            current_amount_by_side[increasing_side] + order.amount - reduced_amount,
        )
        is_reducing = has_position and reduced_amount > 0 and (
            sum(resulting_amount) < sum(current_amount_by_side.values())
        )
        if is_reducing:
            if self.live_market_type != "spot":
                order.reduce_only = True
                order.params = {**order.params, "reduceOnly": True}
            return RiskGateResult(True, effective_price=order_price)

        if not self.allow_live_open_orders:
            return RiskGateResult(
                False,
                "Live opening orders are disabled",
                order_value=self._order_value_for_risk(order, order_price, positions),
                effective_price=order_price,
            )

        if self.live_market_type == "spot" and order.side == OrderSide.SELL and not has_position:
            return RiskGateResult(
                False,
                "Live spot sell requires existing position",
                order_value=self._order_value_for_risk(order, order_price, positions),
                effective_price=order_price,
            )

        order_value = self._order_value_for_risk(order, order_price, positions)
        if self.live_max_order_notional > 0 and order_value > self.live_max_order_notional:
            return RiskGateResult(
                False,
                "Live order exceeds configured notional cap",
                order_value=order_value,
                effective_price=order_price,
            )

        return RiskGateResult(True, order_value=order_value, effective_price=order_price)

    def _other_position_notional(self, strategy_name: str, symbol: str) -> float:
        if self.repository is None or not hasattr(self.repository, "get_open_positions"):
            return 0.0
        source, runtime_mode = self._runtime_provenance()
        try:
            positions = self.repository.get_open_positions(
                strategy_name, source=source, runtime_mode=runtime_mode
            )
        except TypeError:
            positions = self.repository.get_open_positions(strategy_name)
        return sum(
            position_notional(position)
            for position in positions
            if getattr(position, "active", True)
            and self._position_matches_runtime(position)
            and self._canonical_state_symbol(getattr(position, "symbol", "")) != symbol
        )

    @staticmethod
    def _new_client_order_id() -> str:
        return f"bot-{uuid.uuid4().hex[:24]}"

    def _normalize_successful_submit_status(self, order: Order) -> bool:
        if order.status is not OrderStatus.REJECTED:
            return False
        if not order.exchange_order_id:
            return False

        filled_amount = order.filled_amount or 0.0
        try:
            normalized_filled = float(filled_amount)
        except (TypeError, ValueError, OverflowError):
            normalized_filled = 0.0
        if not math.isfinite(normalized_filled) or not 0.0 <= normalized_filled <= order.amount:
            normalized_filled = 0.0
        order.filled_amount = normalized_filled
        order.remaining_amount = order.amount - normalized_filled
        order.status = (
            OrderStatus.FILLED
            if normalized_filled >= order.amount
            else OrderStatus.PARTIALLY_FILLED
            if normalized_filled > 0.0
            else OrderStatus.PENDING
        )
        return True

    async def _record_provider_status_conflict(
        self, order: Order, strategy_name: str
    ) -> None:
        event = {
            "type": "risk_event",
            "strategy": strategy_name,
            "reason_code": "provider_status_conflict_reconciliation_required",
            "reason": (
                "Provider returned a rejected status with a reliable order identity; "
                "local order was accepted and requires reconciliation"
            ),
            "severity": "high",
            "order_id": order.id,
            "symbol": order.symbol,
            "status": order.status.value,
            "exchange_order_id": order.exchange_order_id,
            "client_order_id": order.client_order_id,
            "filled_amount": order.filled_amount,
            "remaining_amount": order.remaining_amount,
            "timestamp": self.timestamp_ms(),
            "event_key": (
                f"provider-status-conflict:{order.exchange_order_id or order.client_order_id}"
            ),
        }
        try:
            self._save_risk_event(event)
        except BaseException:
            logger.exception(
                "Could not persist provider status conflict event; order_id=%s",
                order.id,
            )
        if self.on_risk_event is not None:
            try:
                await self._run_callback(self.on_risk_event, event)
            except BaseException:
                logger.exception(
                    "Provider status conflict event callback failed; order_id=%s",
                    order.id,
                )

    async def _record_post_submit_sync_failure(
        self,
        order: Order,
        strategy_name: str,
        error: Exception,
        *,
        reason_code: str = "post_submit_sync_failed",
        reason: str = "Live order was accepted but post-submit private sync failed",
    ) -> None:
        event = {
            "type": "risk_event",
            "strategy": strategy_name,
            "reason_code": reason_code,
            "reason": reason,
            "severity": "high",
            "order_id": order.id,
            "symbol": order.symbol,
            "side": order.side.value,
            "order_type": order.type.value,
            "amount": order.amount,
            "status": order.status.value,
            "filled_amount": order.filled_amount,
            "remaining_amount": order.remaining_amount,
            "exchange_order_id": order.exchange_order_id,
            "client_order_id": order.client_order_id,
            "timestamp": self.timestamp_ms(),
            "event_key": (
                f"{reason_code}:{order.client_order_id or order.id}"
            ),
            "error": repr(error),
        }
        try:
            self._save_risk_event(event)
        except BaseException:
            logger.exception(
                "Could not persist post-submit sync failure event; order_id=%s",
                order.id,
            )
        if self.on_risk_event is not None:
            try:
                await self._run_callback(self.on_risk_event, event)
            except BaseException:
                logger.exception(
                    "Post-submit sync failure event callback failed; order_id=%s",
                    order.id,
                )

    @staticmethod
    def _classify_submit_exception(error: Exception) -> str:
        if isinstance(error, ccxt.InvalidOrder):
            return "rejected"
        return "accepted_unknown"

    async def _handle_submit_exception(
        self, order: Order, strategy_name: str, error: Exception
    ) -> None:
        try:
            self._apply_exception_identity(order, error)
            if self.router.mode != "live":
                return
            if self._classify_submit_exception(error) == "rejected":
                order.status = OrderStatus.REJECTED
                self._persist_order(order, strategy_name)
                return

            order.status = OrderStatus.PENDING
            self._persist_order(order, strategy_name)
            event = {
                "type": "risk_event",
                "strategy": strategy_name,
                "reason_code": "live_order_submission_unknown",
                "reason": (
                    "Live order submission outcome is unknown; reconciliation required"
                ),
                "severity": "high",
                "order_id": order.id,
                "symbol": order.symbol,
                "side": order.side.value,
                "order_type": order.type.value,
                "amount": order.amount,
                "exchange_order_id": order.exchange_order_id,
                "client_order_id": order.client_order_id,
                "timestamp": self.timestamp_ms(),
                "event_key": f"live-order-submission-unknown:{order.client_order_id}",
            }
            save_risk_event = getattr(self.repository, "save_risk_event", None)
            if callable(save_risk_event):
                try:
                    self._save_risk_event(event)
                except Exception:
                    logger.exception(
                        "Could not persist unknown live submission event; order_id=%s",
                        order.id,
                    )
            if self.on_risk_event is not None:
                try:
                    await self._run_callback(self.on_risk_event, event)
                except Exception:
                    logger.exception(
                        "Unknown live submission event callback failed; order_id=%s",
                        order.id,
                    )
        finally:
            await self._notify_order_update_safely(
                strategy_name, order.id, "submit exception"
            )

    async def _notify_order_update_safely(
        self, strategy_name: str, order_id: str, context: str
    ) -> None:
        if self.on_order_update is None:
            return
        try:
            await self._run_callback(self.on_order_update, strategy_name)
        except BaseException:
            logger.exception(
                "Order update callback failed after %s; order_id=%s",
                context,
                order_id,
            )

    @staticmethod
    def _is_clear_submit_rejection(error: Exception) -> bool:
        return isinstance(error, ccxt.InvalidOrder)

    @staticmethod
    def _apply_exception_identity(order: Order, error: Exception) -> None:
        values: dict[str, object] = {}
        for source in (error, getattr(error, "info", None), getattr(error, "response", None)):
            if isinstance(source, dict):
                values.update(source)
            elif source is not None:
                for name in (
                    "id",
                    "orderId",
                    "exchange_order_id",
                    "clientOrderId",
                    "clientOid",
                    "client_order_id",
                ):
                    value = getattr(source, name, None)
                    if value is not None:
                        values[name] = value
        exchange_order_id = str(
            values.get("exchange_order_id") or values.get("id") or values.get("orderId") or ""
        ).strip()
        client_order_id = str(
            values.get("client_order_id")
            or values.get("clientOrderId")
            or values.get("clientOid")
            or ""
        ).strip()
        if exchange_order_id:
            order.exchange_order_id = exchange_order_id
        if client_order_id:
            order.client_order_id = client_order_id

    async def _reject_order(
        self,
        order: Order,
        strategy_name: str,
        risk_result: RiskGateResult,
    ) -> Order:
        order.status = OrderStatus.REJECTED
        timestamp = self.timestamp_ms()
        self._persist_order(order, strategy_name, timestamp=timestamp)
        try:
            payload = self._risk_event_payload(order, strategy_name, risk_result, timestamp)
            if risk_result.reason in {
                "Live position ownership is unavailable",
                "Live inverse order requires valid contract size",
            }:
                payload["severity"] = "high"
                payload["event_key"] = (
                    f"live-risk:{risk_result.reason}:{strategy_name}:{order.symbol}:"
                    f"{order.side.value}:{order.amount}:{timestamp}"
                )
                save_risk_event = getattr(self.repository, "save_risk_event", None)
                if callable(save_risk_event):
                    self._save_risk_event(payload)
            if self.on_risk_event is not None:
                await self._run_callback(self.on_risk_event, payload)
        finally:
            if self.on_order_update is not None:
                await self._run_callback(self.on_order_update, strategy_name)
        return order

    @staticmethod
    async def _run_callback(callback: Callable[..., Awaitable[None] | None], *args: object) -> None:
        result = callback(*args)
        if inspect.isawaitable(result):
            await result

    def _risk_event_payload(
        self,
        order: Order,
        strategy_name: str,
        result: RiskGateResult,
        timestamp: int,
    ) -> dict[str, object]:
        return {
            "type": "risk_event",
            "strategy": strategy_name,
            "order_id": order.id,
            "symbol": order.symbol,
            "side": order.side.value,
            "order_type": order.type.value,
            "amount": order.amount,
            "price": result.effective_price,
            "requested_price": order.price,
            "order_value": result.order_value,
            "reason": result.reason,
            "reason_code": risk_reason_code(result.reason),
            "timestamp": timestamp,
        }

    def _validate_paper_fill(self, order: Order) -> None:
        if getattr(self.router, "mode", None) == "live":
            return
        status = str(getattr(order.status, "value", order.status))
        filled_amount = float(getattr(order, "filled_amount", 0.0) or 0.0)
        if status not in {OrderStatus.FILLED.value, OrderStatus.PARTIALLY_FILLED.value}:
            return
        if status == OrderStatus.PARTIALLY_FILLED.value and filled_amount <= 0.0:
            return
        price = order.fill_price if order.fill_price is not None else order.price
        try:
            normalized_price = float(price)
        except (TypeError, ValueError, OverflowError):
            normalized_price = 0.0
        if not math.isfinite(normalized_price) or normalized_price <= 0.0:
            raise ValueError("filled order requires a positive fill price")

    def _persist_order(
        self,
        order: Order,
        strategy_name: str,
        timestamp: int | None = None,
    ) -> None:
        if self.repository is None:
            return

        if timestamp is None:
            timestamp = order.fill_time or self.timestamp_ms()
        filled_amount = getattr(order, "filled_amount", 0.0)
        remaining_amount = getattr(order, "remaining_amount", None)
        if order.status == OrderStatus.FILLED and not filled_amount:
            filled_amount = order.amount
            remaining_amount = 0.0

        order_record = OrderRecord(
            order_id=order.id,
            source=self._runtime_provenance()[0],
            runtime_mode=self._runtime_provenance()[1],
            exchange_order_id=order.exchange_order_id,
            client_order_id=order.client_order_id,
            strategy=strategy_name,
            symbol=order.symbol,
            side=order.side.value,
            type=order.type.value,
            amount=order.amount,
            price=order.price or 0.0,
            status=order.status.value,
            fill_price=order.fill_price or 0.0,
            timestamp=timestamp,
            updated_at=order.updated_at,
            filled_amount=filled_amount,
            remaining_amount=remaining_amount,
        )
        accounting = (
            order
            if order.status in {OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED}
            and getattr(self.router, "mode", None) != "live"
            else None
        )
        self._persist_order_record(order_record, strategy_name, timestamp, accounting)

    def _persist_order_record(
        self,
        order_record: OrderRecord,
        strategy_name: str,
        timestamp: int,
        accounting_order: Order | None = None,
    ) -> None:
        if accounting_order is None:
            self.repository.upsert_order(order_record)
            return

        service = PaperAccountingService(
            repository=self.repository,
            initial_equity=self.initial_equity,
            fee_rate=self.fee_rate,
            runtime_mode=self._runtime_provenance()[1],
        )
        persist_atomic = getattr(self.repository, "persist_order_and_accounting", None)
        if callable(persist_atomic):
            try:
                persist_atomic(
                    order_record,
                    lambda transaction: service.process_filled_order(
                        accounting_order,
                        strategy_name,
                        timestamp,
                        transaction=transaction,
                    ),
                )
            except BaseException:
                logger.exception(
                    "Paper order accepted by router but local persistence failed; "
                    "order_id=%s strategy=%s requires reconciliation",
                    order_record.order_id,
                    strategy_name,
                )
                self._record_accounting_reconciliation_required(
                    order_record, strategy_name, timestamp
                )
                raise
            return

        self.repository.upsert_order(order_record)
        service.process_filled_order(accounting_order, strategy_name, timestamp)

    def _record_accounting_reconciliation_required(
        self, order_record: OrderRecord, strategy_name: str, timestamp: int
    ) -> None:
        save_risk_event = getattr(self.repository, "save_risk_event", None)
        if not callable(save_risk_event):
            return
        try:
            self._save_risk_event({
                "type": "risk_event",
                "strategy": strategy_name,
                "reason_code": "paper_accounting_reconciliation_required",
                "reason": "Paper order was accepted but local accounting rolled back",
                "order_id": order_record.order_id,
                "symbol": order_record.symbol,
                "timestamp": timestamp,
                "event_key": f"paper-accounting-reconciliation:{order_record.order_id}",
            })
        except BaseException:
            logger.exception(
                "Could not persist paper accounting reconciliation event; order_id=%s",
                order_record.order_id,
            )

    def _save_risk_event(self, event: dict[str, object]) -> None:
        save_risk_event = getattr(self.repository, "save_risk_event", None)
        if not callable(save_risk_event):
            return
        runtime_mode = "paper" if self.router.mode == "demo" else self.router.mode
        source = "live_exchange" if runtime_mode == "live" else runtime_mode
        try:
            save_risk_event(
                event,
                source=source,
                runtime_mode=runtime_mode,
            )
        except TypeError:
            save_risk_event(event)

    @staticmethod
    def _current_timestamp_ms() -> int:
        return int(time.time() * 1000)
