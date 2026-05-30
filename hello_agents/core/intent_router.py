"""
意图路由器 — 分类 → 加载 Intent → 过滤工具 → 注入 Agent

两段式路由的核心编排层。
"""

from typing import Optional
from dataclasses import dataclass

from .intent import Intent, IntentLabel, DEFAULT_INTENT
from .intent_registry import IntentRegistry


@dataclass
class RouteResult:
    """路由结果"""
    label: IntentLabel
    intent: Intent
    tools_before: int       # 过滤前工具数
    tools_after: int        # 过滤后工具数
    system_prompt: str      # 最终使用的 System Prompt
    few_shot_prompt: str    # 该 Intent 专属的 few-shot 注入


class IntentRouter:
    """意图路由器

    用法:
        router = IntentRouter(registry)
        result = router.route(query, llm, case_store)

        # 将路由结果注入 Agent
        agent.system_prompt = result.system_prompt
        agent.tool_registry = filtered_registry
        agent.max_steps = result.intent.max_steps
    """

    def __init__(self, registry: Optional[IntentRegistry] = None):
        self.registry = registry or IntentRegistry()
        self._last_route: Optional[RouteResult] = None

    @property
    def last_route(self) -> Optional[RouteResult]:
        """最近一次路由结果（用于 UI 展示）"""
        return self._last_route

    def route(
        self,
        query: str,
        llm=None,
        case_store=None,
    ) -> RouteResult:
        """执行完整路由流程

        1. 分类意图
        2. 加载 Intent 定义
        3. 计算工具过滤信息
        4. 构建 few-shot 注入

        Args:
            query: 用户输入
            llm: LLM 实例（可选，用于 LLM 兜底分类）
            case_store: CaseStore 实例（可选，用于 few-shot 注入）

        Returns:
            RouteResult
        """
        # Phase 1: 分类
        label = self.registry.classify(query, llm=llm)
        intent = self.registry.get(label.intent) or DEFAULT_INTENT

        # Phase 2: 计算工具过滤
        all_tools = 5  # 当前总共 5 个工具
        filtered_count = len(intent.required_tools)

        # Phase 3: 构建 few-shot
        few_shot = ""
        if case_store and intent.few_shot_tag:
            similar = case_store.find_similar(query, top_k=2)
            if similar:
                from ..tools.builtin.case_store import build_few_shot_prompt
                few_shot = build_few_shot_prompt(similar)

        # Phase 4: 组装 System Prompt
        system_prompt = intent.system_prompt
        if few_shot:
            system_prompt += "\n" + few_shot

        result = RouteResult(
            label=label,
            intent=intent,
            tools_before=all_tools,
            tools_after=filtered_count,
            system_prompt=system_prompt,
            few_shot_prompt=few_shot,
        )

        self._last_route = result
        return result

    def route_summary(self) -> str:
        """上次路由的摘要（用于日志和 UI）"""
        if not self._last_route:
            return "尚未路由"

        r = self._last_route
        return (
            f"[{r.label.method}] → {r.intent.name} "
            f"(置信度 {r.label.confidence:.0%}) | "
            f"工具 {r.tools_before}→{r.tools_after} | "
            f"max_steps={r.intent.max_steps}"
        )
