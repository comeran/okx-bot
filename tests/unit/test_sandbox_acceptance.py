"""沙盒长跑验收的判定。测试不访问网络。"""

import asyncio
from pathlib import Path

from src.data.repository import Repository
from src.ops.sandbox_acceptance import run_sandbox_acceptance


class _Clock:
    def __init__(self) -> None:
        self.value = 1_000_000_000

    def now(self) -> int:
        return self.value

    async def sleep(self, millis: int) -> None:
        self.value += millis


class _Exchange:
    def __init__(self, header: str = "1") -> None:
        self.headers = {"x-simulated-trading": header} if header else {}
        self.calls: list[str] = []

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 5) -> list[list[float]]:
        self.calls.append("ohlcv")
        return [
            [0, 10, 11, 9, 10, 1],
            [60_000, 10, 12, 9, 11, 2],
        ]

    async def fetch_balance(self) -> dict:
        self.calls.append("balance")
        return {"USDT": {"free": "150"}, "BTC": {"total": "0.01"}}

    async def fetch_open_orders(self, symbol: str) -> list:
        self.calls.append("orders")
        return []


def _run(tmp_path: Path, exchange: _Exchange, **kwargs: object) -> dict:
    clock = _Clock()
    repository = Repository(db_path=str(tmp_path / "drill.db"))
    return asyncio.run(run_sandbox_acceptance(
        exchange,
        repository,
        min_elapsed_ms=int(kwargs.get("min_elapsed_ms", 200)),
        interval_ms=int(kwargs.get("interval_ms", 50)),
        max_cycles=int(kwargs.get("max_cycles", 20)),
        minimum_private_observations=int(kwargs.get("minimum_private_observations", 1)),
        now_ms=clock.now,
        sleep=clock.sleep,
    ))


def test_missing_simulated_header_makes_no_exchange_call(tmp_path: Path) -> None:
    exchange = _Exchange(header="")
    report = _run(tmp_path, exchange, max_cycles=3)
    assert report["accepted"] is False
    assert report["reason"] == "simulated_header_missing"
    assert report["orders_submitted"] == 0
    assert report["live_open_orders"] is False
    assert exchange.calls == []


def test_short_session_is_not_long_running_acceptance(tmp_path: Path) -> None:
    exchange = _Exchange()
    report = _run(
        tmp_path, exchange, min_elapsed_ms=10_000, interval_ms=10, max_cycles=2,
    )
    assert report["accepted"] is False
    assert report["reason"] == "duration"
    assert report["orders_submitted"] == 0
    assert report["elapsed_ms"] < 10_000


def test_long_session_explains_phase_faults_without_orders(tmp_path: Path) -> None:
    exchange = _Exchange()
    report = _run(tmp_path, exchange)
    assert report["accepted"] is True
    assert report["reason"] == "ok"
    assert report["orders_submitted"] == 0
    assert report["live_open_orders"] is False
    assert report["simulated_header"] == "1"
    assert [item["phase"] for item in report["drills"]] == [
        "funding", "sync", "risk", "research", "disconnect", "restart", "restore",
    ]
    assert all(item["passed"] is True and item["explanation"] for item in report["drills"])
    assert "USDT" not in str(report)
    assert "api_key" not in str(report)
