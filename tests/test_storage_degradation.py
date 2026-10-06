"""存储不可用降级场景：异常不抛给调用方，按 fail-open/fail-closed 策略处理。"""

from __future__ import annotations

import pytest

from ratelimit.limiter import RateLimiter
from ratelimit.metrics import RateLimitMetrics
from ratelimit.models import RateLimitRule, RejectReason
from ratelimit.storage import InMemoryStorage, StorageError

RULE = RateLimitRule(
    max_requests=5, window_seconds=10.0, burst_limit=2, burst_window_seconds=1.0
)


class BrokenStorage:
    """永远抛 StorageError 的存储（模拟 Redis 宕机）。"""

    async def record_and_count(self, key, now, window_seconds):
        raise StorageError("connection refused")


class FlakyStorage:
    """前 fail_times 次调用失败，之后恢复正常的存储。"""

    def __init__(self, inner: InMemoryStorage, fail_times: int) -> None:
        self._inner = inner
        self._remaining = fail_times

    async def record_and_count(self, key, now, window_seconds):
        if self._remaining > 0:
            self._remaining -= 1
            raise StorageError("temporary failure")
        return await self._inner.record_and_count(key, now, window_seconds)


async def test_fail_open_allows_and_marks_degraded():
    """fail-open：存储不可用时放行，结果标记 degraded，不抛异常。"""
    metrics = RateLimitMetrics()
    lim = RateLimiter(BrokenStorage(), metrics=metrics, fail_open=True)
    d = await lim.check("c:/r", RULE, now=0.0)  # 不应抛出
    assert d.allowed and d.degraded
    snap = metrics.snapshot()
    assert snap["storage_errors_total"] == 1
    assert snap["degraded_allowed_total"] == 1


async def test_fail_closed_rejects_with_reason_and_retry():
    """fail-closed：存储不可用时拒绝，原因可区分且给出重试等待时间。"""
    metrics = RateLimitMetrics()
    lim = RateLimiter(
        BrokenStorage(), metrics=metrics, fail_open=False, degraded_retry_after=2.5
    )
    d = await lim.check("c:/r", RULE, now=0.0)
    assert not d.allowed
    assert d.reason == RejectReason.STORAGE_UNAVAILABLE
    assert d.degraded and d.retry_after_seconds == 2.5
    snap = metrics.snapshot()
    assert snap["degraded_rejected_total"] == 1
    assert snap["rejected_by_reason"]["storage_unavailable"] == 1


async def test_storage_recovery_resumes_normal_decisions():
    """存储从故障中恢复后，限流判定自动回到正常路径。"""
    inner = InMemoryStorage()
    lim = RateLimiter(FlakyStorage(inner, fail_times=2), fail_open=True)
    d1 = await lim.check("c:/r", RULE, now=0.0)   # 故障期：降级放行
    assert d1.allowed and d1.degraded
    d2 = await lim.check("c:/r", RULE, now=0.0)   # 仍故障（burst 调用失败）
    assert d2.degraded
    d3 = await lim.check("c:/r", RULE, now=0.0)   # 恢复：正常判定
    assert d3.allowed and not d3.degraded


async def test_unexpected_exception_also_degrades():
    """存储抛出非 StorageError 的意外异常时同样按降级策略处理。"""
    class BuggyStorage:
        async def record_and_count(self, key, now, window_seconds):
            raise RuntimeError("bug")

    lim = RateLimiter(BuggyStorage(), fail_open=True)
    d = await lim.check("c:/r", RULE, now=0.0)
    assert d.allowed and d.degraded
