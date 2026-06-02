## 1. 项目骨架与归档（Stage 0 - Day 1）

- [ ] 1.1 git mv `hello_agents/` `legacy/hello_agents/`，git mv `examples/` `legacy/examples/`
- [ ] 1.2 在 `legacy/README.md` 添加"已废弃，仅作 v2 对比基线"标注
- [ ] 1.3 创建 v2 目录骨架：`recon_v2/{core,orchestration,tools,memory,rag,evolution,infra,adapters}`、`apps/{cli,api,notebook}`、`tests/{eval,unit,integration}`、`deploy/`、`docs/v2/adr/`
- [ ] 1.4 创建 `pyproject.toml`（uv 或 pdm 管理），写入完整依赖列表（见 design.md D8 + architecture.md 第 6.1 节）
- [ ] 1.5 创建 `.env.example` 模板，列出 LLM_PROVIDER / QDRANT_URL / REDIS_URL / OTEL_EXPORTER_OTLP_ENDPOINT 等
- [ ] 1.6 提交 baseline commit `chore: v2 scaffold + legacy archive`

## 2. Stage 0：评测先行（Week 0.5）

- [ ] 2.1 设计并实现 `tests/eval/schema.py`：GoldenCase Pydantic 模型
- [ ] 2.2 撰写 `tests/eval/golden_set.jsonl`：50 条 case（单表 10 / 多表 join 10 / 时间窗口 10 / 数值差异 10 / 边界 10）
- [ ] 2.3 实现 `tests/eval/metrics.py`：Exec-Accuracy（结果集 hash）/ Semantic-Match（LLM-as-Judge）/ Latency / Token Cost
- [ ] 2.4 实现 `tests/eval/runner.py`：支持 `--target v1|v2 --compare v1` 参数，输出 markdown 报告到 `reports/`
- [ ] 2.5 准备测试数据：在 SQLite 中生成对账测试库（orders / refunds / payments 三表 + 7 天数据）
- [ ] 2.6 跑通 v1 baseline：`python -m tests.eval.runner --target v1` → 生成 `reports/v1_baseline.md`
- [ ] 2.7 集成到 CI（GitHub Actions）：每次 push 跑全集，accuracy 下降 > 2% 失败

## 3. Stage 1：核心抽象重塑（Week 1）

### 3.1 LLM Gateway 与基础设施
- [ ] 3.1.1 实现 `recon_v2/infra/llm_gateway.py`：基于 LiteLLM 的统一接口，含 chat / embedding 方法
- [ ] 3.1.2 实现 `recon_v2/infra/cache.py`：CacheBackend 抽象（Redis + InMemory），自动降级
- [ ] 3.1.3 实现 query 指纹（SHA256 of messages + temperature + model），cache.get / cache.set
- [ ] 3.1.4 实现 retry（指数退避，默认 3 次，初始 1s）+ 30s 单次超时
- [ ] 3.1.5 实现 CostTracker 类：按 trace_id 累计 token / cost_usd
- [ ] 3.1.6 单元测试：cache hit/miss、provider 切换、retry 触发

### 3.2 SQL 安全护栏
- [ ] 3.2.1 实现 `recon_v2/infra/sql_safety.py`：`is_safe(sql, dialect)` 函数
- [ ] 3.2.2 verb 白名单：ALLOWED_VERBS = {"SELECT", "WITH"}
- [ ] 3.2.3 DANGEROUS_NODES 子树扫描：Delete / Update / Insert / Drop / Alter / Create / TruncateTable
- [ ] 3.2.4 单元测试：10 条已知注入攻击用例全部拦截（含注释绕过 / Unicode 同形）
- [ ] 3.2.5 集成 EXPLAIN 预校验：在 sql_runner 中先 explain 再 execute

### 3.3 OpenTelemetry Tracing
- [ ] 3.3.1 实现 `recon_v2/infra/tracing.py`：init_tracing(service_name) + tracer + traced 装饰器
- [ ] 3.3.2 配置 OTLP exporter 上报到 Phoenix（localhost:6006）
- [ ] 3.3.3 集成 openinference 语义约定（LLM_PROMPTS / LLM_TOKEN_COUNT_PROMPT 等）
- [ ] 3.3.4 在 llm_gateway.chat / sql_runner.run / rag.retrieve 上加 @traced 装饰器
- [ ] 3.3.5 启动本地 Phoenix 容器，验证 trace 上报成功

### 3.4 Tool 系统
- [ ] 3.4.1 实现 `recon_v2/tools/base.py`：ToolBase / ToolInput / ToolOutput 抽象 + to_openai_function()
- [ ] 3.4.2 实现 `recon_v2/tools/registry.py`：ToolRegistry 注册 / 查询 / filter_by_intent
- [ ] 3.4.3 实现 sql_runner：接入 sql_safety + SQLAdapter
- [ ] 3.4.4 实现 diff_calculator：两结果集对比，输出 diff 结构
- [ ] 3.4.5 实现 report_generator：Markdown / JSON 双格式
- [ ] 3.4.6 实现 case_query：Episodic Memory 检索（Memory v2 完成后接入）
- [ ] 3.4.7 实现 rag_searcher：调 HybridRetriever（Stage 3 完成后接入）
- [ ] 3.4.8 单元测试：每个 Tool 校验输入校验 / 失败处理 / OTel span

### 3.5 SQLAdapter
- [ ] 3.5.1 实现 `recon_v2/adapters/base.py`：SQLAdapter 抽象
- [ ] 3.5.2 实现 `recon_v2/adapters/sqlite_adapter.py`：execute / explain 方法
- [ ] 3.5.3 单元测试：基本 SELECT / EXPLAIN 失败处理

### 3.6 Stage 1 收尾
- [ ] 3.6.1 跑 Golden Set 验证 infra 层接入无回归
- [ ] 3.6.2 Phoenix UI 截图存档到 `docs/v2/screenshots/`
- [ ] 3.6.3 打 tag `v2-stage-1-{date}`

## 4. Stage 2：LangGraph Orchestration（Week 2-3.5）

### 4.1 Day 1 PoC 阀门
- [ ] 4.1.1 LangGraph minimal hello-world：3 个 Node，跑通 stateful 流转 + checkpointer
- [ ] 4.1.2 若 PoC 失败 → 切换方案 B：自研 mini state machine，仍用 AgentContext 模式

### 4.2 GraphState 与 AgentContext
- [ ] 4.2.1 实现 `recon_v2/orchestration/state.py`：GraphState Pydantic 模型
- [ ] 4.2.2 实现 `recon_v2/core/context.py`：AgentContext dataclass
- [ ] 4.2.3 实现 `recon_v2/core/budget.py`：CostBudget 类（token 上限 / 时间上限 / step 上限）
- [ ] 4.2.4 实现 `recon_v2/core/types.py`：Intent / GoldenCase 等共享类型

### 4.3 6 个 Node 实现
- [ ] 4.3.1 实现 route_node：keyword 双通道 + LLM 分类，输出 (intent, confidence)
- [ ] 4.3.2 实现 clarify_node：confidence < 0.6 时生成澄清问题
- [ ] 4.3.3 实现 plan_node：复杂 query 走 PlanSolve 多步规划，简单退化 ReAct
- [ ] 4.3.4 实现 act_node：tool selection + 调用 + 失败重试
- [ ] 4.3.5 实现 observe_node：解析结果 + 决定下一跳（act / reflect / END）
- [ ] 4.3.6 实现 reflect_node：异步调 AsyncSkillQueue.submit（Stage 4 接入）

### 4.4 Graph 装配
- [ ] 4.4.1 实现 `recon_v2/orchestration/graph.py`：build_graph(ctx) → compiled StateGraph
- [ ] 4.4.2 配置 conditional edges：route → clarify/plan、observe → act/reflect/END
- [ ] 4.4.3 接入 SqliteSaver checkpointer
- [ ] 4.4.4 Budget 守门员：每个 Node 入口检查 ctx.budget.exceeded()
- [ ] 4.4.5 模式切换：act 检测 step > REACT_MAX_STEPS 切到 plan_solve

### 4.5 验证与收尾
- [ ] 4.5.1 端到端 demo：跑 5 条 query，确认 trace 完整
- [ ] 4.5.2 checkpointer 中断恢复测试
- [ ] 4.5.3 跑 Golden Set 全集，目标 exec_accuracy ≥ 0.80
- [ ] 4.5.4 生成 `reports/v2_stage2_vs_v1.md`
- [ ] 4.5.5 打 tag `v2-stage-2-{date}`

## 5. Stage 3：Hybrid RAG（Week 3.5-4.5）

### 5.1 离线 Indexer
- [ ] 5.1.1 实现 `recon_v2/rag/chunker.py`：表 schema / 业务文档分块
- [ ] 5.1.2 实现 `recon_v2/rag/indexer.py` CLI：`python -m recon_v2.rag.indexer --source ... --collection ...`
- [ ] 5.1.3 集成 Qdrant client：创建 collection（vector_size=512）+ 批量 upsert
- [ ] 5.1.4 准备初始数据：测试库的 3 张表 schema + 业务说明文档

### 5.2 双通道 Retriever
- [ ] 5.2.1 实现 `recon_v2/rag/retriever.py` 的 Dense 通道（bge-small-zh + Qdrant）
- [ ] 5.2.2 实现 Sparse 通道（rank_bm25 in-memory）
- [ ] 5.2.3 实现 RRF 融合：`1/(60+rank)` 累加排序
- [ ] 5.2.4 服务降级：Qdrant 不可达时只用 BM25 + span attribute degraded=true

### 5.3 Cross-Encoder Reranker
- [ ] 5.3.1 实现 `recon_v2/rag/reranker.py`：Cross-Encoder（bge-reranker-v2-m3）封装
- [ ] 5.3.2 在 top-10 上 rerank → top-3
- [ ] 5.3.3 备选远程 rerank（cohere）的 fallback 开关

### 5.4 RAG-as-Tool 集成
- [ ] 5.4.1 完善 rag_searcher tool：调 HybridRetriever
- [ ] 5.4.2 在 act_node 工具列表中注册 rag_searcher
- [ ] 5.4.3 测试 Agent 主动调用：手工跑一条 query，trace 中出现 rag.search span

### 5.5 检索质量评估
- [ ] 5.5.1 给 Golden Set 30 条 case 加 retrieval_label（标注哪些 doc 应该被召回）
- [ ] 5.5.2 实现 `tests/eval/rag_eval.py`：计算 MRR@5 / Recall@10
- [ ] 5.5.3 跑评估，目标 MRR@5 ≥ 0.55（v1 baseline ≈ 0.4）
- [ ] 5.5.4 生成 `reports/v2_stage3_rag.md`
- [ ] 5.5.5 打 tag `v2-stage-3-{date}`

## 6. Stage 4：Memory v2 + Self-Evolution（Week 4.5-6）

### 6.1 SQLite Schema 与 ORM
- [ ] 6.1.1 实现 SQLAlchemy 模型：`episodic_case` / `semantic_rule` / `skill` / `trace_record` 四表
- [ ] 6.1.2 配置 Alembic migrate
- [ ] 6.1.3 创建索引：idx_episodic_intent、idx_episodic_used、idx_semantic_active

### 6.2 三层 Memory 实现
- [ ] 6.2.1 实现 `recon_v2/memory/working.py`：LRU 20 + 内存 dict
- [ ] 6.2.2 实现 `recon_v2/memory/episodic.py`：SQLite case 表 CRUD + embedding 索引
- [ ] 6.2.3 实现 `recon_v2/memory/semantic.py`：SQLite rule 表 CRUD
- [ ] 6.2.4 实现 `recon_v2/memory/store.py`：MemoryStore 统一接口（write / query / promote / decay）

### 6.3 Promotion 与 Consolidation
- [ ] 6.3.1 实现 `recon_v2/memory/promotion.py`：重要性打分函数 + 阈值提升
- [ ] 6.3.2 实现 `recon_v2/memory/consolidation.py`：定期 Job + 聚类 + LLM 归纳 prompt
- [ ] 6.3.3 实现衰减 Job：30 天未用 + conf<0.5 → archived=1
- [ ] 6.3.4 配置 APScheduler 或 cron 定时任务

### 6.4 Self-Evolution 三道门
- [ ] 6.4.1 实现 `recon_v2/evolution/reviewer.py`：从 execution_trace 提炼候选 skill
- [ ] 6.4.2 实现 Dedup 门：embedding 相似度 > 0.85 拒绝
- [ ] 6.4.3 实现 Critic 门：LLM 三维评分（具体性 / 可复用性 / 正交性）+ 加权阈值 0.7
- [ ] 6.4.4 实现 `recon_v2/evolution/sandbox.py`：抽样 Golden Set 10 条 dry-run + 准确率对比
- [ ] 6.4.5 实现 `recon_v2/evolution/governance.py`：Wilson Score 动态 confidence

### 6.5 异步队列与持久化
- [ ] 6.5.1 实现 `recon_v2/evolution/queue.py`：AsyncSkillQueue（producer-consumer pattern）
- [ ] 6.5.2 主线程 submit 立即返回，worker 线程消费
- [ ] 6.5.3 进程退出前 flush 队列（避免丢失）

### 6.6 集成与验证
- [ ] 6.6.1 reflect_node 接入 AsyncSkillQueue.submit
- [ ] 6.6.2 Skill KB 走 RAG 检索：top-3 注入 prompt（替代全量拼接）
- [ ] 6.6.3 跑 Golden Set 3 轮，验证自演进准确率单调上升（80 → 85 → 88）
- [ ] 6.6.4 验证 Skill 入库通过率 ≤ 50%（证明三道门有效）
- [ ] 6.6.5 生成 `reports/v2_stage4_evolution.md`
- [ ] 6.6.6 打 tag `v2-stage-4-{date}`

## 7. Stage 5：生产化封装（Week 6-6.5）

### 7.1 FastAPI 接入层
- [ ] 7.1.1 实现 `apps/api/main.py`：FastAPI app + 路由注册
- [ ] 7.1.2 实现 POST /query：SSE 流式输出中间状态
- [ ] 7.1.3 实现 GET /trace/{trace_id}：从 trace_record 表查 + 返回 Phoenix 链接
- [ ] 7.1.4 实现 GET /metrics：Prometheus 文本格式
- [ ] 7.1.5 实现 GET /health：依赖检查（Qdrant / Redis / Phoenix 状态）

### 7.2 Prometheus Metrics
- [ ] 7.2.1 接入 prometheus_client 库
- [ ] 7.2.2 埋点：query_total / latency_histogram / token_total / cost_usd_total / eval_pass_rate

### 7.3 容器化
- [ ] 7.3.1 编写 `deploy/Dockerfile`：multi-stage build，slim image
- [ ] 7.3.2 编写 `deploy/docker-compose.yml`：app + Qdrant + Redis + Phoenix
- [ ] 7.3.3 测试 `docker-compose up -d` 全栈启动
- [ ] 7.3.4 测试 Degraded Mode：手工停 Redis 验证 app 不挂

### 7.4 CLI Demo
- [ ] 7.4.1 实现 `apps/cli/demo.py`：interactive REPL 模式
- [ ] 7.4.2 支持 `--query` 单次执行

### 7.5 文档
- [ ] 7.5.1 撰写 `docs/v2/runbook.md`：部署 / 环境变量 / 故障排查
- [ ] 7.5.2 撰写 5 条 ADR 到 `docs/v2/adr/`：LangGraph 选择 / Hybrid RAG / Memory 分层 / Sandbox 三道门 / sqlglot AST
- [ ] 7.5.3 重写 `README.md`：v2 简历叙事框架 + Quick Start + 架构图 + Phoenix 截图
- [ ] 7.5.4 生成最终对比报告 `reports/v1_vs_v2_final.md`

### 7.6 Stage 5 收尾
- [ ] 7.6.1 干净环境验证 Quick Start 可重现
- [ ] 7.6.2 打 tag `v2-stage-5-{date}`
- [ ] 7.6.3 push 到 GitHub 公开仓库

## 8. 收尾与归档

- [ ] 8.1 OpenSpec archive：`openspec archive sql-recon-agent-v2-rebuild`
- [ ] 8.2 在 README 中加入"项目阶段：v2 稳定版"标注
- [ ] 8.3 整理简历素材到 `docs/v2/resume-points.md`
