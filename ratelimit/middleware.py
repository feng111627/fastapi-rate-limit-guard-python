"""FastAPI 入口中间件：按客户端 + 路由维度执行限流。"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware

from ratelimit.limiter import RateLimiter
from ratelimit.models import RateLimitRule


class RateLimitMiddleware(BaseHTTPMiddleware):
    """入口限流中间件（占位骨架）。"""

    def __init__(
        self,
        app,
        limiter: RateLimiter,
        default_rule: RateLimitRule | None = None,
        route_rules: dict[str, RateLimitRule] | None = None,
    ) -> None:
        super().__init__(app)
        self._limiter = limiter
        self._default_rule = default_rule or RateLimitRule()
        self._route_rules = route_rules or {}
