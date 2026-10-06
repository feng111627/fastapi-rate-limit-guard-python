# fastapi-rate-limit-guard

在 FastAPI 入口增加一层限流与防护：正常使用基本无感，突发流量或恶意客户端
触发限制时，结果可预期、可解释。

## 配额模型

判定维度为 **客户端 × 路由**（`客户端IP:路由路径` 作为配额 key），
每个维度上有两个滚动的时间窗口：

| 窗口 | 作用 | 拒绝原因 |
| --- | --- | --- |
| 突发窗口（默认 1s / 10 次） | 抑制瞬时突发 | `burst_too_fast` |
| 配额窗口（默认 60s / 60 次） | 控制持续速率 | `quota_exceeded` |

- **滑动窗口计数器**：有效计数 = 当前窗口计数 + 上一窗口计数 × 剩余时间权重
  （线性衰减）。窗口切换处既不重复计数，也不会提前放行；配额随窗口滚动平滑恢复。
- **每次尝试都计数**：被拒绝的请求同样消耗配额，防止恶意客户端用被拒绝的
  流量绕过限制。
- **Retry-After**：被拒绝时返回距当前窗口结束的秒数（保守、可解释），
  同时写入响应头 `Retry-After` 与响应体 `retry_after_seconds`。
- **存储降级**：存储不可用时按 `fail_open`（默认放行）或 `fail_closed`
  （拒绝，原因 `storage_unavailable`）处理，异常绝不抛给调用方。

被拒绝的响应为 `429`：

```json
{"detail": "请求被限流", "reason": "quota_exceeded", "retry_after_seconds": 7.0}
```

放行的响应附带 `X-RateLimit-Limit` / `X-RateLimit-Remaining` 头。

## 并发、内存与可观测

- **计数准确**：存储的 `record_and_count` 是最小原子操作；默认本地实现用
  分段 asyncio 锁保证同 key 并发不超放、不漏记，慢请求只阻塞自己的锁分片。
- **内存有界**：本地存储条目数受 `max_keys` 限制，插入时先回收过期条目再
  LRU 淘汰；每个 key 只保留当前与上一窗口的桶，过期桶惰性清理。
- **可替换存储**：实现 `ratelimit.storage.RateLimitStorage` 协议
  （如 Redis + Lua 保证原子性）即可替换 `InMemoryStorage`。
- **指标**：`GET /metrics` 暴露放行/拒绝总量、按原因与按 key 的拒绝计数、
  存储错误与降级计数，便于排查“什么时候、按什么原因、把谁限制了”。

## 配置项

`RateLimitRule`（可按路由覆盖，见 `main.py` 中 `route_rules`）：

| 字段 | 默认 | 含义 |
| --- | --- | --- |
| `max_requests` | 60 | 配额窗口内最大请求数 |
| `window_seconds` | 60.0 | 配额窗口长度（秒） |
| `burst_limit` | 10 | 突发窗口内最大请求数 |
| `burst_window_seconds` | 1.0 | 突发窗口长度（秒） |

`RateLimiter` 构造参数：

| 参数 | 默认 | 含义 |
| --- | --- | --- |
| `storage` | 必填 | 存储实现（默认 `InMemoryStorage`） |
| `fail_open` | `True` | 存储不可用时放行（`False` 则拒绝） |
| `degraded_retry_after` | 1.0 | fail-closed 降级拒绝时的 Retry-After 秒数 |

`InMemoryStorage` 构造参数：`max_keys`（默认 100000，条目上限）、
`lock_stripes`（默认 64，锁分片数）。

`RateLimitMiddleware` 参数：`default_rule`、`route_rules`（按路径覆盖）、
`exempt_paths`（默认豁免 `/metrics`）。

## 本地验证

```bash
# 安装依赖
uv sync

# 运行测试（日志可见每个用例覆盖的业务场景与结果）
uv run pytest

# 启动服务
uv run python main.py   # 或 uv run fastapi dev main.py

# 手动验证：/api/echo 规则为 5 次/10s、突发 2 次/1s
for i in $(seq 1 8); do curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/api/echo; done
# 观察 429 响应体与 Retry-After 头
curl -i http://127.0.0.1:8000/api/echo
# 查看限流指标
curl -s http://127.0.0.1:8000/metrics
```

## 测试覆盖

| 测试文件 | 覆盖场景 |
| --- | --- |
| `tests/test_window_boundary.py` | 窗口边界：不提前放行、不重复计数、滚动恢复、Retry-After、原因区分 |
| `tests/test_concurrency.py` | 并发竞争：不超放、不漏记、key 隔离、慢请求不阻塞 |
| `tests/test_storage_degradation.py` | 存储不可用：fail-open / fail-closed、故障恢复、意外异常 |
| `tests/test_memory_bounded.py` | 内存有界：条目上限、过期回收、窗口桶惰性清理 |
| `tests/test_middleware.py` | 端到端：429 可解释响应、客户端×路由隔离、/metrics 可观测 |
