"""A credential set can fund and run instruments from independent markets."""

import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from src.core.config import AppConfig, ExchangeConfig
from src.core.runtime_settings import save_runtime_settings
from src.data.allocations import AllocationScope, AllocationStore
from src.data.models import OrderRecord, StrategyConfigRecord
from src.data.repository import Repository
from src.ops.phase3_recovery import build_live_identity_lookup
from src.web.api import allocations, strategies


def configured_settings(*, mode="paper", legacy_market="swap"):
    return AppConfig(mode=mode, exchange=ExchangeConfig(
        api_key="shared-key", secret="secret", passphrase="passphrase",
        market_type=legacy_market,
    ))


def test_explicit_allocation_scopes_keep_existing_hashes_and_ignore_legacy_market():
    settings = configured_settings()
    fingerprint = hashlib.sha256(b"shared-key").hexdigest()
    scopes = {}
    for market in ("spot", "swap"):
        context, scope = allocations.allocation_context(settings, market_type=market)
        expected = hashlib.sha256(json.dumps(
            ["okx", "sandbox", market, fingerprint], separators=(",", ":"),
        ).encode()).hexdigest()
        assert scope is not None
        assert scope.scope_id == expected
        assert context["market_type"] == market
        scopes[market] = scope.scope_id
    assert scopes["spot"] != scopes["swap"]
    assert allocations.allocation_context(settings)[0]["market_type"] == "spot"


def test_strategy_drafts_derive_independent_markets_from_symbols():
    save_runtime_settings(configured_settings())
    repository = Repository()
    runtime = strategies.StrategyRuntimeState()
    for name, symbol in (("spot_alpha", "BTC-USDT"), ("swap_alpha", "BTC-USDT-SWAP")):
        repository.create_strategy_config(StrategyConfigRecord(
            name=name, strategy_type="ma_cross", symbol=symbol,
            timeframe="1m", enabled=True, params={}, created_at=1, updated_at=1,
        ))
        runtime.strategy_status[name] = "stopped"
    app = FastAPI()
    app.include_router(allocations.create_router(runtime), prefix="/allocations")
    with TestClient(app) as client:
        scopes = {}
        for name, symbol, market in (
            ("spot_alpha", "BTC-USDT", "spot"),
            ("swap_alpha", "BTC-USDT-SWAP", "swap"),
        ):
            response = client.get(f"/allocations/{name}")
            assert response.status_code == 200, response.text
            payload = response.json()
            scopes[market] = payload["context"]["scope_id"]
            assert payload["context"]["market_type"] == market
            saved = client.put(f"/allocations/{name}", json={
                "expected_symbol": symbol,
                "expected_scope_id": scopes[market],
                "expected_revision": 0,
                "budget": 100,
            })
            assert saved.status_code == 200, saved.text
            assert saved.json()["draft"]["market_type"] == market
        assert scopes["spot"] != scopes["swap"]
        wrong_scope = client.put("/allocations/spot_alpha", json={
            "expected_symbol": "BTC-USDT", "expected_scope_id": scopes["swap"],
            "expected_revision": 1, "budget": 200,
        })
        assert wrong_scope.status_code == 409
        assert wrong_scope.json()["detail"]["code"] == "allocation_scope_changed"
        for operation in ("activate", "release"):
            rejected = client.post(f"/allocations/swap_alpha/{operation}", json={
                "expected_symbol": "BTC-USDT-SWAP", "expected_scope_id": scopes["swap"],
                "expected_revision": 1,
            })
            assert rejected.status_code == 409, rejected.text
            assert rejected.json()["detail"]["code"] == "paper_spot_only"


def test_allocation_store_saves_derivative_symbols_in_derivative_scope():
    repository = Repository()
    scope = AllocationScope(hashlib.sha256(b"shared-key").hexdigest(), "sandbox", "swap")
    draft = AllocationStore(repository.engine).save_draft(
        scope, strategy="swap_alpha", symbol="BTC-USDT-SWAP", budget=100,
        expected_revision=0, timestamp=1,
    )
    assert draft.symbol == "BTC-USDT-SWAP"
    assert draft.market_type == "swap"
    assert draft.currency == "USDT"


@pytest.mark.asyncio
async def test_order_managers_bind_handler_safeguards_and_funding_to_selected_market(monkeypatch):
    settings = configured_settings(mode="demo", legacy_market="option")
    monkeypatch.setattr(strategies, "load_runtime_settings", lambda: settings)
    monkeypatch.setattr(allocations, "load_runtime_settings", lambda: settings)
    repository = Repository()
    managers = []
    try:
        for market in ("spot", "swap"):
            manager = strategies.create_order_manager(repository=repository, market_type=market)
            managers.append(manager)
            assert manager.live_market_type == market
            assert manager.router.live._default_type == market
            assert manager.funding_scope_id() == allocations.allocation_context(
                settings, market_type=market,
            )[1].scope_id
        assert managers[0].funding_scope_id() != managers[1].funding_scope_id()
    finally:
        for manager in managers:
            await manager.router.live.close()


@pytest.mark.asyncio
async def test_shared_strategy_market_data_has_no_legacy_default(monkeypatch):
    monkeypatch.setattr(strategies, "load_runtime_settings", lambda: configured_settings())
    service = strategies.create_market_data_service()
    try:
        assert service._exchange.options["defaultType"] == "auto"
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_unscoped_order_manager_keeps_account_routing_independent_of_legacy_market(
    monkeypatch,
):
    monkeypatch.setattr(
        strategies, "load_runtime_settings", lambda: configured_settings(mode="demo"),
    )
    manager = strategies.create_order_manager(repository=Repository())
    try:
        assert manager.router.live._default_type == "auto"
        assert manager.live_market_type == ""
        assert manager.funding_scope_id() is None
    finally:
        await manager.router.live.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["paper", "demo"])
async def test_manager_allocation_scope_stays_bound_to_its_captured_credentials(monkeypatch, mode):
    current = configured_settings(mode=mode)
    original_scope = AllocationScope(
        hashlib.sha256(b"shared-key").hexdigest(), "sandbox", "swap",
    ).scope_id
    monkeypatch.setattr(strategies, "load_runtime_settings", lambda: current)
    monkeypatch.setattr(allocations, "load_runtime_settings", lambda: current)
    manager = strategies.create_order_manager(repository=Repository(), market_type="swap")
    try:
        current = AppConfig(mode=mode, exchange=ExchangeConfig(
            api_key="new-key", secret="new-secret", passphrase="new-passphrase",
        ))
        read_scope = manager.paper_scope_id if mode == "paper" else manager.funding_scope_id
        assert read_scope() == original_scope
    finally:
        if manager.router.live is not None:
            await manager.router.live.close()


@pytest.mark.asyncio
async def test_strategy_start_derives_manager_market_from_its_symbol(monkeypatch):
    repository = Repository()
    for name, symbol in (("spot_alpha", "BTC-USDT"), ("swap_alpha", "BTC-USDT-SWAP")):
        repository.create_strategy_config(StrategyConfigRecord(
            name=name, strategy_type="ma_cross", symbol=symbol,
            timeframe="1m", enabled=True, params={}, created_at=1, updated_at=1,
        ))
    settings = configured_settings(mode="demo", legacy_market="option")
    monkeypatch.setattr(strategies, "load_runtime_settings", lambda: settings)
    selected = []

    class Engine:
        def __init__(self, **kwargs):
            pass

        async def start(self):
            pass

        async def stop(self):
            pass

    async def refresh(*args, **kwargs):
        pass

    def manager(**kwargs):
        selected.append(kwargs["market_type"])
        return SimpleNamespace()

    monkeypatch.setattr(strategies, "BotEngine", Engine)
    monkeypatch.setattr(strategies, "refresh_okx_live_state", refresh)
    monkeypatch.setattr(strategies, "create_market_data_service", lambda: SimpleNamespace())
    monkeypatch.setattr(strategies, "create_order_manager", manager)
    runtime = strategies.StrategyRuntimeState()
    app = FastAPI()
    app.include_router(strategies.create_router(runtime=runtime), prefix="/strategies")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for name in ("spot_alpha", "swap_alpha"):
            result = await client.post(f"/strategies/{name}/start")
            assert result.status_code == 200, result.text
        assert selected == ["spot", "swap"]
        assert set(runtime.engines) == {"spot_alpha", "swap_alpha"}
        for name in ("spot_alpha", "swap_alpha"):
            result = await client.post(f"/strategies/{name}/stop")
            assert result.status_code == 200, result.text


@pytest.mark.parametrize("symbol, market", [
    ("BTC-USDT", "spot"),
    ("BTC-USDT-SWAP", "swap"),
    ("BTC-USD-261225", "future"),
    ("BTC-USD-261225-90000-C", "option"),
])
def test_identity_lookup_preserves_instrument_identity(symbol, market):
    repository = Repository()
    repository.upsert_order(OrderRecord(
        order_id="order-1", client_order_id="client-1", exchange_order_id="",
        strategy="alpha", symbol=symbol, side="buy", type="limit",
        amount=1, price=10, status="pending", timestamp=1, updated_at=1,
    ))
    seen = []

    class Client:
        headers = {"x-simulated-trading": "1"}
        options = {"defaultType": "option"}

        def fetch_order(self, order_id, symbol, params):
            seen.append((symbol, self.options["defaultType"]))
            return {"id": "exchange-1", "status": "closed", "filled": 1}

        def close(self):
            pass

    lookup = build_live_identity_lookup(
        configured_settings(mode="demo", legacy_market="spot"), repository,
        factory=lambda settings: Client(),
    )
    assert lookup("client-1")["status"] == "filled"
    assert seen == [("BTC/USDT" if market == "spot" else symbol, market)]
