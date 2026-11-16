"""可复现研究：冻结运行清单、收盘数据政策、版本绑定和全部试验。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import text

_CALLBACKS = ("on_init", "on_bar", "on_order", "on_position")
_QUERIES = ("get_position", "get_positions", "get_balance")


class ReleaseError(ValueError):
    """策略版本和运行时版本没有成对放行。"""


@dataclass(frozen=True)
class ResearchBar:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __getitem__(self, key: str) -> object:
        return getattr(self, key)


@dataclass(frozen=True)
class DatasetIssue:
    kind: str
    timestamp: int


@dataclass(frozen=True)
class PreparedDataset:
    bars: tuple[ResearchBar, ...]
    issues: tuple[DatasetIssue, ...]
    data_hash: str


@dataclass(frozen=True)
class EquityPoint:
    timestamp: int
    equity: float
    drawdown: float


@dataclass(frozen=True)
class RejectedOrder:
    timestamp: int
    reason: str
    symbol: str


@dataclass(frozen=True)
class RunManifest:
    manifest_id: str
    code_version: str
    config_version: str
    cost_model_version: str
    data_hash: str
    bars: tuple[ResearchBar, ...]
    equity: tuple[EquityPoint, ...]
    rejected: tuple[RejectedOrder, ...]


def ensure_research_tables(engine) -> None:
    if getattr(getattr(engine, "dialect", None), "name", None) != "sqlite":
        return
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS research_manifest ("
            "manifest_id VARCHAR PRIMARY KEY, payload_json VARCHAR NOT NULL)"
        ))
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS research_trial ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR NOT NULL, "
            "params_json VARCHAR NOT NULL, accepted BOOLEAN NOT NULL, metric FLOAT NOT NULL)"
        ))


def prepare_dataset(rows: list[dict], *, timeframe_ms: int) -> PreparedDataset:
    """只保留先到的收盘 K 线。缺口、重复、修订和未收盘都记下来，不补造数据。"""
    retained: list[ResearchBar] = []
    issues: list[DatasetIssue] = []
    seen: dict[int, ResearchBar] = {}
    for row in rows:
        timestamp = int(row["timestamp"])
        if not row.get("closed", True):
            issues.append(DatasetIssue("unclosed", timestamp))
            continue
        bar = ResearchBar(
            timestamp, float(row["open"]), float(row["high"]), float(row["low"]),
            float(row["close"]), float(row["volume"]),
        )
        previous = seen.get(timestamp)
        if previous is not None:
            issues.append(DatasetIssue("duplicate", timestamp))
            if _ohlcv(previous) != _ohlcv(bar):
                issues.append(DatasetIssue("revised", timestamp))
            continue
        if retained:
            cursor = retained[-1].timestamp + timeframe_ms
            while cursor < timestamp:
                issues.append(DatasetIssue("missing", cursor))
                cursor += timeframe_ms
        retained.append(bar)
        seen[timestamp] = bar
    return PreparedDataset(tuple(retained), tuple(issues), _bars_hash(tuple(retained)))


def build_manifest(
    *,
    code_version: str,
    config_version: str,
    cost_model_version: str,
    bars: tuple[ResearchBar, ...],
    equity: tuple[tuple[int, float, float], ...],
    rejected: tuple[tuple[int, str, str], ...],
) -> RunManifest:
    points = tuple(EquityPoint(*point) for point in equity)
    orders = tuple(RejectedOrder(*order) for order in rejected)
    payload = _manifest_payload(
        code_version, config_version, cost_model_version, bars, points, orders,
    )
    return RunManifest(
        manifest_id=_digest(payload),
        code_version=code_version,
        config_version=config_version,
        cost_model_version=cost_model_version,
        data_hash=_bars_hash(bars),
        bars=bars,
        equity=points,
        rejected=orders,
    )


def store_manifest(repository, manifest: RunManifest) -> None:
    _require_sqlite(repository)
    payload = _manifest_payload(
        manifest.code_version,
        manifest.config_version,
        manifest.cost_model_version,
        manifest.bars,
        manifest.equity,
        manifest.rejected,
    )
    with repository.engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO research_manifest(manifest_id, payload_json) "
            "VALUES (:manifest_id, :payload) "
            "ON CONFLICT(manifest_id) DO NOTHING"
        ), {"manifest_id": manifest.manifest_id, "payload": _canonical(payload)})


def load_manifest(repository, manifest_id: str) -> RunManifest:
    _require_sqlite(repository)
    with repository.engine.connect() as connection:
        row = connection.execute(text(
            "SELECT payload_json FROM research_manifest WHERE manifest_id = :manifest_id"
        ), {"manifest_id": manifest_id}).first()
    if row is None:
        raise KeyError(manifest_id)
    payload = json.loads(row[0])
    bars = tuple(ResearchBar(**bar) for bar in payload["bars"])
    equity = tuple(EquityPoint(**point) for point in payload["equity"])
    rejected = tuple(RejectedOrder(**order) for order in payload["rejected"])
    manifest = RunManifest(
        manifest_id=_digest(payload),
        code_version=payload["code_version"],
        config_version=payload["config_version"],
        cost_model_version=payload["cost_model_version"],
        data_hash=payload["data_hash"],
        bars=bars,
        equity=equity,
        rejected=rejected,
    )
    if manifest.manifest_id != manifest_id:
        raise ValueError("研究清单已被改动")
    return manifest


def callback_parity(research: object, runtime: object) -> dict[str, list[str]]:
    return {"research": _missing(research), "runtime": _missing(runtime)}


def bind_release(
    strategy_version: str, runtime_version: str, *, allowed: set[tuple[str, str]],
) -> dict[str, object]:
    if (strategy_version, runtime_version) not in allowed:
        raise ReleaseError("策略版本没有绑定到这个运行时")
    return {
        "strategy_version": strategy_version,
        "runtime_version": runtime_version,
        "bound": True,
    }


def attribute_difference(research: dict, online: dict) -> list[dict[str, object]]:
    differences = []
    for field in ("price", "fee", "quantity"):
        if research.get(field) != online.get(field):
            differences.append({
                "field": field,
                "research": research.get(field),
                "online": online.get(field),
            })
    return differences


def separate_samples(
    items: list, *, train_ratio: float, validation_ratio: float,
) -> dict[str, list]:
    train_count = int(len(items) * train_ratio)
    validation_count = int(len(items) * validation_ratio)
    train_end = train_count
    validation_end = train_end + validation_count
    return {
        "train": list(items[:train_end]),
        "validation": list(items[train_end:validation_end]),
        "test": list(items[validation_end:]),
    }


def walk_forward(items: list, *, train_size: int, test_size: int) -> list[dict[str, list]]:
    windows = []
    start = 0
    while start + train_size + test_size <= len(items):
        train_end = start + train_size
        test_end = train_end + test_size
        windows.append({
            "train": list(items[start:train_end]),
            "test": list(items[train_end:test_end]),
        })
        start += test_size
    return windows


def parameter_stability(values: list[float], *, max_range: float) -> dict[str, object]:
    low = min(values)
    high = max(values)
    return {"stable": high - low <= max_range, "low": low, "high": high}


def strategy_benchmark(strategy_return: float, benchmark_return: float) -> dict[str, object]:
    excess = round(strategy_return - benchmark_return, 10)
    return {
        "strategy": strategy_return,
        "benchmark": benchmark_return,
        "excess": excess,
        "beat": excess > 0,
    }


def record_trial(
    repository, *, name: str, params: dict, accepted: bool, metric: float,
) -> None:
    _require_sqlite(repository)
    with repository.engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO research_trial(name, params_json, accepted, metric) "
            "VALUES (:name, :params, :accepted, :metric)"
        ), {
            "name": name,
            "params": json.dumps(params, sort_keys=True),
            "accepted": 1 if accepted else 0,
            "metric": float(metric),
        })


def _missing(strategy: object) -> list[str]:
    return [
        name for name in (*_CALLBACKS, *_QUERIES)
        if not callable(getattr(strategy, name, None))
    ]


def _ohlcv(bar: ResearchBar) -> tuple[float, float, float, float, float]:
    return (bar.open, bar.high, bar.low, bar.close, bar.volume)


def _bars_hash(bars: tuple[ResearchBar, ...]) -> str:
    payload = [
        [bar.timestamp, bar.open, bar.high, bar.low, bar.close, bar.volume] for bar in bars
    ]
    return _digest(payload)


def _manifest_payload(code, config, cost, bars, equity, rejected) -> dict:
    return {
        "code_version": code,
        "config_version": config,
        "cost_model_version": cost,
        "data_hash": _bars_hash(bars),
        "bars": [
            {
                "timestamp": bar.timestamp, "open": bar.open, "high": bar.high,
                "low": bar.low, "close": bar.close, "volume": bar.volume,
            }
            for bar in bars
        ],
        "equity": [
            {"timestamp": point.timestamp, "equity": point.equity, "drawdown": point.drawdown}
            for point in equity
        ],
        "rejected": [
            {"timestamp": order.timestamp, "reason": order.reason, "symbol": order.symbol}
            for order in rejected
        ],
    }


def _digest(payload: object) -> str:
    encoded = _canonical(payload).encode()
    return hashlib.sha256(encoded).hexdigest()


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _sqlite(repository) -> bool:
    engine = getattr(repository, "engine", None)
    if getattr(getattr(engine, "dialect", None), "name", None) != "sqlite":
        return False
    ensure_research_tables(engine)
    return True


def _require_sqlite(repository) -> None:
    if not _sqlite(repository):
        raise RuntimeError("研究清单需要 sqlite")
