"""Public rules remain scoped audit evidence, with no trading authority."""

import asyncio
import hashlib
import json
from dataclasses import replace

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from src.data.repository import Repository
from src.exchange.errors import MalformedProviderData
from src.exchange.okx_instrument_facts import parse_okx_spot_instruments
from tests.unit.test_account_balance_store import database_state
from tests.unit.test_account_balance_store import stores as stores
from tests.unit.test_okx_instrument_facts import (
    OPTIONAL_FIELDS,
    RawInstrumentExchange,
    instrument_payload,
    instrument_row,
)


def commit(repo, raw=None, *, received_at=100, requested_inst_id=None):
    raw = instrument_payload() if raw is None else raw
    decoded = json.loads(raw) if isinstance(raw, str) else raw
    return repo.commit_private_spot_instruments(
        parse_okx_spot_instruments(decoded), raw_payload=raw, received_at=received_at,
        requested_inst_id=requested_inst_id,
    )


def unchanged_state(repo):
    return {name: rows for name, rows in database_state(repo).items()
            if name not in {"private_spot_instrument", "private_spot_instrument_response"}}


async def sync(*args, **kwargs):
    from src.ops.exact_private_sync import sync_exact_spot_instruments

    return await sync_exact_spot_instruments(*args, **kwargs)


def test_roundtrip_exact_rules_original_json_hash_and_restart_without_uid(stores):
    _, (repo, _) = stores
    raw = instrument_payload(instrument_row(), instrument_row(instId="ETH-USDT", baseCcy="ETH"))
    raw_text = json.dumps(raw, indent=2)
    first, second = commit(repo, raw_text)
    assert (first.inst_id, second.inst_id) == ("BTC-USDT", "ETH-USDT")
    assert (first.base_ccy, first.quote_ccy) == ("BTC", "USDT")
    assert (first.tick_sz, first.lot_sz, first.min_sz) == (
        "0.00000000000000000100", "9007199254740993.000100", "0000.00001000",
    )
    assert first.received_at == 100 and first.raw_json == second.raw_json == raw_text
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    assert first.payload_hash == hashlib.sha256(canonical.encode()).hexdigest()
    assert first.account_scope_id == second.account_scope_id
    assert repo.get_private_uid() is None
    first.tick_sz = "1"
    first.account_scope_id = "detached"
    reopened = Repository(db_path=repo.engine.url.database)
    stored, = reopened.get_private_spot_instruments("BTC-USDT")
    assert stored.tick_sz == "0.00000000000000000100" and stored.raw_json == raw_text
    assert stored.account_scope_id != "detached"
    with repo.engine.connect() as connection:
        count = connection.exec_driver_sql(
            "SELECT count(*) FROM private_spot_instrument_response",
        ).scalar()
    assert count == 1
    columns = inspect(repo.engine).get_columns("private_spot_instrument")
    assert "raw_json" not in {c["name"] for c in columns}


@pytest.mark.parametrize("raw_name,name", OPTIONAL_FIELDS)
@pytest.mark.parametrize("value,state", [(None, "null"), ("", "value"), ("raw", "value"),
                                         ("absent", "missing")])
def test_optional_states_remain_exact(stores, raw_name, name, value, state):
    _, (repo, _) = stores
    row = instrument_row(**{raw_name: value})
    if state == "missing":
        del row[raw_name]
    record, = commit(repo, instrument_payload(row))
    assert getattr(record, name) == (None if state == "missing" else value)
    assert getattr(record, f"{name}_state") == state


def test_replay_preserves_first_time_and_changed_rules_remain_separate(stores):
    _, (repo, _) = stores
    first, = commit(repo, json.dumps(instrument_payload(), indent=2))
    replay, = commit(repo, received_at=200)
    assert replay.model_dump() == first.model_dump() and replay.raw_json == first.raw_json
    changed, = commit(repo, instrument_payload(instrument_row(tickSz="1.00")), received_at=300)
    assert changed.payload_hash != first.payload_hash
    assert [r.received_at for r in repo.get_private_spot_instruments()] == [100, 300]


def test_scope_isolation_and_legacy_ledgers_activation_receipts_unchanged(stores):
    master, (left, right) = stores
    before = [unchanged_state(r) for r in (master, left, right)]
    commit(left)
    commit(right, instrument_payload(instrument_row(tickSz="2.00")))
    assert left.get_private_spot_instruments()[0].tick_sz == "0.00000000000000000100"
    assert right.get_private_spot_instruments()[0].tick_sz == "2.00"
    with pytest.raises(ValueError, match="facts store"):
        commit(master)
    with pytest.raises(ValueError, match="facts store"):
        master.get_private_spot_instruments()
    assert [unchanged_state(r) for r in (master, left, right)] == before


@pytest.mark.parametrize("raw", [
    "{bad", "[]", {"padding": "x" * 4_194_305}, {"authorization": "secret"},
    '{"code":"0","code":"0","data":[]}', {"x": float("nan")},
    {"code": "0", "data": [], "nested": {"apiSecretKey": "secret"}},
])
def test_invalid_credential_or_oversize_raw_is_atomic(stores, raw):
    _, (repo, _) = stores
    before = database_state(repo)
    with pytest.raises(ValueError):
        repo.commit_private_spot_instruments(
            parse_okx_spot_instruments(instrument_payload()), raw_payload=raw, received_at=100,
        )
    assert database_state(repo) == before


@pytest.mark.parametrize("received_at", [-1, True, 1.5, "1", None])
def test_invalid_received_time_is_atomic(stores, received_at):
    _, (repo, _) = stores
    before = database_state(repo)
    with pytest.raises(ValueError):
        commit(repo, received_at=received_at)
    assert database_state(repo) == before


def test_typed_raw_mismatch_is_atomic(stores):
    _, (repo, _) = stores
    fact, = parse_okx_spot_instruments(instrument_payload())
    before = database_state(repo)
    for facts in [(replace(fact, tick_sz="1.00"),), (), (fact, fact), (object(),)]:
        with pytest.raises(ValueError, match="typed"):
            repo.commit_private_spot_instruments(
                facts, raw_payload=instrument_payload(), received_at=1,
            )
        assert database_state(repo) == before


@pytest.mark.parametrize("changes", [{}, {"tickSz": "1.00"}])
def test_duplicate_or_conflicting_rows_fail_atomically(stores, changes):
    _, (repo, _) = stores
    before = database_state(repo)
    with pytest.raises(MalformedProviderData, match="duplicate|conflict"):
        repo.commit_private_spot_instruments(
            parse_okx_spot_instruments(instrument_payload()),
            raw_payload=instrument_payload(instrument_row(), instrument_row(**changes)),
            received_at=1,
        )
    assert database_state(repo) == before


@pytest.mark.parametrize("requested", ["ETH-USDT", "BTC/USDT", True])
def test_requested_filter_cannot_be_overridden(stores, requested):
    _, (repo, _) = stores
    before = database_state(repo)
    with pytest.raises((ValueError, MalformedProviderData)):
        commit(repo, requested_inst_id=requested)
    assert database_state(repo) == before


def test_empty_response_is_journaled_once_without_instruments_or_authority(stores):
    _, (repo, _) = stores
    assert commit(repo, {"code": "0", "data": []}) == []
    assert commit(repo, {"data": [], "code": "0"}, received_at=999) == []
    assert repo.get_private_spot_instruments() == [] and repo.get_private_uid() is None
    with repo.engine.connect() as connection:
        times = connection.exec_driver_sql(
            "SELECT received_at FROM private_spot_instrument_response",
        ).all()
    assert times == [(100,)]


@pytest.mark.parametrize("table", ["private_spot_instrument", "private_spot_instrument_response"])
@pytest.mark.parametrize("operation", ["UPDATE {table} SET received_at=999", "DELETE FROM {table}",
                                       "INSERT OR REPLACE INTO {table} SELECT * FROM {table}"])
def test_append_only_even_under_direct_sql(stores, table, operation):
    _, (repo, _) = stores
    commit(repo)
    before = database_state(repo)
    with pytest.raises(IntegrityError):
        with repo.engine.begin() as connection:
            connection.execute(text(operation.format(table=table)))
    assert database_state(repo) == before


@pytest.mark.parametrize("inst_id,params", [(None, {"instType": "SPOT"}),
    ("BTC-USDT", {"instType": "SPOT", "instId": "BTC-USDT"})])
async def test_sync_fetches_single_raw_response_with_exact_params(stores, inst_id, params):
    master, (repo, _) = stores
    before = unchanged_state(master), unchanged_state(repo)
    exchange = RawInstrumentExchange(instrument_payload())
    result = await sync(repo, exchange, 1234, inst_id=inst_id)
    assert exchange.requests == [params]
    assert result[0].received_at == 1234
    assert json.loads(result[0].raw_json) == instrument_payload()
    assert (unchanged_state(master), unchanged_state(repo)) == before


@pytest.mark.parametrize("response,error", [(OSError("offline"), OSError),
    (asyncio.CancelledError(), asyncio.CancelledError),
    ({"code": "0", "data": [None]}, MalformedProviderData),
    ({**instrument_payload(), "headers": {"apiSecretKey": "secret"}}, ValueError)])
async def test_sync_failures_preserve_all_state(stores, response, error):
    _, (repo, _) = stores
    await sync(repo, RawInstrumentExchange(instrument_payload()), 1)
    before = database_state(repo)
    exchange = RawInstrumentExchange(response)
    with pytest.raises(error):
        await sync(repo, exchange, 2)
    assert exchange.requests == [{"instType": "SPOT"}]
    assert database_state(repo) == before


async def test_storage_failure_rolls_back_entire_response(stores, monkeypatch):
    _, (repo, _) = stores
    before = database_state(repo)
    atomic = repo.atomic_accounting

    def interrupted(callback):
        def fail_after_writes(tx):
            callback(tx)
            assert len(tx.get_private_spot_instruments()) == 2
            raise RuntimeError("storage interrupted")
        return atomic(fail_after_writes)

    monkeypatch.setattr(repo, "atomic_accounting", interrupted)
    raw = instrument_payload(instrument_row(), instrument_row(instId="ETH-USDT", baseCcy="ETH"))
    with pytest.raises(RuntimeError, match="storage interrupted"):
        await sync(repo, RawInstrumentExchange(raw), 1)
    assert database_state(repo) == before


async def test_pending_cancellation_after_response_prevents_commit(stores):
    _, (repo, _) = stores
    before = database_state(repo)

    class CancelAfterResponse(RawInstrumentExchange):
        async def public_get_public_instruments(self, params):
            result = await super().public_get_public_instruments(params)
            asyncio.current_task().cancel()
            return result

    exchange = CancelAfterResponse(instrument_payload())
    task = asyncio.create_task(sync(repo, exchange, 1))
    with pytest.raises(asyncio.CancelledError):
        await task
    assert database_state(repo) == before


async def test_sync_invalid_filter_fails_before_fetch(stores):
    _, (repo, _) = stores
    exchange = RawInstrumentExchange(instrument_payload())
    with pytest.raises(ValueError):
        await sync(repo, exchange, 1, inst_id="BTC/USDT")
    assert exchange.requests == []


@pytest.mark.parametrize("column,value", [("payload_hash", "'" + "a" * 64 + "'"),
    ("received_at", "999"), ("account_scope_id", "'foreign'")])
def test_direct_insert_cannot_detach_rules_from_response(stores, column, value):
    _, (repo, _) = stores
    commit(repo)
    columns = [c["name"] for c in inspect(repo.engine).get_columns("private_spot_instrument")]
    selected = [value if name == column else name for name in columns]
    statement = (f"INSERT INTO private_spot_instrument ({','.join(columns)}) "
                 f"SELECT {','.join(selected)} FROM private_spot_instrument")
    before = database_state(repo)
    with pytest.raises(IntegrityError):
        with repo.engine.begin() as connection:
            connection.execute(text(statement))
    assert database_state(repo) == before


async def test_sync_response_filter_mismatch_does_not_commit(stores):
    _, (repo, _) = stores
    before = database_state(repo)
    exchange = RawInstrumentExchange(instrument_payload())
    with pytest.raises(MalformedProviderData, match="request"):
        await sync(repo, exchange, 1, inst_id="ETH-USDT")
    assert exchange.requests == [{"instType": "SPOT", "instId": "ETH-USDT"}]
    assert database_state(repo) == before
