## Context

SQL Reconciliation Agent v1 已落地基本功能（NL → SQL → 执行 → 差异分析），但在严肃架构审查下暴露出 14 项设计缺陷：双层 Agent 不共享 context、三层记忆没有 promotion、自进化无 governance、SQL 黑名单不抗注入、全栈无可观测和评测体系等。

项目当前规模：
- 仓库地址：`Marbacj/SQL-Reconciliation-Agent`
- 现有代码：自研 `recon_core` 框架 + ReconciliationAgent + 7 个工具 + 4 条 demo
- 团队：单人开发（mabohui）
- 用例：个人面试简历项目，需要支撑严肃面试官的深度技术追问
- 时间约束：~6.5 周

详细背景见 [proposal.md](./proposal.md) 与 [docs/v2/architecture.md](../../../docs/v2/architecture.md)。

## Goals / Non-Goals

**Goals:**
- 用 LangGraph 替代自研编排，实现单一 AgentContext 贯穿全程的 5-Node 状态机
- 重构 Tool 系统为 Pydantic Schema，废弃 `@tool_action` 反射魔法
- RAG 从"prompt 拼接"升级为"RAG-as-Tool + Hybrid + Rerank"
- Memory 从 JSON 文件升级为 SQLite + promotion / consolidation / 衰减机制
- 自进化引入 Dedup + Critic + Sandbox 三道门，确保只有高质量 skill 入库
- SQL 安全护栏从黑名单升级为 sqlglot AST 解析
- 引入 OpenTelemetry 全链路 trace + Phoenix UI 可视化
- 构建 50 条 Golden Set + 4 维 metric，每次架构改动 regression 验证
- 提供 FastAPI + docker-compose 一键启动方案
- 总周期 6.5 周内完成 Stage 0-5

**Non-Goals:**
- 不追求生产 SLA（无限流 / 灰度 / 多租户隔离）
- 不实现多用户认证授权
- 不支持除 SQLite 外的真实数据源（MySQL / ClickHouse 留接口但不开发）
- 不做前端 UI（仅 CLI + FastAPI API + Phoenix UI 即可）
- 不优化 LLM 模型本身（不微调、不蒸馏）
- 不替换业务逻辑（差异计算、报告生成等保持现有语义）

## Decisions

### D1：编排框架选择 LangGraph 而非保留自研

**决策**：废弃 `recon_core/core/agent.py` 自研编排，全量迁移至 LangGraph StateGraph。

**理由**：
- LangGraph 提供工业标准的 state machine 抽象 + 内置 checkpoint + 可视化
- 自研 mini state machine 实现 checkpoint / 中断恢复需要再花 4 周以上
- 面试场景下"为什么不用 LangGraph"是必问题，自研叙事容易被质疑工程判断力
- LangGraph 与 LangChain 生态对接更顺畅（LiteLLM / Phoenix 都有现成 integration）

**替代方案**：
- AutoGen：多 Agent 协作强，但 state machine 不显式，调试困难
- CrewAI：场景偏多 Agent role-play，对单 Agent 编排不友好
- 自研 mini state machine：实现成本高，简历可信度低

### D2：单一 AgentContext 而非每个模式独立依赖

**决策**：定义 `AgentContext` 数据类，持有 trace_id / memory / rag / tools / llm / tracer / budget / step_counter / mode，所有 Node 和 Tool 共享同一份 context。

**理由**：
- 解决 v1 双层 Agent 各自初始化依赖、无法共享中间状态的问题
- 模式切换（ReAct ⇄ PlanSolve）由 step_counter + complexity_score 触发，不需要重新建 Agent
- Budget 作为一等公民贯穿全程，超限可在任意 Node 终止
- trace_id 串联所有 LLM / Tool / RAG 调用，与 OpenTelemetry 天然对接

**替代方案**：
- 每个 Node 用全局变量：测试困难、并发不安全
- LangChain 的 `RunnableConfig`：偏 callback 风格，类型不显式

### D3：Tool 用 Pydantic Schema 而非 `@tool_action` 反射

**决策**：所有工具实现 `ToolBase` 抽象，输入用 Pydantic `BaseModel` 显式声明 schema。

**理由**：
- IDE 类型提示 / 断点调试可用
- 直接调 `.model_json_schema()` 转 OpenAI Function Calling schema
- 业界主流做法（LangChain `StructuredTool` / Pydantic-AI），面试官好理解
- 反射魔法节省的代码量不抵带来的可维护性损失

**替代方案**：
- 保留 `@tool_action`：v1 已证明在工具数 < 10 时无收益
- Function 直接转 schema（OpenAI 原生）：缺少校验和默认值机制

### D4：Hybrid RAG（BM25 + Dense + RRF + Rerank）而非单通道

**决策**：BM25 和 Dense 永远并存，用 RRF 融合，再用 Cross-Encoder Rerank 精排，最后 RAG-as-Tool 让 Agent 主动调用。

**理由**：
- 长尾 query / 术语精确匹配 BM25 优于 Dense；语义相似度 Dense 优于 BM25 → 必须 hybrid
- RRF（k=60）是业界验证过的简单有效融合方法
- Cross-Encoder Reranker 在 top-10 上做二次精排，MRR 提升显著
- RAG-as-Tool 让 Agent 决定何时检索，避免无意义检索浪费 token

**替代方案**：
- 单 Dense：术语精确匹配差
- 单 BM25：语义相似度差
- 在 v1 那样根据"是否安装 qdrant"做 fallback：这是降级不是混合

### D5：Memory v2 用 SQLite + promotion 机制

**决策**：
- Working：内存 LRU 20 条
- Episodic：SQLite，含 success_count / fail_count / last_used_at / embedding
- Semantic：SQLite，含 confidence / usage_count / archived
- Promotion：重要性打分 > 阈值 → Episodic；重复 case ≥ 5 次 → LLM Consolidation → Semantic
- Decay：30 天未用 + confidence < 0.5 → 归档

**理由**：
- SQLite 提供 schema 化存储 + 索引 + 并发安全，比 JSON 文件好得多
- 三层之间有真正的提升路径，回答了 "Episodic 怎么 consolidate 到 Semantic" 的标准提问
- 衰减机制阻止知识库无限膨胀

**替代方案**：
- 继续 JSON：并发不安全、无索引、无 schema
- DuckDB：embedded 但无 vector 支持，与 Qdrant 重复
- Redis：纯 KV，做不了关系查询

### D6：Self-Evolution 三道门（Dedup + Critic + Sandbox）

**决策**：候选 skill 入库前必须通过：
1. **Dedup**：embedding 余弦相似度 > 0.85 视为重复，丢弃
2. **Critic**：LLM self-evaluation，按"具体性 / 可复用性 / 正交性"打分，< 0.7 丢弃
3. **Sandbox**：在 Golden Set 抽样 10 条上 dry-run，准确率不下降（容差 -2%）才入库

**理由**：
- 这是 v1 自进化的最大短板，必须解决"垃圾经验污染知识库"
- 三道门职责正交，避免单一过滤误判
- Sandbox 是核心创新点，自进化第一次有 governance，面试讲故事价值最大

**替代方案**：
- 只 dedup：垃圾经验照样入库
- 只 critic：critic 本身可能有偏差
- 只 sandbox：sandbox 慢，没必要让明显重复的 skill 跑 sandbox

### D7：SQL 安全用 sqlglot AST 而非黑名单

**决策**：所有 SQL 在执行前用 sqlglot 解析为 AST，校验根 verb 是否在白名单（SELECT / WITH），子树扫描是否含危险节点（Delete / Update / Insert / Drop / Alter / Create / TruncateTable）。

**理由**：
- 黑名单方案对注释绕过、Unicode 同形字符无能为力
- AST 解析是真正解释 SQL 语义，不可绕过
- sqlglot 支持多方言（sqlite / mysql / postgres / clickhouse），未来切换数据源不用改代码

**替代方案**：
- 关键字黑名单：v1 方案，已证明不安全
- 用数据库账号权限控制（GRANT SELECT only）：依赖运维，不可移植
- 让 LLM 判断：太不可靠

### D8：可观测用 OpenTelemetry + Phoenix 而非 LangSmith

**决策**：使用 OpenTelemetry SDK + Phoenix（Arize 开源 LLM observability UI）做全链路 trace。

**理由**：
- OTel 是 CNCF 标准，可切换厂商
- Phoenix 是开源 + 本地部署，无 vendor lock-in
- LangSmith 是 SaaS，数据上云有顾虑
- openinference 提供 LLM 专属语义，结合 OTel 既标准又有 LLM 特色

**替代方案**：
- LangSmith：SaaS lock-in，但 UI 体验更好
- 自研 print + stdout tee：v1 方案，生产无法 debug
- Helicone / Langfuse：商业偏多，本地不友好

### D9：评测先行 Eval-Driven 改造

**决策**：Stage 0 必须先沉淀 50 条 Golden Set + 实现 Eval Harness，后续所有 Stage 完成后必跑 regression。

**理由**：
- 没有评测，任何架构改动都是手感工程
- 50 条 case 在覆盖度和工作量之间平衡（个人项目）
- 4 维 metric（Exec-Accuracy / Semantic-Match / Latency / Token Cost）覆盖功能 + 性能 + 成本
- 自然产出"v1 vs v2 对比报告"作为简历素材

**替代方案**：
- 不做评测：靠"看着对"判断（不可接受）
- 跑 BIRD / Spider 公开评测集：覆盖业务对账场景差
- 100+ 条：超出个人项目工作量

### D10：v1 归档至 `legacy/` 而非删除

**决策**：v1 全部代码迁移至 `legacy/` 目录，标记只读，作为 v2 改造的对照基线。

**理由**：
- 每个 Stage 结束都要在 Golden Set 上跑 v1 vs v2 对比
- 简历中"我从 v1 自研切到 v2 工业实践"的叙事需要 v1 代码可查
- 删除丢失了"成长叙事"的素材
- Git history 也能找回，但目录里直接可见更高效

**替代方案**：
- 直接删除：丢失对比基线
- Git tag 标记：找起来不直观

## Risks / Trade-offs

### R1：LangGraph 范式跳跃成本 → Stage 2 Day 1 PoC 阀门
- **风险**：第一次使用 LangGraph 容易在 conditional edges / checkpointer 上踩坑
- **缓解**：Stage 2 第一天做 minimal hello-world PoC，1 天跑不通立刻切换方案 B（自研 mini state machine + 同样的 AgentContext 模式），不损失架构成果

### R2：LLM Consolidation Prompt 难调 → 多轮迭代 + Golden Set 验证
- **风险**：Episodic → Semantic 的归纳 prompt 需要反复试，可能产出"不具体 / 重复 / 矛盾"的规则
- **缓解**：Stage 4 预留 +3 天 buffer，每轮归纳完跑 Golden Set 子集验证不退化，关键 prompt 写入 Critic 评分维度

### R3：Cross-Encoder 本地推理慢 → 备选远程 Rerank API
- **风险**：bge-reranker-v2-m3 在 CPU 上单次 rerank 可能要 1-2 秒，影响端到端延迟
- **缓解**：备选 cohere rerank API / Jina rerank（按需选用），离线推理也可以预热到 GPU 容器

### R4：Sandbox 验证耗时 → 抽样 10 条而非全集
- **风险**：每次 skill 入库都跑 50 条 Golden Set 太慢（~30 分钟）
- **缓解**：抽样 10 条代表性 case（按 intent_label 分层抽样），3-5 分钟内完成；夜间跑全量回归

### R5：50 条 Golden Set 覆盖不全 → 后期补到 80 条
- **风险**：50 条可能漏关键 corner case
- **缓解**：Stage 0 先 50 条上线，Stage 1-5 期间发现 corner case 持续补充至 80 条上限

### R6：外部依赖增多（Qdrant / Redis / Phoenix）→ docker-compose 一键启动
- **风险**：环境部署门槛升高，README 演示可能跑不起来
- **缓解**：docker-compose up -d 一键启动全栈；提供 degraded mode（Redis 降级到内存、Phoenix 关闭仍可工作）

### R7：时间超期 → 砍 Stage 5 Prometheus 与运维文档
- **风险**：6.5 周时间紧，最容易超期
- **缓解**：每 Stage 留 1-2 天 buffer；最差情况砍掉 Stage 5 的 Prometheus metrics + runbook，保留 docker-compose + README，不影响简历叙事

### R8：模式切换逻辑复杂 → 简化为单方向 ReAct → PlanSolve
- **风险**：双向切换（PlanSolve 降级 ReAct）实现复杂
- **缓解**：v2 只实现单向（ReAct 步数 > N 升级 PlanSolve），双向放 v3

## Migration Plan

### M1：v1 代码归档（Stage 0 第一天）
1. `git mv recon_core/ legacy/recon_core/`
2. `git mv examples/ legacy/examples/`
3. 给 `legacy/README.md` 加入"已废弃，仅作 v2 对比基线"标注
4. 不动 `docs/`，保留 `docs/v2/` 作为新方案文档

### M2：v2 目录骨架（Stage 0 第二天）
按 `docs/v2/architecture.md` 第 3.2 节目录树创建空文件夹和 `__init__.py`，提交一次 baseline commit。

### M3：Stage 间增量替换
- Stage 0：评测框架独立可跑（不依赖 v2 代码）
- Stage 1：infra 层先行，对外暴露干净接口
- Stage 2：capability 层逐个接入
- Stage 3-4：复杂能力一次一个 capability
- Stage 5：接入层封装

### M4：回滚策略
- 每个 Stage 完成都打 tag（`v2-stage-{n}-{date}`）
- 任意 Stage 评测全面退化 → checkout 上一个 tag
- v1 始终保留在 `legacy/`，最坏情况整套切回 v1

## Open Questions

### Q1：是否需要支持非 SQLite 的 SQL 适配器？
- **当前倾向**：保留 `recon_v2/adapters/base.py` 抽象但仅实现 SQLiteAdapter
- **决策时点**：Stage 1 开始时
- **影响范围**：sql_runner 工具的输入参数

### Q2：是否要做前端 UI？
- **当前倾向**：不做，Phoenix UI 即可演示 trace；FastAPI 用 Postman 演示
- **决策时点**：Stage 5
- **影响范围**：apps/ 目录是否新增 web/

### Q3：模型 provider 默认选哪个？
- **当前倾向**：默认 DeepSeek（成本低），可切换 OpenAI / Claude
- **决策时点**：Stage 1 LLM Gateway 实现时
- **影响范围**：LiteLLM 配置 + .env 模板

### Q4：是否将 Golden Set 公开到 GitHub？
- **当前倾向**：公开 30 条通用案例，敏感业务案例放本地 `.gitignore`
- **决策时点**：Stage 0
- **影响范围**：简历背书力度

### Q5：是否引入 LangFuse 做生产 metrics 而非自己埋 Prometheus？
- **当前倾向**：Stage 5 评估，时间不够就先用 Phoenix + 简易日志
- **决策时点**：Stage 5 开始时
- **影响范围**：deploy/docker-compose.yml
