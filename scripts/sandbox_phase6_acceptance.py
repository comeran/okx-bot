"""对 OKX 模拟仓做长跑验收。这个脚本不在 pytest 里运行，也不会下单。"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from src.core.runtime_settings import load_runtime_settings
from src.data.repository import Repository
from src.exchange.okx_client import create_okx_client
from src.ops.sandbox_acceptance import run_sandbox_acceptance

_MIN_ELAPSED_MS = 15 * 60 * 1000
_INTERVAL_MS = 30_000
_REPORT = Path("data/sandbox-acceptance/report.json")


def _guard(settings: object) -> str | None:
    mode = str(getattr(settings, "mode", ""))
    exchange = getattr(settings, "exchange", None)
    demo = bool(getattr(exchange, "demo", False))
    market_type = str(getattr(exchange, "market_type", ""))
    if mode != "demo" or not demo:
        return "当前配置不是 OKX 模拟仓，验收停止"
    if market_type != "spot":
        return "当前不是现货，验收停止"
    key = str(getattr(exchange, "api_key", ""))
    secret = str(getattr(exchange, "secret", ""))
    passphrase = str(getattr(exchange, "passphrase", ""))
    if not key.strip() or not secret.strip() or not passphrase.strip():
        return "缺少模拟仓凭证，验收停止"
    return None


async def _main() -> int:
    settings = load_runtime_settings()
    blocked = _guard(settings)
    if blocked is not None:
        print(blocked)
        return 2
    exchange = settings.exchange
    client = create_okx_client(
        api_key=exchange.api_key,
        secret=exchange.secret,
        passphrase=exchange.passphrase,
        default_type="spot",
        demo=True,
    )
    database = _REPORT.parent / "drill.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    repository = Repository(db_path=str(database))

    def progress(cycle: int, phase: str) -> None:
        print(f"cycle={cycle} phase={phase}", flush=True)

    try:
        report = await run_sandbox_acceptance(
            client,
            repository,
            min_elapsed_ms=_MIN_ELAPSED_MS,
            interval_ms=_INTERVAL_MS,
            max_cycles=80,
            minimum_private_observations=10,
            now_ms=lambda: int(time.time() * 1000),
            sleep=lambda millis: asyncio.sleep(millis / 1000),
            progress=progress,
        )
    finally:
        await client.close()
    _REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"accepted={report['accepted']} reason={report['reason']} "
        f"elapsed_ms={report['elapsed_ms']} cycles={report['cycles']} "
        f"private_observations={report['private_observations']} "
        f"orders_submitted={report['orders_submitted']}"
    )
    for item in report["drills"]:
        print(f"{item['phase']} passed={item['passed']} {item['explanation']}")
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
