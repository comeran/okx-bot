from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from src.exchange.base import ExchangeAdapter
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
) -> ExchangeAdapter:
    if not exchange.api_key or not exchange.secret or not exchange.passphrase:
        raise ValueError("Live trading requires OKX api_key, secret, and passphrase")

    market_type = exchange.market_type.strip().lower()
    adapters = adapter_classes or OKX_ADAPTERS
    adapter_cls = adapters.get(market_type)
    if adapter_cls is None:
        raise ValueError(f"Unsupported OKX market_type for live trading: {exchange.market_type}")

    return adapter_cls(exchange.api_key, exchange.secret, exchange.passphrase, demo=exchange.demo)
