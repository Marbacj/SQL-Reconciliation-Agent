"""LangGraph 装配：build_graph(ctx) -> compiled StateGraph。"""

from __future__ import annotations

import logging
from typing import Any, Optional

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph

from recon_v2.core.context import AgentContext
from recon_v2.orchestration import ctx_registry
from recon_v2.orchestration.nodes.act import act_node
from recon_v2.orchestration.nodes.clarify import clarify_node
from recon_v2.orchestration.nodes.observe import observe_decide, observe_node
from recon_v2.orchestration.nodes.plan import plan_node
from recon_v2.orchestration.nodes.reflect import reflect_node
from recon_v2.orchestration.nodes.route import route_decide, route_node
from recon_v2.orchestration.state import GraphState
from recon_v2.tools import build_default_registry

logger = logging.getLogger(__name__)


def build_graph(checkpointer: Optional[Any] = None):
    """构造编译后的 StateGraph。

    Args:
        checkpointer: 可选；None 时用 InMemorySaver（默认）。
    """
    graph = StateGraph(GraphState)

    graph.add_node("route", route_node)
    graph.add_node("clarify", clarify_node)
    graph.add_node("plan", plan_node)
    graph.add_node("act", act_node)
    graph.add_node("observe", observe_node)
    graph.add_node("reflect", reflect_node)

    graph.set_entry_point("route")

    # route → clarify | plan
    # "reject" 也走 clarify 节点，由 clarify_node 内部处理安全拒绝逻辑
    graph.add_conditional_edges(
        "route",
        route_decide,
        {"clarify": "clarify", "reject": "clarify", "plan": "plan"},
    )

    # clarify → END
    graph.add_edge("clarify", END)

    # plan → act
    graph.add_edge("plan", "act")

    # act → observe
    graph.add_edge("act", "observe")

    # observe → act (retry) | reflect (terminate)
    graph.add_conditional_edges(
        "observe",
        observe_decide,
        {"act": "act", "reflect": "reflect"},
    )

    # reflect → END
    graph.add_edge("reflect", END)

    return graph.compile(checkpointer=checkpointer or InMemorySaver())


def run_once(query: str, db_path: str, ctx: Optional[AgentContext] = None) -> dict:
    """便捷入口:跑一次 query,返回最终 state dict。"""
    # 确保 .env 被加载（CLI / 测试场景下不会自动加载）
    try:
        from dotenv import load_dotenv
        load_dotenv(override=False)
    except ImportError:
        pass

    if ctx is None:
        ctx = AgentContext(query=query, db_path=db_path)
        ctx.tools = build_default_registry(db_path)
        # 初始化 LLM Gateway (支持环境变量配置)
        try:
            from recon_v2.infra.llm_gateway import LLMGateway
            ctx.llm = LLMGateway()
            logger.info(f"LLM Gateway initialized: {ctx.llm.provider}/{ctx.llm.model}")
        except Exception as e:
            logger.warning(f"LLM Gateway init failed, fallback to template mode: {e}")

    ctx_registry.register(ctx)
    try:
        graph = build_graph()
        config = {"configurable": {"thread_id": ctx.trace_id}}
        out = graph.invoke(
            {
                "query": query,
                "db_path": db_path,
                "ctx_id": ctx.trace_id,
            },
            config=config,
        )
        return dict(out)
    finally:
        ctx_registry.remove(ctx.trace_id)
