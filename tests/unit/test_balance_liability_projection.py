"""Liability asset fields reparse immutable balance journals without migration."""

import hashlib
import json

import pytest

from src.core.private_facts import MISSING
from src.data.models import PrivateAccountBalanceRecord
from src.data.repository import Repository
from src.exchange.okx_balance_facts import AssetBalanceFact, parse_okx_account_balance
from tests.unit.test_account_balance_store import bind, commit, database_state
from tests.unit.test_account_balance_store import stores as stores
from tests.unit.test_okx_balance_facts import balance_payload

LIABILITY_FIELDS = (
    ("crossLiab", "cross_liab"), ("isoLiab", "iso_liab"), ("uplLiab", "upl_liab"),
)


def raw_with_field(raw_name, value):
    asset = {"ccy": "BTC", "liab": "0", "cashBal": "1"}
    if value is not MISSING:
        asset[raw_name] = value
    return balance_payload(details=[asset])


def schema_state(repo):
    with repo.engine.connect() as connection:
        return connection.exec_driver_sql(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name",
        ).all()


def insert_legacy_balance(repo, older, raw, *, received_at=200):
    # Previously these fields were ignored by the asset parser, but retained in
    # both journals. Insert that old shape through the real immutable table.
    raw_json = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    payload_hash = hashlib.sha256(raw_json.encode()).hexdigest()
    values = {
        **older.model_dump(), "raw_json": raw_json, "payload_hash": payload_hash,
        "received_at": received_at,
        "details_json": json.dumps(raw["data"][0]["details"],
                                   sort_keys=True, separators=(",", ":")),
    }
    with repo.engine.begin() as connection:
        connection.execute(PrivateAccountBalanceRecord.__table__.insert(), values)
    return payload_hash


@pytest.mark.parametrize("raw_name,name", LIABILITY_FIELDS)
@pytest.mark.parametrize("value", [MISSING, None, "", "0.000", "+0.00000000000000000100",
                                  "9007199254740993.000100", "future-provider-text"])
def test_asset_liability_fields_preserve_optional_states_and_raw_spelling(raw_name, name, value):
    parsed = parse_okx_account_balance(raw_with_field(raw_name, value)).details[0]
    direct = AssetBalanceFact("BTC", **{name: value})
    assert getattr(parsed, name) == getattr(direct, name) == value


@pytest.mark.parametrize("raw_name,name", LIABILITY_FIELDS)
@pytest.mark.parametrize("bad", [1, True, 1.5, [], {}, " padded ", "x" * 257])
def test_new_liability_fields_reject_invalid_parser_and_direct_values(raw_name, name, bad):
    with pytest.raises(ValueError, match=name):
        parse_okx_account_balance(raw_with_field(raw_name, bad))
    with pytest.raises(ValueError, match=name):
        AssetBalanceFact("BTC", **{name: bad})


@pytest.mark.parametrize("raw_name,name", LIABILITY_FIELDS)
@pytest.mark.parametrize("value", [MISSING, None, "", "9007199254740993.000100"])
def test_store_replay_reopen_and_snapshot_preserve_values_without_schema_or_row_changes(
    stores, raw_name, name, value,
):
    _, (repo, _) = stores
    bind(repo)
    raw = raw_with_field(raw_name, value)
    before_schema = schema_state(repo)
    stored = commit(repo, raw)
    before = database_state(repo)
    assert not hasattr(stored, name)
    assert commit(repo, raw, received_at=999).model_dump() == stored.model_dump()
    for reader in (repo, Repository(db_path=repo.engine.url.database)):
        result = reader.get_spot_preflight_evidence(
            "BTC-USDT", expected_account_scope_id=stored.account_scope_id,
        )
        assert getattr(result.balance.fact.details[0], name) == value
        assert result.balance.received_at == 100
        assert result.balance.payload_hash == stored.payload_hash
        assert database_state(reader) == before
        assert schema_state(reader) == before_schema


def test_valid_old_journal_projects_new_fields_without_rewriting_evidence(stores):
    _, (repo, _) = stores
    bind(repo)
    older = commit(repo, balance_payload(details=[{"ccy": "BTC", "liab": "0"}]))
    raw = balance_payload(details=[{
        "ccy": "BTC", "crossLiab": "+0.00000000000000000100", "isoLiab": None,
        "uplLiab": "", "liab": "0.000",
    }])
    before_schema = schema_state(repo)
    payload_hash = insert_legacy_balance(repo, older, raw)
    before = database_state(repo)
    for reader in (repo, Repository(db_path=repo.engine.url.database)):
        result = reader.get_spot_preflight_evidence(
            "BTC-USDT", expected_account_scope_id=older.account_scope_id,
        )
        fact = result.balance.fact.details[0]
        assert (fact.cross_liab, fact.iso_liab, fact.upl_liab) == (
            "+0.00000000000000000100", None, "",
        )
        assert result.balance.payload_hash == payload_hash and result.balance.received_at == 200
        assert database_state(reader) == before and schema_state(reader) == before_schema


@pytest.mark.parametrize("raw_name,name", LIABILITY_FIELDS)
@pytest.mark.parametrize("bad", [1, True, [], {}, " padded ", "x" * 257])
def test_latest_invalid_legacy_field_fails_closed_without_fallback_or_rewrite(
    stores, raw_name, name, bad,
):
    _, (repo, _) = stores
    bind(repo)
    older = commit(repo, balance_payload(details=[{"ccy": "BTC", "liab": "0"}]))
    arguments = {"expected_account_scope_id": older.account_scope_id}
    initial = repo.get_spot_preflight_evidence("BTC-USDT", **arguments)
    assert initial.balance.fact.details[0].liab == "0"
    before_schema = schema_state(repo)
    insert_legacy_balance(repo, older, raw_with_field(raw_name, bad))
    before = database_state(repo)
    for reader in (repo, Repository(db_path=repo.engine.url.database)):
        with pytest.raises(ValueError, match=name):
            reader.get_spot_preflight_evidence("BTC-USDT", **arguments)
        assert database_state(reader) == before and schema_state(reader) == before_schema
