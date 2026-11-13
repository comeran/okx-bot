"""把 demo 和交易所沙盒绑在一起，把 live 和真实账户绑在一起。"""

from __future__ import annotations


def normalized_mode(mode: object) -> str:
    return str(mode or "").strip().lower()


def account_environment(mode: object, exchange_demo: bool) -> str:
    """返回资金草稿所属的账户环境。"""
    normalized = normalized_mode(mode)
    if normalized == "live":
        return "live"
    if normalized == "demo":
        return "sandbox"
    return "sandbox" if exchange_demo else "live"


def adapter_uses_sandbox(mode: object, exchange_demo: bool) -> bool:
    """决定 OKX 适配器是否走沙盒。"""
    normalized = normalized_mode(mode)
    if normalized == "live":
        return False
    if normalized == "demo":
        return True
    return bool(exchange_demo)
