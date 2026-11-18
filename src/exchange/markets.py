"""Derive market routing from the full instrument identity."""

from src.exchange.base import canonical_symbol, is_supported_canonical_symbol


def market_type_for_symbol(symbol: object) -> str:
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("A supported instrument symbol is required")
    for market in ("option", "future", "swap", "spot"):
        normalized = canonical_symbol(symbol, market_type=market)
        if is_supported_canonical_symbol(normalized, market_type=market):
            return market
    raise ValueError("Unsupported instrument symbol")
