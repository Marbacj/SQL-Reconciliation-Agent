## Why

现有的 SQL Reconciliation Agent v1（基于自研 HelloAgents 框架）经过资深架构审查暴露出严重的"形似神不似"问题：双层 Agent 不共享 context、三层记忆没有 promotion/consolidation 机制、自进化沉淀经验没有质量门槛、SQL 安全靠关键字黑名单、全程无可观测和评测体系。为了让项目能够支撑严肃面试的深度技术追问，并对标 2026 年 Agent 工程主流实践，需要进行系统性重构。

## What Changes

- **BREAKING**：废弃自研 HelloAgents 编排层（`hello_agents/agents/`、`hello_agents/core/`），改用 LangGraph StateGraph
- **BREAKING**：Tool 系统从 `@tool_action` 反射魔法迁移至 Pydantic Schema 显式声明
- **BREAKING**：SQL 安全护栏从字符串黑名单升级为 sqlglot AST 解析 + 白名单 verb
- **BREAKING**：Memory 持久化从 JSON 文件迁移至 SQLite（带 promotion / consolidation / 衰减机制）
- 新增 5-Node LangGraph 状态机：`route → plan → act → observe → reflect`，单一 `AgentContext` 贯穿全程，支持 ReAct / PlanSolve 模式切换、checkpoint 中断恢复、token budget 控制
- 新增 Hybrid RAG：BM25 + Dense（bge-small-zh）+ RRF 融合 + Cross-Encoder Rerank（bge-reranker-v2-m3），并升级为 RAG-as-Tool 让 Agent 主动调用
- 新增 Self-Evolution Sandbox：Skill 入库前必须通过 Dedup（embedding 相似度）+ Critic（LLM 自我评估）+ Sandbox（Golden Set 子集 dry-run）三道门
- 新增 Golden Set 评测体系：50 条业务级 case + 4 维 metric（Exec-Accuracy / Semantic-Match / Latency / Token Cost）+ Eval-Driven Regression
- 新增 LLM Gateway：基于 LiteLLM 的统一接口，带 query 指纹 cache、retry、cost 累计
- 新增 OpenTelemetry 全链路 trace + Phoenix UI 可视化
- 新增 FastAPI 接入层 + docker-compose 一键启动（app + Qdrant + Redis + Phoenix）
- v1 代码迁移至 `legacy/` 目录作为对照基线，不删除

## Capabilities

### New Capabilities

- `agent-orchestration`：基于 LangGraph 的状态机编排，含 AgentContext 共享上下文、5-Node 流程、模式切换、Budget 控制、Checkpoint 中断恢复
- `tool-system`：Pydantic Schema 显式工具定义，含 ToolBase / ToolInput / ToolOutput 抽象、OpenAI Function Calling 兼容、5 个内置工具（sql_runner / diff_calculator / report_generator / rag_searcher / case_query）
- `hybrid-rag`：BM25 + Dense + RRF + Cross-Encoder Rerank 四阶段混合检索，RAG-as-Tool 形态，离线 indexer + 在线 retriever + 检索质量评估（MRR@5 / Recall@10）
- `memory-system`：三层记忆（Working LRU / Episodic SQLite / Semantic SQLite），含重要性打分 promotion、LLM Consolidation Job、confidence 衰减 / 淘汰
- `self-evolution`：Skill 自进化闭环，含异步队列、Dedup、Critic、Sandbox（Golden Set 子集 dry-run）三道质量门、动态 confidence 调权
- `sql-safety`：基于 sqlglot AST 的 SQL 安全护栏，verb 白名单 + 危险节点扫描 + EXPLAIN 预校验
- `llm-gateway`：基于 LiteLLM 的统一 LLM 接入，含 query 指纹 cache（Redis / 内存兜底）、retry、cost 累计、多厂商兼容
- `observability`：OpenTelemetry + Phoenix UI 全链路 trace，每次 LLM / Tool / RAG 调用 emit span，trace_id 串联全流程
- `eval-harness`：Golden Set 评测系统，含 50 条 case schema、4 维 metric（Exec-Accuracy / Semantic-Match / Latency / Token Cost）、可对比 v1/v2 的 runner
- `deployment`：FastAPI 接入层 + docker-compose 编排（app + Qdrant + Redis + Phoenix）+ Prometheus metrics + 部署文档

### Modified Capabilities

（无 - 这是一次从零重构，所有现有能力均以新 capability 形式重新定义，原 v1 代码归档至 `legacy/` 目录作为参考基线）

## Impact

### 受影响的代码
- **归档至 `legacy/`**：`hello_agents/agents/`、`hello_agents/core/`、`hello_agents/tools/`、`examples/`
- **新增 `recon_v2/`**：core / orchestration / tools / memory / rag / evolution / infra / adapters
- **新增 `apps/`**：cli / api / notebook
- **新增 `tests/eval/`**：golden_set.jsonl / metrics.py / runner.py / reports/
- **新增 `docs/v2/`**：architecture.md（已存在）/ adr/ / runbook.md
- **新增 `deploy/`**：docker-compose.yml / Dockerfile

### 受影响的 API
- 新增 `POST /query` 流式输出
- 新增 `GET /trace/{trace_id}` 查询单次 trace
- 新增 `GET /metrics` Prometheus 指标
- v1 CLI demo 保留但仅作为对照展示

### 新增依赖
- `langgraph>=0.2.0`、`langchain-core>=0.3.0`
- `litellm>=1.40.0`、`tiktoken>=0.6.0`
- `sqlglot>=23.0`
- `qdrant-client>=1.9`、`rank-bm25>=0.2.2`、`sentence-transformers>=3.0`、`FlagEmbedding>=1.2`
- `opentelemetry-*>=1.25`、`openinference-instrumentation>=0.1`、`arize-phoenix>=4.0`
- `fastapi>=0.110`、`uvicorn>=0.27`、`httpx>=0.27`
- `redis>=5.0`、`cachetools>=5.3`
- `sqlalchemy>=2.0`、`alembic>=1.13`
- `pytest>=8.0`、`pytest-asyncio>=0.23`

### 受影响的系统
- 开发环境需要 Docker（用于跑 Qdrant / Redis / Phoenix）
- 运行需要 LLM API Key（OpenAI / DeepSeek / 其他 LiteLLM 兼容的 provider）
- 至少一个 SQL 数据源（SQLite 即可，含对账测试样例数据）
- Python 3.10+

### 周期与节奏
- 总周期 ~6.5 周（Stage 0-5），分 7 个 milestone
- 关键风险点：Stage 2（LangGraph 范式跳跃）、Stage 4（Memory consolidation + Sandbox prompt 工程）
- 每个 Stage 必须跑 Golden Set regression 验证不退化
