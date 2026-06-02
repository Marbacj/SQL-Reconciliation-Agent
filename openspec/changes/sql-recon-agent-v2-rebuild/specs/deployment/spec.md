## ADDED Requirements

### Requirement: FastAPI 接入层
系统 SHALL 提供 FastAPI 服务，至少暴露三个端点：`POST /query` / `GET /trace/{trace_id}` / `GET /metrics`。

#### Scenario: POST /query 流式输出
- **WHEN** POST `/query` 请求 body 含 query 字段
- **THEN** 返回 SSE 流，依次推送各 Node 中间状态，最终返回 final_answer + trace_id

#### Scenario: GET /trace 查询
- **WHEN** GET `/trace/{trace_id}` 且 trace_id 存在
- **THEN** 返回该 trace 的 span 列表 + 总成本 + Phoenix UI 链接

#### Scenario: GET /metrics Prometheus
- **WHEN** GET `/metrics`
- **THEN** 返回 Prometheus 文本格式 metric

### Requirement: docker-compose 一键启动
系统 SHALL 提供 `deploy/docker-compose.yml`，启动 app + Qdrant + Redis + Phoenix 四个服务。

#### Scenario: 全栈启动
- **WHEN** 执行 `docker-compose up -d`
- **THEN** 四个容器全部启动，`curl http://localhost:8000/health` 返回 200

#### Scenario: Degraded Mode
- **WHEN** 故意停止 Redis 容器
- **THEN** app 仍可正常处理请求，LLM Gateway 自动降级到内存 cache

### Requirement: 部署文档
系统 SHALL 提供 `docs/v2/runbook.md`，含部署步骤 / 环境变量说明 / 常见故障排查。

#### Scenario: README Quick Start 可重现
- **WHEN** 干净环境按 README Quick Start 步骤操作
- **THEN** 能跑通 demo query 并查看 trace

### Requirement: ADR 决策记录
系统 SHALL 在 `docs/v2/adr/` 至少记录 5 条 Architecture Decision Record：选 LangGraph / Hybrid RAG 设计 / Memory 分层 / Sandbox 三道门 / sqlglot AST。

#### Scenario: ADR 完整存在
- **WHEN** 列出 `docs/v2/adr/` 目录
- **THEN** 至少 5 个 ADR 文件，每个含 Context / Decision / Consequences 三段
