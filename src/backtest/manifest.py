"""回测运行的可复现摘要。"""

from __future__ import annotations

import hashlib
import math

_YEAR_MS = 365 * 24 * 60 * 60 * 1000


def data_digest(bars: list[object]) -> str:
    lines = []
    for bar in bars:
        lines.append(
            ",".join(
                str(getattr(bar, name))
                for name in ("timestamp", "open", "high", "low", "close", "volume")
            )
        )
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def annualized_return(initial: float, final: float, elapsed_ms: int) -> float:
    if initial <= 0 or final <= 0 or elapsed_ms <= 0:
        return 0.0
    years = elapsed_ms / _YEAR_MS
    if years <= 0:
        return 0.0
    try:
        value = (final / initial) ** (1 / years) - 1
    except (OverflowError, ValueError, ZeroDivisionError):
        return 0.0
    if isinstance(value, complex) or not math.isfinite(value):
        return 0.0
    return float(value)


def split_oos[T](items: list[T], ratio: float) -> tuple[list[T], list[T]]:
    if ratio <= 0 or len(items) < 2:
        return list(items), []
    held = max(1, int(len(items) * ratio))
    cut = len(items) - held
    if cut < 1:
        cut = 1
    return list(items[:cut]), list(items[cut:])
