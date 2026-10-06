"""限流决策相关的类型定义。"""
from __future__ import annotations

import enum
from dataclasses import dataclass


class RejectReason(enum.Enum):
    """请求被拒绝的原因。"""

    QUOTA_EXCEEDED = "quota_exceeded"      # 窗口配额耗尽
    BURST_TOO_FAST = "burst_too_fast"      # 突发过快（短窗口超限）
    STORAGE_FAIL_CLOSED = "storage_fail_closed"  # 存储不可用且策略为拒绝


@dataclass(frozen=True)
class Decision:
    """一次限流判定的结果。"""

    allowed: bool
    reason: RejectReason | None = None
    retry_after: float = 0.0       # 建议等待秒数
    remaining: int = 0             # 当前窗口剩余配额
    limit: int = 0                 # 当前窗口配额上限
    degraded: bool = False         # 是否走了降级路径
