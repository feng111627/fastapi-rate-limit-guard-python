"""配额状态存储：可替换的存储协议与默认本地内存实现。"""

from __future__ import annotations

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
    """存储协议：可替换为 Redis 等外部实现。"""

    async def record_and_count(
        self, key: str, now: float, window_seconds: float
    ) -> WindowCounts:
        """原子地记录一次命中并返回当前/上一窗口计数。"""
        ...


class InMemoryStorage:
    """默认本地内存实现（占位骨架）。"""

    def __init__(self, max_keys: int = 100_000) -> None:
        self._max_keys = max_keys

    async def record_and_count(
        self, key: str, now: float, window_seconds: float
    ) -> WindowCounts:
        raise NotImplementedError
