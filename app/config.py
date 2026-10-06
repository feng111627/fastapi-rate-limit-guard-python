"""限流配置项。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RouteQuota:
    """单条路由的配额规则。"""

    limit: int = 60                # 长窗口内允许的请求数
    window_seconds: float = 60.0   # 长窗口长度（滚动恢复）
    burst_limit: int = 10          # 短窗口内允许的请求数
    burst_window_seconds: float = 1.0  # 短窗口长度（突发保护）


@dataclass(frozen=True)
class RateLimitSettings:
    """全局限流配置。"""

    default_quota: RouteQuota = field(default_factory=RouteQuota)
    route_quotas: dict[str, RouteQuota] = field(default_factory=dict)
    fail_open: bool = True         # 存储不可用时放行(True)还是拒绝(False)
    max_keys: int = 100_000        # 本地存储的最大键数（内存有界）
    sweep_interval_seconds: float = 30.0  # 过期状态回收周期

    def quota_for(self, route: str) -> RouteQuota:
        """取路由配额，未配置的路由用默认配额。"""
        return self.route_quotas.get(route, self.default_quota)
