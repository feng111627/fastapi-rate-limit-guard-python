"""并发竞争场景：多请求并发命中同一配额，计数准确、不超发、不漏记。"""
from __future__ import annotations

import asyncio
import logging

from app.config import RateLimitSettings, RouteQuota

logger = logging.getLogger("tests.concurrency")


async def test_concurrent_hits_never_over_admit(make_limiter):
    """100 个并发请求抢 10 个配额：恰好放行 10 个，拒绝 90 个。"""
    settings = RateLimitSettings(default_quota=RouteQuota(
        limit=10, window_seconds=60.0, burst_limit=10_000, burst_window_seconds=1.0))
    limiter, metrics = make_limiter(settings=settings)
    decisions = await asyncio.gather(*(
        limiter.check("c1", "/api", 100.0) for _ in range(100)))
    allowed = sum(1 for d in decisions if d.allowed)
    rejected = sum(1 for d in decisions if not d.allowed)
    logger.info("并发结果: allowed=%d rejected=%d", allowed, rejected)
    assert allowed == 10, "并发下不得超额放行"
    assert rejected == 90
    snap = metrics.snapshot()
    assert snap["allowed:ok:/api"] == 10
    assert snap["rejected:quota_exceeded:/api"] == 90


async def test_concurrent_count_is_exact(make_limiter):
    """并发计数无丢失：放行数 + 拒绝数 == 总请求数，且最后一次计数准确。"""
    settings = RateLimitSettings(default_quota=RouteQuota(
        limit=50, window_seconds=60.0, burst_limit=10_000, burst_window_seconds=1.0))
    limiter, _ = make_limiter(settings=settings)
    total = 200
    decisions = await asyncio.gather(*(
        limiter.check("c1", "/api", 100.0) for _ in range(total)))
    assert len(decisions) == total
    assert sum(1 for d in decisions if d.allowed) == 50
    # 每个被放行的请求拿到的 remaining 应互不相同且覆盖 0..49，
    # 说明每次计数都唯一、无重复无遗漏
    remainings = sorted(d.remaining for d in decisions if d.allowed)
    assert remainings == list(range(50))


async def test_concurrent_clients_do_not_block_each_other(make_limiter):
    """不同客户端的并发请求互不阻塞，各自独立计数。"""
    settings = RateLimitSettings(default_quota=RouteQuota(
        limit=5, window_seconds=60.0, burst_limit=10_000, burst_window_seconds=1.0))
    limiter, _ = make_limiter(settings=settings)
    clients = [f"c{i}" for i in range(20)]
    decisions = await asyncio.gather(*(
        limiter.check(c, "/api", 100.0) for c in clients for _ in range(5)))
    assert all(d.allowed for d in decisions), "各客户端前 5 个请求都应放行"
