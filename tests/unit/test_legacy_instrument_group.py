"""New raw-only group validation fails closed on retained legacy responses."""

import hashlib
import json

import pytest
from sqlalchemy import inspect

from src.data.models import PrivateSpotInstrumentRecord, PrivateSpotInstrumentResponseRecord
from src.data.repository import Repository
from tests.unit.test_account_balance_store import database_state
from tests.unit.test_account_balance_store import stores as stores
from tests.unit.test_okx_instrument_facts import instrument_payload, instrument_row
from tests.unit.test_spot_instrument_store import commit


@pytest.mark.parametrize("legacy_group", [1, True, [], {}, " padded ", "x" * 257])
def test_latest_legacy_invalid_raw_group_is_rejected_without_rewrite_or_fallback(
    stores, legacy_group,
):
    _, (repo, _) = stores
    older, = commit(repo, instrument_payload(instrument_row(groupId="001")))
    arguments = {"expected_account_scope_id": older.account_scope_id}
    initial = repo.get_spot_preflight_evidence("BTC-USDT", **arguments)
    assert initial.instrument.fact.group_id == "001"

    # The previous parser ignored groupId, so such a response could have been
    # retained with otherwise valid projections. Insert that historical shape
    # directly, preserving the response hash and all append-only protections.
    raw = instrument_payload(instrument_row(groupId=legacy_group))
    raw_json = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    payload_hash = hashlib.sha256(raw_json.encode()).hexdigest()
    projection = {**older.model_dump(), "payload_hash": payload_hash, "received_at": 200}
    assert "group_id" not in projection
    with repo.engine.begin() as connection:
        connection.execute(PrivateSpotInstrumentResponseRecord.__table__.insert(), {
            "account_scope_id": older.account_scope_id, "payload_hash": payload_hash,
            "received_at": 200, "raw_json": raw_json,
        })
        connection.execute(PrivateSpotInstrumentRecord.__table__.insert(), projection)

    columns = {column["name"] for column in inspect(repo.engine).get_columns(
        "private_spot_instrument",
    )}
    assert "group_id" not in columns
    before = database_state(repo)
    for reader in (repo, Repository(db_path=repo.engine.url.database)):
        with pytest.raises(ValueError, match="groupId"):
            reader.get_spot_preflight_evidence("BTC-USDT", **arguments)
        assert database_state(reader) == before
