## ADDED Requirements

### Requirement: OpenTelemetry 全链路 Trace
所有 LLM / Tool / RAG / SQL 调用 MUST emit OTel span，使用 OTLP exporter 上报至 Phoenix。

#### Scenario: 单次 Query 完整 Span 树
- **WHEN** 用户提交 query 完成全流程
- **THEN** Phoenix UI 显示完整 span 树：route → plan → act(tool/rag/llm) → observe → reflect

### Requirement: trace_id 串联全流程
每次 Query MUST 在入口生成唯一 trace_id，AgentContext 持有该 ID，所有下游 span 共享同一 trace_id。

#### Scenario: 通过 trace_id 查询单次执行
- **WHEN** 调用 `GET /trace/{trace_id}`
- **THEN** 返回该 trace 全部 span 列表与时序

### Requirement: 关键 metric 输出
系统 SHALL 暴露 Prometheus metrics endpoint，至少包含：QPS / latency p50 p99 / total_tokens / cost_usd_total / eval_pass_rate。

#### Scenario: GET /metrics 输出
- **WHEN** 访问 `GET /metrics`
- **THEN** 返回 Prometheus 文本格式，含上述全部 metric

### Requirement: openinference LLM 语义
LLM 相关 span MUST 使用 openinference 语义约定，含 SpanAttributes.LLM_PROMPTS / LLM_OUTPUT_MESSAGES / LLM_TOKEN_COUNT_PROMPT 等。

#### Scenario: Phoenix 自动识别 LLM span
- **WHEN** LLM Gateway emit 的 span 含 openinference 标准 attributes
- **THEN** Phoenix UI 可视化展示 prompt / completion / token usage 详情
