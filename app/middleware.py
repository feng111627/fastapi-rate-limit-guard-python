"""FastAPI 入口中间件：把限流判定接到请求链路上。"""
from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .limiter import RateLimiter


class RateLimitMiddleware(BaseHTTPMiddleware):
    """对每个入站请求执行限流判定，拒绝时返回 429 与重试提示。"""

    def __init__(self, app, limiter: RateLimiter) -> None:  # noqa: ANN001
        super().__init__(app)
        self._limiter = limiter

    async def dispatch(self, request: Request, call_next) -> Response:  # noqa: ANN001
        client = self._client_id(request)
        route = request.url.path
        decision = await self._limiter.check(client, route, time.time())

        if not decision.allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "rate limit exceeded",
                    "reason": decision.reason.value if decision.reason else "unknown",
                    "retry_after": round(decision.retry_after, 3),
                },
                headers={
                    "Retry-After": str(max(1, int(decision.retry_after + 0.999))),
                    "X-RateLimit-Limit": str(decision.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(decision.limit)
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        if decision.degraded:
            response.headers["X-RateLimit-Degraded"] = "true"
        return response

    @staticmethod
    def _client_id(request: Request) -> str:
        """客户端标识：优先取代理透传的首个地址，否则用对端地址。"""
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"
