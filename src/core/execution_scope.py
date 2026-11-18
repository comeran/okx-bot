"""Immutable configured account identity, independent of market capabilities."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from src.core.config import ExchangeConfig


@dataclass(frozen=True)
class ExecutionScope:
    exchange: str
    environment: str
    account_fingerprint: str

    def __post_init__(self) -> None:
        if self.exchange != "okx" or self.environment not in {"sandbox", "live"}:
            raise ValueError("Unsupported execution account context")
        if not isinstance(self.account_fingerprint, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.account_fingerprint,
        ):
            raise ValueError("A configured credential fingerprint is required")

    @property
    def account_scope_id(self) -> str:
        identity = [self.exchange, self.environment, self.account_fingerprint]
        return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


_TRADABLE_MARKETS = frozenset({"spot", "swap", "future", "option"})


def tradable_market(value: object) -> str | None:
    """现货、永续、交割和期权都可以作为交易账户。"""
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if text == "futures":
        text = "future"
    if text == "options":
        text = "option"
    return text if text in _TRADABLE_MARKETS else None


def build_execution_context(
    exchange: ExchangeConfig,
) -> tuple[dict[str, object], ExecutionScope | None]:
    environment = "sandbox" if exchange.demo else "live"
    configured = all(
        isinstance(value, str) and value.strip()
        for value in (exchange.api_key, exchange.secret, exchange.passphrase)
    )
    reason = "credentials_unavailable" if not configured else None
    scope = None
    if reason is None:
        scope = ExecutionScope(
            exchange="okx", environment=environment,
            account_fingerprint=hashlib.sha256(exchange.api_key.strip().encode()).hexdigest(),
        )
    return {
        "account_scope_id": scope.account_scope_id if scope is not None else None,
        "exchange": "okx", "environment": environment, "market_type": "all",
        "market_types": ["spot", "swap", "future", "option"],
        "identity_verified": False, "available": scope is not None, "reason": reason,
        "execution_ready": False,
    }, scope
