"""Clarify node：低置信度或 boundary_edge 走澄清/拒绝。"""

from __future__ import annotations

import logging

from recon_v2.infra.sql_safety import is_safe
from recon_v2.infra.tracing import span
from recon_v2.orchestration.ctx_registry import get as get_ctx
from recon_v2.orchestration.state import GraphState
from recon_v2.orchestration.nodes.error_diagnosis import build_rejected_message

logger = logging.getLogger(__name__)

# 低置信度时的引导模板
_CLARIFY_EXAMPLES = """
**示例（好的提问方式）：**
- 「对比昨天直播 GMV 和订单金额的差异」
- 「查一下上周退款率最高的渠道」
- 「orders 表和 payments 表的金额不一致在哪里」

**提问建议：**
1. 明确涉及哪张表（如「订单表」「支付表」）
2. 指定时间范围（如「昨天」「本月」「最近 7 天」）
3. 说明关注的指标（如「金额」「笔数」「差异」）
"""


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

        # DDL/DML 操作 → 安全拒绝，给出友好解释
        if not verdict.is_safe and any(
            kw in query.lower() for kw in ["drop ", "delete ", "update ", "insert ", "alter ", "truncate "]
        ):
            ctx.step()
            return {
                "sql": "REJECT",
                "answer": build_rejected_message(query),
                "final_status": "rejected",
                "step_counter": ctx.step_counter,
            }

        # 注入攻击模式 → 安全拒绝，给出友好解释
        if "; --" in query or "/*" in query:
            ctx.step()
            return {
                "sql": "REJECT",
                "answer": build_rejected_message(query),
                "final_status": "rejected",
                "step_counter": ctx.step_counter,
            }

        # 与对账业务无关（boundary_edge 非 DDL，如"今天有什么菜"）
        if intent == "boundary_edge":
            # 判断是否是明显的 DDL/写操作
            is_write_op = any(
                kw in query.lower()
                for kw in ["drop", "delete", "update", "insert", "alter", "truncate", "create"]
            )
            if is_write_op:
                answer = build_rejected_message(query)
                final_status = "rejected"
                sql_marker = "REJECT"
            else:
                answer = (
                    "🚫 您的问题超出 SQL 对账助手的服务范围\n\n"
                    "本系统专注于**数据对账与差异分析**，支持：\n"
                    "- 跨表数据比对（订单 vs 支付 vs 退款）\n"
                    "- 金额/笔数差异查找\n"
                    "- 时间窗口数据核对\n"
                    "- 单表聚合统计查询\n\n"
                    + _CLARIFY_EXAMPLES
                )
                final_status = "clarify"
                sql_marker = "CLARIFY"

            ctx.step()
            return {
                "sql": sql_marker,
                "answer": answer,
                "final_status": final_status,
                "step_counter": ctx.step_counter,
            }

        # 普通低置信度 → 给出引导性澄清问题
        ctx.step()
        return {
            "sql": "CLARIFY",
            "answer": (
                f"🤔 我对您的问题理解不太确定（置信度 {conf:.0%}），需要进一步确认\n\n"
                f"您的问题：「{query}」\n\n"
                f"当前猜测意图：`{intent}`\n\n"
                "**请帮我确认一下：**\n"
                "- 您想查询哪张表？（如订单表、支付表、退款表）\n"
                "- 时间范围是？（如昨天、本周、上个月）\n"
                "- 想看什么结果？（如总金额、差异条数、具体记录）\n"
                + _CLARIFY_EXAMPLES
            ),
            "clarify_question": "请提供更具体的查询表名、时间范围和目标指标。",
            "final_status": "clarify",
            "step_counter": ctx.step_counter,
        }
