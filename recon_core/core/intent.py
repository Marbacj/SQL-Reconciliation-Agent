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

# 默认意图（不清楚时反问用户）
DEFAULT_INTENT = Intent(
    name="clarify",
    description="意图不明确，需要用户澄清",
    keywords=[],
    system_prompt="""你是一个对账系统助手。用户的问题不够明确，你需要友好地反问：

可以帮用户理解你的系统能做什么：
1. **对账** — 对比两组数据找出差异（如"对比GMV和订单金额"）
2. **即席查询** — 单表统计查询（如"GMV最高的5个直播间"）
3. **查看表结构** — 了解数据表有哪些字段（如"live_gmv 表结构"）

请用户选择或更具体地描述需求。""",
    required_tools=[],
    max_steps=1,
    few_shot_tag="",
)

# 所有已注册意图
ALL_INTENTS = [RECONCILIATION_INTENT, ADHOC_QUERY_INTENT, SCHEMA_LOOKUP_INTENT]
