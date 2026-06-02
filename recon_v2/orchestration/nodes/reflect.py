"""Reflect node：异步提炼 skill（Stage 4 接入 AsyncSkillQueue），主流程不阻塞。"""

from __future__ import annotations

import logging

from recon_v2.infra.tracing import span
from recon_v2.orchestration.ctx_registry import get as get_ctx
from recon_v2.orchestration.state import GraphState

logger = logging.getLogger(__name__)


def reflect_node(state: GraphState) -> dict:
    """终态节点：异步提交 skill candidate，主流程立即返回。"""
    ctx = get_ctx(state["ctx_id"])
    with span("reflect"):
        # Stage 4：ctx.memory.async_skill_queue.submit(candidate)
        if ctx.memory is not None and hasattr(ctx.memory, "submit_skill_review"):
            try:
                ctx.memory.submit_skill_review(
                    trace_id=ctx.trace_id,
                    query=state.get("query", ""),
                    sql=state.get("sql", ""),
                    answer=state.get("answer", ""),
                    success=state.get("final_status") == "ok",
                )
            except Exception as e:
                logger.warning("submit_skill_review failed: %s", e)

        budget = ctx.budget.snapshot()
        return {
            "token_cost": budget["tokens_used"],
            "cost_usd": 0.0,  # 后续从 CostTracker 取
            "final_status": state.get("final_status", "ok"),
        }
