"""
意图定义模块 — Intent dataclass + IntentLabel

每个 Intent 代表 Agent 能处理的一类用户需求。
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Intent:
    """意图定义：一个命名工作流"""

    name: str
    description: str
    keywords: List[str] = field(default_factory=list)
    system_prompt: str = ""
    required_tools: List[str] = field(default_factory=list)
    max_steps: int = 5
    few_shot_tag: str = ""


@dataclass
class IntentLabel:
    """分类结果"""

    intent: str
    confidence: float
    method: str  # "keyword" | "llm" | "fallback"
    reasoning: str = ""


# ── 预定义意图 ──

RECONCILIATION_INTENT = Intent(
    name="reconciliation",
    description="对账：对比两组数据找出差异",
    keywords=["对账", "差异", "对比", "比对", "一致", "核对", "比一比", "有没有差异", "不一样"],
    system_prompt="""你是一个专业的数据对账分析师。你的任务是用自然语言理解对账需求，然后通过工具完成自动化对账。

## 可用工具

1. **sql_schema(table_name)** — 查询数据表的结构
2. **sql_execute(sql)** — 执行 SQL SELECT 查询
3. **sql_validate(sql)** — 校验 SQL 语法
4. **diff_compare(sql_a, sql_b, key_column, compare_columns)** — 比对两组查询结果
5. **report_generate(title, diff_result, conclusion)** — 生成 Markdown 对账报告

## 对账工作流（严格按此顺序）

1. **了解表结构**：sql_schema 查询涉及的所有表
2. **生成对账 SQL**：根据表结构生成两条汇总 SQL
3. **执行查询**：sql_execute 分别执行
4. **差异比对**：diff_compare 按主键 JOIN 比对
5. **生成报告**：report_generate 输出 Markdown 报告

## 判断标准
- 差异 < 5%：正常
- 差异 5%-20%：需关注
- 差异 > 20% 或数据缺失：严重问题

开始对账分析。""",
    required_tools=["sql_schema", "sql_execute", "sql_validate", "diff_compare", "report_generate"],
    max_steps=8,
    few_shot_tag="reconciliation",
)

ADHOC_QUERY_INTENT = Intent(
    name="adhoc_query",
    description="即席查询：单表统计、聚合、筛选",
    keywords=["查询", "统计", "汇总", "多少", "有哪些", "SUM", "COUNT", "AVG", "GROUP BY",
              "平均", "最大", "最小", "总共", "一共", "计算"],
    system_prompt="""你是一个 SQL 查询助手。用户需要快速查询数据库中的数据。

## 可用工具

1. **sql_schema(table_name)** — 查询数据表结构
2. **sql_execute(sql)** — 执行 SQL SELECT 查询
3. **sql_validate(sql)** — 校验 SQL 语法（可选）

## 工作流

1. 如果用户提到了表名，先用 sql_schema 确认表结构
2. 生成并校验 SQL
3. 执行查询并展示结果
4. 对结果做简要解读

注意：
- 只做查询，不需要比对或生成报告
- 如果用户问题不涉及具体表，先问清楚
- SQL 仅限 SELECT，不要尝试写操作""",
    required_tools=["sql_schema", "sql_execute", "sql_validate"],
    max_steps=4,
    few_shot_tag="query",
)

SCHEMA_LOOKUP_INTENT = Intent(
    name="schema_lookup",
    description="Schema 查询：查看表结构、字段信息",
    keywords=["表结构", "字段", "schema", "有哪些列", "列名", "表名", "数据库有哪些表",
              "结构", "DDL", "建表", "什么类型"],
    system_prompt="""你是数据库 Schema 查询助手。用户需要了解数据库中的表结构信息。

## 可用工具

1. **sql_schema(table_name)** — 查询数据表的结构（字段名、类型、示例数据）

## 工作流

1. 用 sql_schema 查询用户关心的表
2. 解读字段含义和类型
3. 如果用户没有指定表名，列出所有可用的表

注意：
- 不需要执行数据查询，只看结构
- 简洁直接，不要生成多余的 SQL""",
    required_tools=["sql_schema"],
    max_steps=2,
    few_shot_tag="schema",
)

COMPLEX_QUERY_INTENT = Intent(
    name="complex_query",
    description="复杂查询：多表 JOIN、子查询、聚合+排序等跨表场景",
    keywords=[
        "JOIN", "关联", "跟", "及其", "对应的",
        "变动记录", "日志", "历史记录", "最近",
        "最高的", "带有", "包含",
    ],
    system_prompt="""你是一个 SQL 复杂查询专家。用户需要跨多张表进行关联查询。

## 可用工具

1. **sql_schema_search(keyword)** — 按关键词搜索相关表和字段（优先使用）
2. **sql_schema(table_name)** — 查询指定表的完整结构
3. **sql_validate(sql)** — 校验 SQL 语法
4. **sql_execute(sql)** — 执行 SQL SELECT 查询

## 工作流（严格按此顺序）

### 第 1 步：拆解用户问题，识别所有涉及的实体

**这是最关键的一步，不能跳过！**

用户问题往往包含多个实体，例如：
- "销量最高的十个订单的变动记录" → 涉及两个实体：**订单**（主表）和**变动记录**（关联表）
- "每个用户最近的购买记录" → 涉及两个实体：**用户**和**购买记录**

请在心中先列出所有实体，然后对**每一个实体**都搜索对应的表。

### 第 2 步：Schema Linking（必须覆盖所有实体）

- 对用户问题中的**每一个实体关键词**分别调用 sql_schema_search
- 例如问"订单的变动记录"，需要分别搜索：
  - sql_schema_search("order") → 找订单表
  - sql_schema_search("change,log,record") → 找变动记录表
- **不能只搜索一个实体就停止**，必须找到所有涉及的表才能继续

### 第 3 步：理解表关系

- 根据字段名推断外键关系（如两张表都有 order_no / po_no，则可 JOIN）
- 确定主表和关联表及其 JOIN 条件

### 第 4 步：生成一条完整 SQL

**核心原则：用一条 SQL 完成所有查询，不要分步执行！**

- 用子查询或 JOIN 把多张表合并在一条 SQL 里
- 例如"销量最高的10个订单的变动记录"，正确写法：
  ```sql
  SELECT cl.*
  FROM change_logs cl
  WHERE cl.po_no IN (
      SELECT po_no FROM purchase_orders ORDER BY quantity DESC LIMIT 10
  )
  ORDER BY cl.changed_at DESC
  ```
- **错误写法**：先执行 SELECT ... LIMIT 10，再另外执行第二条 SQL（这样会丢失关联）

### 第 5 步：校验并执行

- 先调用 sql_validate 校验语法
- 再调用 sql_execute 执行

### 第 6 步：结果异常处理

- 如果返回 0 行，重新审视 JOIN/IN 条件是否正确，修改后重试
- 如果结果行数远超预期，检查是否存在笛卡尔积问题

## 注意事项

- SQL 仅限 SELECT，不允许任何写操作
- JOIN 必须有明确的 ON 条件，禁止隐式笛卡尔积
- 中文关键词搜不到时，尝试对应的英文词（"订单" → "order"，"变动" → "change,log"）
- **不允许只返回中间结果**（如只返回 TOP N 列表而不继续查关联数据）""",
    required_tools=["sql_schema_search", "sql_schema", "sql_validate", "sql_execute"],
    max_steps=8,
    few_shot_tag="complex_query",
)

# 默认意图（不清楚时反问用户）
DEFAULT_INTENT = Intent(
    name="clarify",
    description="意图不明确，需要用户澄清",
    keywords=[],
    system_prompt="""你是一个对账系统助手。用户的问题不够明确，你需要友好地反问：

可以帮用户理解你的系统能做什么：
1. **对账** — 对比两组数据找出差异（如"对比GMV和订单金额"）
2. **即席查询** — 单表统计查询（如"GMV最高的5个直播间"）
3. **复杂查询** — 多表关联查询（如"销量最高的订单的变动记录"）
4. **查看表结构** — 了解数据表有哪些字段（如"live_gmv 表结构"）

请用户选择或更具体地描述需求。""",
    required_tools=[],
    max_steps=1,
    few_shot_tag="",
)

# 所有已注册意图
ALL_INTENTS = [RECONCILIATION_INTENT, ADHOC_QUERY_INTENT, SCHEMA_LOOKUP_INTENT, COMPLEX_QUERY_INTENT]
