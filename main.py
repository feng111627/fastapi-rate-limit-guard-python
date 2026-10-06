import uvicorn
from fastapi import FastAPI

from app.config import RateLimitSettings, RouteQuota
from app.limiter import RateLimiter
from app.metrics import MetricsRegistry
from app.middleware import RateLimitMiddleware
from app.storage import InMemoryStorage

settings = RateLimitSettings(
    default_quota=RouteQuota(limit=60, window_seconds=60.0,
                             burst_limit=10, burst_window_seconds=1.0),
    route_quotas={
        # 示例：对昂贵路由单独收紧配额
        "/expensive": RouteQuota(limit=5, window_seconds=60.0,
                                 burst_limit=2, burst_window_seconds=1.0),
    },
    fail_open=True,
    max_keys=100_000,
)
metrics = MetricsRegistry()
storage = InMemoryStorage(max_keys=settings.max_keys,
                          sweep_interval=settings.sweep_interval_seconds)
limiter = RateLimiter(storage, settings, metrics)

app = FastAPI()
app.add_middleware(RateLimitMiddleware, limiter=limiter)


@app.get("/")
def read_root():
    return {"Hello": "World"}


@app.get("/expensive")
def expensive():
    return {"ok": True}


@app.get("/_internal/rate-limit-metrics")
def rate_limit_metrics():
    """限流决策计数快照，便于排查什么时候、按什么原因限制了谁。"""
    return {"counters": metrics.snapshot()}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=0)
