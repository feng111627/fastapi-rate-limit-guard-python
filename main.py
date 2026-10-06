import uvicorn
from fastapi import FastAPI

from ratelimit import (
    InMemoryStorage,
    RateLimiter,
    RateLimitMetrics,
    RateLimitMiddleware,
    RateLimitRule,
)

# 限流组件装配：默认本地内存存储，fail-open 降级
metrics = RateLimitMetrics()
storage = InMemoryStorage(max_keys=100_000)
limiter = RateLimiter(storage, metrics=metrics, fail_open=True)

app = FastAPI(title="fastapi-rate-limit-guard")
app.add_middleware(
    RateLimitMiddleware,
    limiter=limiter,
    default_rule=RateLimitRule(
        max_requests=60, window_seconds=60.0, burst_limit=10, burst_window_seconds=1.0
    ),
    route_rules={
        # 示例：敏感路由单独收紧配额
        "/api/echo": RateLimitRule(
            max_requests=5, window_seconds=10.0, burst_limit=2, burst_window_seconds=1.0
        ),
    },
)


@app.get("/")
def read_root():
    return {"Hello": "World"}


@app.get("/api/echo")
def echo(q: str = ""):
    return {"echo": q}


@app.get("/metrics")
def rate_limit_metrics():
    """限流决策的可观测计数（豁免限流）。"""
    return metrics.snapshot()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=0)
