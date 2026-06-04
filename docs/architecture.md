# 架构设计文档

## 总览

SQL Reconciliation Agent 采用**双层架构**：

```
recon_core/     ← Agent 基础框架层（可复用的 Agent Infra）
recon_v2/       ← 对账业务编排层（LangGraph 状态机）
apps/           ← 接入层（API / UI / CLI）
```

---

## 一、LangGraph 状态机

### 节点设计

```
Entry
  │
  ▼
route_node          意图识别 → simple_query / multi_table / time_window / recon / boundary
  │
  ├── clarify_node  意图不明确时反问用户
  │
  └── plan_node     任务分解 + Schema RAG 检索
        │
        ▼
       act_node     工具执行（SQLRunner / DiffCalc / Reporter）
        │
        ▼
    observe_node    结果观察：成功 → reflect | SQL错误 → act（最多3次）
        │
        ▼
    reflect_node    反思总结 → 输出报告 → END
```

### 状态流转

```python
GraphState = {
    "query": str,           # 用户原始问题
    "intent": str,          # 识别到的意图
    "plan": List[str],      # 分解后的步骤
    "act_result": dict,     # 工具执行结果
    "obs_count": int,       # 重试计数
    "last_sql_error": str,  # 上次SQL错误（反馈给LLM修复）
    "final_report": str,    # 最终报告
}
```

---

## 二、Agent 层次结构

```
recon_core/agents/
├── SimpleAgent          最简 Chain：用户输入 → LLM → 输出
├── ReActAgent           ReAct 循环：Thought → Action → Observation
├── ReflectionAgent      带反思的 Agent：执行 → 自我评估 → 改进
└── PlanSolveAgent       先规划再执行：分解任务 → 逐步执行 → 汇总
```

**ReAct 循环核心：**

```
while not done:
    thought = LLM.think(context + history)
    action  = LLM.select_tool(thought)
    result  = ToolRegistry.execute(action)
    context.append(Observation(result))
    if LLM.is_terminal(thought):
        break
```

---

## 三、工具系统（Tool Registry）

### Expandable Tool 模式

每个 Tool 是一个可展开的工具集，通过装饰器将多个子动作收敛到一个类：

```python
class SQLTool(ExpandableTool):
    @tool_action("sql_schema")
    def schema(self, table: str) -> str:
        # PRAGMA table_info + 示例数据
    
    @tool_action("sql_execute")  
    def execute(self, sql: str) -> str:
        # SELECT 执行（内置 DDL/DML 安全拦截）
    
    @tool_action("sql_validate")
    def validate(self, sql: str) -> str:
        # EXPLAIN 语法校验，不实际执行
```

### 注册的工具

| 工具 | 作用 |
|------|------|
| `sql_runner` | SQL 执行，支持参数化查询 |
| `diff_calculator` | FULL OUTER JOIN 双表差异比对 |
| `rag_searcher` | 向量检索表结构和业务规则 |
| `schema_inspector` | 实时 PRAGMA/DESC 查询 |
| `report_generator` | Markdown 报告生成与归档 |
| `case_query` | 历史对账案例检索 |

### 熔断器

每个工具调用都有熔断保护：

```
正常状态 → 调用失败 → 半开状态（1次试探）→ 恢复/断开
                                              ↓ 断开
                                         降级响应（缓存/空值）
```

---

## 四、SQL 自动修复机制

这是本项目的核心技术亮点之一：

```
act_node: 执行 SQL
    │
    ▼ SQL 失败（语法错误 / 表名错误 / 字段不存在）
    │
observe_node: 检测错误类型
    │
    ├── PermissionError（DDL/DML） → 直接终止，不重试
    │
    └── 其他错误 → 将错误信息注入上下文
                       │
                       ▼
                  act_node（带 last_sql_error 上下文）
                       │
                  LLM 读取错误 → 重新生成修复后的 SQL
                       │
                  最多重试 3 次 → 超限终止
```

**错误反馈格式：**

```
上次 SQL 执行失败：
错误：no such column: amount
原始 SQL：SELECT amount FROM orders WHERE ...
请修正上述错误，重新生成正确的 SQL。
```

---

## 五、并行 SQL 执行

multi-table 场景下，多张表的查询并发执行：

```python
async def parallel_act(ctx, plan_steps):
    tasks = [execute_sql(step) for step in plan_steps]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return merge_results(results)
```

**性能对比：**

| 模式 | 3张表查询耗时 |
|------|--------------|
| 串行 | ~3s |
| 并行 | ~1.2s（快 60%+） |

---

## 六、RAG Schema Linking

解决 LLM 幻觉问题（生成不存在的表名/列名）：

```
用户问题
    │
    ▼
向量化（text-embedding）
    │
    ▼
Milvus / JSON Store 相似度检索
    │
    ▼
Top-K 相关 Schema 片段
    │
    ▼
注入 Plan Prompt → LLM 生成 SQL
```

**Schema 存储结构：**

```json
{
  "table": "live_gmv",
  "columns": ["live_id", "anchor_id", "gmv_amount", "date"],
  "description": "直播间 GMV 汇总表，记录每场直播的成交金额",
  "sample_rows": [...]
}
```

**两种后端：**
- `SCHEMA_STORE=json` → 本地 JSON 文件（零依赖，快速启动）
- `SCHEMA_STORE=milvus` → Milvus 向量库（生产推荐）

---

## 七、三层记忆系统

```
Working Memory（当前会话）
    │ 当 Episodic 记忆触发时提升
    ▼
Episodic Memory（对账案例库）    ← 成功对账案例存储
    │ 多次成功后提炼为规则
    ▼
Semantic Memory（业务规则库）    ← "GMV 以下单时间为准" 等领域规则
```

**案例检索：**

```python
# 新问题来了，先搜索历史相似案例
similar_cases = memory.search(query, top_k=3)
# 相似度 > 0.85 时直接复用历史 SQL
if similar_cases[0].score > 0.85:
    return similar_cases[0].sql
```

---

## 八、接入层

### REST API（FastAPI）

```
POST /api/v2/query          同步对账
POST /api/v2/query/stream   SSE 流式推理（前端实时展示 Thought/Action）
GET  /api/v2/sessions       历史会话列表
GET  /api/v2/reports        历史报告列表
GET  /api/health            健康检查
```

### SSE 流式输出

```
data: {"type": "thought", "content": "需要先看表结构"}
data: {"type": "action", "tool": "sql_schema", "input": "live_gmv"}
data: {"type": "observation", "content": "6个字段, 26行"}
data: {"type": "report", "content": "# 对账报告\n..."}
data: {"type": "done"}
```

---

## 九、部署架构

### 最小化部署（SQLite，零依赖）

```bash
pip install -e .
python apps/api/main.py
```

### Docker 完整部署

```bash
cd deploy
docker-compose up -d
```

```yaml
services:
  api:      FastAPI 应用
  milvus:   向量库（可选，SCHEMA_STORE=json 时不需要）
```

---

## 十、扩展点

| 扩展维度 | 实现方式 |
|----------|----------|
| 新增数据库 | 实现 `adapters/base.py` → `DBAdapter` |
| 新增工具 | 继承 `ToolBase`，注册到 `ToolRegistry` |
| 新增 Agent | 继承 `ReActAgent` 或实现 `AgentBase` |
| 切换 LLM | 修改 `.env` 中 `LLM_MODEL_ID` / `LLM_BASE_URL` |
| 切换向量库 | 修改 `SCHEMA_STORE=milvus` / `json` |
