"""内存有界场景：条目数有上限，过期状态被回收。"""

from __future__ import annotations

from ratelimit.storage import InMemoryStorage


async def test_entry_count_bounded_by_max_keys():
    """大量不同 key 写入时，条目数不超过 max_keys（LRU 淘汰兜底）。"""
    storage = InMemoryStorage(max_keys=100)
    for i in range(1000):
        await storage.record_and_count(f"key{i}", now=1000.0, window_seconds=60.0)
    assert storage.entry_count() <= 100


async def test_expired_entries_reclaimed_on_insert():
    """插入新条目时优先回收过期条目，而不是淘汰仍活跃的条目。"""
    storage = InMemoryStorage(max_keys=3)
    # 三个老 key，窗口 [0,60)
    for k in ("old1", "old2", "old3"):
        await storage.record_and_count(k, now=0.0, window_seconds=60.0)
    # 时间前进到 200s：老条目全部过期
    # 活跃 key 在当前窗口有数据
    await storage.record_and_count("old1", now=200.0, window_seconds=60.0)
    # 再插入两个新 key：应回收 old2/old3（已过期），保留活跃的 old1
    await storage.record_and_count("new1", now=200.0, window_seconds=60.0)
    await storage.record_and_count("new2", now=200.0, window_seconds=60.0)
    assert storage.entry_count() <= 3
    c = await storage.record_and_count("old1", now=200.0, window_seconds=60.0)
    assert c.current == 2  # old1 的活跃数据未被淘汰（200s 窗口内第 2 次 + 本次）


async def test_stale_window_buckets_pruned_lazily():
    """同一 key 的历史窗口桶在后续访问时被惰性清理，不无限累积。"""
    storage = InMemoryStorage()
    for t in range(0, 1000, 10):  # 100 个不同窗口各写一次
        await storage.record_and_count("k", now=float(t), window_seconds=10.0)
    # 惰性回收只保留当前与上一窗口：条目数始终为 1
    assert storage.entry_count() == 1
    bucket = storage._buckets[("k", 10.0)]
    assert len(bucket.windows) <= 2
