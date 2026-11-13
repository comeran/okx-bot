"""用交易所可用余额确认策略资金归属。草稿本身仍然不授权交易。"""

from __future__ import annotations

from src.exchange.base import canonical_symbol


class OwnershipError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def grant_ownership_from_balance(
    repository,
    *,
    scope_id: str,
    strategy: str,
    symbol: str,
    budget: float,
    available: float,
    environment: str,
    timestamp: int,
) -> None:
    if environment not in {"sandbox", "live"}:
        raise OwnershipError(
            "ownership_environment_invalid",
            "Ownership can only be recorded for sandbox or live",
        )
    if budget <= 0 or available + 1e-8 < budget:
        raise OwnershipError(
            "balance_insufficient",
            "Exchange quote balance does not cover the draft budget",
        )
    try:
        normalized = canonical_symbol(symbol, market_type="spot")
    except ValueError as exc:
        raise OwnershipError("invalid_symbol", "A spot symbol is required") from exc
    repository.upsert_strategy_ownership(
        scope=scope_id,
        strategy=strategy,
        symbol=normalized,
        environment=environment,
        budget=float(budget),
        available=float(available),
        updated_at=int(timestamp),
    )
