"""配额状态存储：协议定义 + 本地内存实现。"""
from __future__ import annotations

import asyncio
import time
from typing import Protocol


class RateLimitStorage(Protocol):
    """配额计数存储接口，可替换为 Redis 等实现。"""

    async def hit(self, key: str, window_start: float, ttl: float) -> int:
        """在 (key, window_start) 桶内计数 +1，返回桶内当前计数。

        实现方需保证并发下计数准确；ttl 用于过期回收。
        """
        ...


class InMemoryStorage:
    """默认本地实现：分桶计数 + 惰性过期 + 周期回收，内存有界。

    - 每个客户端键一把 asyncio.Lock，同一键的计数串行化，不同键互不阻塞；
    - 桶以 (键, 窗口起点) 为索引，过期后惰性覆盖，并由周期 sweep 兜底回收；
    - 桶数量超过 max_keys 时按过期时间从近到远驱逐，保证内存有界。
    """

    def __init__(self, max_keys: int = 100_000, sweep_interval: float = 30.0) -> None:
        self._max_keys = max_keys
        self._sweep_interval = sweep_interval
        # (key, window_start) -> (count, expires_at)，时间用单调时钟
        self._buckets: dict[tuple[str, float], tuple[int, float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()
        self._last_sweep = time.monotonic()

    async def hit(self, key: str, window_start: float, ttl: float) -> int:
        now = time.monotonic()
        async with self._locks_guard:
            lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            self._maybe_sweep(now)
            bkey = (key, window_start)
            entry = self._buckets.get(bkey)
            if entry is None or entry[1] <= now:
                # 桶不存在或已过期：开启新桶（过期桶直接覆盖，不重复计数）
                self._ensure_capacity(now)
                self._buckets[bkey] = (1, now + ttl)
                return 1
            count, expires_at = entry
            self._buckets[bkey] = (count + 1, expires_at)
            return count + 1

    def _maybe_sweep(self, now: float) -> None:
        if now - self._last_sweep >= self._sweep_interval:
            self._sweep(now)
            self._last_sweep = now

    def _sweep(self, now: float) -> None:
        """回收过期桶，并清理不再持有任何桶的键对应的锁。"""
        self._buckets = {
            k: v for k, v in self._buckets.items() if v[1] > now
        }
        live_keys = {k for (k, _) in self._buckets}
        self._locks = {
            k: lk for k, lk in self._locks.items() if k in live_keys or lk.locked()
        }

    def _ensure_capacity(self, now: float) -> None:
        """插入新桶前保证容量：先清过期，仍满则按过期时间从近到远驱逐。"""
        if len(self._buckets) < self._max_keys:
            return
        self._sweep(now)
        if len(self._buckets) < self._max_keys:
            return
        by_expiry = sorted(self._buckets, key=lambda k: self._buckets[k][1])
        for k in by_expiry[: len(self._buckets) - self._max_keys + 1]:
            del self._buckets[k]
