# HelloAgents → SQL 对账 Agent 平台 · 技术方案

> 目标岗位：AI Agent + 数据平台 + 大模型工程化基础设施  
> 项目定位：基于 HelloAgents 多 Agent 框架，构建自然语言驱动的自动化 SQL 对账系统

---

## 一、JD 需求 → HelloAgents 能力映射

| JD 核心需求 | HelloAgents 现有能力 | 方案中的新建设 |
|------------|---------------------|---------------|
| **Agent Runtime** | `Agent` 基类 + `ReActAgent` + `PlanAndSolveAgent` + `ReflectionAgent` | 对账专用 `ReconciliationAgent` |
| **Tool Calling** | `Tool` 基类 + `@tool_action` 装饰器 + `ToolRegistry` | `SQLTool` (生成+执行+校错) |
| **Task Planning** | `Planner` + `Executor` (PlanAndSolveAgent) | 对账任务编排器 |
| **多轮状态管理** | `MemoryTool` (Working/Episodic/Semantic) + `Message` 历史 | 对账会话上下文 |
| **RAG** | `RAGTool` + Qdrant + Neo4j + Embedding | 表结构知识库 + Query Rewrite |
| **ReAct 范式** | `ReActAgent` (Thought → Action → Observation 循环) | 对账专用 custom_prompt |
| **Reflection** | `ReflectionAgent` (自我纠错) | SQL 语法自纠 + 对账结果反思 |
| **MCP 协议** | `MCPClient` + `MCPServer` | 预留：对接真实 Hive/ClickHouse |
| **Skill 编排** | `SkillTool` + `skill_tool` | 对账规则 Skill 库 |
| **流式输出** | `HelloAgentsLLM.invoke_stream()` | ✅ 已有 |
| **多 Agent 协作** | 多 Agent 实例独立运行 | Agent 编排器 |

**核心结论**：HelloAgents 现成的 Agent 框架 + Tool 体系 + Memory/RAG 已经覆盖 JD 70% 的技术需求。本项目只需新建 **4 个 Tool** + **1 个对账 Agent subclass** + **1 套 Demo 数据**。

---

## 二、整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                        用户输入                              │
│          "昨天直播 GMV 和订单系统有没有差异？"                 │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    ReconciliationAgent                       │
│                  (extends ReActAgent)                        │
│                                                              │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐ │
│  │ 意图路由  │   │ 任务拆解  │   │ SQL生成  │   │ 结果反思  │ │
│  │ (Prompt)  │──▶│(Planner) │──▶│(LLM+Tool)│──▶│(Reflect) │ │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘ │
│                                      │                       │
│                           ┌──────────▼──────────┐           │
│                           │    ToolRegistry      │           │
│                           │                      │           │
│                           │  🔧 sql_schema       │           │
│                           │  🔧 sql_generate     │           │
│                           │  🔧 sql_execute      │           │
│                           │  🔧 sql_validate     │           │
│                           │  🔧 diff_compare     │           │
│                           │  📄 report_generate  │           │
│                           │  🧠 memory (历史)     │           │
│                           │  🔍 rag_table_doc    │           │
│                           └──────────────────────┘           │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    数据层 (Demo: SQLite)                     │
│                                                              │
│  ┌─────────────────────┐    ┌─────────────────────┐         │
│  │  table_live_gmv     │    │  table_order_amount │         │
│  │  (直播GMV表)         │    │  (订单金额表)        │         │
│  │  date/live_id/gmv   │    │  date/order_id/amt  │         │
│  └─────────────────────┘    └─────────────────────┘         │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              knowledge_base/table_docs/              │    │
│  │  table_live_gmv.md     (字段说明 + 业务含义)          │    │
│  │  table_order_amount.md                               │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

---

## 三、新建模块清单

### 3.1 新建文件结构

```
HelloAgents/
├── hello_agents/
│   ├── tools/
│   │   └── builtin/
│   │       ├── sql_tool.py          ← NEW: SQL 生成 + 执行 + 校验
│   │       ├── diff_tool.py         ← NEW: 数据差异比对
│   │       └── report_tool.py       ← NEW: Markdown 报告生成
│   └── agents/
│       └── reconciliation_agent.py  ← NEW: 对账专用 Agent
├── examples/
│   └── reconciliation_demo.py      ← NEW: 完整 Demo
├── knowledge_base/
│   └── table_docs/                  ← NEW: 表结构文档
│       ├── live_gmv.md
│       └── order_amount.md
└── data/
    └── mock_reconciliation.db       ← NEW: SQLite 模拟数据
```

### 3.2 SQLTool (`sql_tool.py`)

核心工具，可展开为 3 个子工具：

```python
class SQLTool(Tool):
    """SQL 工具集 — 表结构查询 + SQL 生成 + 执行"""
    expandable = True

    @tool_action("sql_schema", "查询数据表的结构信息（字段名、类型、示例值）")
    def _get_schema(self, table_name: str) -> str:
        """返回 CREATE TABLE + 前3行示例数据"""

    @tool_action("sql_execute", "执行 SQL 查询并返回结果")
    def _execute(self, sql: str) -> str:
        """执行 SQL，返回前 50 行结果的 Markdown 表格"""

    @tool_action("sql_validate", "校验 SQL 语法是否正确，返回错误信息或确认")
    def _validate(self, sql: str) -> str:
        """用 sqlite3 的 EXPLAIN 做语法校验"""
```

### 3.3 DiffTool (`diff_tool.py`)

```python
class DiffTool(Tool):
    """数据对账工具 — 比对两次查询结果"""

    @tool_action("diff_compare", "比对两组查询结果，找出差异行")
    def _compare(
        self,
        sql_a: str,        # 左表 SQL
        sql_b: str,        # 右表 SQL  
        key_column: str,   # 主键列（用于关联比对）
        compare_columns: str  # 要比对的数值列（逗号分隔）
    ) -> str:
        """
        1. 分别在 SQLite 执行两条 SQL
        2. 按 key_column JOIN
        3. 逐列计算差异
        4. 返回差异行 + 差异量
        """
```

### 3.4 ReportTool (`report_tool.py`)

```python
class ReportTool(Tool):
    """报告生成工具"""

    @tool_action("report_generate", "将对账结果格式化为可读的 Markdown 报告")
    def _generate(
        self,
        title: str,
        diff_result: str,    # diff_compare 的输出
        conclusion: str      # Agent 的结论
    ) -> str:
        """组装完整 Markdown 报告 + 保存到文件"""
```

### 3.5 ReconciliationAgent (`reconciliation_agent.py`)

```python
class ReconciliationAgent(ReActAgent):
    """
    对账专用 Agent
    
    与普通 ReActAgent 的区别：
    1. 使用对账专用 System Prompt（包含完整的对账工作流指令）
    2. 默认注册全部对账工具
    3. 内置 Reflection 机制（SQL 失败后自动重试修正）
    4. 支持多表对账的任务规划
    """

    DEFAULT_RECONCILIATION_PROMPT = """
你是一个专业的数据对账分析师。你可以通过以下工具完成自动化对账：

## 可用工具
{tools}

## 对账工作流
请严格按照以下步骤执行：

1. **了解表结构**：使用 sql_schema 查询相关数据表的结构
2. **生成查询 SQL**：根据用户问题生成针对性的 SQL
3. **执行查询**：使用 sql_execute 执行 SQL，获取数据
4. **对账比对**：使用 diff_compare 比对两组数据，找出差异
5. **输出报告**：使用 report_generate 生成 Markdown 对账报告

## 关键注意事项
- 生成 SQL 之前必须先查表结构
- SQL 如果执行失败，分析错误信息并修正 SQL (最多重试2次)
- 对账必须指定主键列和数值列
- 最后必须生成报告

## 当前对账任务
**Question:** {question}
**历史:** {history}

开始你的对账分析：
"""
```

---

## 四、完整对账工作流（一次典型调用）

### 输入
```
用户: "对比昨天直播 GMV 和订单系统金额，找出差异超过 100 元的直播间"
```

### Agent 执行轨迹（预期 6-8 步）

```
Step 1:
  Thought: 需要先了解两张表的结构
  Action: sql_schema[live_gmv] + sql_schema[order_amount]

Step 2:
  Observation: 
    表 live_gmv: live_id(INT), gmv(DECIMAL), live_date(DATE), live_title(TEXT)
    表 order_amount: order_id(INT), live_id(INT), total_amount(DECIMAL), order_date(DATE)
  Thought: 两表通过 live_id 关联，需要按 live_id 汇总后比对
  Action: sql_generate[昨天(2026-05-27)每个直播间的GMV和订单总金额对比]

Step 3:
  Observation: 生成了两条SQL
  Action: sql_execute[
    SELECT live_id, SUM(gmv) as total_gmv 
    FROM live_gmv WHERE live_date='2026-05-27' 
    GROUP BY live_id ORDER BY live_id
  ]

Step 4:
  Observation: 返回 23 行数据
  Action: sql_execute[
    SELECT live_id, SUM(total_amount) as total_order 
    FROM order_amount WHERE order_date='2026-05-27' 
    GROUP BY live_id ORDER BY live_id
  ]

Step 5:
  Observation: 返回 25 行数据（多了2个直播间只有订单没有GMV记录）
  Action: diff_compare[
    sql_a=SQL1,
    sql_b=SQL2,
    key_column="live_id",
    compare_columns="total_gmv,total_order"
  ]

Step 6:
  Observation: 
    差异行 3 个:
    - live_id=105: GMV=12500, 订单=11800, 差额=700
    - live_id=208: 无GMV, 订单=3500, 差额=3500 (数据缺失)
    - live_id=312: GMV=8900, 订单=9200, 差额=-300
  Thought: 有 2 个直播间差额超 100 元需要关注。live_id=208 可能漏记了 GMV
  Action: report_generate[...]

Step 7:
  Thought: 对账完成
  Action: Finish[对账报告已生成，发现3处差异，其中2处超阈值...]
```

---

## 五、Demo 数据设计

### 模拟场景：直播电商对账

**数据表 1: live_gmv（直播 GMV 记录）**
```sql
CREATE TABLE live_gmv (
    id INTEGER PRIMARY KEY,
    live_id INTEGER,
    live_title TEXT,
    gmv DECIMAL(12,2),
    live_date DATE,
    platform TEXT,
    anchor_name TEXT
);

-- 插入 25 条数据，2026-05-27 当天
-- 其中 live_id=208 故意缺失（模拟漏记）
```

**数据表 2: order_amount（订单金额记录）**
```sql
CREATE TABLE order_amount (
    id INTEGER PRIMARY KEY,
    order_id TEXT,
    live_id INTEGER,
    total_amount DECIMAL(12,2),
    order_date DATE,
    order_status TEXT
);

-- 插入 50 条数据，部分直播间有多笔订单
-- live_id=105 的总金额故意设为 11800（与 GMV 12500 有差异）
```

**表结构文档（知识库 RAG 用）**：
- `knowledge_base/table_docs/live_gmv.md`：字段说明、业务含义、常见查询示例
- `knowledge_base/table_docs/order_amount.md`：同上

---

## 六、与 JD 面试点的直接对应

| 面试高频问题 | 方案中的体现 | 可说的话 |
|------------|-------------|---------|
| ReAct vs Plan&Execute 区别 | 对账 Agent 基于 ReActAgent，复杂多表对账用 PlanAndSolveAgent | "简单对账用 ReAct 逐步推理，多表跨系统对账用 Planner 拆成子任务" |
| 如何设计 Tool Registry | 基于 HelloAgents `ToolRegistry` + `@tool_action` 装饰器 | "每个工具可独立展开为子工具，Agent 通过统一 Schema 发现和调用" |
| 多轮记忆怎么做 | `MemoryTool` (Working/Episodic/Semantic) + `Message` 历史 | "工作记忆存当前对账状态，情景记忆存历史对账案例，语义记忆存表结构知识" |
| Tool 调用失败怎么办 | `sql_validate` 预校验 + ReAct 循环中重试 + `ReflectionAgent` | "SQL 执行前先 EXPLAIN 校验，失败后 Agent 分析错误信息自动修正，最多重试 2 次" |
| Query Rewrite | RAG 从表结构文档检索 + LLM 改写 | "用户说的'GMV'对应哪个字段？RAG 先检索表文档找到映射，再生成精准 SQL" |
| 如何控制 hallucination | SQL 先 validate 再 execute + diff 结果必须可复现 | "不信任 LLM 直接输出的数据，所有结果都经过 SQLite 实际执行验证" |
| vLLM / 高并发 | `HelloAgentsLLM` 支持 `provider='vllm'` | "LLM 层抽象支持 DeepSeek/vLLM/Ollama，可切换本地部署降低延迟" |
| 数据权限 | 工具层做 SQL 注入防护 + 可扩展 RBAC | "SQL 执行前过滤 DROP/DELETE/UPDATE 等危险操作" |
| Streaming | `HelloAgentsLLM.invoke_stream()` | "工具调用结果是批量返回的，但 LLM 推理过程支持流式输出" |
| MCP 协议 | `hello_agents/protocols/mcp/` 已实现 | "未来对接真实 Hive/ClickHouse 时，通过 MCP Server 暴露数据源" |

---

## 七、实施路径（3 天完成）

| 阶段 | 内容 | 预计时间 |
|------|------|---------|
| **Day 1 上午** | 创建 SQLite mock 数据 + 表结构文档 | 1h |
| **Day 1 下午** | 实现 `SQLTool`（schema + execute + validate） | 3h |
| **Day 2 上午** | 实现 `DiffTool`（数据比对） + `ReportTool` | 2h |
| **Day 2 下午** | 实现 `ReconciliationAgent` + 对账专用 Prompt | 2h |
| **Day 3 上午** | 串联 Demo + 录制运行轨迹 | 2h |
| **Day 3 下午** | 打磨面试话术 + README + 架构图 | 2h |

---

## 八、面试讲述框架（STAR 法则）

**Situation**：公司内部业务方经常需要手工对账，SQL 编写门槛高、容易出错、耗时长。

**Task**：基于 HelloAgents 框架构建一个 AI Agent，让业务人员用自然语言完成自动化 SQL 对账。

**Action**：
1. 基于 HelloAgents 的 `Tool` 基类和 `@tool_action` 装饰器，构建了 3 个可展开工具集（SQL 生成/执行/校验、数据比对、报告生成）
2. 继承 `ReActAgent` 实现对账专用 Agent，内置完整的 Thought→Action→Observation 循环和 SQL 失败自动修正逻辑
3. 利用 `MemoryTool` 的多层记忆（工作/情景/语义）实现跨对账会话的上下文保留
4. 用 `RAGTool` + Qdrant 向量库存储表结构文档，支持自然语言到 SQL 字段的映射
5. 所有数据操作经过 SQLite 实际执行验证，杜绝幻觉

**Result**：一次典型对账（2 张表、50+ 行数据）Agent 自动完成表结构查询→SQL 生成→执行→比对→报告输出，5-7 步完成。

---

## 九、项目 README 描述（面试用）

> **HelloAgents — SQL 对账 Agent 平台**
>
> 基于多 Agent 协作框架构建的企业级数据对账系统。用户以自然语言描述对账需求（如"对比昨天直播 GMV 和订单金额差异"），Agent 自动完成：
>
> 1. **意图理解** → 解析对账场景和目标
> 2. **表结构发现** → 自动查询相关数据表 Schema
> 3. **SQL 生成** → 基于 RAG 增强的字段映射生成精准 SQL
> 4. **数据执行** → 通过 SQLite 引擎实际执行（可扩展至 Hive/ClickHouse）
> 5. **差异比对** → 按主键 JOIN 后逐列计算差异
> 6. **报告输出** → 生成 Markdown 对账报告，标注差异行和差异量
>
> **技术栈**：Python · HelloAgents Agent Framework · ReAct/Plan&Solve · SQLite · RAG (Qdrant + DashScope Embedding) · Tool Registry · MCP 协议支持
