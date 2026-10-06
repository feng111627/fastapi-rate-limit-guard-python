"""测试公共配置：让每个用例覆盖的业务场景与执行结果体现在日志里。"""

from __future__ import annotations

import logging

import pytest

logger = logging.getLogger("tests.coverage")


@pytest.fixture(autouse=True)
def log_business_scenario(request):
    """用例开始前记录其覆盖的业务场景（取 docstring 首行）。"""
    doc = (request.function.__doc__ or "").strip().splitlines()
    scenario = doc[0] if doc else "(未标注场景)"
    logger.info("【业务场景】%s::%s —— %s", request.module.__name__, request.function.__name__, scenario)
    yield


def pytest_runtest_logreport(report):
    """用例结束后记录结果，日志可直接看出每个用例通过与否。"""
    if report.when == "call":
        outcome = {"passed": "✅ 通过", "failed": "❌ 失败", "skipped": "⏭ 跳过"}.get(
            report.outcome, report.outcome
        )
        logger.info("【用例结果】%s —— %s", report.nodeid, outcome)
