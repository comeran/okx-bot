"""把 demo 和交易所沙盒绑在一起，把 live 和真实账户绑在一起。"""

from __future__ import annotations


def normalized_mode(mode: object) -> str:
    return str(mode or "").strip().lower()


def sandbox_for_mode(mode: object) -> bool | None:
    """Exchange execution modes own their environment; offline modes keep legacy settings."""
    return {"demo": True, "live": False}.get(normalized_mode(mode))


def validate_execution_environment(mode: object, exchange_demo: bool) -> None:
    expected = sandbox_for_mode(mode)
    if expected is not None and exchange_demo != expected:
        raise ValueError(
            f"运行模式 {normalized_mode(mode)} 与 exchange.demo={exchange_demo} 冲突；"
            f"{normalized_mode(mode)} 模式要求 exchange.demo={expected}。"
            "请在设置页重新选择运行模式并保存后再启动策略。"
        )


def account_environment(mode: object, exchange_demo: bool) -> str:
    """返回资金草稿所属的账户环境。"""
    validate_execution_environment(mode, exchange_demo)
    return "sandbox" if exchange_demo else "live"


def adapter_uses_sandbox(mode: object, exchange_demo: bool) -> bool:
    """决定 OKX 适配器是否走沙盒。"""
    validate_execution_environment(mode, exchange_demo)
    return bool(exchange_demo)
