from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from src.exchange.base import ExchangeAdapter
from src.exchange.okx_account import OKXAccountAdapter
from src.exchange.okx_futures import OKXFuturesAdapter
from src.exchange.okx_options import OKXOptionsAdapter
from src.exchange.okx_spot import OKXSpotAdapter
from src.exchange.okx_swap import OKXSwapAdapter


class OKXAdapterFactory(Protocol):
    def __call__(
        self,
        api_key: str,
        secret: str,
        passphrase: str,
        demo: bool = True,
    ) -> ExchangeAdapter: ...


OKX_ADAPTERS: Mapping[str, OKXAdapterFactory] = {
    "spot": OKXSpotAdapter,
    "swap": OKXSwapAdapter,
    "future": OKXFuturesAdapter,
    "futures": OKXFuturesAdapter,
    "option": OKXOptionsAdapter,
    "options": OKXOptionsAdapter,
}


def create_okx_adapter(
    exchange: object,
    adapter_classes: Mapping[str, OKXAdapterFactory] | None = None,
    *,
    market_type: str | None = None,
) -> ExchangeAdapter:
    credentials = (exchange.api_key, exchange.secret, exchange.passphrase)
    if any(not isinstance(value, str) or not value.strip() for value in credentials):
        raise ValueError("Live trading requires OKX api_key, secret, and passphrase")

    adapters = adapter_classes or OKX_ADAPTERS
    if market_type is None:
        return OKXAccountAdapter(
            exchange.api_key, exchange.secret, exchange.passphrase,
            demo=exchange.demo, adapter_classes=adapters,
        )
    normalized = market_type.strip().lower()
    adapter_cls = adapters.get(normalized)
    if adapter_cls is None:
        raise ValueError(f"Unsupported OKX market_type for live trading: {market_type}")

    return adapter_cls(exchange.api_key, exchange.secret, exchange.passphrase, demo=exchange.demo)
