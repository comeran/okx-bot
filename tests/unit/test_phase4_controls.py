"""第四阶段：持续风控、分开的值班动作、配置新鲜度，以及不依赖界面的告警。"""

from src.data.repository import Repository
from src.ops.phase4_controls import (
    AlertOutbox,
    PushQueue,
    apply_control,
    assess_runtime_risk,
    classify_config,
    deliver_alert,
    enqueue_alert,
    publish_runtime_risk,
    readiness_payload,
    record_source_heartbeat,
    runtime_risk_reason,
    source_freshness,
    supervise_observation,
)


def _risk(**overrides):
    fields = dict(
        price_status="fresh",
        account_status="fresh",
        position_notional=0,
        pending_notional=0,
        equity=100,
        peak_equity=100,
        daily_pnl=0,
        market_type="spot",
        margin_ratio=None,
        max_total_position_pct=0.8,
        max_daily_loss_pct=0.05,
        max_drawdown_pct=0.15,
    )
    fields.update(overrides)
    return assess_runtime_risk(**fields)


def test_unknown_or_stale_state_blocks_new_exposure() -> None:
    unknown = _risk(price_status="unknown")
    assert unknown["blocked"] is True
    assert unknown["reason"] == "unknown_price"
    assert unknown["derivative_margin"] == "not_applicable"

    assert _risk(price_status="stale")["reason"] == "stale_price"
    assert _risk(account_status="unknown")["reason"] == "unknown_account"
    assert _risk(account_status="stale")["reason"] == "stale_account"
    assert _risk()["blocked"] is False


def test_exposure_loss_drawdown_and_derivative_margin_are_separate() -> None:
    exposure = _risk(position_notional=70, pending_notional=15)
    assert exposure["reason"] == "exposure_limit"
    assert exposure["exposure"] == 85

    assert _risk(daily_pnl=-6)["reason"] == "daily_loss"
    assert _risk(equity=80, peak_equity=100)["reason"] == "drawdown"
    assert _risk(market_type="swap", margin_ratio=None)["reason"] == "derivative_margin"
    assert _risk(market_type="swap", margin_ratio=0.4)["derivative_margin"] == "insufficient"
    clear = _risk(market_type="swap", margin_ratio=1.5)
    assert clear["blocked"] is False
    assert clear["derivative_margin"] == "ok"


def test_controls_stay_distinct_and_recovery_is_manual(tmp_path) -> None:
    repository = Repository(db_path=str(tmp_path / "bot.db"))
    stopped: list[str] = []

    paused = apply_control(
        repository, "pause_opening", now_ms=1, reason="stand aside",
    )
    assert paused == {
        "action": "pause_opening",
        "engine_stopped": [],
        "orders_cancelled": [],
        "positions_closed": [],
        "recovered": False,
    }
    assert repository.get_opening_pause().paused is True
    assert repository.get_kill_switch().engaged is False

    engine = apply_control(
        repository,
        "stop_engine",
        now_ms=2,
        names=["alpha"],
        stop=lambda name: stopped.append(name),
    )
    assert engine["engine_stopped"] == ["alpha"]
    assert engine["orders_cancelled"] == []
    assert stopped == ["alpha"]

    cancelled = apply_control(
        repository,
        "cancel_orders",
        now_ms=3,
        worker=lambda: {"cancelled": ["ord-1"], "failed": [], "closed": []},
    )
    assert cancelled["orders_cancelled"] == ["ord-1"]
    assert cancelled["positions_closed"] == []

    flattened = apply_control(
        repository,
        "flatten",
        now_ms=4,
        worker=lambda: {"cancelled": [], "failed": [], "closed": [{"symbol": "BTC-USDT"}]},
    )
    assert flattened["positions_closed"] == [{"symbol": "BTC-USDT"}]

    publish_runtime_risk(repository, _risk(price_status="unknown"), now_ms=5)
    refused = apply_control(repository, "recover", now_ms=6)
    assert refused["recovered"] is False
    assert refused["reason"] == "unknown_price"
    assert repository.get_opening_pause().paused is True

    publish_runtime_risk(repository, _risk(), now_ms=7)
    repository.set_kill_switch(True, "Flatten positions", 7)
    recovered = apply_control(repository, "recover", now_ms=8)
    assert recovered["recovered"] is True
    assert repository.get_opening_pause().paused is False
    assert repository.get_kill_switch().engaged is False
    actions = repository.list_operator_actions(limit=10)
    assert [item["action"] for item in actions] == [
        "recover", "recover", "flatten", "cancel_orders", "stop_engine", "pause_opening",
    ]
    assert actions[0]["result"]["recovered"] is True
    assert actions[1]["result"]["recovered"] is False


def test_heartbeats_config_state_queue_and_alert_retries(tmp_path) -> None:
    repository = Repository(db_path=str(tmp_path / "bot.db"))
    record_source_heartbeat(repository, "market", observed_at=1_000, status="fresh")
    record_source_heartbeat(repository, "account", observed_at=1_000, status="fresh")
    fresh = source_freshness(repository, now_ms=2_000, max_age_ms=15_000)
    assert fresh["market"] == "fresh"
    assert fresh["account"] == "fresh"
    assert fresh["private_sync"] == "unknown"

    record_source_heartbeat(repository, "market", observed_at=1_000, status="fresh")
    assert source_freshness(repository, now_ms=20_000, max_age_ms=15_000)["market"] == "stale"

    startup = {"mode": "backtest", "market_type": "spot", "demo": True, "risk": 0.05}
    assert classify_config(startup, dict(startup), applied=False) == "saved"
    assert classify_config(startup, dict(startup), applied=True) == "effective"
    assert classify_config(
        startup, {**startup, "risk": 0.02}, applied=True,
    ) == "effective"
    assert classify_config(
        startup, {**startup, "mode": "live"}, applied=True,
    ) == "restart_required"

    queue = PushQueue(max_size=2)
    assert queue.push("one") is True
    assert queue.push("two") is True
    assert queue.push("three") is False
    assert queue.dropped == 1
    assert queue.items == ["one", "two"]

    def disconnected(_item: object) -> None:
        raise ConnectionError("ui closed")

    supervised = supervise_observation(
        _risk(price_status="stale"), push=disconnected,
    )
    assert supervised["reason"] == "stale_price"
    assert supervised["published"] is False
    assert supervised["supervision_continued"] is True

    sent: list[dict] = []

    def sender(payload: dict) -> None:
        sent.append(payload)
        if len(sent) < 3:
            raise TimeoutError("alert down")

    enqueue_alert(repository, "risk", {"reason": "stale_price"}, now_ms=1)
    assert deliver_alert(repository, sender, now_ms=1, max_attempts=3)["status"] == "pending"
    assert deliver_alert(repository, sender, now_ms=2, max_attempts=3)["status"] == "pending"
    delivered = deliver_alert(repository, sender, now_ms=3, max_attempts=3)
    assert delivered["status"] == "delivered"
    assert delivered["attempts"] == 3
    assert deliver_alert(repository, sender, now_ms=4, max_attempts=3)["status"] == "delivered"
    assert len(sent) == 3

    def always_fail(_payload: dict) -> None:
        raise TimeoutError("still down")

    enqueue_alert(repository, "risk", {"reason": "drawdown"}, now_ms=10)
    for moment in (10, 11, 12):
        failed = deliver_alert(repository, always_fail, now_ms=moment, max_attempts=3)
    assert failed["status"] == "failed"
    assert failed["attempts"] == 3
    assert AlertOutbox(repository).pending(now_ms=13) == []

    ready = readiness_payload(repository, now_ms=30_000, max_price_age_ms=15_000)
    assert ready["ready"] is False
    assert ready["reasons"] == ["stale_price"]
    assert runtime_risk_reason(repository) is None
