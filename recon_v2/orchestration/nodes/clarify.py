"""Clarify node：低置信度或 boundary_edge 走澄清/拒绝。"""

from __future__ import annotations

import logging

from recon_v2.infra.sql_safety import is_safe
from recon_v2.infra.tracing import span
from recon_v2.orchestration.ctx_registry import get as get_ctx
from recon_v2.orchestration.state import GraphState

logger = logging.getLogger(__name__)


def clarify_node(state: GraphState) -> dict:
    """boundary_edge / 低置信度 → 输出拒绝或澄清问题。"""
    ctx = get_ctx(state["ctx_id"])
    query = state["query"]

    with span("clarify"):
        intent = state.get("intent", "")
        conf = state.get("confidence", 0.0)

        # 直接看 query 是不是 DDL/DML
        verdict = is_safe(query)
        query_upper = query.strip().upper()
        # 已经是 REJECT/CLARIFY 占位符 (不该进 clarify，但防御一下)
        if query_upper in {"REJECT", "CLARIFY"}:
            return {
                "sql": query_upper,
                "answer": "已拒绝/已澄清。",
                "final_status": "rejected" if query_upper == "REJECT" else "clarify",
                "step_counter": state.get("step_counter", 0) + 1,
            }
        if not verdict.is_safe and any(
            kw in query.lower() for kw in ["drop ", "delete ", "update ", "insert ", "alter ", "truncate "]
        ):
            return {
                "sql": "REJECT",
                "answer": f"拒绝执行：仅允许只读 SELECT/WITH 查询。原因：{verdict.reason}",
                "final_status": "rejected",
                "step_counter": state.get("step_counter", 0) + 1,
            }

        # 注入攻击模式
        if "; --" in query or "/*" in query:
            return {
                "sql": "REJECT",
                "answer": "拒绝执行：检测到可疑的多语句/注释模式（疑似 SQL 注入）。",
                "final_status": "rejected",
                "step_counter": state.get("step_counter", 0) + 1,
            }

        # 与对账业务无关
        if intent == "boundary_edge":
            answer = (
                "您的问题超出 SQL 对账 Agent 的服务范围（如 DDL/DML、注入、非业务查询）。"
                "请提供与订单 / 退款 / 支付对账相关的问题。"
            )
            ctx.step()
            return {
                "sql": "CLARIFY" if "今天有什么菜" in query else "REJECT",
                "answer": answer,
                "final_status": "clarify",
                "step_counter": ctx.step_counter,
            }

        # 普通低置信度
        ctx.step()
        return {
            "sql": "CLARIFY",
            "answer": (
                f"请澄清您的对账意图。当前识别为 `{intent}` (confidence={conf:.2f})，"
                "建议明确：1) 涉及哪张表 / 时间范围 / 维度 2) 期望返回什么字段 / 聚合。"
            ),
            "clarify_question": "请提供更具体的对账维度和时间窗口。",
            "final_status": "clarify",
            "step_counter": ctx.step_counter,
        }
