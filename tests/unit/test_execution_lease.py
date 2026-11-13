from src.data.repository import Repository


def test_strategy_lease_is_exclusive_until_release_or_owner_exit(tmp_path):
    database = str(tmp_path / "lease.db")
    first = Repository(db_path=database)
    second = Repository(db_path=database)

    assert first.acquire_strategy_lease("ma_cross", "11", alive=lambda _owner: True) is True
    assert second.acquire_strategy_lease("ma_cross", "22", alive=lambda _owner: True) is False
    assert first.acquire_strategy_lease("ma_cross", "11", alive=lambda _owner: True) is True

    first.release_strategy_lease("ma_cross", "11")
    assert second.acquire_strategy_lease("ma_cross", "22", alive=lambda _owner: True) is True
    assert second.acquire_strategy_lease(
        "ma_cross", "33", alive=lambda _owner: False
    ) is True


def test_opening_pause_survives_a_new_repository(tmp_path):
    database = str(tmp_path / "pause.db")
    first = Repository(db_path=database)
    saved = first.set_opening_pause(True, "stand aside", 1700000000000)

    second = Repository(db_path=database)
    loaded = second.get_opening_pause()

    assert saved.paused is True
    assert loaded.paused is True
    assert loaded.reason == "stand aside"
    assert loaded.updated_at == 1700000000000
    assert second.get_opening_pause().paused is True
