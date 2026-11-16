"""OKX 模拟仓长跑验收。只注入故障，不下单，也不打开实盘。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

from src.data.models import OrderRecord
from src.market.bars import is_closed_bar

_FAULTS = ("funding", "sync", "risk", "research", "disconnect", "restart", "restore")
_TIMEFRAME_MS = 60_000


async def run_sandbox_acceptance(
    exchange: Any,
    repository: Any,
    *,
    min_elapsed_ms: int,
    interval_ms: int,
    max_cycles: int,
    minimum_private_observations: int,
    now_ms: Callable[[], int],
    sleep: Callable[[int], Awaitable[None]],
    progress: Callable[[int, str], None] | None = None,
) -> dict[str, Any]:
    """连着读取模拟仓，并依次注入第 2 到第 5 阶段的故障。"""
    started = now_ms()
    header = _header(exchange)
    if header != "1":
        return _report(
            accepted=False, reason="simulated_header_missing", elapsed_ms=0,
            cycles=0, private_observations=0, header=header, drills=[], residuals=[],
        )
    pending = list(_FAULTS)
    drills: list[dict[str, Any]] = []
    residuals: list[dict[str, Any]] = []
    cycles = 0
    private_ok = 0
    failures = 0
    while cycles < max_cycles:
        elapsed = now_ms() - started
        if elapsed >= min_elapsed_ms and not pending:
            break
        header = _header(exchange)
        if header != "1":
            return _report(
                accepted=False, reason="simulated_header_missing", elapsed_ms=elapsed,
                cycles=cycles, private_observations=private_ok, header=header,
                drills=drills, residuals=residuals,
            )
        cycles += 1
        phase = pending[0] if pending else "observe"
        if progress is not None:
            progress(cycles, phase)
        if phase == "disconnect":
            drills.append(_drill(
                "disconnect", True, "这一轮注入断开，没有发送私有请求，也没有下单",
            ))
            pending.pop(0)
            await sleep(interval_ms)
            continue
        try:
            observation = await _observe(exchange, now_ms())
            private_ok += 1
        except Exception:
            failures += 1
            residuals.append(_drill(
                "observe", True, "交易所请求失败，没有下单，也没有改本地账本",
            ))
            if private_ok == 0 and failures >= 3:
                break
            await sleep(interval_ms)
            continue
        if phase != "observe":
            drills.append(_inject(phase, observation, repository, now_ms()))
            pending.pop(0)
        await sleep(interval_ms)
    elapsed = now_ms() - started
    reason = _reason(
        pending=pending,
        elapsed_ms=elapsed,
        min_elapsed_ms=min_elapsed_ms,
        private_ok=private_ok,
        minimum_private=minimum_private_observations,
        drills=drills,
    )
    return _report(
        accepted=reason == "ok", reason=reason, elapsed_ms=elapsed, cycles=cycles,
        private_observations=private_ok, header="1", drills=drills, residuals=residuals,
    )


def _inject(
    phase: str, observation: dict[str, Any], repository: Any, now_ms: int,
) -> dict[str, Any]:
    try:
        if phase == "funding":
            return _funding(repository, now_ms)
        if phase == "sync":
            return _sync(repository, now_ms)
        if phase == "risk":
            return _risk()
        if phase == "research":
            return _research(observation)
        if phase == "restart":
            return _restart(repository)
        if phase == "restore":
            return _restore(repository)
    except Exception:
        return _drill(phase, False, "演练没有完成，没有下单")
    return _drill(phase, False, "演练没有完成，没有下单")


def _funding(repository: Any, now_ms: int) -> dict[str, Any]:
    untrusted = _activation_code(
        repository, strategy="alpha", scope_id="drill-untrusted", budget=Decimal("1"),
        available=Decimal("100"), now_ms=now_ms, passed=False,
    )
    first = _activation_code(
        repository, strategy="alpha", scope_id="drill-overlap", budget=Decimal("40"),
        available=Decimal("100"), now_ms=now_ms, passed=True,
    )
    second = _activation_code(
        repository, strategy="beta", scope_id="drill-overlap", budget=Decimal("40"),
        available=Decimal("100"), now_ms=now_ms, passed=True,
    )
    ok = (
        untrusted == "preflight_not_trusted"
        and first == "activated"
        and second == "position_overlap"
    )
    return _drill(
        "funding", ok,
        "模拟仓余额只作观测。预检未通过不能拨款。同一现货已被占用时拒绝第二笔。",
    )


def _activation_code(
    repository: Any, *, strategy: str, scope_id: str, budget: Decimal,
    available: Decimal, now_ms: int, passed: bool,
) -> str:
    from src.data.funded_allocation import (
        FundingError,
        TrustedActivationFacts,
        activate_funded_allocation,
    )

    facts = TrustedActivationFacts(
        available_quote=available, observed_at=now_ms, preflight_passed=passed,
        market_type="spot", account_scope_id=scope_id,
    )
    try:
        activate_funded_allocation(
            repository, scope_id=scope_id, strategy=strategy, symbol="BTC-USDT",
            budget=budget, environment="sandbox", revision=1, facts=facts,
            now_ms=now_ms, market_type="spot",
        )
    except FundingError as exc:
        return exc.code
    return "activated"


def _sync(repository: Any, now_ms: int) -> dict[str, Any]:
    from src.ops.phase3_recovery import (
        apply_fill_pages,
        explain_order_by_identity,
        run_bounded_sync,
    )

    repository.upsert_order(OrderRecord(
        order_id="drill-1", client_order_id="drill-cid", strategy="drill", symbol="BTC-USDT",
        side="buy", type="limit", amount=1, price=10, status="pending", fill_price=0,
        timestamp=now_ms, updated_at=now_ms, remaining_amount=1,
    ))

    def lookup(_client_id: str) -> dict:
        raise TimeoutError

    explained = explain_order_by_identity(
        repository, client_order_id="drill-cid", lookup=lookup, kind="submit",
    )
    page = [{"trade_id": "drill-trade", "client_order_id": "drill-cid", "amount": 1, "price": 10}]
    apply_fill_pages(repository, [page])
    replay = apply_fill_pages(repository, [page])
    calls = {"count": 0}

    def fetch() -> dict[str, int]:
        calls["count"] += 1
        if calls["count"] == 1:
            raise TimeoutError
        return {"observed_at": now_ms, "cursor_ms": now_ms}

    synced = run_bounded_sync(repository, fetch, now_ms=now_ms)
    orders = repository.get_orders(order_id="drill-1")
    ok = (
        explained["action"] == "blocked"
        and explained["resubmitted"] is False
        and len(orders) == 1
        and replay["fills"] == 1
        and synced["complete"] is False
        and synced["attempts"] == 2
    )
    return _drill("sync", ok, "丢失的查询保持阻断，没有重新下单。重复成交页只记一次。")


def _risk() -> dict[str, Any]:
    from src.ops.phase4_controls import assess_runtime_risk, supervise_observation

    assessment = assess_runtime_risk(
        price_status="stale", account_status="fresh", position_notional=0,
        pending_notional=0, equity=100, peak_equity=100, daily_pnl=0,
        market_type="spot", margin_ratio=None, max_total_position_pct=0.8,
        max_daily_loss_pct=0.1, max_drawdown_pct=0.2,
    )
    watched = supervise_observation(
        {"reason": "stale_price", "blocked": True},
        push=lambda _item: (_ for _ in ()).throw(ConnectionError("ui closed")),
    )
    ok = (
        assessment["blocked"] is True
        and assessment["reason"] == "stale_price"
        and watched["supervision_continued"] is True
        and watched["published"] is False
    )
    return _drill("risk", ok, "过期价格拦住新敞口。界面断开后监督继续。")


def _research(observation: dict[str, Any]) -> dict[str, Any]:
    from src.research.phase5 import prepare_dataset

    closed = [bar for bar in observation["bars"] if bar["closed"]]
    if not closed:
        return _drill("research", False, "没有已收盘的行情，没有下单")
    first = closed[0]
    revised = dict(first)
    revised["close"] = float(first["close"]) + 1
    prepared = prepare_dataset([*closed, revised], timeframe_ms=_TIMEFRAME_MS)
    replay = prepare_dataset([*closed, revised], timeframe_ms=_TIMEFRAME_MS)
    ok = (
        prepared.bars[0]["close"] == first["close"]
        and prepared.data_hash == replay.data_hash
        and any(issue.kind == "duplicate" for issue in prepared.issues)
    )
    return _drill("research", ok, "重复的收盘价保留第一根，回放哈希不变。")


def _restart(repository: Any) -> dict[str, Any]:
    from src.ops.lifecycle import startup_reconcile
    from src.ops.phase3_recovery import record_runtime_intent

    record_runtime_intent(
        repository, name="drill", intended="running", actual="running",
        owner="old-process", updated_at=1,
    )
    result = startup_reconcile(repository, alive=lambda owner: owner != "old-process")
    ok = result["paused"] is True and result["resume"] is False
    return _drill("restart", ok, "已退出的进程被标成暂停，恢复前不能继续开仓。")


def _restore(repository: Any) -> dict[str, Any]:
    from src.data.repository import Repository
    from src.ops.phase6_release import rehearse_rollback

    repository.set_opening_pause(True, "drill", 5)
    database = str(repository.engine.url.database)
    rehearsal = rehearse_rollback(database, f"{database}.backup")
    restored = Repository(db_path=database)
    ok = rehearsal["restored"] is True and restored.get_opening_pause().paused is True
    return _drill("restore", ok, "恢复后探测写入消失，暂停状态还在。")


async def _observe(exchange: Any, now_ms: int) -> dict[str, Any]:
    raw = await exchange.fetch_ohlcv("BTC/USDT", "1m", limit=5)
    await exchange.fetch_balance()
    open_orders = await exchange.fetch_open_orders("BTC/USDT")
    bars = []
    for row in raw:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        timestamp = int(row[0])
        bars.append({
            "timestamp": timestamp,
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
            "closed": is_closed_bar(timestamp, _TIMEFRAME_MS, now_ms),
        })
    return {"bars": bars, "open_order_count": len(open_orders)}


def _reason(
    *, pending: list[str], elapsed_ms: int, min_elapsed_ms: int, private_ok: int,
    minimum_private: int, drills: list[dict[str, Any]],
) -> str:
    if elapsed_ms < min_elapsed_ms or pending:
        return "duration"
    if private_ok < minimum_private:
        return "private_observation_missing"
    if not drills or any(item["passed"] is not True or not item["explanation"] for item in drills):
        return "fault"
    return "ok"


def _report(
    *, accepted: bool, reason: str, elapsed_ms: int, cycles: int,
    private_observations: int, header: str, drills: list[dict[str, Any]],
    residuals: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "accepted": accepted,
        "reason": reason,
        "elapsed_ms": elapsed_ms,
        "cycles": cycles,
        "private_observations": private_observations,
        "orders_submitted": 0,
        "live_open_orders": False,
        "simulated_header": header,
        "drills": drills,
        "residuals": residuals,
    }


def _drill(phase: str, passed: bool, explanation: str) -> dict[str, Any]:
    return {"phase": phase, "passed": passed, "explanation": explanation}


def _header(exchange: Any) -> str:
    headers = getattr(exchange, "headers", None) or {}
    return str(headers.get("x-simulated-trading", ""))
