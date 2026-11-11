import asyncio
import json
from types import SimpleNamespace

import pytest
from ccxt.base.errors import AuthenticationError, NetworkError, NotSupported, PermissionDenied

from src.core.execution_scope import ExecutionScope
from src.core.types import (
    AccountSnapshot,
    ExchangeOrderSnapshot,
    ExchangeTradeSnapshot,
    OrderSide,
    OrderStatus,
    OrderType,
)
from src.data.exchange_store import open_exchange_repository
from src.data.models import EXCHANGE_STRATEGY
from src.data.repository import Repository, _AccountingTransaction
from src.exchange.errors import MalformedProviderData
from src.ops.private_sync import sync_private_state

SCOPE = ExecutionScope("okx", "sandbox", "a" * 64)


def repository_at(path):
    master = Repository(db_path=str(path))
    return open_exchange_repository(SCOPE, master_engine=master.engine)


class Adapter:
    def __init__(self, *, fail_at=None, error=None, equity=100, provider_time=100):
        self.fail_at = fail_at
        self.error = error
        self.equity = equity
        self.provider_time = provider_time
        self.calls = []

    def check(self, part):
        self.calls.append(part)
        if self.fail_at == part:
            raise self.error

    async def fetch_account_snapshot(self):
        self.check("account")
        return AccountSnapshot(equity=self.equity, timestamp=self.provider_time)

    async def fetch_open_order_snapshots(self, symbols=None):
        self.check("orders")
        return []

    async def fetch_recent_trade_snapshots(self, symbols=None, since=None, limit=100):
        self.check("trades")
        return []


async def sync(repo, adapter, **values):
    return await sync_private_state(
        repo, adapter, account_scope_id=SCOPE.account_scope_id,
        timestamp_ms=lambda: 1000, **values,
    )


async def test_attempt_is_durable_before_first_provider_call_and_commit_survives_reopen(tmp_path):
    path = tmp_path / "master.db"
    repo = repository_at(path)

    class InspectingAdapter(Adapter):
        async def fetch_account_snapshot(self):
            status = repository_at(path).get_private_sync_status()
            assert status["latest_attempt"].state == "in_progress"
            assert status["last_committed"] is None
            return await super().fetch_account_snapshot()

    result = await sync(repo, InspectingAdapter(), symbols=["BTC-USDT"], since=1, limit=200)
    receipt = repository_at(path).get_private_sync_status()["last_committed"]
    assert receipt.state == "committed"
    for key, value in result.as_response().items():
        if key != "divergences":
            assert receipt.result[key] == value
    assert receipt.result["divergence_count"] == 0
    assert receipt.result["divergence_sample"] == []
    assert receipt.symbols == ["BTC-USDT"] and receipt.since == 1 and receipt.limit == 200
    assert receipt.parts["account"]["received_at"] == 1000
    assert receipt.parts["account"]["provider_timestamp"] == 100
    assert all(part["coverage"]["completeness"] == "unknown"
               for part in receipt.parts.values())
    assert receipt.cursor["durable"] is False
    assert receipt.cursor["complete"] is False
    assert receipt.cursor["watermark"] is None


@pytest.mark.parametrize("stage", ["account", "orders", "trades"])
@pytest.mark.parametrize("error,code", [
    (AuthenticationError("secret token=do-not-store"), "authentication_failed"),
    (PermissionDenied("secret token=do-not-store"), "permission_denied"),
    (NotSupported("secret token=do-not-store"), "capability_unsupported"),
    (MalformedProviderData("secret token=do-not-store"), "malformed_data"),
])
async def test_provider_failure_keeps_previous_committed_evidence_and_projections(
    tmp_path, stage, error, code,
):
    repo = repository_at(tmp_path / "master.db")
    await sync(repo, Adapter())
    committed = repo.get_private_sync_status()["last_committed"]
    with pytest.raises(type(error)):
        await sync(repo, Adapter(fail_at=stage, error=error, equity=200, provider_time=200))
    status = repo.get_private_sync_status()
    failed = status["latest_attempt"]
    assert failed.state == "failed" and failed.error_code == code
    assert failed.received_at is None
    assert failed.parts[stage]["state"] == "failed"
    assert "secret" not in json.dumps(failed.model_dump())
    assert status["last_committed"].model_dump() == committed.model_dump()
    assert repo.get_account(EXCHANGE_STRATEGY).equity == 100


@pytest.mark.parametrize("stage", ["account", "orders"])
async def test_account_or_order_network_failure_commits_no_projection(tmp_path, stage):
    repo = repository_at(tmp_path / "master.db")
    with pytest.raises(NetworkError):
        await sync(repo, Adapter(fail_at=stage, error=NetworkError("secret")))
    status = repo.get_private_sync_status()
    assert status["latest_attempt"].error_code == "provider_unavailable"
    assert status["last_committed"] is None
    assert repo.get_account(EXCHANGE_STRATEGY) is None


async def test_trade_network_failure_commits_explicit_partial_receipt(tmp_path):
    repo = repository_at(tmp_path / "master.db")
    result = await sync(repo, Adapter(fail_at="trades", error=NetworkError("secret")))
    receipt = repo.get_private_sync_status()["last_committed"]
    assert result.trades_status == "unavailable"
    assert receipt.state == "partial"
    assert receipt.parts["account"]["state"] == "available"
    assert receipt.parts["orders"]["state"] == "available"
    assert receipt.parts["trades"]["state"] == "unavailable"
    assert receipt.parts["trades"]["coverage"]["completeness"] == "unknown"
    assert repo.get_account(EXCHANGE_STRATEGY).equity == 100


async def test_nonempty_trade_receipt_is_partial_and_never_creates_durable_watermark(tmp_path):
    repo = repository_at(tmp_path / "master.db")

    class TradeAdapter(Adapter):
        async def fetch_recent_trade_snapshots(self, symbols=None, since=None, limit=100):
            return [ExchangeTradeSnapshot(
                exchange_trade_id="trade-1", exchange_order_id="order-1", client_order_id="",
                symbol="BTC-USDT", side=OrderSide.BUY, amount=1, price=10, fee=0, timestamp=500,
            )]

    await sync(repo, TradeAdapter(), symbols=["BTC-USDT"], since=1, limit=200)
    receipt = repo.get_private_sync_status()["last_committed"]
    assert receipt.parts["trades"]["coverage"] == {
        "kind": "recent_trades", "completeness": "partial", "symbols": ["BTC-USDT"],
        "since": 1, "limit": 200,
    }
    assert receipt.parts["trades"]["provider_timestamp"] == 500
    assert receipt.cursor["complete"] is False
    assert receipt.cursor["durable"] is False
    assert receipt.cursor["watermark"] is None


async def test_large_divergence_batch_preserves_full_projection_and_bounds_audit_result(tmp_path):
    repo = repository_at(tmp_path / "master.db")

    class ManyOrdersAdapter(Adapter):
        async def fetch_open_order_snapshots(self, symbols=None):
            return [ExchangeOrderSnapshot(
                exchange_order_id=f"order-{index}", client_order_id=f"client-{index}",
                symbol="BTC-USDT", side=OrderSide.BUY, type=OrderType.LIMIT,
                amount=1, price=10, status=OrderStatus.PENDING, fill_price=0,
                timestamp=100, updated_at=100,
            ) for index in range(150)]

    result = await sync(repo, ManyOrdersAdapter())
    receipt = repo.get_private_sync_status()["last_committed"]
    assert len(repo.get_orders()) == len(result.divergences) == result.risk_events_saved == 150
    assert repo.get_kill_switch().engaged is True
    assert receipt.state == "committed"
    assert receipt.result["divergence_count"] == 150
    assert receipt.result["divergences_truncated"] is True
    assert len(receipt.result["divergence_sample"]) <= 5
    assert len(json.dumps(receipt.result).encode()) < 8000


async def test_single_oversized_divergence_is_omitted_from_audit_without_losing_risk(tmp_path):
    repo = repository_at(tmp_path / "master.db")
    identifier = "\U0001f600" * 20_000

    class OversizedIdentityAdapter(Adapter):
        async def fetch_open_order_snapshots(self, symbols=None):
            return [ExchangeOrderSnapshot(
                exchange_order_id="order-1", client_order_id=identifier,
                symbol="BTC-USDT", side=OrderSide.BUY, type=OrderType.LIMIT,
                amount=1, price=10, status=OrderStatus.PENDING, fill_price=0,
                timestamp=100, updated_at=100,
            )]

    result = await sync(repo, OversizedIdentityAdapter())
    receipt = repo.get_private_sync_status()["last_committed"]
    assert result.divergences[0]["client_order_id"] == identifier
    assert result.risk_events_saved == 1 and repo.get_kill_switch().engaged is True
    assert receipt.result["divergence_count"] == 1
    assert receipt.result["divergence_sample"] == []
    assert receipt.result["divergences_truncated"] is True


@pytest.mark.parametrize("stage", ["account", "orders", "trades"])
async def test_malformed_response_shape_records_failure_without_projection(tmp_path, stage):
    repo = repository_at(tmp_path / "master.db")

    class MalformedAdapter(Adapter):
        async def fetch_account_snapshot(self):
            return {} if stage == "account" else await super().fetch_account_snapshot()

        async def fetch_open_order_snapshots(self, symbols=None):
            return {} if stage == "orders" else []

        async def fetch_recent_trade_snapshots(self, symbols=None, since=None, limit=100):
            return {} if stage == "trades" else []

    with pytest.raises(MalformedProviderData):
        await sync(repo, MalformedAdapter())
    receipt = repo.get_private_sync_status()["latest_attempt"]
    assert receipt.state == "failed" and receipt.error_code == "malformed_data"
    assert receipt.parts[stage]["received_at"] == 1000
    assert repo.get_account(EXCHANGE_STRATEGY) is None


async def test_fake_repository_still_accepts_explicit_scope_without_receipt_methods():
    accounts = []
    repo = SimpleNamespace(
        get_orders=lambda *args, **kwargs: [], get_trades=lambda *args, **kwargs: [],
        upsert_account=accounts.append, kill_switch=False,
    )
    result = await sync(repo, Adapter())
    assert result.account_upserted == 1
    assert len(accounts) == 1


async def test_empty_scope_records_skipped_unknown_receipt_without_provider_calls(tmp_path):
    repo = repository_at(tmp_path / "master.db")
    adapter = Adapter()
    result = await sync(repo, adapter, symbols=[])
    receipt = repo.get_private_sync_status()["last_committed"]
    assert result.account_upserted == 0 and result.trades_status == "available"
    assert adapter.calls == []
    assert receipt.symbols == []
    assert all(part["state"] == "skipped" for part in receipt.parts.values())
    assert all(part["coverage"]["completeness"] == "unknown"
               for part in receipt.parts.values())
    assert receipt.cursor["complete"] is False
    assert repo.get_account(EXCHANGE_STRATEGY) is None


async def test_projection_and_receipt_rollback_together_on_finalization_failure(
    tmp_path, monkeypatch,
):
    repo = repository_at(tmp_path / "master.db")
    await sync(repo, Adapter())
    committed = repo.get_private_sync_status()["last_committed"]
    original = _AccountingTransaction.finalize_private_sync_receipt

    def broken(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise RuntimeError("injected after receipt finalization")

    monkeypatch.setattr(_AccountingTransaction, "finalize_private_sync_receipt", broken)
    with pytest.raises(RuntimeError, match="injected"):
        await sync(repo, Adapter(equity=200, provider_time=200))
    status = repo.get_private_sync_status()
    assert status["latest_attempt"].state == "failed"
    assert status["last_committed"].model_dump() == committed.model_dump()
    assert repo.get_account(EXCHANGE_STRATEGY).equity == 100


async def test_superseded_sync_rolls_back_projection_and_records_failure(tmp_path):
    repo = repository_at(tmp_path / "master.db")
    entered = asyncio.Event()
    release = asyncio.Event()
    slow_attempt = []

    class SlowAdapter(Adapter):
        async def fetch_account_snapshot(self):
            slow_attempt.append(repo.get_private_sync_status()["latest_attempt"].attempt_id)
            entered.set()
            await release.wait()
            return await super().fetch_account_snapshot()

    pending = asyncio.create_task(sync(repo, SlowAdapter(equity=300, provider_time=300)))
    await asyncio.wait_for(entered.wait(), timeout=2)
    await sync(repo, Adapter(equity=200, provider_time=200))
    release.set()
    with pytest.raises(ValueError, match="superseded"):
        await pending
    status = repo.get_private_sync_status()
    assert status["last_committed"].generation == 2
    assert status["last_committed"].state == "committed"
    assert repo.get_private_sync_receipt(slow_attempt[0]).error_code == "superseded"
    assert repo.get_account(EXCHANGE_STRATEGY).equity == 200


async def test_explicit_wrong_account_scope_rejects_before_provider_calls(tmp_path):
    repo = repository_at(tmp_path / "master.db")
    adapter = Adapter()
    with pytest.raises(ValueError, match="scope"):
        await sync_private_state(repo, adapter, account_scope_id="wrong-account")
    assert adapter.calls == []
    assert repo.get_private_sync_status()["latest_attempt"] is None
