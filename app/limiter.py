"""限流判定核心：滚动窗口配额 + 突发保护 + 存储降级。"""
from __future__ import annotations

import asyncio
import logging

from .config import RateLimitSettings
from .metrics import MetricsRegistry
from .storage import RateLimitStorage
from .types import Decision, RejectReason

logger = logging.getLogger("rate_limit")


class RateLimiter:
    """按 (客户端, 路由) 维度做配额判定。

    判定模型：两个对齐的固定窗口——
    - 长窗口：配额随窗口滚动整体恢复，窗口内计数达到 limit 即拒绝；
    - 短窗口：突发保护，窗口内计数达到 burst_limit 即拒绝。
    桶以窗口起点为索引，窗口切换时旧桶自然过期，
    不会重复计数，也不会在窗口结束前提前放行。
    """

    def __init__(
        self,
        storage: RateLimitStorage,
        settings: RateLimitSettings,
        metrics: MetricsRegistry,
        storage_timeout: float = 0.2,
    ) -> None:
        self._storage = storage
        self._settings = settings
        self._metrics = metrics
        self._storage_timeout = storage_timeout

    async def check(self, client: str, route: str, now: float) -> Decision:
        """判定一次请求是否放行，并记录指标。存储异常时按降级策略处理。"""
        quota = self._settings.quota_for(route)
        key = f"{client}|{route}"
        try:
            burst_count, quota_count = await asyncio.wait_for(
                self._count(key, now, quota.window_seconds, quota.burst_window_seconds),
                timeout=self._storage_timeout,
            )
        except Exception:
            # 存储不可用（异常或超时）：按配置的降级策略处理，不向调用方抛错
            logger.warning("rate-limit storage unavailable, degrade fail_open=%s",
                           self._settings.fail_open)
            return self._degraded(route, quota.limit)

        burst_reset = self._window_end(now, quota.burst_window_seconds) - now
        if burst_count > quota.burst_limit:
            return self._reject(route, RejectReason.BURST_TOO_FAST,
                                retry_after=burst_reset,
                                remaining=0, limit=quota.burst_limit)
        quota_reset = self._window_end(now, quota.window_seconds) - now
        if quota_count > quota.limit:
            return self._reject(route, RejectReason.QUOTA_EXCEEDED,
                                retry_after=quota_reset,
                                remaining=0, limit=quota.limit)

        decision = Decision(allowed=True,
                            remaining=max(0, quota.limit - quota_count),
                            limit=quota.limit)
        self._metrics.record(allowed=True, reason="ok", route=route)
        return decision

    async def _count(self, key: str, now: float, window: float,
                     burst_window: float) -> tuple[int, int]:
        """同一把键锁内完成两个窗口的计数，保证并发下计数准确。"""
        burst_start = self._window_start(now, burst_window)
        quota_start = self._window_start(now, window)
        burst_count = await self._storage.hit(
            f"burst|{key}", burst_start, ttl=burst_window * 2)
        quota_count = await self._storage.hit(
            f"quota|{key}", quota_start, ttl=window * 2)
        return burst_count, quota_count

    @staticmethod
    def _window_start(now: float, window: float) -> float:
        return now - (now % window)

    @staticmethod
    def _window_end(now: float, window: float) -> float:
        return RateLimiter._window_start(now, window) + window

    def _reject(self, route: str, reason: RejectReason, *, retry_after: float,
                remaining: int, limit: int) -> Decision:
        self._metrics.record(allowed=False, reason=reason.value, route=route)
        return Decision(allowed=False, reason=reason,
                        retry_after=max(retry_after, 0.001),
                        remaining=remaining, limit=limit)

    def _degraded(self, route: str, limit: int) -> Decision:
        self._metrics.record(allowed=self._settings.fail_open,
                             reason="degraded", route=route)
        if self._settings.fail_open:
            return Decision(allowed=True, remaining=0, limit=limit, degraded=True)
        return Decision(allowed=False, reason=RejectReason.STORAGE_FAIL_CLOSED,
                        retry_after=1.0, remaining=0, limit=limit, degraded=True)
