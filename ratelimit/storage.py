"""配额状态存储：可替换的存储协议与默认本地内存实现。

设计要点：
- record_and_count 是存储层的最小原子操作（本地实现用分段锁保证，
  替换为 Redis 时可用 Lua 脚本保证同样的原子性）；
- 每个 (key, window_seconds) 只保留当前与上一窗口的桶，过期桶即时回收；
- 条目数超过 max_keys 时先清理过期条目，再按 LRU 淘汰，内存占用有界。
"""

from __future__ import annotations

import asyncio
import math
from collections import OrderedDict
from dataclasses import dataclass
from typing import Protocol


class StorageError(Exception):
    """存储层不可用或操作失败时抛出的异常。"""


@dataclass(frozen=True)
class WindowCounts:
    """某 key 在当前与上一时间窗口内的计数。"""

    current: int
    previous: int
    window_start: float
    window_seconds: float


class RateLimitStorage(Protocol):
    """存储协议：可替换为 Redis 等外部实现。

    实现方必须保证 record_and_count 的原子性：
    同一 key 的并发调用不得超额放行或漏记。
    """

    async def record_and_count(
        self, key: str, now: float, window_seconds: float
    ) -> WindowCounts:
        """原子地记录一次命中，并返回当前/上一窗口的计数。"""
        ...


class _Bucket:
    """单个 (key, window_seconds) 的窗口桶集合。"""

    __slots__ = ("windows",)

    def __init__(self) -> None:
        self.windows: dict[int, int] = {}


class InMemoryStorage:
    """默认本地内存实现。

    - 分段 asyncio 锁：同一分片内串行，保证计数原子性；
    - 惰性过期回收：每次访问只保留当前与上一窗口；
    - max_keys 上限：先扫过期条目，再 LRU 淘汰，内存有界。
    """

    def __init__(self, max_keys: int = 100_000, lock_stripes: int = 64) -> None:
        self._max_keys = max_keys
        self._buckets: OrderedDict[tuple[str, float], _Bucket] = OrderedDict()
        self._locks = [asyncio.Lock() for _ in range(lock_stripes)]

    def _stripe(self, bucket_key: tuple[str, float]) -> asyncio.Lock:
        return self._locks[hash(bucket_key) % len(self._locks)]

    async def record_and_count(
        self, key: str, now: float, window_seconds: float
    ) -> WindowCounts:
        bucket_key = (key, window_seconds)
        async with self._stripe(bucket_key):
            index = math.floor(now / window_seconds)
            bucket = self._buckets.get(bucket_key)
            if bucket is None:
                self._evict_if_needed(now)
                bucket = _Bucket()
                self._buckets[bucket_key] = bucket
            else:
                self._buckets.move_to_end(bucket_key)

            # 惰性回收：只保留当前与上一窗口，过期窗口立即删除
            for stale in [i for i in bucket.windows if i < index - 1]:
                del bucket.windows[stale]

            bucket.windows[index] = bucket.windows.get(index, 0) + 1
            return WindowCounts(
                current=bucket.windows[index],
                previous=bucket.windows.get(index - 1, 0),
                window_start=index * window_seconds,
                window_seconds=window_seconds,
            )

    def _evict_if_needed(self, now: float) -> None:
        """插入新条目前保证容量有界：先清过期，再 LRU 淘汰。"""
        if len(self._buckets) < self._max_keys:
            return
        # 第一遍：清掉所有窗口都已过期的条目
        for bk in [bk for bk, b in self._buckets.items() if self._expired(bk, b, now)]:
            del self._buckets[bk]
        # 第二遍：仍超容则按 LRU 淘汰最久未访问的条目
        while len(self._buckets) >= self._max_keys:
            self._buckets.popitem(last=False)

    @staticmethod
    def _expired(bucket_key: tuple[str, float], bucket: _Bucket, now: float) -> bool:
        window_seconds = bucket_key[1]
        current_index = math.floor(now / window_seconds)
        return all(i < current_index - 1 for i in bucket.windows)

    def entry_count(self) -> int:
        """当前条目数（用于测试与观测内存是否有界）。"""
        return len(self._buckets)
