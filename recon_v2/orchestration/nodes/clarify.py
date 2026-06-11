"""Clarify node：低置信度或 boundary_edge 走澄清/拒绝。

LLM 澄清流程：
1. ctx.llm 存在时：调用 LLM 生成个性化澄清问题 + 3~4 条快捷建议
2. ctx.llm 不存在时：降级为关键词规则模板（保持离线可用）
"""

from __future__ import annotations

import json
import logging
import re
from typing import List

from recon_v2.infra.sql_safety import is_safe
from recon_v2.infra.tracing import span
from recon_v2.orchestration.ctx_registry import get as get_ctx
from recon_v2.orchestration.state import GraphState
from recon_v2.orchestration.nodes.error_diagnosis import build_rejected_message

logger = logging.getLogger(__name__)

# ── 系统提示 ──────────────────────────────────────────────────────────────
_CLARIFY_SYSTEM = (
    "你是一个 SQL 数据对账助手。用户的问题不够具体，你需要用友好、简洁的方式问清楚。\n"
    "输出要求（必须是合法 JSON，不要其他内容）:\n"
    "{\n"
    '  "question": "一段 Markdown 格式的澄清问题，2~4 个要点，用 **加粗** 高亮关键词",\n'
    '  "suggestions": ["选项1", "选项2", "选项3", "选项4"]  // 3~4 个完整的示例查询，用户点击后可直接发送\n'
    "}\n"
    "约束：\n"
    "- question 不超过 150 字\n"
    "- suggestions 是可直接发送的完整自然语言查询，不是单个词\n"
    "- suggestions 要与用户原始问题的语义相关，覆盖常见补充方向\n"
    "- 语言：中文"
)

# ── 降级模板（LLM 不可用时使用）──────────────────────────────────────────
_FALLBACK_QUESTION = (
    "我需要更多信息来理解您的问题，请帮我确认：\n\n"
    "- **查哪张表？**（如订单表、支付表、退款表）\n"
    "- **时间范围？**（如昨天、本周、上个月）\n"
    "- **关注什么指标？**（如总金额、差异条数、具体记录）"
)


def _build_suggestions_fallback(query: str) -> List[str]:
    """关键词规则生成快捷建议（LLM 降级时使用）。"""
    q = query.lower()
    if any(kw in q for kw in ["今天", "昨天", "本周", "上周", "本月", "时间"]):
        return [
            "今天的订单总金额是多少",
            "昨天的 GMV 和今天对比",
            "本周支付笔数汇总",
            "最近 7 天退款率走势",
        ]
    if any(kw in q for kw in ["对账", "差异", "不一致", "对比", "diff"]):
        return [
            "订单表和支付表金额不一致的记录",
            "昨天 GMV 数据对账",
            "退款金额和订单金额的差额",
            "各渠道支付成功率与退款率对比",
        ]
    if any(kw in q for kw in ["走势", "趋势", "变化", "增长"]):
        return [
            "最近 30 天每天的 GMV 走势",
            "按月统计过去半年支付笔数",
            "本月 GMV 比上月增长多少",
            "每周退款率趋势",
        ]
    return [
        "今天订单总金额是多少",
        "订单表和支付表的差异",
        "最近 7 天 GMV 走势",
        "按渠道统计支付成功率",
    ]


def _llm_clarify(ctx, query: str, intent: str, conf: float) -> tuple[str, List[str]]:
    """调用 LLM 生成澄清问题和快捷选项。

    返回 (question_markdown, suggestions_list)。
    失败时降级返回模板内容。
    """
    if ctx.llm is None:
        return _FALLBACK_QUESTION, _build_suggestions_fallback(query)

    user_msg = (
        f"用户输入：「{query}」\n"
        f"系统猜测意图：{intent}（置信度 {conf:.0%}，太低，需要澄清）\n\n"
        "请生成澄清问题和快捷建议，输出 JSON。"
    )
    try:
        out = ctx.llm.chat(
            messages=[
                {"role": "system", "content": _CLARIFY_SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
            trace_id=ctx.trace_id,
            temperature=0.3,
            max_tokens=300,
        )
        raw = out.content.strip()
        # 提取 JSON（防止 LLM 输出额外说明）
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start < 0 or end <= start:
            raise ValueError(f"no JSON found: {raw[:100]}")
        payload = json.loads(raw[start:end])
        question    = payload.get("question", "").strip() or _FALLBACK_QUESTION
        suggestions = payload.get("suggestions", [])
        # 过滤空字符串，最多取 4 条
        suggestions = [s for s in suggestions if isinstance(s, str) and s.strip()][:4]
        if not suggestions:
            suggestions = _build_suggestions_fallback(query)
        return question, suggestions
    except Exception as e:
        logger.warning("[CLARIFY] LLM clarify failed, using fallback: %s", e)
        return _FALLBACK_QUESTION, _build_suggestions_fallback(query)


def clarify_node(state: GraphState) -> dict:
    """boundary_edge / 低置信度 → 输出拒绝或澄清问题。"""
    ctx = get_ctx(state["ctx_id"])
    query = state["query"]

    with span("clarify"):
        intent = state.get("intent", "")
        conf   = state.get("confidence", 0.0)

        # 直接看 query 是不是 DDL/DML
        verdict     = is_safe(query)
        query_upper = query.strip().upper()

        # 已经是 REJECT/CLARIFY 占位符（防御）
        if query_upper in {"REJECT", "CLARIFY"}:
            return {
                "sql": query_upper,
                "answer": "已拒绝/已澄清。",
                "final_status": "rejected" if query_upper == "REJECT" else "clarify",
                "step_counter": state.get("step_counter", 0) + 1,
            }

        # DDL/DML 操作 → 安全拒绝
        if not verdict.is_safe and any(
            kw in query.lower()
            for kw in ["drop ", "delete ", "update ", "insert ", "alter ", "truncate "]
        ):
            ctx.step()
            return {
                "sql": "REJECT",
                "answer": build_rejected_message(query),
                "final_status": "rejected",
                "step_counter": ctx.step_counter,
            }

        # 注入攻击模式 → 安全拒绝
        if "; --" in query or "/*" in query:
            ctx.step()
            return {
                "sql": "REJECT",
                "answer": build_rejected_message(query),
                "final_status": "rejected",
                "step_counter": ctx.step_counter,
            }

        # boundary_edge（非 DDL，如离题话题）
        if intent == "boundary_edge":
            is_write_op = any(
                kw in query.lower()
                for kw in ["drop", "delete", "update", "insert", "alter", "truncate", "create"]
            )
            if is_write_op:
                ctx.step()
                return {
                    "sql": "REJECT",
                    "answer": build_rejected_message(query),
                    "final_status": "rejected",
                    "step_counter": ctx.step_counter,
                }
            ctx.step()
            return {
                "sql": "CLARIFY",
                "answer": (
                    "🚫 您的问题超出 SQL 对账助手的服务范围\n\n"
                    "本系统专注于**数据对账与差异分析**，支持：\n"
                    "- 跨表数据比对（订单 vs 支付 vs 退款）\n"
                    "- 金额/笔数差异查找\n"
                    "- 时间窗口数据核对\n"
                    "- 单表聚合统计查询"
                ),
                "final_status": "clarify",
                "step_counter": ctx.step_counter,
            }

        # ── 普通低置信度 / 模糊 query → LLM 智能澄清 ──────────────────
        ctx.step()
        prev_ctx       = state.get("clarify_context") or {}
        turn           = prev_ctx.get("turn", 0) + 1
        original_query = prev_ctx.get("original_query", query)

        # LLM 生成澄清问题和快捷建议
        clarify_question, suggestions = _llm_clarify(ctx, original_query, intent, conf)

        # 组装最终回复（Markdown 格式，前端用 marked.js 渲染）
        answer_text = (
            f"**我对您的问题理解不太确定**（置信度 {conf:.0%}），需要进一步确认\n\n"
            f"您的问题：「{original_query}」\n\n"
            + clarify_question
        )

        return {
            "sql": "CLARIFY",
            "answer": answer_text,
            "clarify_question": clarify_question,
            "clarify_context": {
                "original_query": original_query,
                "clarify_question": clarify_question,
                "suggestions": suggestions,
                "turn": turn,
            },
            "final_status": "awaiting_clarification",
            "step_counter": ctx.step_counter,
        }
