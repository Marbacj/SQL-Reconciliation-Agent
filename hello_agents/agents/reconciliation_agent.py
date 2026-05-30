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
import re
from typing import Optional, List, Dict, Any

from .react_agent import ReActAgent
from ..core.llm import HelloAgentsLLM
from ..core.config import Config
from ..core.intent import Intent, IntentLabel, RECONCILIATION_INTENT
from ..core.intent_registry import IntentRegistry
from ..core.intent_router import IntentRouter, RouteResult
from ..tools.registry import ToolRegistry
from ..tools.builtin.sql_tool import SQLTool
from ..tools.builtin.diff_tool import DiffTool
from ..tools.builtin.report_tool import ReportTool
from ..tools.builtin.case_store import CaseStore, build_few_shot_prompt


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
        output_dir: str = "reports",
        case_store: Optional[CaseStore] = None,
        intent_router: Optional[IntentRouter] = None,
    ):
        # 1. 案例库（技能积累）
        self.case_store = case_store or CaseStore()

        # 2. 意图路由器
        self.intent_router = intent_router or IntentRouter()
        self._last_route: Optional[RouteResult] = None

        # 3. 构建工具注册表（注册全部 5 个工具，路由时按需过滤）
        tool_registry = ToolRegistry()
        tool_registry.register_tool(SQLTool(db_path=db_path))
        tool_registry.register_tool(DiffTool(db_path=db_path))
        tool_registry.register_tool(ReportTool(output_dir=output_dir))

        tool_names = tool_registry.list_tools()
        print(f"🔧 已注册 {len(tool_names)} 个工具: {', '.join(tool_names)}")

        # 4. System Prompt（保留旧兼容路径 + 新意图路由路径）
        base_prompt = system_prompt or RECONCILIATION_SYSTEM_PROMPT
        self._base_prompt = base_prompt

        # 5. 初始化基类
        super().__init__(
            name=name,
            llm=llm,
            tool_registry=tool_registry,
            system_prompt=base_prompt,
            config=config,
            max_steps=max_steps
        )

        self.db_path = db_path
        self.output_dir = output_dir

    def run(self, input_text: str, **kwargs) -> str:
        """运行对账分析（含意图路由 + 技能积累）

        1. 意图路由 → 选择 Prompt + 工具过滤 + max_steps
        2. CaseStore 检索 → few-shot 注入
        3. ReAct 推理执行
        4. 自动保存案例

        Args:
            input_text: 自然语言对账需求

        Returns:
            最终对账报告
        """
        print(f"\n📊 {self.name} 启动")
        print(f"📝 用户需求: {input_text}")

        # ── Phase 1: 意图路由 ──
        route = self.intent_router.route(
            input_text,
            llm=self.llm,
            case_store=self.case_store,
        )
        self._last_route = route

        print(f"🎯 意图路由: {self.intent_router.route_summary()}")
        if route.label.reasoning:
            print(f"   理由: {route.label.reasoning}")

        # 应用路由结果
        intent = route.intent
        self.system_prompt = route.system_prompt
        self.max_steps = intent.max_steps

        # 工具过滤：只保留该 Intent 需要的工具
        if intent.required_tools:
            self.tool_registry.keep_only(intent.required_tools)
            remaining = self.tool_registry.list_tools()
            print(f"🔧 工具过滤后: {', '.join(remaining)}")

        print("=" * 60)

        result = super().run(input_text, **kwargs)

        # ── 保存案例（仅对账意图） ──
        if route.label.intent == "reconciliation":
            self._save_case(input_text)

        print("\n" + "=" * 60)
        print(f"✅ 任务完成 [{route.label.intent}]")

        return result

    def _save_case(self, query: str):
        """从最近一次执行中提取 SQL 并保存案例"""
        try:
            # 从 Agent 的消息历史中提取关键信息
            sql_a = ""
            sql_b = ""
            key_column = ""
            compare_columns = ""

            # 遍历历史消息找 SQL 执行记录
            for msg in getattr(self, '_history', []):
                content = str(msg)
                # 提取 SQL（匹配 SELECT ... FROM ...）
                sqls = re.findall(
                    r'(SELECT\s+.+?\s+FROM\s+.+?)(?:ORDER BY|GROUP BY|LIMIT|$|\))',
                    content, re.IGNORECASE | re.DOTALL
                )
                if len(sqls) >= 2:
                    sql_a = sqls[0].strip()
                    sql_b = sqls[1].strip()

                # 提取 key_column
                key_match = re.search(r"key_column[\"']?\s*[:=]\s*[\"'](\w+)", content)
                if key_match:
                    key_column = key_match.group(1)

                # 提取 compare_columns
                cmp_match = re.search(
                    r"compare_columns[\"']?\s*[:=]\s*[\"']([\w,]+)",
                    content
                )
                if cmp_match:
                    compare_columns = cmp_match.group(1)

            if not sql_a:
                return  # 没有 SQL 就不保存

            # 提取差异摘要
            diff_summary = "对账完成"
            conclusion = "见完整报告"

            self.case_store.save(
                query=query,
                sql_a=sql_a,
                sql_b=sql_b,
                key_column=key_column or "unknown",
                compare_columns=compare_columns or "unknown",
                diff_summary=diff_summary,
                conclusion=conclusion,
            )
        except Exception:
            pass  # 案例保存失败不应影响对账主流程
