"""Exact fee observations are account/request-bound evidence, never funds."""

import hashlib
import json
from dataclasses import replace

import pytest
from sqlalchemy.exc import IntegrityError

from src.data.repository import Repository
from tests.unit.test_account_balance_store import bind, database_state
from tests.unit.test_account_balance_store import stores as stores
from tests.unit.test_spot_preflight_evidence import tamper


def payload(**fields):
    return {"code": "0", "data": [{
        "instType": "SPOT", "level": "Lv1", "maker": "0.00000000000000000100",
        "taker": "-0.00100000000000000001", "makerUSDC": None, "takerUSDC": "",
        "ts": "001763979985847", "ruleType": "normal", "feeGroup": [{
            "groupId": "001", "maker": "0.0001", "taker": "-0.00200", "rpiMaker": "",
        }], **fields,
    }]}


def commit(repo, raw=None, *, at=100, inst_id="BTC-USDT", uid=None):
    method = repo.commit_private_spot_trade_fee
    from src.exchange.okx_trade_fee_facts import parse_okx_spot_trade_fee

    raw = payload() if raw is None else raw
    decoded = json.loads(raw) if isinstance(raw, str) else raw
    return method(parse_okx_spot_trade_fee(decoded, inst_id=inst_id), raw_payload=raw,
                  received_at=at, uid=uid)


def operational_state(repo):
    return {name: rows for name, rows in database_state(repo).items()
            if name != "private_spot_trade_fee"}


def test_exact_signed_roundtrip_original_json_hash_states_detach_and_restart(stores):
    _, (repo, _) = stores
    bind(repo)
    before = operational_state(repo)
    raw = json.dumps(payload(), indent=2)
    record = commit(repo, raw)
    assert (record.uid, record.requested_inst_id, record.inst_type, record.received_at) == (
        "00123", "BTC-USDT", "SPOT", 100,
    )
    assert record.raw_json == raw
    canonical = json.dumps(payload(), sort_keys=True, separators=(",", ":"))
    assert record.payload_hash == hashlib.sha256(canonical.encode()).hexdigest()
    assert record.maker == "0.00000000000000000100"
    assert record.taker == "-0.00100000000000000001"
    assert (record.maker_usdc, record.maker_usdc_state) == (None, "null")
    assert (record.taker_usdc, record.taker_usdc_state) == ("", "value")
    assert record.category is None and record.category_state == "missing"
    assert json.loads(record.fee_group_json) == payload()["data"][0]["feeGroup"]
    assert record.fee_group_state == "value"
    record.maker = "mutated-copy"
    reopened = Repository(db_path=repo.engine.url.database)
    stored, = reopened.get_private_spot_trade_fees("BTC-USDT")
    assert stored.maker == "0.00000000000000000100" and stored.raw_json == raw
    assert operational_state(repo) == before


@pytest.mark.parametrize("value,state,want", [(None, "null", None), ([], "value", "[]"),
                                               ("absent", "missing", None)])
def test_fee_group_missing_null_empty_are_distinct(stores, value, state, want):
    _, (repo, _) = stores
    bind(repo)
    raw = payload(feeGroup=value)
    if value == "absent":
        del raw["data"][0]["feeGroup"]
    record = commit(repo, raw)
    assert (record.fee_group_state, record.fee_group_json) == (state, want)
    assert repo.get_private_spot_trade_fees()[0].model_dump() == record.model_dump()


def test_request_identity_separates_same_payload_and_replays_keep_first_receipt(stores):
    _, (repo, _) = stores
    bind(repo)
    first = commit(repo)
    replay = commit(repo, json.dumps(payload(), indent=2), at=999)
    other = commit(repo, inst_id="ETH-USDT", at=200)
    changed = commit(repo, payload(taker="-0.00300"), at=300)
    assert replay.model_dump() == first.model_dump()
    assert other.payload_hash == first.payload_hash and other.requested_inst_id == "ETH-USDT"
    assert changed.payload_hash != first.payload_hash
    assert [r.received_at for r in repo.get_private_spot_trade_fees()] == [100, 200, 300]
    assert [r.received_at for r in repo.get_private_spot_trade_fees("BTC-USDT")] == [100, 300]


def test_requires_existing_uid_and_refuses_conflicts_scopes_and_master(stores):
    master, (left, right) = stores
    before = database_state(left)
    with pytest.raises(ValueError, match="UID"):
        commit(left)
    assert database_state(left) == before
    bind(left)
    bind(right, "00456")
    first = commit(left)
    second = commit(right)
    assert first.account_scope_id != second.account_scope_id
    assert (first.uid, second.uid) == ("00123", "00456")
    with pytest.raises(ValueError, match="UID"):
        commit(left, uid="00456")
    with pytest.raises(ValueError, match="UID"):
        left.get_private_spot_trade_fees(uid="00456")
    with pytest.raises(ValueError, match="facts store"):
        commit(master)
    with pytest.raises(ValueError, match="facts store"):
        master.get_private_spot_trade_fees()


@pytest.mark.parametrize("at", [-1, True, 1.5, "1", None, 2**63])
def test_invalid_receipt_time_is_atomic(stores, at):
    _, (repo, _) = stores
    bind(repo)
    before = database_state(repo)
    with pytest.raises(ValueError):
        commit(repo, at=at)
    assert database_state(repo) == before


@pytest.mark.parametrize("raw", [
    "{bad", "[]", {"x": float("nan")}, {"padding": "x" * 4_194_305},
    {"authorization": "secret"}, '{"code":"0","code":"0","data":[]}',
    {"code": "0", "data": [{"apiSecretKey": "secret"}]},
])
def test_invalid_raw_cannot_write_or_refresh_observation(stores, raw):
    _, (repo, _) = stores
    bind(repo)
    # Obtain a valid DTO independently, then exercise raw/typed validation.
    from src.exchange.okx_trade_fee_facts import parse_okx_spot_trade_fee

    fact = parse_okx_spot_trade_fee(payload(), inst_id="BTC-USDT")
    before = database_state(repo)
    with pytest.raises(ValueError):
        repo.commit_private_spot_trade_fee(fact, raw_payload=raw, received_at=100)
    assert database_state(repo) == before


def test_typed_raw_mismatch_and_response_symbol_mismatch_are_atomic(stores):
    _, (repo, _) = stores
    bind(repo)
    from src.exchange.okx_trade_fee_facts import parse_okx_spot_trade_fee

    fact = parse_okx_spot_trade_fee(payload(), inst_id="BTC-USDT")
    before = database_state(repo)
    with pytest.raises(ValueError):
        repo.commit_private_spot_trade_fee(
            replace(fact, taker="-0.5"), raw_payload=payload(), received_at=100,
        )
    with pytest.raises(ValueError):
        repo.commit_private_spot_trade_fee(
            fact, raw_payload=payload(instId="ETH-USDT"), received_at=100,
        )
    assert database_state(repo) == before


@pytest.mark.parametrize("operation", [
    "UPDATE private_spot_trade_fee SET maker='0'",
    "DELETE FROM private_spot_trade_fee",
    "INSERT OR REPLACE INTO private_spot_trade_fee SELECT * FROM private_spot_trade_fee",
])
def test_sql_observations_are_append_only(stores, operation):
    _, (repo, _) = stores
    bind(repo)
    commit(repo)
    before = database_state(repo)
    with pytest.raises(IntegrityError):
        with repo.engine.begin() as connection:
            connection.exec_driver_sql(operation)
    assert database_state(repo) == before


@pytest.mark.parametrize("assignment", [
    "payload_hash='bad'", "raw_json='{bad'", "maker='0'", "uid='other'",
    "account_scope_id='other'", "inst_type='SWAP'", "requested_inst_id='BTC/USDT'",
    "fee_group_json='[]'", "fee_group_json=NULL,fee_group_state='missing'",
    "maker=NULL,maker_state='missing'",
])
def test_getter_refuses_corrupt_raw_identity_projection_or_groups(stores, assignment):
    _, (repo, _) = stores
    bind(repo)
    commit(repo)
    # Ignore constraints only for simulated disk corruption, then restore enforcement.
    with repo.engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
    try:
        tamper(repo, "private_spot_trade_fee", assignment)
    finally:
        with repo.engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")
    before = database_state(repo)
    with pytest.raises(ValueError):
        repo.get_private_spot_trade_fees()
    assert database_state(repo) == before


def test_transaction_failure_rolls_back_fee_append(stores):
    _, (repo, _) = stores
    bind(repo)
    from src.exchange.okx_trade_fee_facts import parse_okx_spot_trade_fee

    fact = parse_okx_spot_trade_fee(payload(), inst_id="BTC-USDT")
    before = database_state(repo)

    def fail(transaction):
        transaction.commit_private_spot_trade_fee(fact, raw_payload=payload(), received_at=100)
        raise RuntimeError("forced rollback")

    with pytest.raises(RuntimeError, match="forced rollback"):
        repo.atomic_accounting(fail)
    assert database_state(repo) == before
