from sqlalchemy import text

from src.data.repository import Repository
from src.ops.phase4_controls import record_source_heartbeat
from src.web.api import ops


def test_desk_uses_current_market_heartbeat_before_historical_cache(tmp_path, monkeypatch):
    repository = Repository(db_path=str(tmp_path / "bot.db"))
    record_source_heartbeat(repository, "market", observed_at=90_000, status="fresh")
    monkeypatch.setattr(ops, "Repository", lambda: repository)
    monkeypatch.setattr(ops, "current_timestamp_ms", lambda: 100_000)
    monkeypatch.setattr(repository, "latest_market_timestamp", lambda: 1)

    assert ops._desk_price_age() == 10_000


def test_desk_sources_follow_configured_freshness_limit(tmp_path, monkeypatch):
    from src.risk import supervisor

    repository = Repository(db_path=str(tmp_path / "bot.db"))
    record_source_heartbeat(repository, "market", observed_at=40_000, status="fresh")
    monkeypatch.setattr(ops, "current_timestamp_ms", lambda: 100_000)
    monkeypatch.setattr(supervisor, "_limits", lambda: {"max_price_age_ms": 90_000})

    assert ops._desk_sources(repository)["market"] == "fresh"
    with repository.engine.begin() as connection:
        connection.execute(text("UPDATE source_heartbeat SET observed_at = 1"))
    assert ops._desk_sources(repository)["market"] == "stale"
