## SQL 对账 Agent 平台 — 实现备忘

### 双层 Agent 架构：Plan & Solve + ReAct

todo：搭建双层 Agent 架构，Plan & Solve 负责全局任务拆解与跨表对账编排，ReAct 负责单步 SQL 生成、执行与自动纠错

done：
1. 基于 PlanAndSolveAgent 基类实现 ReconciliationPlanAndSolveAgent
2. Planner 使用对账专用 Prompt，将复杂需求拆解为独立可执行子任务
3. Executor 集成对账工具注册表（SQLTool + DiffTool + ReportTool）
4. 意图路由自动分流：简单查询（schema/ad-hoc）跳过 Planner，直接 ReAct 执行；复杂对账走 Plan & Solve 全链路

```python
# 核心调用
from hello_agents.agents.plan_solve_recon_agent import ReconciliationPlanAndSolveAgent

agent = ReconciliationPlanAndSolveAgent(
    name="对账分析师",
    llm=llm,
    db_path="data/mock_reconciliation.db",
    max_plan_steps=6,
)
result = agent.run("对比昨天GMV和订单差异")
```

failed：
- 无需处理

pending：
- Planner 子任务间的依赖关系管理（当前为线性执行）
- 并行子任务优化

---

### 三层记忆系统：Working / Episodic / Semantic

todo：构建 Working/Episodic/Semantic 三层记忆，支持跨会话上下文保留与知识积累

done：
1. **Working Memory**：内存 dict，存储当前对账会话的 SQL、中间结果、状态，会话结束后自动清空
2. **Episodic Memory**：JSON 文件持久化（memory_store/episodic.json），存储历史对账案例，支持关键词检索，上限 500 条自动截断
3. **Semantic Memory**：结构化知识库（memory_store/semantic.json），存储表结构 Schema、术语 → 字段映射、业务规则
4. `bootstrap_schema_knowledge()` 自动从 SQLite 数据库提取表结构初始化语义记忆
5. `episodic_search()` 支持关键词搜索历史案例

```python
# 初始化
memory = MemoryTool(store_dir="memory_store")
memory.bootstrap_schema_knowledge("data/mock_reconciliation.db")

# 写入工作记忆
memory.working_set("current_sql", "SELECT * FROM live_gmv...")

# 搜索历史案例
cases = memory.episodic_search("GMV 差异")

# 术语映射
memory.semantic_set("term:GMV", {"field": "gmv", "table": "live_gmv"})
```

failed：
- 无需处理

pending：
- Working Memory 超时自动清理
- Episodic Memory 重要性衰减（旧案例降权）

---

### 技能积累：Skill Reviewer 异步提炼

todo：对话经验经 Skill Reviewer 异步提炼后写入 Skill 库，下次会话自动加载，Agent 能力随使用持续累积

done：
1. **SkillReviewer.review()** 在每次 Agent 执行完成后调用，异步（daemon 线程）提取技能
2. 三类技能提取：
   - **sql_pattern**：从执行轨迹中用正则提取成功的 SQL 模板
   - **rule**：提取差异判断阈值和规则
   - **term_mapping**：提取跨表列名对应关系（如 total_gmv ⟷ total_order）
3. Skill 库持久化到 skill_library/_index.json，支持去重更新
4. `find_skills()` 支持关键词检索 + 使用频率加权排序
5. 意图路由集成：每次路由时加载匹配的 Skill 作为 few-shot 参考

```python
reviewer = SkillReviewer(skill_dir="skill_library")

# Agent 执行后异步审查
reviewer.review(
    query="对比GMV和订单差异",
    execution_trace=captured_output,
    final_result=result,
    async_mode=True,  # 非阻塞
)

# 检索技能
skills = reviewer.find_skills("GMV 对账", category="sql_pattern")
```

failed：
- 无需处理

pending：
- LLM 驱动的技能质量评分（当前为规则提取）
- 技能冲突检测（两个相似技能的合并）

---

### SQL 多引擎适配器：Hive / ClickHouse / SQLite

todo：构建多引擎 SQL 适配层，支持跨异构数据源（Hive / ClickHouse / SQLite）统一查询接口

done：
1. `DataSourceConnector` 抽象基类，定义 get_tables / get_schema / execute / validate 四个标准接口
2. `SQLiteConnector`：完整实现（Demo 用），基于 sqlite3 原生接口
3. `HiveConnector`：生产桩（连接 Thriftserver，待替换 PyHive 实现）
4. `ClickHouseConnector`：生产桩（HTTP/TCP 接口，待替换 clickhouse-driver）
5. `SQLAdapter` 统一路由：按数据源名称注册和切换连接器

```python
from hello_agents.tools.builtin.sql_adapter import SQLAdapter, SQLiteConnector, HiveConnector

adapter = SQLAdapter()
adapter.register("sqlite", SQLiteConnector("data/mock_reconciliation.db"))
adapter.register("hive", HiveConnector(host="hive-prod", port=10000))

# 统一调用
tables = adapter.get_tables(source="hive")
schema = adapter.get_schema("live_gmv", source="sqlite")
```

failed：
- HiveConnector / ClickHouseConnector 为生产桩，需替换实际驱动

pending：
- 连接池管理
- SQL 方言转换（Hive → ClickHouse 语法适配）

---

### RAG 增强：Qdrant 表结构语义检索 + Query Rewrite

todo：基于 Qdrant 向量库存储表结构文档，将业务术语映射为精确字段，支持 Hybrid Search

done：
1. `TableDocRetriever` 双模式检索器
2. **本地关键词模式**（默认）：加载 knowledge_base/table_docs/*.md，Jaccard + 中文 2-gram 分词匹配
3. **Qdrant 向量模式**（可选）：需要 qdrant-client + embedding API，支持语义检索
4. `rewrite_query()` 从文档中自动提取术语 → 字段映射（如 "GMV" → "live_gmv.gmv"）
5. `index_documents()` 批量索引导入 Qdrant

```python
from hello_agents.tools.builtin.rag_retriever import TableDocRetriever

retriever = TableDocRetriever(
    doc_dir="knowledge_base/table_docs",
    use_qdrant=False,  # 本地模式
)

docs = retriever.search("GMV 是什么字段")
rewritten = retriever.rewrite_query("查 GMV 和订单金额的差异")
# → "查 GMV(live_gmv.gmv) 和 订单金额(order_amount.total_amount) 的差异"
```

failed：
- Qdrant 模式需要 embedding API，Demo 用本地关键词模式替代

pending：
- Hybrid Search 混合检索（向量 + 关键词融合排序）
- 文档自动更新（监听表结构变更）
