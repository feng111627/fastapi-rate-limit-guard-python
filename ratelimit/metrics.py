"""限流决策的可观测计数器。

所有计数器按 (决策结果, 拒绝原因, 限流维度 key) 聚合，
便于排查“什么时候、按什么原因、把谁限制了”。
计数器使用锁保护，保证高并发下计数准确。
"""

from __future__ import annotations

import threading
from collections import Counter

from ratelimit.models import RejectReason


class RateLimitMetrics:
    """线程安全的限流决策计数器。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._allowed: Counter[str] = Counter()
        self._rejected: Counter[tuple[str, str]] = Counter()
        self._storage_errors = 0
        self._degraded_allowed = 0
        self._degraded_rejected = 0

    def record_allowed(self, key: str) -> None:
        with self._lock:
            self._allowed[key] += 1

    def record_rejected(self, key: str, reason: RejectReason) -> None:
        with self._lock:
            self._rejected[(key, str(reason))] += 1

    def record_storage_error(self) -> None:
        with self._lock:
            self._storage_errors += 1

    def record_degraded(self, allowed: bool) -> None:
        with self._lock:
            if allowed:
                self._degraded_allowed += 1
            else:
                self._degraded_rejected += 1

    def snapshot(self) -> dict[str, object]:
        """导出当前计数快照（用于 /metrics 端点与测试断言）。"""
        with self._lock:
            return {
                "allowed_total": sum(self._allowed.values()),
                "rejected_total": sum(self._rejected.values()),
                "rejected_by_reason": {
                    reason: count
                    for (_, reason), count in self._rejected.items()
                },
                "rejected_by_key": {
                    key: count for (key, _), count in self._rejected.items()
                },
                "storage_errors_total": self._storage_errors,
                "degraded_allowed_total": self._degraded_allowed,
                "degraded_rejected_total": self._degraded_rejected,
            }
