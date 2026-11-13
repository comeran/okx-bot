from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from fastapi import HTTPException

from src.core.execution_scope import ExecutionScope, build_execution_context
from src.data.exchange_store import ExchangeStoreError, open_exchange_repository
from src.data.repository import Repository


@dataclass(frozen=True)
class BoundExchangeStore:
    exchange: object
    context: dict[str, object]
    scope: ExecutionScope | None
    master: Repository
    repository: Repository | None


def bind_exchange_store(
    exchange: object,
    master: Repository,
    expected_account_scope_id: str | None,
    *,
    require_available: bool = False,
) -> BoundExchangeStore:
    """Capture credentials, identity and facts storage before any provider await."""
    captured_exchange = deepcopy(exchange)
    context, scope = build_execution_context(captured_exchange)
    if scope is None:
        if require_available:
            raise HTTPException(status_code=409, detail={
                "code": "account_context_unavailable",
                "message": "Exchange account context is unavailable",
            })
        return BoundExchangeStore(captured_exchange, context, None, master, None)
    if expected_account_scope_id != scope.account_scope_id:
        raise HTTPException(status_code=409, detail={
            "code": "account_scope_changed",
            "message": "Reload the current exchange account context",
        })
    try:
        repository = open_exchange_repository(scope, master_engine=master.engine)
    except ExchangeStoreError as exc:
        raise HTTPException(status_code=503, detail={
            "code": "account_store_unavailable",
            "message": "Exchange account facts are unavailable",
        }) from exc
    return BoundExchangeStore(captured_exchange, context, scope, master, repository)
