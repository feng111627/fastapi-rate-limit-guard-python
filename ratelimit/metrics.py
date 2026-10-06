"""限流决策的可观测计数器。"""

from __future__ import annotations


class RateLimitMetrics:
    """按原因聚合的限流计数器（占位骨架）。"""

    def snapshot(self) -> dict[str, int]:
        raise NotImplementedError
