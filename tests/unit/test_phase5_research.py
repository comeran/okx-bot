"""第五阶段：可复现研究、样本试验，以及按币种对上的账本。"""

from decimal import Decimal

import pytest

from src.data.journal import Journal
from src.data.repository import Repository
from src.research.phase5 import (
    ReleaseError,
    attribute_difference,
    bind_release,
    build_manifest,
    callback_parity,
    load_manifest,
    parameter_stability,
    prepare_dataset,
    record_trial,
    separate_samples,
    store_manifest,
    strategy_benchmark,
    walk_forward,
)
from src.strategy.base import BaseStrategy


def _bar(timestamp: int, close: float, *, closed: bool = True) -> dict:
    return {
        "timestamp": timestamp,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1,
        "closed": closed,
    }


def test_manifest_is_immutable_and_replay_matches_the_retained_dataset(tmp_path) -> None:
    rows = [
        _bar(0, 10),
        _bar(60_000, 11),
        _bar(60_000, 99),
        _bar(180_000, 12),
        _bar(240_000, 13, closed=False),
    ]
    prepared = prepare_dataset(rows, timeframe_ms=60_000)
    assert [bar["timestamp"] for bar in prepared.bars] == [0, 60_000, 180_000]
    assert [bar["close"] for bar in prepared.bars] == [10, 11, 12]
    assert [issue.kind for issue in prepared.issues] == [
        "duplicate", "revised", "missing", "unclosed",
    ]
    assert prepared.issues[2].timestamp == 120_000
    again = prepare_dataset(rows, timeframe_ms=60_000)
    assert again.data_hash == prepared.data_hash

    manifest = build_manifest(
        code_version="code-1",
        config_version="config-1",
        cost_model_version="cost-1",
        bars=prepared.bars,
        equity=((0, 100.0, 0.0), (180_000, 90.0, 0.1)),
        rejected=((60_000, "risk", "BTC-USDT"),),
    )
    repeat = build_manifest(
        code_version="code-1",
        config_version="config-1",
        cost_model_version="cost-1",
        bars=prepared.bars,
        equity=((0, 100.0, 0.0), (180_000, 90.0, 0.1)),
        rejected=((60_000, "risk", "BTC-USDT"),),
    )
    assert manifest.manifest_id == repeat.manifest_id
    assert manifest.data_hash == prepared.data_hash
    with pytest.raises(AttributeError):
        manifest.code_version = "changed"  # type: ignore[misc]
    changed = build_manifest(
        code_version="code-2",
        config_version="config-1",
        cost_model_version="cost-1",
        bars=prepared.bars,
        equity=((0, 100.0, 0.0), (180_000, 90.0, 0.1)),
        rejected=((60_000, "risk", "BTC-USDT"),),
    )
    assert changed.manifest_id != manifest.manifest_id

    repository = Repository(db_path=str(tmp_path / "bot.db"))
    store_manifest(repository, manifest)
    loaded = load_manifest(repository, manifest.manifest_id)
    assert loaded == manifest
    assert loaded.equity[1].drawdown == 0.1
    assert loaded.rejected[0].reason == "risk"


def test_release_binding_and_callback_parity_attribute_online_differences() -> None:
    class Research(BaseStrategy):
        async def on_bar(self, bar) -> None:
            return None

    class Runtime(BaseStrategy):
        async def on_bar(self, bar) -> None:
            return None

    assert callback_parity(Research(), Runtime()) == {"research": [], "runtime": []}

    class Partial:
        async def on_bar(self, bar) -> None:
            return None

    missing = callback_parity(Research(), Partial())
    assert "get_balance" in missing["runtime"]
    assert missing["research"] == []

    bound = bind_release("sma-1", "runtime-1", allowed={("sma-1", "runtime-1")})
    assert bound["bound"] is True
    with pytest.raises(ReleaseError):
        bind_release("sma-1", "runtime-2", allowed={("sma-1", "runtime-1")})

    assert attribute_difference(
        {"price": 10, "fee": 1, "quantity": 2},
        {"price": 10, "fee": 1, "quantity": 2},
    ) == []
    assert attribute_difference(
        {"price": 10, "fee": 1, "quantity": 2},
        {"price": 11, "fee": 1, "quantity": 2},
    ) == [{"field": "price", "research": 10, "online": 11}]


def test_every_trial_is_recorded_with_walk_forward_and_benchmark(tmp_path) -> None:
    samples = separate_samples(list(range(10)), train_ratio=0.5, validation_ratio=0.2)
    assert samples == {
        "train": [0, 1, 2, 3, 4],
        "validation": [5, 6],
        "test": [7, 8, 9],
    }
    windows = walk_forward(list(range(10)), train_size=4, test_size=2)
    assert windows == [
        {"train": [0, 1, 2, 3], "test": [4, 5]},
        {"train": [2, 3, 4, 5], "test": [6, 7]},
        {"train": [4, 5, 6, 7], "test": [8, 9]},
    ]
    assert parameter_stability([0.01, 0.02, 0.015], max_range=0.02)["stable"] is True
    assert parameter_stability([0.01, 0.05], max_range=0.02)["stable"] is False
    compared = strategy_benchmark(0.10, 0.04)
    assert compared == {"strategy": 0.10, "benchmark": 0.04, "excess": 0.06, "beat": True}

    repository = Repository(db_path=str(tmp_path / "bot.db"))
    record_trial(repository, name="sma", params={"window": 5}, accepted=True, metric=0.1)
    record_trial(repository, name="sma", params={"window": 2}, accepted=False, metric=-0.2)
    stored = repository.engine.connect().execute(
        __import__("sqlalchemy").text(
            "SELECT name, accepted, metric FROM research_trial ORDER BY id"
        )
    ).all()
    assert [(row[0], bool(row[1]), row[2]) for row in stored] == [
        ("sma", True, 0.1),
        ("sma", False, -0.2),
    ]


def test_currency_ledger_reconciles_fees_rebates_and_flows() -> None:
    book = Journal("backtest")
    book.open_cash(Decimal("100"), "USDT", timestamp=1)
    book.open_cash(Decimal("1"), "BTC", timestamp=1)
    book.post_trade(
        side="buy", quantity=Decimal("1"), price=Decimal("10"),
        currency="USDT", symbol="BTC-USDT", ref="buy-1", timestamp=2,
    )
    book.post_fee(Decimal("0.1"), "BTC", ref="fee-1", timestamp=2)
    book.post_rebate(Decimal("0.02"), "BTC", ref="rebate-1", timestamp=3)
    book.post_funding(Decimal("0.5"), "USDT", ref="funding-1", timestamp=4)
    book.post_transfer(Decimal("5"), "USDT", ref="transfer-1", timestamp=5, direction="in")
    book.post_settlement(Decimal("0.25"), "USDT", ref="settle-1", timestamp=6, direction="pay")

    assert book.reconciles() is True
    assert book.nav_change_by_currency()["USDT"] == book.flows_by_currency()["USDT"]
    assert book.nav_change_by_currency()["BTC"] == Decimal("-0.08")
    assert book.flows_by_currency()["BTC"] == Decimal("-0.08")
