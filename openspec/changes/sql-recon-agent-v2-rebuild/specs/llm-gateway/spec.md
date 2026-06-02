## ADDED Requirements

### Requirement: LiteLLM 多厂商接入
系统 SHALL 通过 LiteLLM 接入 LLM，支持至少 OpenAI / DeepSeek / Claude 三家 provider 切换，配置由环境变量或配置文件指定。

#### Scenario: 切换 provider
- **WHEN** 设置 env LLM_PROVIDER=deepseek 并调用 gateway.chat
- **THEN** 内部走 DeepSeek API，调用方代码无需修改

### Requirement: Query 指纹 Cache
所有 LLM 调用 MUST 先计算 query 指纹（messages + temperature + model 的 SHA256），命中 cache 直接返回。

#### Scenario: 重复 query 命中 cache
- **WHEN** 同一 messages + 同样参数调用 gateway.chat 两次
- **THEN** 第二次命中 cache，cost_tracker.record(source="cache")，不访问真实 API

#### Scenario: Cache 后端服务降级
- **WHEN** Redis 不可达
- **THEN** 自动降级到 InMemoryCache（cachetools），不抛异常

### Requirement: Retry 与超时
LLM 调用 MUST 支持指数退避 retry（默认 3 次，初始 1s），单次调用超时 30s。

#### Scenario: 临时网络错误自动重试
- **WHEN** 第一次调用收到 ConnectionError
- **THEN** 等待 1s 重试，第二次成功后返回

### Requirement: Cost 累计
LLM Gateway MUST 记录每次调用的 prompt_tokens / completion_tokens / cost_usd，按 trace_id 累计。

#### Scenario: 累计成本可查询
- **WHEN** 一次 query 跑完后调用 cost_tracker.get_by_trace(trace_id)
- **THEN** 返回该 trace 累计 token 数和美元成本

### Requirement: OTel Span 埋点
LLM Gateway 每次 chat 调用 MUST emit OTel span，attributes 至少包含 model / prompt_tokens / completion_tokens / latency_ms / cache_hit。

#### Scenario: Phoenix UI 可见 span
- **WHEN** 任何 LLM 调用结束
- **THEN** Phoenix UI 显示该 span 树节点 + 全部 attributes
