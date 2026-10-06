"""内存有界场景：过期桶被回收，键数超过上限时按过期时间驱逐。"""
from __future__ import annotations

import logging
import time

from app.storage import InMemoryStorage

logger = logging.getLogger("tests.bounds")


async def test_expired_buckets_are_swept():
    """过期桶在 sweep 后被回收，桶数量回落。"""
    storage = InMemoryStorage(max_keys=1000, sweep_interval=0.0)  # 每次都 sweep
    await storage.hit("k1", 1.0, ttl=0.01)
    assert len(storage._buckets) == 1
    time.sleep(0.02)
    await storage.hit("k2", 1.0, ttl=60.0)  # 触发 sweep
    keys = [k for (k, _) in storage._buckets]
    logger.info("sweep 后存活键: %s", keys)
    assert "k1" not in keys, "过期桶应被回收"
    assert "k2" in keys


async def test_max_keys_bound_evicts_earliest_expiring():
    """桶数达到上限时驱逐最早过期的桶，内存占用有界。"""
    storage = InMemoryStorage(max_keys=10, sweep_interval=9999.0)
    for i in range(10):
        await storage.hit(f"k{i}", 1.0, ttl=60.0 + i)  # k0 最早过期
    await storage.hit("k-new", 1.0, ttl=60.0)  # 触发驱逐
    logger.info("驱逐后桶数: %d", len(storage._buckets))
    assert len(storage._buckets) <= 10, "桶数不得超过上限"
    assert "k0" not in [k for (k, _) in storage._buckets], "应驱逐最早过期的桶"
    assert "k-new" in [k for (k, _) in storage._buckets]


async def test_locks_are_pruned_with_sweep():
    """不再持有桶的键对应的锁也被清理，锁表不无限增长。"""
    storage = InMemoryStorage(max_keys=1000, sweep_interval=0.0)
    await storage.hit("gone", 1.0, ttl=0.01)
    time.sleep(0.02)
    await storage.hit("alive", 1.0, ttl=60.0)
    assert "gone" not in storage._locks
    assert "alive" in storage._locks
