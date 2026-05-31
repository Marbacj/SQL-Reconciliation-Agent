"""
双层 Agent 架构 — Plan & Solve（宏观编排） + ReAct（微观执行）

ReconciliationPlanAndSolveAgent:
  - Planner: 将"对比多表差异"拆解为独立对账子任务
  - Executor: 每个子任务调用对账工具链执行
  - 意图路由: 对账/即席查询/Schema 查询自动分流
"""

from typing import Optional

from .plan_solve_agent import PlanAndSolveAgent, Planner, Executor
from ..core.llm import HelloAgentsLLM
from ..core.config import Config
from ..core.intent_router import IntentRouter
from ..tools.registry import ToolRegistry
from ..tools.builtin.sql_tool import SQLTool
from ..tools.builtin.diff_tool import DiffTool
from ..tools.builtin.report_tool import ReportTool
from ..tools.builtin.case_store import CaseStore


RECON_PLANNER_PROMPT = """你是数据对账规划专家。将对账需求拆解为独立可执行的子任务。

每个子任务必须是单一、明确的操作：
- 查询表结构
- 汇总某个表的指标
- 比对两组数据
- 生成报告

规划原则：
1. 先查结构，再写 SQL
2. 每个 SQL 查询是独立子任务
3. 最后一步是生成报告"""


class ReconciliationPlanAndSolveAgent(PlanAndSolveAgent):
    """对账专用 Plan & Solve Agent

    双层架构：
      Planner  → 宏观任务拆解
      Executor → 微观工具调用执行
    """

    def __init__(
        self,
        name: str,
        llm: HelloAgentsLLM,
        db_path: str,
        config: Optional[Config] = None,
        max_plan_steps: int = 6,
        output_dir: str = "reports",
        case_store: Optional[CaseStore] = None,
        intent_router: Optional[IntentRouter] = None,
    ):
        self.db_path = db_path
        self.output_dir = output_dir
        self.case_store = case_store or CaseStore()
        self.intent_router = intent_router or IntentRouter()
        self._last_route = None

        # 构建对账工具注册表
        tool_registry = ToolRegistry()
        tool_registry.register_tool(SQLTool(db_path=db_path))
        tool_registry.register_tool(DiffTool(db_path=db_path))
        tool_registry.register_tool(ReportTool(output_dir=output_dir))

        tool_names = tool_registry.list_tools()
        print(f"🔧 [Plan&Solve] 已注册 {len(tool_names)} 个工具: {', '.join(tool_names)}")

        # Planner + Executor（使用框架原生的）
        planner = Planner(llm, system_prompt=RECON_PLANNER_PROMPT)
        executor = Executor(
            llm_client=llm,
            tool_registry=tool_registry,
            enable_tool_calling=True,
            max_tool_iterations=3,
        )

        super().__init__(
            name=name,
            llm=llm,
            planner=planner,
            executor=executor,
            tool_registry=tool_registry,
            config=config,
            max_plan_steps=max_plan_steps,
        )

    def run(self, input_text: str, **kwargs) -> str:
        """运行双层对账：Planner 拆解 → Executor 逐步执行"""
        print(f"\n📊 {self.name} (Plan & Solve) 启动")
        print(f"📝 用户需求: {input_text}")

        # 意图路由（决定是否需要 Plan & Solve）
        route = self.intent_router.route(input_text, llm=self.llm, case_store=self.case_store)
        self._last_route = route
        print(f"🎯 路由: {self.intent_router.route_summary()}")

        # 简单意图——跳过 Plan，直接执行
        if route.label.intent in ("schema_lookup", "adhoc_query"):
            print("⚡ 简单意图，跳过 Planner 直接执行")
            from .reconciliation_agent import ReconciliationAgent
            agent = ReconciliationAgent(
                name=self.name,
                llm=self.llm,
                db_path=self.db_path,
                output_dir=self.output_dir,
                case_store=self.case_store,
                intent_router=self.intent_router,
            )
            return agent.run(input_text, **kwargs)

        # 复杂对账——Plan & Solve
        result = super().run(input_text, **kwargs)
        print(f"✅ Plan & Solve 完成 [{route.label.intent}]")
        return result
