"""限流规则与判定结果的数据模型。"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class RejectReason(enum.StrEnum):
    """请求被拒绝的原因。"""

    QUOTA_EXCEEDED = "quota_exceeded"
    BURST_TOO_FAST = "burst_too_fast"
    STORAGE_UNAVAILABLE = "storage_unavailable"


@dataclass(frozen=True)
class RateLimitRule:
    """单个路由（或默认）的限流规则。"""

    max_requests: int = 60
    window_seconds: float = 60.0
    burst_limit: int = 10
    burst_window_seconds: float = 1.0


@dataclass(frozen=True)
class Decision:
    """一次限流判定的结果。"""

    allowed: bool
    reason: RejectReason | None = None
    retry_after_seconds: float = 0.0
    remaining: int = 0
    limit: int = 0
    degraded: bool = False
