from __future__ import annotations

import pytest

from src.exchange.okx_client import (
    OKX_RUNTIME_TIMEFRAME_MILLISECONDS,
    SafeOKXExchange,
    create_okx_client,
)


def test_runtime_timeframes_are_fixed_duration_okx_capabilities():
    assert OKX_RUNTIME_TIMEFRAME_MILLISECONDS == {
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
    assert {"7m", "invalid", "1M", "3M"}.isdisjoint(
        OKX_RUNTIME_TIMEFRAME_MILLISECONDS
    )


def _market_record(market_id: object, symbol: object) -> dict[str, object]:
    return {
        "id": market_id,
        "symbol": symbol,
        "base": "BTC",
        "quote": "USDT",
        "settle": "USDT",
        "type": "swap",
        "spot": False,
        "swap": True,
        "future": False,
        "option": False,
        "active": True,
        "contract": True,
        "linear": True,
        "inverse": False,
        "taker": 0.001,
        "maker": 0.001,
        "precision": {"amount": 0.001, "price": 0.1},
        "limits": {"amount": {"min": 0.001}, "price": {"min": 0.1}, "cost": {"min": 1}},
        "info": {},
    }


def _synthetic_raw_markets() -> tuple[list[object], dict[str, object]]:
    raw_valid = _market_record("BTC-USDT-SWAP", "BTC/USDT:USDT")

    missing_id = _market_record("ETH-USDT-SWAP", "ETH/USDT:USDT")
    missing_id.pop("id")

    missing_symbol = _market_record("SOL-USDT-SWAP", "SOL/USDT:USDT")
    missing_symbol.pop("symbol")

    return (
        [
            _market_record(None, "BTC/USDT:USDT"),
            _market_record("", "BTC/USDT:USDT"),
            _market_record("  ", "BTC/USDT:USDT"),
            missing_id,
            missing_symbol,
            _market_record("XRP-USDT-SWAP", ""),
            _market_record("DOGE-USDT-SWAP", "   "),
            "not-a-market-record",
            raw_valid,
        ],
        raw_valid,
    )


@pytest.mark.asyncio
async def test_fetch_markets_filters_invalid_records_and_keeps_valid_records_unchanged(monkeypatch):
    raw_markets, raw_valid = _synthetic_raw_markets()

    async def fake_fetch_markets(self, params=None):
        assert params == {}
        return raw_markets

    monkeypatch.setattr(SafeOKXExchange.__mro__[1], "fetch_markets", fake_fetch_markets)

    exchange = SafeOKXExchange()
    filtered = await exchange.fetch_markets()
    await exchange.close()

    assert filtered == [raw_valid]
    assert filtered[0] is raw_valid


@pytest.mark.asyncio
async def test_real_set_markets_rejects_unfiltered_synthetic_markets_with_mixed_ids():
    raw_markets, _ = _synthetic_raw_markets()
    exchange = SafeOKXExchange()

    try:
        with pytest.raises(TypeError):
            exchange.set_markets(raw_markets, None)
    finally:
        await exchange.close()


@pytest.mark.asyncio
async def test_filtered_markets_can_be_loaded_into_real_set_markets_without_type_error(monkeypatch):
    raw_markets, raw_valid = _synthetic_raw_markets()

    async def fake_fetch_markets(self, params=None):
        return raw_markets

    monkeypatch.setattr(SafeOKXExchange.__mro__[1], "fetch_markets", fake_fetch_markets)

    exchange = SafeOKXExchange()
    filtered = await exchange.fetch_markets()
    exchange.set_markets(filtered, None)

    assert filtered == [raw_valid]
    assert filtered[0] is raw_valid
    assert exchange.markets_by_id == {"BTC-USDT-SWAP": [filtered[0]]}
    await exchange.close()


class FakeSafeOKXExchange:
    instances: list[FakeSafeOKXExchange] = []

    def __init__(self, config):
        self.config = config
        self.sandbox_enabled = None
        self.instances.append(self)

    def set_sandbox_mode(self, enabled):
        self.sandbox_enabled = enabled


def test_create_okx_client_passes_non_empty_credentials_and_demo_mode(monkeypatch):
    FakeSafeOKXExchange.instances = []
    monkeypatch.setattr("src.exchange.okx_client.SafeOKXExchange", FakeSafeOKXExchange)

    exchange = create_okx_client(
        api_key="api-key",
        secret="secret",
        passphrase="passphrase",
        default_type="swap",
        demo=True,
    )

    assert exchange is FakeSafeOKXExchange.instances[0]
    assert exchange.config == {
        "apiKey": "api-key",
        "secret": "secret",
        "password": "passphrase",
        "options": {"defaultType": "swap"},
    }
    assert exchange.sandbox_enabled is True


def test_create_okx_client_omits_blank_credentials_and_skips_sandbox_for_live(monkeypatch):
    FakeSafeOKXExchange.instances = []
    monkeypatch.setattr("src.exchange.okx_client.SafeOKXExchange", FakeSafeOKXExchange)

    exchange = create_okx_client(
        api_key="",
        secret="   ",
        passphrase="",
        default_type="future",
        demo=False,
    )

    assert exchange.config == {"options": {"defaultType": "future"}}
    assert exchange.sandbox_enabled is None
