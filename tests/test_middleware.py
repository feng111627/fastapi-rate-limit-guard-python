"""入口集成场景：中间件 429 响应、Retry-After 头、限流响应头、指标端点。"""
from __future__ import annotations

import logging

import httpx
from fastapi import FastAPI

from app.config import RateLimitSettings, RouteQuota
from app.limiter import RateLimiter
from app.metrics import MetricsRegistry
from app.middleware import RateLimitMiddleware
from app.storage import InMemoryStorage

logger = logging.getLogger("tests.middleware")


def build_app() -> tuple[FastAPI, MetricsRegistry]:
    settings = RateLimitSettings(default_quota=RouteQuota(
        limit=2, window_seconds=60.0, burst_limit=100, burst_window_seconds=1.0))
    metrics = MetricsRegistry()
    limiter = RateLimiter(InMemoryStorage(), settings, metrics)
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, limiter=limiter)

    @app.get("/api")
    def api():
        return {"ok": True}

    @app.get("/_internal/rate-limit-metrics")
    def m():
        return {"counters": metrics.snapshot()}

    return app, metrics


async def test_allowed_requests_carry_rate_limit_headers():
    """放行的请求带 X-RateLimit-* 响应头。"""
    app, _ = build_app()
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://t") as client:
        r = await client.get("/api")
        logger.info("放行响应头: %s", dict(r.headers))
        assert r.status_code == 200
        assert r.headers["x-ratelimit-limit"] == "2"
        assert r.headers["x-ratelimit-remaining"] == "1"


async def test_rejected_request_returns_429_with_reason_and_retry_after():
    """超限请求返回 429、可区分的原因和 Retry-After。"""
    app, _ = build_app()
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://t") as client:
        await client.get("/api")
        await client.get("/api")
        r = await client.get("/api")
        logger.info("拒绝响应: %s %s", r.status_code, r.json())
        assert r.status_code == 429
        body = r.json()
        assert body["reason"] == "quota_exceeded"
        assert body["retry_after"] > 0
        assert "retry-after" in r.headers


async def test_metrics_endpoint_exposes_counters():
    """指标端点能看到按结果/原因/路由维度的计数。"""
    app, _ = build_app()
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://t") as client:
        await client.get("/api")
        await client.get("/api")
        await client.get("/api")
        r = await client.get("/_internal/rate-limit-metrics")
        counters = r.json()["counters"]
        logger.info("指标快照: %s", counters)
        assert counters["allowed:ok:/api"] == 2
        assert counters["rejected:quota_exceeded:/api"] == 1
