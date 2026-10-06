"""存储不可用降级场景：异常/超时按策略处理，绝不向调用方抛错。"""
from __future__ import annotations

import asyncio
import logging

from app.config import RateLimitSettings, RouteQuota
from app.types import RejectReason

logger = logging.getLogger("tests.fallback")


class FailingStorage:
    """模拟存储故障：每次调用都抛异常。"""

    async def hit(self, key: str, window_start: float, ttl: float) -> int:
        raise ConnectionError("storage is down")


class SlowStorage:
    """模拟存储缓慢：调用耗时超过超时阈值。"""

    def __init__(self, delay: float) -> None:
        self._delay = delay

    async def hit(self, key: str, window_start: float, ttl: float) -> int:
        await asyncio.sleep(self._delay)
        return 1


async def test_fail_open_allows_when_storage_raises(make_limiter):
    """fail_open=True：存储异常时放行并标记 degraded，不抛错。"""
    limiter, metrics = make_limiter(storage=FailingStorage())
    d = await limiter.check("c1", "/api", 100.0)
    logger.info("fail_open 降级决策: %s", d)
    assert d.allowed and d.degraded
    assert metrics.snapshot()["allowed:degraded:/api"] == 1


async def test_fail_closed_rejects_when_storage_raises(make_limiter):
    """fail_open=False：存储异常时以 storage_fail_closed 拒绝，不抛错。"""
    settings = RateLimitSettings(fail_open=False)
    limiter, metrics = make_limiter(storage=FailingStorage(), settings=settings)
    d = await limiter.check("c1", "/api", 100.0)
    logger.info("fail_closed 降级决策: %s", d)
    assert not d.allowed and d.degraded
    assert d.reason is RejectReason.STORAGE_FAIL_CLOSED
    assert d.retry_after > 0
    assert metrics.snapshot()["rejected:degraded:/api"] == 1


async def test_slow_storage_times_out_and_degrades(make_limiter):
    """慢存储触发超时后按降级策略处理，不阻塞调用方。"""
    limiter, metrics = make_limiter(storage=SlowStorage(delay=5.0),
                                    storage_timeout=0.05)
    d = await asyncio.wait_for(limiter.check("c1", "/api", 100.0), timeout=1.0)
    logger.info("慢存储降级决策: %s", d)
    assert d.allowed and d.degraded, "默认 fail_open，超时后应放行"
    assert metrics.snapshot()["allowed:degraded:/api"] == 1


async def test_storage_recovers_after_failure(make_limiter):
    """存储恢复后回到正常计数路径。"""
    from app.storage import InMemoryStorage

    class FlakyStorage:
        def __init__(self) -> None:
            self.inner = InMemoryStorage()
            self.down = True

        async def hit(self, key: str, window_start: float, ttl: float) -> int:
            if self.down:
                raise ConnectionError("down")
            return await self.inner.hit(key, window_start, ttl)

    flaky = FlakyStorage()
    limiter, _ = make_limiter(storage=flaky)
    assert (await limiter.check("c1", "/api", 100.0)).degraded
    flaky.down = False
    d = await limiter.check("c1", "/api", 100.0)
    assert d.allowed and not d.degraded and d.remaining == 2
