"""Exact collectors reject local observation timestamps before any I/O."""

import pytest

from src.ops.exact_private_sync import (
    sync_exact_account_balance,
    sync_exact_account_config,
    sync_exact_spot_fill_page,
    sync_exact_spot_instruments,
)

INVALID_TIMES = [-1, True, 1.5, "1", None, 2**63]


class NoIOExchange:
    def __init__(self):
        self.requests = []

    async def private_get_account_config(self, params):
        self.requests.append(("config", params))
        raise AssertionError("invalid timestamp must fail before provider access")

    async def private_get_account_balance(self, params):
        self.requests.append(("balance", params))
        raise AssertionError("invalid timestamp must fail before provider access")

    async def public_get_public_instruments(self, params):
        self.requests.append(("instruments", params))
        raise AssertionError("invalid timestamp must fail before provider access")

    async def private_get_trade_fills_history(self, params):
        self.requests.append(("fills", params))
        raise AssertionError("invalid timestamp must fail before provider access")


class NoIORepository:
    def __getattr__(self, name):
        raise AssertionError(f"invalid timestamp must fail before repository access: {name}")


@pytest.mark.asyncio
@pytest.mark.parametrize("received_at", INVALID_TIMES)
async def test_config_timestamp_is_validated_before_provider_or_repository(
    received_at,
):
    exchange = NoIOExchange()
    with pytest.raises(ValueError):
        await sync_exact_account_config(NoIORepository(), exchange, received_at)
    assert exchange.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("received_at", INVALID_TIMES)
async def test_instrument_timestamp_is_validated_before_provider_or_repository(
    received_at,
):
    exchange = NoIOExchange()
    with pytest.raises(ValueError):
        await sync_exact_spot_instruments(
            NoIORepository(), exchange, received_at, inst_id="BTC-USDT",
        )
    assert exchange.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("received_at", INVALID_TIMES)
async def test_balance_timestamp_is_validated_before_uid_lookup_or_provider(
    received_at,
):
    exchange = NoIOExchange()
    with pytest.raises(ValueError):
        await sync_exact_account_balance(
            NoIORepository(), exchange, received_at, uid="00123",
        )
    assert exchange.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("received_at", INVALID_TIMES)
async def test_fill_timestamp_is_validated_before_cursor_lookup_or_provider(
    received_at,
):
    exchange = NoIOExchange()
    with pytest.raises(ValueError):
        await sync_exact_spot_fill_page(
            NoIORepository(), exchange, uid="00123", received_at=received_at,
        )
    assert exchange.requests == []
