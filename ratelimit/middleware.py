"""FastAPI 入口中间件：按 客户端 × 路由 维度执行限流。

- 客户端标识：优先 X-Forwarded-For 首跳，否则用连接对端 IP；
- 路由维度：优先匹配 route_rules 中的精确路径，否则用默认规则；
- 放行时附带 X-RateLimit-* 响应头；拒绝时返回 429 + Retry-After，
  响应体中的 reason 区分 超出配额 / 突发过快 / 存储不可用。
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ratelimit.limiter import RateLimiter
from ratelimit.models import RateLimitRule


class RateLimitMiddleware(BaseHTTPMiddleware):
    """入口限流中间件。"""

    def __init__(
        self,
        app,
        limiter: RateLimiter,
        default_rule: RateLimitRule | None = None,
        route_rules: dict[str, RateLimitRule] | None = None,
        exempt_paths: frozenset[str] | None = None,
    ) -> None:
        super().__init__(app)
        self._limiter = limiter
        self._default_rule = default_rule or RateLimitRule()
        self._route_rules = route_rules or {}
        self._exempt_paths = exempt_paths or frozenset({"/metrics"})

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if path in self._exempt_paths:
            return await call_next(request)

        client = self._client_key(request)
        rule = self._route_rules.get(path, self._default_rule)
        decision = await self._limiter.check(
            f"{client}:{path}", rule, time.monotonic()
        )

        if not decision.allowed:
            retry = max(0.0, decision.retry_after_seconds)
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "请求被限流",
                    "reason": str(decision.reason) if decision.reason else None,
                    "retry_after_seconds": round(retry, 3),
                },
                headers={
                    "Retry-After": str(max(1, int(retry + 0.5))),
                    "X-RateLimit-Limit": str(decision.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(decision.limit)
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        return response

    @staticmethod
    def _client_key(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"
