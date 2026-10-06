"""窗口边界场景：切换处不重复计数、不提前放行、配额随窗口滚动恢复。"""

from __future__ import annotations

from ratelimit.limiter import RateLimiter
from ratelimit.models import RateLimitRule, RejectReason
from ratelimit.storage import InMemoryStorage

RULE = RateLimitRule(
    max_requests=4, window_seconds=10.0, burst_limit=100, burst_window_seconds=1.0
)


def make_limiter() -> RateLimiter:
    return RateLimiter(InMemoryStorage())


async def test_boundary_no_early_pass():
    """窗口刚切换时上一窗口计数仍按权重生效，不会提前放行。"""
    lim = make_limiter()
    for i in range(4):
        d = await lim.check("c:/r", RULE, now=99.0 + i * 0.1)
        assert d.allowed
    # 进入下一窗口 0.5s：上一窗口权重 0.95，有效计数 ≈ 1 + 4*0.95 > 4
    d = await lim.check("c:/r", RULE, now=100.5)
    assert not d.allowed
    assert d.reason == RejectReason.QUOTA_EXCEEDED


async def test_boundary_no_double_counting():
    """同一请求只落入一个窗口桶，窗口切换处不会重复计数。"""
    lim = make_limiter()
    # 上一窗口末尾 2 个请求
    assert (await lim.check("c:/r", RULE, now=99.8)).allowed
    assert (await lim.check("c:/r", RULE, now=99.9)).allowed
    # 新窗口开始：有效计数 = 1 + 2*1.0 = 3，仍应放行第 2 个（=4 未超限）
    assert (await lim.check("c:/r", RULE, now=100.0)).allowed
    d = await lim.check("c:/r", RULE, now=100.0)
    assert d.allowed  # 有效计数恰好到 4，未超额
    # 再来一个才超限：说明边界处没有重复计数（否则更早被拒）
    d = await lim.check("c:/r", RULE, now=100.1)
    assert not d.allowed


async def test_quota_recovers_as_window_rolls():
    """配额随窗口滚动逐步恢复，上一窗口完全滚出后全额恢复。"""
    lim = make_limiter()
    for i in range(4):
        await lim.check("c:/r", RULE, now=100.0 + i * 0.1)
    assert not (await lim.check("c:/r", RULE, now=105.0)).allowed
    # 窗口 [100,110) 在 111s 时仍有 0.9 权重，不会提前放行
    assert not (await lim.check("c:/r", RULE, now=111.0)).allowed
    # 窗口 [100,110) 完全滚出（>= 120s）后放行
    assert (await lim.check("c:/r", RULE, now=121.0)).allowed
    # 所有历史窗口都滚出（>= 140s，w11/w12 均已失效）后全额恢复
    d = await lim.check("c:/r", RULE, now=141.0)
    assert d.allowed
    assert d.remaining == RULE.max_requests - 1


async def test_retry_after_points_to_window_reset():
    """被拒绝时给出的重试等待时间指向当前窗口结束时刻。"""
    lim = make_limiter()
    for i in range(4):
        await lim.check("c:/r", RULE, now=102.0 + i * 0.1)
    d = await lim.check("c:/r", RULE, now=103.0)
    assert not d.allowed
    # 当前窗口为 [100, 110)，retry_after 应等于 110 - 103
    assert abs(d.retry_after_seconds - 7.0) < 1e-6


async def test_burst_reason_distinguished_from_quota():
    """突发过快与超出配额是两种不同的拒绝原因。"""
    rule = RateLimitRule(
        max_requests=100, window_seconds=60.0, burst_limit=2, burst_window_seconds=1.0
    )
    lim = make_limiter()
    assert (await lim.check("c:/r", rule, now=0.0)).allowed
    assert (await lim.check("c:/r", rule, now=0.1)).allowed
    d = await lim.check("c:/r", rule, now=0.2)
    assert not d.allowed and d.reason == RejectReason.BURST_TOO_FAST
    assert 0 < d.retry_after_seconds <= rule.burst_window_seconds
