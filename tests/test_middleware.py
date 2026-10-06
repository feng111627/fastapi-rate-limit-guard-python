"""端到端场景：中间件按 客户端×路由 限流，429 响应可解释，指标可观测。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ratelimit import (
    InMemoryStorage,
    RateLimiter,
    RateLimitMetrics,
    RateLimitMiddleware,
    RateLimitRule,
)


@pytest.fixture()
def client():
    metrics = RateLimitMetrics()
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        limiter=RateLimiter(InMemoryStorage(), metrics=metrics),
        default_rule=RateLimitRule(
            max_requests=100, window_seconds=60.0,
            burst_limit=100, burst_window_seconds=1.0,
        ),
        route_rules={
            "/tight": RateLimitRule(
                max_requests=3, window_seconds=60.0,
                burst_limit=100, burst_window_seconds=1.0,
            ),
        },
    )

    @app.get("/tight")
    def tight():
        return {"ok": True}

    @app.get("/loose")
    def loose():
        return {"ok": True}

    @app.get("/metrics")
    def metrics_endpoint():
        return metrics.snapshot()

    return TestClient(app), metrics


def test_allowed_requests_carry_ratelimit_headers(client):
    """正常请求放行且基本无感：200 + X-RateLimit-* 响应头。"""
    c, _ = client
    r = c.get("/loose")
    assert r.status_code == 200
    assert r.headers["x-ratelimit-limit"] == "100"
    assert int(r.headers["x-ratelimit-remaining"]) >= 0


def test_route_specific_quota_returns_429_with_reason(client):
    """路由级配额打满后返回 429，body 含 reason 与 retry_after，含 Retry-After 头。"""
    c, _ = client
    for _ in range(3):
        assert c.get("/tight").status_code == 200
    r = c.get("/tight")
    assert r.status_code == 429
    body = r.json()
    assert body["reason"] == "quota_exceeded"
    assert body["retry_after_seconds"] > 0
    assert "retry-after" in {k.lower() for k in r.headers}


def test_quota_is_per_client_and_per_route(client):
    """配额按 客户端×路由 隔离：/tight 打满不影响 /loose，其它客户端不受影响。"""
    c, _ = client
    for _ in range(4):
        c.get("/tight")
    assert c.get("/tight").status_code == 429
    assert c.get("/loose").status_code == 200
    r = c.get("/tight", headers={"X-Forwarded-For": "9.9.9.9"})
    assert r.status_code == 200


def test_metrics_endpoint_observes_rejections(client):
    """/metrics 能看到按原因、按 key 的拒绝计数，且自身不被限流。"""
    c, metrics = client
    for _ in range(5):
        c.get("/tight")
    r = c.get("/metrics")
    assert r.status_code == 200
    snap = r.json()
    assert snap["rejected_total"] >= 2
    assert snap["rejected_by_reason"].get("quota_exceeded", 0) >= 2
    assert any("tight" in k for k in snap["rejected_by_key"])
