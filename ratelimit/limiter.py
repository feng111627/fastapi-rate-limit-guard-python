"""限流判定核心：滑动窗口配额 + 突发控制 + 降级策略。"""

from __future__ import annotations

from ratelimit.metrics import RateLimitMetrics
from ratelimit.models import Decision, RateLimitRule
from ratelimit.storage import RateLimitStorage


class RateLimiter:
    """基于存储的限流判定器（占位骨架）。"""

    def __init__(
        self,
        storage: RateLimitStorage,
        metrics: RateLimitMetrics | None = None,
        fail_open: bool = True,
    ) -> None:
        self._storage = storage
        self._metrics = metrics
        self._fail_open = fail_open

    async def check(self, key: str, rule: RateLimitRule, now: float) -> Decision:
        raise NotImplementedError
