"""窗口边界场景：配额耗尽、窗口切换精确恢复、突发窗口、维度隔离。"""
from __future__ import annotations

import logging

from app.config import RateLimitSettings, RouteQuota
from app.types import RejectReason

logger = logging.getLogger("tests.window")


async def test_quota_exhausted_rejects_with_reason_and_retry_after(make_limiter):
    """窗口内第 limit+1 个请求被拒绝，原因可区分，重试等待指向窗口结束。"""
    limiter, metrics = make_limiter()
    now = 100.0
    for i in range(3):
        d = await limiter.check("c1", "/api", now)
        assert d.allowed and d.remaining == 3 - 1 - i
    d = await limiter.check("c1", "/api", now)
    logger.info("第4个请求决策: %s", d)
    assert not d.allowed
    assert d.reason is RejectReason.QUOTA_EXCEEDED
    assert abs(d.retry_after - 10.0) < 1e-6  # 窗口 [100,110)，剩余 10s
    snap = metrics.snapshot()
    assert snap["allowed:ok:/api"] == 3
    assert snap["rejected:quota_exceeded:/api"] == 1


async def test_window_rollover_recovers_exactly_not_early(make_limiter):
    """窗口结束前不放行，切换瞬间精确恢复。"""
    limiter, _ = make_limiter()
    for i in range(4):
        await limiter.check("c1", "/api", 100.0)
    d = await limiter.check("c1", "/api", 109.999)
    assert not d.allowed, "窗口未结束不得提前放行"
    assert d.retry_after <= 0.01
    d = await limiter.check("c1", "/api", 110.0)
    logger.info("窗口切换后决策: %s", d)
    assert d.allowed, "窗口切换后配额应精确恢复"
    assert d.remaining == 2


async def test_no_double_count_at_boundary(make_limiter):
    """边界处的请求只计入新窗口一次，旧窗口计数不泄漏。"""
    limiter, _ = make_limiter()
    for _ in range(3):
        await limiter.check("c1", "/api", 109.5)  # 旧窗口打满
    d = await limiter.check("c1", "/api", 110.0)  # 边界请求
    assert d.allowed and d.remaining == 2, "边界请求应只在新窗口计 1 次"
    d = await limiter.check("c1", "/api", 110.0)
    assert d.allowed and d.remaining == 1


async def test_burst_window_rejects_fast_clients(make_limiter):
    """突发过快以 burst_too_fast 拒绝，重试等待来自短窗口。"""
    settings = RateLimitSettings(default_quota=RouteQuota(
        limit=1000, window_seconds=60.0, burst_limit=2, burst_window_seconds=1.0))
    limiter, metrics = make_limiter(settings=settings)
    now = 200.3
    assert (await limiter.check("c1", "/api", now)).allowed
    assert (await limiter.check("c1", "/api", now)).allowed
    d = await limiter.check("c1", "/api", now)
    logger.info("突发拒绝决策: %s", d)
    assert not d.allowed and d.reason is RejectReason.BURST_TOO_FAST
    assert abs(d.retry_after - 0.7) < 1e-6  # 短窗口 [200,201)
    assert (await limiter.check("c1", "/api", 201.0)).allowed
    assert metrics.snapshot()["rejected:burst_too_fast:/api"] == 1


async def test_client_and_route_dimensions_isolated(make_limiter):
    """配额按 (客户端, 路由) 隔离，互不影响。"""
    limiter, _ = make_limiter()
    for _ in range(4):
        await limiter.check("c1", "/api", 100.0)
    assert (await limiter.check("c2", "/api", 100.0)).allowed, "其它客户端不受影响"
    assert (await limiter.check("c1", "/other", 100.0)).allowed, "其它路由不受影响"
