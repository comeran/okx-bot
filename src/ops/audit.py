"""记录操作员在本机做出的安全动作。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def record_operator_action(repository, action: str, result: dict, timestamp: int) -> None:
    writer = getattr(repository, "record_operator_action", None)
    if not callable(writer):
        return
    try:
        writer(action=action, result=result, timestamp=timestamp)
    except Exception:
        logger.exception("写入操作记录失败")
