"""Stored diagnostics must consume one exact, scoped observation set."""

import json
from dataclasses import FrozenInstanceError

import pytest
from sqlalchemy import event, text

from src.core.private_facts import MISSING
from src.data.repository import Repository
from src.exchange.okx_account_facts import parse_okx_account_config
from src.exchange.okx_balance_facts import parse_okx_account_balance
from src.exchange.okx_instrument_facts import parse_okx_spot_instruments
from tests.unit.test_account_balance_store import database_state
from tests.unit.test_account_balance_store import stores as stores
from tests.unit.test_okx_balance_facts import balance_payload
from tests.unit.test_okx_instrument_facts import instrument_payload, instrument_row


def config(repo, at=100, **fields):
    raw = {"code": "0", "data": [{"uid": "00123", "autoLoan": "0",
                                   "enableSpotBorrow": "0", **fields}]}
    return repo.commit_private_account_config(
        parse_okx_account_config(raw), raw_payload=raw, received_at=at,
    )


def balance(repo, at=100, **fields):
    raw = balance_payload(**fields)
    return repo.commit_private_account_balance(
        parse_okx_account_balance(raw), raw_payload=raw, received_at=at,
    )


def instruments(repo, at=100, raw=None):
    raw = raw if raw is not None else instrument_payload(instrument_row(
        tickSz="0.10", lotSz="0.00100", minSz="0.00100",
    ))
    return repo.commit_private_spot_instruments(
        parse_okx_spot_instruments(raw), raw_payload=raw, received_at=at,
    )


def scope(repo):
    with repo.engine.connect() as connection:
        return connection.exec_driver_sql(
            "SELECT account_scope_id FROM execution_scope_header",
        ).scalar_one()


def read(repo, **kwargs):
    return repo.get_spot_preflight_evidence(
        "BTC-USDT", expected_account_scope_id=(
            kwargs["scope_id"] if "scope_id" in kwargs else scope(repo)
        ),
    )


def test_reconstructs_exact_immutable_facts_without_writing_any_rows(stores):
    _, (repo, _) = stores
    c = config(repo, acctLv=None, mainUid="", settleCcy="USD")
    b = balance(repo)
    i, = instruments(repo)
    before = database_state(repo)
    evidence = read(repo)
    assert evidence.account_scope_id == scope(repo) and evidence.uid == "00123"
    assert evidence.config.fact.acct_lv is None
    assert evidence.config.fact.pos_mode is MISSING
    assert evidence.config.fact.main_uid == ""
    assert evidence.balance.fact.total_eq == "9007199254740993.000100"
    assert evidence.balance.fact.details[0].cash_bal == "0.00000000000000000100"
    assert evidence.instrument.fact.lot_sz == "0.00100"
    for observation, record in ((evidence.config, c), (evidence.balance, b),
                                (evidence.instrument, i)):
        assert (observation.payload_hash, observation.received_at) == (record.payload_hash, 100)
    with pytest.raises(FrozenInstanceError):
        evidence.balance.received_at = 999
    assert database_state(repo) == before
    reopened = Repository(db_path=repo.engine.url.database)
    assert read(reopened) == evidence


def test_missing_sources_and_public_only_store_never_invent_uid_or_facts(stores):
    _, (repo, _) = stores
    evidence = read(repo)
    assert evidence.uid is evidence.config is evidence.balance is evidence.instrument is None
    instruments(repo)
    assert read(repo).uid is None and read(repo).instrument.fact.inst_id == "BTC-USDT"


def test_latest_equal_time_policy_wins_and_replay_never_refreshes_receipt(stores):
    _, (repo, _) = stores
    config(repo)
    config(repo, autoLoan="1")
    config(repo, at=500)
    balance(repo)
    balance(repo, totalEq="1.00")
    balance(repo, at=500)
    instruments(repo)
    instruments(repo, raw=instrument_payload(instrument_row(state="suspend")))
    instruments(repo, at=500)
    result = read(repo)
    assert result.config.fact.auto_loan == "1"
    assert result.balance.fact.total_eq == "1.00"
    assert result.instrument.fact.state == "suspend"
    assert result.config.received_at == result.balance.received_at == 100
    assert result.instrument.received_at == 100


@pytest.mark.parametrize("latest", [{"code": "0", "data": []}, instrument_payload(
    instrument_row(instId="ETH-USDT", baseCcy="ETH"),
)])
def test_latest_response_omission_never_falls_back_to_older_rules(stores, latest):
    _, (repo, _) = stores
    instruments(repo)
    instruments(repo, at=200, raw=latest)
    result = read(repo)
    assert result.instrument.fact is None and result.instrument.received_at == 200


def test_refuses_master_and_wrong_expected_scope(stores):
    master, (repo, other) = stores
    with pytest.raises(ValueError, match="facts store"):
        master.get_spot_preflight_evidence("BTC-USDT", expected_account_scope_id=scope(repo))
    with pytest.raises(ValueError, match="scope"):
        read(repo, scope_id=scope(other))


def tamper(repo, table, assignments):
    # Simulate disk/operator corruption after bypassing append-only protections.
    with repo.engine.begin() as connection:
        triggers = connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,),
        ).scalars().all()
        for trigger in triggers:
            connection.exec_driver_sql(f'DROP TRIGGER "{trigger}"')
        connection.exec_driver_sql(f'UPDATE "{table}" SET {assignments}')


@pytest.mark.parametrize("table,assignments", [
    ("private_account_config", "auto_loan='1'"),
    ("private_account_config", "auto_loan=NULL, auto_loan_state='missing'"),
    ("private_account_config", "payload_hash='broken'"),
    ("private_account_config", "uid='different'"),
    ("private_account_config", "account_scope_id='other'"),
    ("private_account_balance", "details_json='[]'"),
    ("private_account_balance", "total_eq='1'"),
    ("private_account_balance", "uid='different'"),
    ("private_spot_instrument", "tick_sz='1'"),
    ("private_spot_instrument", "state='suspend'"),
    ("private_spot_instrument", "received_at=101"),
    ("private_spot_instrument", "payload_hash='" + "a" * 64 + "'"),
    ("private_spot_instrument_response", "raw_json='{}'"),
    ("private_uid_binding", "uid='different'"),
])
def test_corrupt_selected_raw_hash_projection_or_binding_fails_closed(stores, table, assignments):
    _, (repo, _) = stores
    config(repo)
    balance(repo)
    instruments(repo)
    tamper(repo, table, assignments)
    before = database_state(repo)
    with pytest.raises(ValueError):
        read(repo)
    assert database_state(repo) == before


def test_missing_instrument_projection_fails_instead_of_using_raw_or_older_rows(stores):
    _, (repo, _) = stores
    instruments(repo)
    tamper(repo, "private_spot_instrument", "received_at=received_at")
    with repo.engine.begin() as connection:
        connection.exec_driver_sql("DELETE FROM private_spot_instrument")
    with pytest.raises(ValueError):
        read(repo)


def test_newer_malformed_raw_never_falls_back_to_older_config(stores):
    _, (repo, _) = stores
    config(repo)
    config(repo, at=200, autoLoan="1")
    tamper(repo, "private_account_config", "raw_json='{broken' WHERE received_at=200")
    with pytest.raises(ValueError):
        read(repo)


def test_one_read_uses_one_transaction_for_header_uid_and_all_sources(stores):
    _, (repo, _) = stores
    config(repo)
    balance(repo)
    instruments(repo)
    expected_scope = scope(repo)
    transactions = set()

    def capture(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            assert connection.in_transaction()
            assert connection.connection.in_transaction
            transactions.add(id(connection.get_transaction()))

    event.listen(repo.engine, "before_cursor_execute", capture)
    try:
        read(repo, scope_id=expected_scope)
    finally:
        event.remove(repo.engine, "before_cursor_execute", capture)
    assert len(transactions) == 1


def test_duplicate_raw_keys_are_rejected_even_if_projection_looks_safe(stores):
    _, (repo, _) = stores
    config(repo)
    raw = json.dumps({"code": "0", "data": [{"uid": "00123"}]}).replace(
        '"code": "0"', '"code": "1", "code": "0"',
    )
    with repo.engine.begin() as connection:
        for trigger in connection.execute(text(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND tbl_name='private_account_config'",
        )).scalars().all():
            connection.exec_driver_sql(f'DROP TRIGGER "{trigger}"')
        connection.execute(text("UPDATE private_account_config SET raw_json=:raw"), {"raw": raw})
    with pytest.raises(ValueError):
        read(repo)
