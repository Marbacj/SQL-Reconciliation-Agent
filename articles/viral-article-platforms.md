# 用 LLM 写的 SQL 敢直接跑吗？从大厂离职后我造了一套企业级 SQL 对账 Agent

> 开源地址：[github.com/Marbacj/SQL-Reconciliation-Agent](https://github.com/Marbacj/SQL-Reconciliation-Agent)  
> 
> 官网地址：[https://chatsql.top/](chatsql.top)
> 一句话：**不打广告。不写一行 SQL，自动对比两张表的数据差异，给你一份完整的差异报告。**

---

## 先看效果，再聊技术

你只需要输入一句话：

```
对比昨天直播 GMV 和订单金额的差异
```

然后 Agent 自动完成 7 步推理：

```
[Thought] 需要先看 live_gmv 和 order_summary 的表结构
[Action]  sql_schema(live_gmv)
[Obs]     6个字段, 26行, 主键 live_id

[Thought] 按 live_id 聚合两表并对比
[Action]  sql_execute(GMV汇总) + sql_execute(订单汇总)  ← 并行执行
[Obs]     左表 25行, 右表 27行 → 行数不一致

[Thought] 需要 FULL OUTER JOIN 精确定位差异行
[Action]  diff_compare(左表, 右表)
[Obs]     发现 3 处差异

[Action]  report_generate(差异报告)
[Finish]  ✅ 报告已保存至 reports/
```

输出的差异报告长这样：

| live_id | 问题类型 | GMV    | 订单金额   | 差异       |
| ------- | ---- | ------ | ------ | -------- |
| 105     | 数值差异 | 12,500 | 11,800 | **+700** |
| 208     | 数据缺失 | N/A    | 3,500  | ⚠️ 仅右表   |
| 312     | 数值差异 | 8,900  | 9,200  | **-300** |

**7 步推理，自动识别 3 处人工注入的差异，不写一行 SQL。**

---

## 这解决了什么真实问题？

去问任何一个财务、风控或数据同学：

> "你们每个月手工对账要花多久？"

答案通常是：**2～5 天。**

Excel 跨表 VLOOKUP、手工核对流水、找差异行…… 这是无数企业每个月都在重复的人工劳动。

- 现有的 **BI 工具**（Tableau、帆软）解决的是"可视化"问题，不是"对账"问题
- **Text2SQL 工具**（Vanna、DAIL-SQL）解决的是"查询"问题，不是"比对"问题

**SQL Reconciliation Agent 填的就是这个空白：**

```
自然语言 → 自动 SQL 生成 → 自动执行 → 自动发现差异 → 生成差异报告
```

---

## 架构图：一条完整的 Agent 流水线

![Agent 完整执行流程](https://raw.githubusercontent.com/Marbacj/SQL-Reconciliation-Agent/main/articles/images/3_agent-workflow.svg)

整个系统由 **5 个 LangGraph 节点**驱动，每个节点职责单一、可独立测试：

| 节点          | 职责                              |
| ----------- | ------------------------------- |
| **Route**   | 识别用户意图，路由到对应执行路径                |
| **Plan**    | 拆解任务，调用 RAG 检索相关 Schema         |
| **Act**     | 并行生成并执行 SQL，`asyncio.gather` 驱动 |
| **Observe** | 观察执行结果，检测错误，触发自动修复              |
| **Reflect** | 反思结果质量，生成 Markdown 差异报告         |

---

## 6 个最难的技术挑战，以及我怎么解决的

### 🧩 挑战 1：LLM 的幻觉 —— 表名、列名瞎编

这是所有 NL2SQL 项目的第一道坎。

LLM 不知道你的数据库长什么样，它会根据"常识"推测一个表名，然后自信满满地写出来。

**我用了两层防御：**

![RAG Schema Linking + PRAGMA 双层防御](https://raw.githubusercontent.com/Marbacj/SQL-Reconciliation-Agent/main/articles/images/5_rag-pragma-defense.svg)

**第一层：RAG Schema Linking**

```python
# 启动时：所有表结构向量化
for table in db.get_all_tables():
    embedding = embed(f"{table.name}: {table.comment} | {table.columns}")
    vector_store.upsert(embedding, metadata=table)

# 查询时：语义检索相关表，注入 Prompt
relevant_tables = vector_store.search(user_question, top_k=5)
prompt = build_prompt(question, schema=relevant_tables)
```

**第二层：实时 PRAGMA 校验**

LLM 生成 SQL 后，用 `PRAGMA table_info()` 实时验证列是否真实存在，拦截幻觉字段。

两层加起来，Schema 幻觉问题基本消除。

---

### 🔁 挑战 2：SQL 一次成功率不到 60%

这个数字让我一开始很沮丧。

跨表 JOIN 写错 ON 条件、聚合忘写 GROUP BY、字段名大小写不对…… 光靠 Prompt Engineering 是不够的。

**解决方案：错误反馈循环**

![SQL 自动修复：错误反馈循环](https://raw.githubusercontent.com/Marbacj/SQL-Reconciliation-Agent/main/articles/images/4_sql-self-correction.svg)

```python
for attempt in range(MAX_RETRIES):  # 最多重试 3 次
    sql = llm.generate(question, schema, error_context)
    result = db.execute(sql)

    if result.success:
        return result
    else:
        error_context = result.error_message  # 把真实错误原样喂给 LLM
        # "no such column: order_amount" → LLM 下次自动修正
```

关键点：**把数据库返回的真实错误回喂给 LLM**，不是让它重新猜，而是让它看着错误改。

上线这个机制后，端到端成功率从 60% → **90%+**。

---

### ⚡ 挑战 3：多表串行查询慢到难以接受

对账天然涉及多张表：订单表、支付流水、退款记录、GMV 汇总……

最早是串行查的。5 张表 = 5 次 LLM 调用 + 5 次 DB 查询，端到端要等 **12 秒**。

**解决方案：`asyncio.gather` 并行执行**

```python
# Plan 阶段：标记哪些子任务可以并行
parallel_tasks = plan.get_parallel_tasks()

# Act 阶段：一次性并发发出去
results = await asyncio.gather(*[
    execute_sql(task) for task in parallel_tasks
])
```

Plan 节点智能判断哪些 SQL 之间没有依赖，打包成并行任务。

**结果：P99 延迟从 ~12s 降到 ~3s，降幅 75%+**

---

### 🛡️ 挑战 4：LLM 写的 SQL，凭什么敢直接跑？

> 如果 LLM 写了一句 `DROP TABLE orders`，谁来兜底？

很多 NL2SQL 项目对这块处理得很粗糙——要么靠 Prompt 约束，要么直接忽略。

**我用了 AST 级别的权限拦截：**

```python
import sqlparse

def check_sql_safety(sql: str) -> bool:
    parsed = sqlparse.parse(sql)[0]

    # 黑名单：DDL 和 DML 一律拒绝
    FORBIDDEN_DDL = {'DROP', 'ALTER', 'CREATE', 'TRUNCATE'}
    FORBIDDEN_DML = {'DELETE', 'UPDATE', 'INSERT'}

    for token in parsed.flatten():
        normalized = token.normalized.upper()
        if normalized in FORBIDDEN_DDL | FORBIDDEN_DML:
            raise PermissionError(f"⛔ 禁止执行危险操作: {normalized}")

    return True
```

数据库连接同时强制 `read_only=True`。

**双层防护：AST 拦截 + 只读连接，一层被绕过还有第二层。**

---

### 📊 挑战 5：两张表列名不同，怎么比对？

这是对账场景特有的问题。

左表叫 `total_gmv`，右表叫 `order_amount`——两列本质上对的是同一个指标，但列名不同，直接 JOIN 对不上。

**解决方案：语义 + 位置的双重配对策略**

```python
def align_columns(left_df, right_df):
    # 先尝试语义匹配（embedding cosine similarity）
    semantic_pairs = semantic_match(left_df.columns, right_df.columns)

    # 语义不确定时，退回到位置配对
    # 输出带原始列名的差异报告：total_gmv ⟷ order_amount: 差异 +700
```

输出报告中保留原始列名，**保证可追溯性**，用户能直接看到是哪个字段的问题。

---

### 🧠 挑战 6：每次都重新理解 Schema，太慢太贵

重复对账任务（比如每天都对一次 GMV 和订单），每次让 LLM 重新"理解"表结构，既慢又烧 token。

**解决方案：三层记忆系统**

```
┌─────────────────────────────────────────────┐
│              三层记忆架构                    │
│                                             │
│  Working Memory   → 当次对话临时状态        │
│  Episodic Memory  → 历史对账案例 + SQL      │
│  Semantic Memory  → 提炼的业务规则          │
│                                             │
│  第二次做同类对账：直接召回历史案例复用      │
│  响应时间 -40%，token 消耗 -35%             │
└─────────────────────────────────────────────┘
```

---

## 和其他 Text2SQL 方案的核心差异

| 对比维度      | SQL-Reconciliation-Agent | 通用 Text2SQL（Vanna 等） |
| --------- | ------------------------ | -------------------- |
| SQL 修复    | ✅ 自动重写，最多 3 次            | ❌ 失败即止               |
| 并行执行      | ✅ asyncio.gather         | ❌ 串行                 |
| Schema 检索 | ✅ RAG + 实时 PRAGMA        | ❌ 静态 schema          |
| 差异对账      | ✅ Diff + 跨列名比对           | ❌ 无此能力               |
| 记忆复用      | ✅ 三层记忆 + 案例库             | ❌ 无状态                |
| 企业安全      | ✅ AST 拦截 DDL/DML         | ⚠️ 仅靠 Prompt         |
| 澄清追问      | ✅ 智能追问 + Chip 建议         | ❌ 无                  |

---

## 核心功能全景

| 能力                | 说明                                                   |
| ----------------- | ---------------------------------------------------- |
| 🤖 Multi-Agent 编排 | LangGraph 状态机：Route → Plan → Act → Observe → Reflect |
| 🔄 SQL 自动修复       | 失败时错误信息反馈 LLM，自动重写，最多重试 3 次                          |
| ⚡ 并行 SQL 执行       | asyncio.gather 并发多表查询，P99 降低 75%+                    |
| 🧠 RAG Schema 检索  | 向量化表结构，自动定位相关表，解决幻觉问题                                |
| 🛡️ AST 级权限控制     | 拦截 DDL/DML，只读执行，企业安全合规                               |
| 📊 跨列名比对          | 列名不同时按语义自动配对，输出 `total_gmv ⟷ order_amount`           |
| 💾 三层记忆系统         | Working + Episodic + Semantic Memory，历史复用            |
| 🔌 多数据库适配         | SQLite / MySQL / ClickHouse / Hive 方言自动适配            |
| 💬 自然语言澄清         | 问题模糊时主动追问，带快捷建议 Chip                                 |
| 🌐 Web UI         | 零依赖纯 HTML，开箱即用                                       |
| 🐳 Docker 部署      | 一键 docker-compose，含 Milvus 可选                        |

---

## 5 分钟跑起来

### 1. 克隆安装

```bash
git clone https://github.com/Marbacj/SQL-Reconciliation-Agent.git
cd SQL-Reconciliation-Agent
pip install -e .
```

### 2. 配置 LLM（支持 DeepSeek / OpenAI / Claude）

```bash
cp .env.example .env
# 编辑 .env
```

```env
LLM_MODEL_ID=deepseek-chat          # 推荐：便宜好用
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com
DB_PATH=data/unified_test.db
```

### 3. 生成测试数据

```bash
# 含 3 处故意注入差异的企业测试数据（GMV + 订单 + 支付多表）
python data/generate_mock_data.py
```

### 4. 启动 Web UI

```bash
python apps/api/main.py
# 访问 http://localhost:8000
```

### 5. 然后直接说中文

```
对比昨天直播 GMV 和订单金额有没有差异？
查询支付失败的订单，按渠道分组统计
这个月的 GMV 比上个月减少了多少？
找出 live_gmv 表和 order_summary 表的数据不一致项
```

---

## 项目结构

```
SQL-Reconciliation-Agent/
├── recon_core/          # 🏗️ Agent 基础框架（LLM抽象·工具系统·熔断器）
├── recon_v2/            # 🚀 业务编排层（LangGraph节点·RAG·记忆系统）
│   ├── orchestration/  #    route · plan · act · observe · reflect
│   ├── rag/            #    Schema Linking · Milvus · Chunker
│   └── memory/         #    三层记忆系统
├── apps/
│   ├── api/main.py     # 🌐 FastAPI REST API + SSE 流式
│   └── ui/             # 💻 Web UI（无依赖纯 HTML）
├── data/               # 📊 测试数据集 + 生成脚本
├── docs/               # 📚 技术文档
└── tests/              # 🧪 20+ 测试文件
```

---

## 写在最后

我在做这个项目之前，认真想过：**为什么不做 OnCall 助手？为什么不做智能客服？**

结论只有一句：

> **OnCall 和客服，本质上都是"问答 wrapper"。它们只检索信息，不做决策。**

DataAgent 不一样。它不是告诉你"你应该看哪份报表"，而是直接告诉你：

> "昨天华东大区退款率涨了 3.2%，主要是这 17 笔订单，差异定位到 SKU 维度了。"

**差的不是交互形式，差的是"决策权"。**

LLM 时代真正的红利，不在"信息检索"，而在"自动化决策"。

---

## 📌 项目地址

**👉 [github.com/Marbacj/SQL-Reconciliation-Agent](https://github.com/Marbacj/SQL-Reconciliation-Agent)**

- ⭐ 觉得有帮助，点个 Star 是最大的鼓励
- 💬 有对账 / BI Agent / DataAgent 经验，欢迎 Issue 或 PR
- 🔖 适合：后端同学了解 AI Agent 工程化实践的真实项目

**Java 后端 × AI Agent × 企业数据对账 — 真实场景，不是 Demo。**

---

*如果你也在做 DataAgent / BI Agent / 对账系统，欢迎在评论区聊，我们一起踩坑。*

---

**技术标签：** `AI Agent` `LangGraph` `Text2SQL` `RAG` `Python` `数据对账` `NL2SQL` `Multi-Agent` `企业级` `开源`
