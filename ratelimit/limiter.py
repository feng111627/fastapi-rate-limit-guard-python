"""限流判定核心。

判定模型（滑动窗口计数器）：
- 每次尝试都在“突发窗口”和“配额窗口”各记一次数（拒绝的尝试也计数，
  防止恶意客户端用被拒绝的流量绕过配额）；
- 有效计数 = 当前窗口计数 + 上一窗口计数 × 上一窗口剩余权重，
  窗口切换处权重线性衰减：既不重复计数，也不会提前放行；
- 先判突发（burst），再判配额（quota），拒绝原因可区分；
- 存储不可用时按 fail_open / fail_closed 策略降级，绝不把异常抛给调用方。
"""

from __future__ import annotations

import logging
import math

from ratelimit.metrics import RateLimitMetrics
from ratelimit.models import Decision, RateLimitRule, RejectReason
from ratelimit.storage import RateLimitStorage, WindowCounts

logger = logging.getLogger("ratelimit.limiter")


class RateLimiter:
    """基于可替换存储的限流判定器。"""

    def __init__(
        self,
        storage: RateLimitStorage,
        metrics: RateLimitMetrics | None = None,
        fail_open: bool = True,
        degraded_retry_after: float = 1.0,
    ) -> None:
        self._storage = storage
        self._metrics = metrics or RateLimitMetrics()
        self._fail_open = fail_open
        self._degraded_retry_after = degraded_retry_after

    @property
    def metrics(self) -> RateLimitMetrics:
        return self._metrics

    async def check(self, key: str, rule: RateLimitRule, now: float) -> Decision:
        """对 (客户端, 路由) 维度 key 做一次限流判定。永不抛存储异常。"""
        try:
            burst = await self._storage.record_and_count(
                f"{key}|burst", now, rule.burst_window_seconds
            )
            quota = await self._storage.record_and_count(
                f"{key}|quota", now, rule.window_seconds
            )
        except Exception:
            logger.exception("限流存储不可用，按降级策略处理 key=%s", key)
            return self._degraded_decision(rule)

        burst_used = self._effective_count(burst, now)
        if burst_used > rule.burst_limit:
            return self._reject(
                key,
                RejectReason.BURST_TOO_FAST,
                retry_after=self._window_reset_in(burst, now),
                limit=rule.burst_limit,
            )

        quota_used = self._effective_count(quota, now)
        if quota_used > rule.max_requests:
            return self._reject(
                key,
                RejectReason.QUOTA_EXCEEDED,
                retry_after=self._window_reset_in(quota, now),
                limit=rule.max_requests,
            )

        remaining = max(0, rule.max_requests - math.ceil(quota_used))
        self._metrics.record_allowed(key)
        return Decision(
            allowed=True, remaining=remaining, limit=rule.max_requests
        )

    @staticmethod
    def _effective_count(counts: WindowCounts, now: float) -> float:
        """滑动窗口有效计数：上一窗口按剩余时间占比线性衰减。"""
        elapsed = min(max(now - counts.window_start, 0.0), counts.window_seconds)
        previous_weight = (counts.window_seconds - elapsed) / counts.window_seconds
        return counts.current + counts.previous * previous_weight

    @staticmethod
    def _window_reset_in(counts: WindowCounts, now: float) -> float:
        """距当前窗口结束的秒数（作为 Retry-After，保守且可解释）。"""
        return max(0.0, counts.window_start + counts.window_seconds - now)

    def _reject(
        self,
        key: str,
        reason: RejectReason,
        retry_after: float,
        limit: int,
    ) -> Decision:
        self._metrics.record_rejected(key, reason)
        logger.info(
            "限流拒绝 key=%s reason=%s retry_after=%.3fs", key, reason, retry_after
        )
        return Decision(
            allowed=False,
            reason=reason,
            retry_after_seconds=retry_after,
            remaining=0,
            limit=limit,
        )

    def _degraded_decision(self, rule: RateLimitRule) -> Decision:
        self._metrics.record_storage_error()
        self._metrics.record_degraded(allowed=self._fail_open)
        if self._fail_open:
            logger.warning("存储不可用，fail-open 放行本次请求")
            return Decision(
                allowed=True,
                remaining=0,
                limit=rule.max_requests,
                degraded=True,
            )
        logger.warning("存储不可用，fail-closed 拒绝本次请求")
        return Decision(
            allowed=False,
            reason=RejectReason.STORAGE_UNAVAILABLE,
            retry_after_seconds=self._degraded_retry_after,
            remaining=0,
            limit=rule.max_requests,
            degraded=True,
        )
