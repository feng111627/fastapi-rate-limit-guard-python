"""限流决策的可观测计数。"""
from __future__ import annotations

from collections import Counter


class MetricsRegistry:
    """按 (结果, 原因, 路由) 维度记录决策计数。"""

    def __init__(self) -> None:
        self._counters: Counter[tuple[str, str, str]] = Counter()

    def record(self, *, allowed: bool, reason: str, route: str) -> None:
        outcome = "allowed" if allowed else "rejected"
        self._counters[(outcome, reason, route)] += 1

    def snapshot(self) -> dict[str, int]:
        """返回扁平化的计数快照，键形如 'rejected:quota_exceeded:/api'。"""
        return {f"{o}:{r}:{rt}": n for (o, r, rt), n in sorted(self._counters.items())}
