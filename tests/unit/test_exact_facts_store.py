"""Durable raw observations must never become trading or activation evidence."""

import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.core.execution_scope import ExecutionScope
from src.core.private_facts import MISSING, FactCoverage
from src.data.exchange_store import open_exchange_repository
from src.data.repository import Repository
from src.exchange.okx_private_facts import parse_okx_spot_fill_page


@pytest.fixture
def stores(tmp_path):
    master = Repository(db_path=str(tmp_path / "master.db"))
    scopes = [ExecutionScope("okx", "sandbox", c * 64) for c in "ab"]
    return master, [open_exchange_repository(s, master_engine=master.engine) for s in scopes]


def payload(bill="009", **changes):
    return {"code": "0", "data": [{
        "instType": "SPOT", "billId": bill, "instId": "BTC-USDT", "tradeId": "0007",
        "ordId": "0008", "side": "buy", "fillSz": "0.0000000100",
        "fillPx": "9007199254740993.0001", "fillTime": "001700000000000",
        "ts": "001700000000123", "fee": "-0.000000000100", "feeCcy": "BTC",
        **changes,
    }]}


def commit(repo, raw=None, *, after=None, before=None, inst_id=None, version=0, uid="00123"):
    raw = payload() if raw is None else raw
    page = parse_okx_spot_fill_page(raw, after=after, before=before, inst_id=inst_id)
    return repo.commit_exact_spot_fill_page(
        page, uid=uid, raw_payload=raw, received_at=1700000001000,
        expected_cursor_version=version,
    )


def test_uid_binding_survives_reopen_and_cannot_be_changed_or_replaced(stores):
    _, (repo, _) = stores
    assert repo.get_private_uid() is None
    bound = repo.bind_private_uid("00123", 1234)
    assert (bound.uid, bound.first_seen_at) == ("00123", 1234)
    assert repo.bind_private_uid("00123", 9999).first_seen_at == 1234
    assert Repository(engine=repo.engine).get_private_uid() == "00123"
    with pytest.raises(ValueError, match="UID"):
        repo.bind_private_uid("456", 1235)
    for statement in (
        "UPDATE private_uid_binding SET uid='456'",
        "DELETE FROM private_uid_binding",
        "INSERT OR REPLACE INTO private_uid_binding(account_scope_id,uid,first_seen_at) "
        "SELECT account_scope_id,'456',1235 FROM private_uid_binding",
    ):
        with pytest.raises(IntegrityError):
            with repo.engine.begin() as connection:
                connection.execute(text(statement))
    assert repo.get_private_uid() == "00123"


@pytest.mark.parametrize("uid", ["", " ", " 123", "x" * 257, None, 123, True])
def test_invalid_uid_never_binds(stores, uid):
    _, (repo, _) = stores
    with pytest.raises(ValueError):
        repo.bind_private_uid(uid, 1)
    assert repo.get_private_uid() is None


def test_exact_values_optionals_and_page_journal_survive_reopen(stores):
    _, (repo, _) = stores
    first = commit(repo)
    assert (first.version, first.next_after, first.complete) == (1, "009", False)
    raw = payload("008", clOrdId=None, fee=None, feeCcy="")
    commit(repo, raw, after="009", version=1)
    commit(repo, payload("007", clOrdId="", fee="", feeCcy=None), after="008", version=2)
    reopened = Repository(engine=repo.engine)
    facts = {f.bill_id: f for f in reopened.get_exact_spot_fills(uid="00123")}
    assert set(facts) == {"009", "008", "007"}
    fact = facts["009"]
    assert fact.symbol == fact.inst_id == "BTC-USDT"
    assert (fact.trade_id, fact.ord_id, fact.side) == ("0007", "0008", "buy")
    assert (fact.fill_sz, fact.fill_px) == ("0.0000000100", "9007199254740993.0001")
    assert (fact.fee, fact.fee_ccy) == ("-0.000000000100", "BTC")
    assert (fact.fill_time, fact.record_time) == ("001700000000000", "001700000000123")
    assert fact.cl_ord_id is MISSING
    assert (facts["008"].cl_ord_id, facts["008"].fee, facts["008"].fee_ccy) == (None, None, "")
    assert (facts["007"].cl_ord_id, facts["007"].fee, facts["007"].fee_ccy) == ("", "", None)
    pages = reopened.get_exact_spot_fill_pages(uid="00123")
    assert len(pages) == 3
    assert pages[0].received_at == 1700000001000
    assert json.loads(pages[1].raw_json) == raw
    assert all(p.coverage == "partial" for p in pages)


def test_missing_fee_and_currency_remain_missing(stores):
    _, (repo, _) = stores
    raw = payload()
    del raw["data"][0]["fee"]
    del raw["data"][0]["feeCcy"]
    commit(repo, raw)
    fact, = repo.get_exact_spot_fills(uid="00123")
    assert fact.fee is MISSING and fact.fee_ccy is MISSING


def test_replay_is_idempotent_even_after_cursor_advances(stores):
    _, (repo, _) = stores
    commit(repo)
    commit(repo, payload("008"), after="009", version=1)
    replay = commit(repo)
    assert (replay.version, replay.next_after) == (2, "008")
    assert len(repo.get_exact_spot_fills(uid="00123")) == 2
    assert len(repo.get_exact_spot_fill_pages(uid="00123")) == 2


def test_accounts_and_uids_do_not_leak_or_mutate_master_or_legacy(stores):
    master, (left, right) = stores
    commit(left)
    commit(right, payload(fillSz="2.00"), uid="00456")
    assert left.get_exact_spot_fills(uid="00123")[0].fill_sz == "0.0000000100"
    assert right.get_exact_spot_fills(uid="00456")[0].fill_sz == "2.00"
    with pytest.raises(ValueError, match="UID"):
        commit(left, uid="00456")
    with pytest.raises(ValueError, match="UID"):
        left.get_exact_spot_fills(uid="00456")
    for repo in (master, left, right):
        assert repo.get_orders() == repo.get_trades() == repo.get_positions() == []
        assert repo.get_account("__exchange__") is None
        assert repo.get_kill_switch().engaged is False
        assert repo.get_cash_ledger() == []
        assert repo.get_strategy_configs() == []
    assert left.get_private_sync_status() == {"latest_attempt": None, "last_committed": None}
    with pytest.raises(ValueError, match="facts store"):
        commit(master)
    with pytest.raises(ValueError, match="facts store"):
        master.bind_private_uid("00123", 1)


@pytest.mark.parametrize("changes", [{"fillSz": "1"}, {"fee": "0.1"}, {"fillPx": "1"}])
def test_conflicting_bill_rolls_back_earlier_new_fact_and_cursor(stores, changes):
    _, (repo, _) = stores
    initial = payload()
    initial["data"].append(payload("010")["data"][0])
    commit(repo, initial)
    raw = payload("008")
    raw["data"].append(payload("009", **changes)["data"][0])
    with pytest.raises(ValueError, match="conflict"):
        commit(repo, raw, after="010", version=1)
    assert {f.bill_id for f in repo.get_exact_spot_fills(uid="00123")} == {"009", "010"}
    assert len(repo.get_exact_spot_fill_pages(uid="00123")) == 1
    assert repo.get_exact_spot_fill_cursor(uid="00123").version == 1


def test_new_page_stale_version_and_wrong_request_cursor_roll_back(stores):
    _, (repo, _) = stores
    commit(repo)
    for version, after in [(0, "009"), (1, "010")]:
        with pytest.raises(ValueError, match="cursor"):
            commit(repo, payload("008"), after=after, version=version)
    assert len(repo.get_exact_spot_fills(uid="00123")) == 1
    assert len(repo.get_exact_spot_fill_pages(uid="00123")) == 1
    assert repo.get_exact_spot_fill_cursor(uid="00123").version == 1


def test_all_new_state_rolls_back_on_failure_after_page_commit(stores):
    _, (repo, _) = stores

    def interrupted(transaction):
        commit(transaction)
        raise RuntimeError("injected after page/facts/cursor")

    with pytest.raises(RuntimeError, match="injected"):
        repo.atomic_accounting(interrupted)
    assert repo.get_private_uid() is None
    assert repo.get_exact_spot_fills(uid="00123") == []
    assert repo.get_exact_spot_fill_pages(uid="00123") == []
    assert repo.get_exact_spot_fill_cursor(uid="00123") is None


def test_raw_journal_accepts_large_response_and_preserves_original_text(stores):
    _, (repo, _) = stores
    raw = payload()
    raw["message"] = "x" * 100_000
    raw_text = json.dumps(raw, indent=2)
    page = parse_okx_spot_fill_page(raw)
    repo.commit_exact_spot_fill_page(page, uid="00123", raw_payload=raw_text, received_at=10)
    assert repo.get_exact_spot_fill_pages(uid="00123")[0].raw_json == raw_text
    # Mapping order/format is irrelevant to the deterministic payload hash.
    assert commit(repo, raw).version == 1


@pytest.mark.parametrize("raw", ["{bad", "[]", {"x": float("nan")}, {"padding": "x" * 4_194_305},
                                  {"authorization": "secret"}])
def test_bad_or_oversize_raw_payload_is_rejected_without_binding(stores, raw):
    _, (repo, _) = stores
    page = parse_okx_spot_fill_page(payload())
    with pytest.raises(ValueError):
        repo.commit_exact_spot_fill_page(page, uid="00123", raw_payload=raw, received_at=10)
    assert repo.get_private_uid() is None
    assert repo.get_exact_spot_fill_pages(uid="00123") == []


def test_raw_and_typed_page_mismatch_fails_closed(stores):
    _, (repo, _) = stores
    with pytest.raises(ValueError):
        repo.commit_exact_spot_fill_page(
            parse_okx_spot_fill_page(payload()), uid="00123", raw_payload=payload("008"),
            received_at=10,
        )
    assert repo.get_private_uid() is None


def test_same_page_identity_cannot_be_replaced_with_different_raw_content(stores):
    _, (repo, _) = stores
    commit(repo)
    with pytest.raises(ValueError, match="conflict"):
        commit(repo, payload(fillSz="1"))
    assert repo.get_exact_spot_fill_cursor(uid="00123").version == 1


def test_empty_page_keeps_coverage_unknown_and_filters_have_distinct_cursors(stores):
    _, (repo, _) = stores
    commit(repo)
    empty = commit(repo, {"code": "0", "data": []}, after="009", version=1)
    assert empty.version == 2 and empty.complete is False and empty.next_after is None
    assert repo.get_exact_spot_fill_pages(uid="00123")[-1].coverage == FactCoverage.UNKNOWN
    distinct = commit(repo, payload("007"), inst_id="BTC-USDT", before="100")
    assert distinct.version == 1
    assert repo.get_exact_spot_fill_cursor(uid="00123").version == 2
    assert repo.get_exact_spot_fill_cursor(
        uid="00123", inst_id="BTC-USDT", before="100",
    ).next_after == "007"


@pytest.mark.parametrize("key", ["password", "apiPassphrase", "apiSecretKey", "private_key"])
def test_raw_journal_rejects_credentials_in_valid_response(stores, key):
    _, (repo, _) = stores
    raw = payload()
    raw["headers"] = {key: "must-never-persist"}
    with pytest.raises(ValueError, match="credential"):
        commit(repo, raw)
    assert repo.get_private_uid() is None


def test_cursor_reads_fail_closed_on_foreign_account_record(stores):
    _, (repo, _) = stores
    commit(repo)
    with repo.engine.begin() as connection:
        connection.execute(text("UPDATE exact_spot_fill_cursor SET account_scope_id='foreign'"))
    with pytest.raises(ValueError, match="scope"):
        repo.get_exact_spot_fill_cursor(uid="00123")
