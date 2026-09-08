from __future__ import annotations

import ccxt.async_support as ccxt

OKX_RUNTIME_TIMEFRAME_MILLISECONDS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "1w": 604_800_000,
}


class SafeOKXExchange(ccxt.okx):
    async def fetch_markets(self, params: dict | None = None) -> list[dict]:
        markets = await super().fetch_markets(params or {})
        return [
            market
            for market in markets
            if isinstance(market, dict)
            and isinstance(market.get("id"), str)
            and market["id"].strip()
            and isinstance(market.get("symbol"), str)
            and market["symbol"].strip()
        ]


def create_okx_client(
    *,
    api_key: str,
    secret: str,
    passphrase: str,
    default_type: str,
    demo: bool,
) -> SafeOKXExchange:
    config: dict[str, object] = {"options": {"defaultType": default_type}}
    if api_key.strip():
        config["apiKey"] = api_key
    if secret.strip():
        config["secret"] = secret
    if passphrase.strip():
        config["password"] = passphrase

    exchange = SafeOKXExchange(config)
    if demo:
        exchange.set_sandbox_mode(True)
    return exchange
