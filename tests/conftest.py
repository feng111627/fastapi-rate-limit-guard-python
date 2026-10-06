"""测试公共夹具与结果日志。

运行 pytest 时会打印：
- 会话开始时的业务覆盖清单；
- 每个用例的 PASS/FAIL 结果行（[RESULT] 前缀）。
"""
from __future__ import annotations

import logging

import pytest

from app.config import RateLimitSettings, RouteQuota
from app.limiter import RateLimiter
from app.metrics import MetricsRegistry
from app.storage import InMemoryStorage

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("tests")

COVERAGE = [
    "窗口边界：配额耗尽拒绝 / 窗口切换精确恢复 / 不提前放行 / 不重复计数",
    "突发保护：短窗口超限以 burst_too_fast 拒绝并给出重试等待",
    "并发竞争：多请求并发命中同一配额，计数准确、不超发",
    "存储降级：存储异常/超时按 fail_open / fail_closed 处理，不向调用方抛错",
    "内存有界：过期桶回收、键数上限驱逐",
    "入口集成：中间件 429 响应、Retry-After 与限流响应头",
]


def pytest_sessionstart(session):  # noqa: ANN001
    print("\n=== 本次测试覆盖的业务场景 ===")
    for i, item in enumerate(COVERAGE, 1):
        print(f"  {i}. {item}")


def pytest_runtest_logreport(report):  # noqa: ANN001
    if report.when == "call":
        status = "PASS" if report.passed else "FAIL" if report.failed else "SKIP"
        print(f"[RESULT] {status} {report.nodeid}")


@pytest.fixture
def make_limiter():
    """构造限流器的工厂：可注入存储与配置，便于各场景定制。"""
    def factory(*, storage=None, settings=None, storage_timeout=0.2):
        s = settings or RateLimitSettings(
            default_quota=RouteQuota(limit=3, window_seconds=10.0,
                                     burst_limit=100, burst_window_seconds=1.0))
        st = storage or InMemoryStorage(max_keys=s.max_keys)
        m = MetricsRegistry()
        return RateLimiter(st, s, m, storage_timeout=storage_timeout), m
    return factory
