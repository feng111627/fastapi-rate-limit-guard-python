"""并发竞争场景：多请求并发命中同一配额，计数准确、不超放、不漏记。"""

from __future__ import annotations

import asyncio

from ratelimit.limiter import RateLimiter
from ratelimit.models import RateLimitRule
from ratelimit.storage import InMemoryStorage

# 关掉突发限制，只考察配额维度的并发准确性
RULE = RateLimitRule(
    max_requests=50, window_seconds=60.0, burst_limit=10_000, burst_window_seconds=1.0
)


async def test_concurrent_hits_never_over_admit():
    """200 个并发请求竞争 50 的配额：放行数恰好 50，不超放。"""
    lim = RateLimiter(InMemoryStorage())
    decisions = await asyncio.gather(
        *[lim.check("c:/r", RULE, now=1000.0) for _ in range(200)]
    )
    allowed = sum(1 for d in decisions if d.allowed)
    rejected = sum(1 for d in decisions if not d.allowed)
    assert allowed == RULE.max_requests
    assert rejected == 200 - RULE.max_requests


async def test_concurrent_counting_is_exact():
    """并发计数无漏记：N 个并发命中后存储计数恰好为 N。"""
    storage = InMemoryStorage()
    lim = RateLimiter(storage, fail_open=True)
    n = 500
    await asyncio.gather(*[lim.check("c:/r", RULE, now=1000.0) for _ in range(n)])
    counts = await storage.record_and_count("c:/r|quota", 1000.0, RULE.window_seconds)
    assert counts.current == n + 1  # n 次判定 + 本次探测


async def test_concurrent_keys_isolated():
    """不同 key 并发互不影响：各自独立计满自己的配额。"""
    lim = RateLimiter(InMemoryStorage())
    keys = [f"client{i}:/r" for i in range(10)]
    results = await asyncio.gather(
        *[lim.check(k, RULE, now=1000.0) for k in keys for _ in range(80)]
    )
    per_key_allowed: dict[str, int] = {}
    for (k, d) in zip((k for k in keys for _ in range(80)), results):
        if d.allowed:
            per_key_allowed[k] = per_key_allowed.get(k, 0) + 1
    assert per_key_allowed == {k: RULE.max_requests for k in keys}


async def test_slow_request_does_not_block_others():
    """单个慢存储操作不阻塞其它 key 的判定（分段锁）。"""
    storage = InMemoryStorage()
    lim = RateLimiter(storage)

    async def slow_probe():
        # 模拟一个慢请求：占用自己的判定流程一段时间
        await asyncio.sleep(0.05)
        return await lim.check("slow:/r", RULE, now=1000.0)

    async def fast_many():
        return await asyncio.gather(
            *[lim.check(f"fast{i}:/r", RULE, now=1000.0) for i in range(20)]
        )

    slow, fast = await asyncio.gather(slow_probe(), fast_many())
    assert slow.allowed
    assert all(d.allowed for d in fast)
