"""Reflect node：提炼 skill + 更新 skill 使用统计 + 记录对账差异模式 + RAG Feedback。"""

from __future__ import annotations

import logging
import re
from typing import List

from recon_v2.infra.tracing import span
from recon_v2.orchestration.ctx_registry import get as get_ctx
from recon_v2.orchestration.state import GraphState

logger = logging.getLogger(__name__)

# Tables that suggest a reconciliation discrepancy was the focus of the query
_RECON_KEYWORDS = re.compile(
    r"(对账|差异|不一致|mismatch|discrepanc|reconcil|missing|缺失|多了|少了|重复|duplicate)",
    re.IGNORECASE,
)


def _extract_tables_from_sql(sql: str) -> List[str]:
    """Best-effort extraction of table names from a SQL string."""
    return list(set(re.findall(r"FROM\s+(\w+)|JOIN\s+(\w+)", sql, re.IGNORECASE)))


def _flatten_tables(raw: list) -> List[str]:
    result = []
    for item in raw:
        if isinstance(item, tuple):
            result.extend(t for t in item if t)
        elif item:
            result.append(item)
    return list(set(result))


def reflect_node(state: GraphState) -> dict:
    """终态节点：提炼 skill + 更新 skill 使用统计 + 记录对账差异模式 + RAG Feedback。"""
    ctx = get_ctx(state["ctx_id"])
    with span("reflect"):
        final_status = state.get("final_status", "ok")
        query = state.get("query", "")
        sql = state.get("sql", "")
        answer = state.get("answer", "")
        success = final_status == "ok"

        # ── Skill 提炼 ────────────────────────────────────────────────────────
        review_result = {}
        if ctx.memory is not None and hasattr(ctx.memory, "submit_skill_review"):
            try:
                review_result = ctx.memory.submit_skill_review(
                    trace_id=ctx.trace_id,
                    query=query,
                    sql=sql,
                    answer=answer,
                    success=success,
                )
                if review_result.get("skill_added"):
                    logger.info("reflect: new skill added id=%s", review_result.get("skill_id"))
            except Exception as e:
                logger.warning("submit_skill_review failed: %s", e)

        # ── Skill 使用反馈（闭合回路）─────────────────────────────────────────
        matched_skill_ids = ctx.extra.pop("_matched_skill_ids", [])
        if matched_skill_ids and ctx.memory is not None and hasattr(ctx.memory, "update_skill_usage"):
            for skill_id in matched_skill_ids:
                try:
                    ctx.memory.update_skill_usage(skill_id, success)
                except Exception as e:
                    logger.debug("update_skill_usage failed for skill#%s: %s", skill_id, e)
            logger.debug("reflect: updated usage for %d skills (success=%s)", len(matched_skill_ids), success)

        # ── Discrepancy Pattern 记录 ──────────────────────────────────────────
        if ctx.memory is not None and hasattr(ctx.memory, "log_discrepancy_pattern"):
            if _RECON_KEYWORDS.search(query) or _RECON_KEYWORDS.search(answer):
                try:
                    tables = _flatten_tables(_extract_tables_from_sql(sql))
                    # Derive a concise pattern from the answer (first 200 chars)
                    pattern_text = answer[:200].strip()
                    if pattern_text and len(pattern_text) > 10:
                        category = "recon_mismatch" if success else "recon_error"
                        ctx.memory.log_discrepancy_pattern(
                            pattern_text=pattern_text,
                            tables_involved=tables,
                            category=category,
                            example_query=query,
                        )
                        logger.debug("reflect: logged discrepancy pattern (category=%s)", category)
                except Exception as e:
                    logger.debug("log_discrepancy_pattern failed: %s", e)

        # ── RAG Feedback ──────────────────────────────────────────────────────
        rag_sources = state.get("rag_sources") or []
        if rag_sources and ctx.memory is not None and hasattr(ctx.memory, "log_retrieval_feedback"):
            try:
                ctx.memory.log_retrieval_feedback(
                    trace_id=ctx.trace_id,
                    query=query,
                    rag_sources=rag_sources,
                    final_status=final_status,
                )
                logger.debug("reflect: logged rag_feedback for %d docs", len(rag_sources))
            except Exception as e:
                logger.warning("log_retrieval_feedback failed: %s", e)

        # ── Episodic Memory 自动写入（自我改进闭环）──────────────────────────
        # 每次查询结束都落盘，outcome=1 时 importance 足够高会自动 promoted=1
        # promoted 案例在 route_node few-shot 注入时优先使用，无需人工干预
        if ctx.memory is not None and hasattr(ctx.memory, "write"):
            try:
                result = ctx.memory.write(
                    trace_id=ctx.trace_id,
                    query=query,
                    intent=state.get("intent", ""),
                    sql=sql,
                    answer=answer,
                    outcome=1 if success else 0,
                )
                if result.get("promoted"):
                    logger.info(
                        "reflect: case auto-promoted to few-shot pool (trace=%s intent=%s)",
                        ctx.trace_id, state.get("intent", ""),
                    )
                else:
                    logger.debug(
                        "reflect: episodic case written (trace=%s importance=%.2f promoted=%s)",
                        ctx.trace_id, result.get("importance", 0), result.get("promoted"),
                    )
            except Exception as e:
                logger.warning("reflect: memory.write failed: %s", e)

        budget = ctx.budget.snapshot()
        return {
            "token_cost": budget["tokens_used"],
            "cost_usd": 0.0,
            "final_status": final_status,
        }
