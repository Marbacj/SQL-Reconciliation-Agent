"""对账专用 Agent — 基于 ReActAgent，自然语言驱动的自动化 SQL 对账

用法:
    from hello_agents.agents.reconciliation_agent import ReconciliationAgent
    from hello_agents.core.llm import HelloAgentsLLM

    llm = HelloAgentsLLM(provider="deepseek", model="deepseek-chat", ...)
    agent = ReconciliationAgent(
        name="ReconAgent",
        llm=llm,
        db_path="data/mock_reconciliation.db",
        max_steps=8
    )
    result = agent.run("对比昨天直播GMV和订单系统金额，找出差异超过100元的直播间")
"""

import json
from typing import Optional, List, Dict, Any

from .react_agent import ReActAgent
from ..core.llm import HelloAgentsLLM
from ..core.config import Config
from ..tools.registry import ToolRegistry
from ..tools.builtin.sql_tool import SQLTool
from ..tools.builtin.diff_tool import DiffTool
from ..tools.builtin.report_tool import ReportTool


# ==================== 对账专用 System Prompt ====================

RECONCILIATION_SYSTEM_PROMPT = """你是一个专业的数据对账分析师。你的任务是用自然语言理解对账需求，然后通过工具完成自动化对账。

## 可用工具

你拥有以下工具（已自动注册，无需手动指定）：

1. **sql_schema(table_name)** — 查询数据表的结构（字段名、类型、示例数据）
2. **sql_execute(sql)** — 执行 SQL SELECT 查询并返回结果
3. **sql_validate(sql)** — 校验 SQL 语法（通过 EXPLAIN 解析，不实际执行）
4. **diff_compare(sql_a, sql_b, key_column, compare_columns)** — 比对两组 SQL 查询结果
5. **report_generate(title, diff_result, conclusion)** — 生成并保存 Markdown 对账报告

## 对账工作流（严格按此顺序执行）

请严格按照以下步骤完成每一次对账任务：

### 第 1 步：了解表结构
使用 sql_schema 查询涉及的所有数据表的结构。在生成任何 SQL 之前必须完成此步骤。
- 如果用户提到了"GMV"、"直播"、"订单"等关键词，查询对应的表
- 确保理解每个字段的业务含义

### 第 2 步：生成对账 SQL
根据用户的问题和你了解的表结构，生成两条查询 SQL：
- SQL A: 查询左表（如 live_gmv 的汇总数据）
- SQL B: 查询右表（如 order_amount 的汇总数据）
- 两条 SQL 必须包含相同的主键列（用于后续 JOIN 比对）
- 时间范围要与用户要求一致

### 第 3 步：执行查询
使用 sql_execute 分别执行两条 SQL，获取实际数据。
- 如果 SQL 执行失败，分析错误信息并修正后重试（最多重试 2 次）
- 记录每个查询返回的行数

### 第 4 步：差异比对
使用 diff_compare 比对两组结果：
- sql_a: 左表的 SQL
- sql_b: 右表的 SQL
- key_column: 主键列名（两表的关联字段，如 live_id）
- compare_columns: 要比对的数值列名，逗号分隔（如 total_gmv,total_order）

### 第 5 步：生成报告
使用 report_generate 生成对账报告：
- title: 报告标题（包含日期和对账主题）
- diff_result: 第 4 步 diff_compare 的完整输出
- conclusion: 你的分析和结论（包括差异原因分析、建议后续行动）

## 关键注意事项

- **先生成 SQL，再执行** — 不要在不知道表结构的情况下写 SQL
- **SQL 仅限 SELECT** — 不要尝试 INSERT/UPDATE/DELETE/DROP 操作
- **主键必须存在** — diff_compare 需要 key_column 在两表结果中都存在
- **数值列要精确** — compare_columns 应该是数值类型的字段
- **报告必须生成** — 每次对账任务必须以 report_generate 结束

## 对账判断标准

- 差异在 5% 以内：正常（统计口径差异）
- 差异 5%-20%：需要关注
- 差异超过 20% 或一方数据完全缺失：严重问题

开始你的对账分析工作。"""


class ReconciliationAgent(ReActAgent):
    """对账专用 Agent

    与普通 ReActAgent 的区别：
    1. 使用对账专用 System Prompt（包含完整的对账工作流指令）
    2. 默认注册所有对账工具（SQLTool + DiffTool + ReportTool）
    3. 内置 SQL 失败重试意识（在 Prompt 中指导）
    4. 更高的默认 max_steps（8 步，适应完整对账流程）

    Args:
        name: Agent 名称
        llm: LLM 实例
        db_path: SQLite 数据库路径
        system_prompt: 自定义系统提示词（可选，默认使用对账专用 Prompt）
        config: 配置对象
        max_steps: 最大执行步数（默认 8）
        output_dir: 报告输出目录（默认 "reports"）
    """

    def __init__(
        self,
        name: str,
        llm: HelloAgentsLLM,
        db_path: str,
        system_prompt: Optional[str] = None,
        config: Optional[Config] = None,
        max_steps: int = 8,
        output_dir: str = "reports"
    ):
        # 1. 构建工具注册表
        tool_registry = ToolRegistry()

        # 注册对账工具（expandable=True 会自动展开子工具）
        tool_registry.register_tool(SQLTool(db_path=db_path))
        tool_registry.register_tool(DiffTool(db_path=db_path))
        tool_registry.register_tool(ReportTool(output_dir=output_dir))

        # 打印已注册的工具
        tool_names = tool_registry.list_tools()
        print(f"🔧 已注册 {len(tool_names)} 个对账工具: {', '.join(tool_names)}")

        # 2. 初始化基类
        super().__init__(
            name=name,
            llm=llm,
            tool_registry=tool_registry,
            system_prompt=system_prompt or RECONCILIATION_SYSTEM_PROMPT,
            config=config,
            max_steps=max_steps
        )

        self.db_path = db_path
        self.output_dir = output_dir

    def run(self, input_text: str, **kwargs) -> str:
        """运行对账分析

        Args:
            input_text: 自然语言对账需求（如 "对比昨天直播GMV和订单金额差异"）

        Returns:
            最终对账报告
        """
        print(f"\n📊 {self.name} 启动对账任务")
        print(f"📝 用户需求: {input_text}")
        print(f"📁 数据库: {self.db_path}")
        print(f"📂 报告目录: {self.output_dir}")
        print("=" * 60)

        result = super().run(input_text, **kwargs)

        print("\n" + "=" * 60)
        print(f"✅ 对账任务完成")
        return result
