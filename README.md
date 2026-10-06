# FastAPI Rate Limit Guard

在 FastAPI 入口加一层限流与防护：正常使用无感，突发流量或恶意客户端下行为可预期、可解释。

## 配额模型

按 **(客户端, 路由)** 两个维度计数，每个维度组合有两个对齐的固定窗口：

| 窗口 | 作用 | 配置 |
| --- | --- | --- |
| 长窗口（配额窗口） | 窗口内最多放行 `limit` 个请求，配额随窗口滚动整体恢复 | `limit` / `window_seconds` |
| 短窗口（突发窗口） | 窗口内最多放行 `burst_limit` 个请求，防止瞬时打爆 | `burst_limit` / `burst_window_seconds` |

- 计数桶以窗口起点为索引，窗口切换时旧桶自然过期：**不重复计数、不提前放行**，恢复时刻精确对齐窗口边界。
- 客户端标识取 `X-Forwarded-For` 首个地址，否则用对端 IP；路由取请求路径。
- 并发安全：本地存储对每个客户端键加 `asyncio.Lock`，同一键计数串行化，不同键互不阻塞，单个慢请求不会拖住其它请求。

### 拒绝响应（HTTP 429）

```json
{"detail": "rate limit exceeded", "reason": "quota_exceeded", "retry_after": 42.0}
```

`reason` 可区分三种原因：

| reason | 含义 |
| --- | --- |
| `quota_exceeded` | 长窗口配额耗尽 |
| `burst_too_fast` | 短窗口突发过快 |
| `storage_fail_closed` | 存储不可用且降级策略为拒绝 |

响应同时带 `Retry-After` 头（秒），`retry_after` 指向对应窗口的结束时刻。放行的响应带
`X-RateLimit-Limit` / `X-RateLimit-Remaining` 头；走降级路径时额外带 `X-RateLimit-Degraded: true`。

## 配置项（`app/config.py`）

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `default_quota` | 60 次/60s，突发 10 次/1s | 未单独配置的路由使用 |
| `route_quotas` | `{"/expensive": ...}` | 按路径覆盖默认配额 |
| `fail_open` | `True` | 存储不可用时放行（True）还是拒绝（False） |
| `max_keys` | 100000 | 本地存储桶数上限，超出按过期时间从近到远驱逐 |
| `sweep_interval_seconds` | 30 | 过期桶周期回收间隔 |
| `storage_timeout`（`RateLimiter` 参数） | 0.2s | 存储调用超时，超时按存储不可用处理 |

## 存储可替换与降级

存储通过协议 `app.storage.RateLimitStorage` 抽象：

```python
class RateLimitStorage(Protocol):
    async def hit(self, key: str, window_start: float, ttl: float) -> int: ...
```

默认实现 `InMemoryStorage`（分桶计数 + 惰性过期 + 周期回收 + 上限驱逐，内存有界）。
换成 Redis 等实现时只需实现同一个 `hit` 方法并注入 `RateLimiter`。
存储抛异常或超时不会被抛给调用方，而是按 `fail_open` 降级：放行并标记 degraded，或以
`storage_fail_closed` 拒绝。

## 可观测

`GET /_internal/rate-limit-metrics` 返回决策计数快照，键形如
`allowed:ok:/api`、`rejected:quota_exceeded:/expensive`，即 **结果 : 原因 : 路由**，
可直接看出什么时候、按什么原因限制了谁。存储降级时 `rate_limit` 日志会输出 warning。

## 本地验证

```bash
# 运行测试（日志会列出覆盖的业务场景与每个用例的结果）
uv run pytest -v

# 启动服务
uv run python main.py   # 或 uv run uvicorn main:app --port 8000

# 手动触发限流（/expensive 配额 5 次/60s、突发 2 次/1s）
for i in $(seq 8); do curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/expensive; done
curl -s http://127.0.0.1:8000/_internal/rate-limit-metrics | jq
```

## 目录结构

```
app/
  config.py      # 配额与降级配置
  types.py       # 决策结果与拒绝原因
  storage.py     # 存储协议 + 本地内存实现
  limiter.py     # 判定核心：窗口计数、突发保护、降级
  metrics.py     # 决策计数
  middleware.py  # FastAPI 入口中间件（429 / 响应头）
tests/           # 窗口边界、并发竞争、存储降级、内存有界、入口集成
```
