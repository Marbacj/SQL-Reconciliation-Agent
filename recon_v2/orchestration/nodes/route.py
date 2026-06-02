"""Intent route node：keyword 规则 + LLM 兜底（无 LLM 时纯规则）。

返回 (intent, confidence) 二元组写入 GraphState。
"""

from __future__ import annotations

import logging
import re
from typing import Tuple

from recon_v2.infra.tracing import span
from recon_v2.orchestration.ctx_registry import get as get_ctx
from recon_v2.orchestration.state import GraphState

logger = logging.getLogger(__name__)


# --- 规则字典：基于 Golden Set 覆盖的五大意图 ---
_KEYWORD_RULES = [
    # (intent, [keywords], confidence_if_hit)
    ("boundary_edge", ["drop ", "delete ", "update ", "insert ", "truncate ", "alter ", "; --", "/*"], 0.99),
    ("boundary_edge", ["今天有什么菜", "什么菜", "外卖"], 0.85),
    # NOTE: 明天(tomorrow)/null user_id 等可能是合法查询，不加入 boundary_edge
    ("time_window_recon", ["对账", "对比", "差额", "差异", "不平", "脏数据", "不一致"], 0.9),
    ("numeric_diff", ["金额", "退款比例", "差额", "abs", "比例", "标准差", "方差", "环比", "同比", "差异"], 0.6),
    ("multi_table_join", ["join", "连接", "对应", "没有对应", "和退款", "和支付", "渠道统计", "每个用户的"], 0.85),
    ("simple_query", ["总数", "总额", "多少", "几", "count", "sum", "avg", "max", "min", "list", "列出"], 0.6),
]


def _rule_match(query: str) -> Tuple[str, float]:
    q = query.lower()
    best_intent = "simple_query"
    best_conf = 0.0
    for intent, keywords, conf in _KEYWORD_RULES:
        if any(kw in q for kw in keywords):
            if conf > best_conf:
                best_intent = intent
                best_conf = conf
    return best_intent, best_conf


def _llm_classify(ctx, query: str) -> Tuple[str, float]:
    """LLM 兜底分类（仅在规则未明确命中且 LLM 可用时调用）。"""
    if ctx.llm is None:
        return "simple_query", 0.4

    sys_msg = (
        "You classify a SQL reconciliation query into one of these intents:\n"
        "- simple_query: single-table aggregation/lookup (including queries with IS NULL, IS NOT NULL, future/past dates)\n"
        "- multi_table_join: requires JOIN of two or more tables\n"
        "- time_window_recon: reconcile data across time windows (find missing/extra)\n"
        "- numeric_diff: compute differences/ratios between numeric columns\n"
        "- boundary_edge: ONLY for DDL/DML (DROP/DELETE/UPDATE/INSERT), SQL injection, or totally unrelated topics (food, etc.)\n"
        "NOTE: NULL value queries, future date queries, edge case data queries are VALID reconciliation queries (simple_query)\n"
        "Respond JSON: {\"intent\": <label>, \"confidence\": <0-1>}"
    )
    try:
        out = ctx.llm.chat(
            messages=[
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": query},
            ],
            trace_id=ctx.trace_id,
            temperature=0.0,
            max_tokens=80,
        )
        import json

        obj = json.loads(out.content)
        return obj.get("intent", "simple_query"), float(obj.get("confidence", 0.5))
    except Exception as e:
        logger.warning("LLM classify failed: %s", e)
        return "simple_query", 0.3


def route_node(state: GraphState) -> dict:
    """Route node：写入 intent / confidence。"""
    ctx = get_ctx(state["ctx_id"])
    query = state["query"]
    ctx.query = query

    with span("route", attributes={"trace_id": ctx.trace_id}) as s:
        intent, conf = _rule_match(query)

        # 关键词没命中 → 走 LLM 兜底
        if conf < 0.5:
            intent, conf = _llm_classify(ctx, query)

        ctx.intent = intent
        ctx.confidence = conf

        try:
            s.set_attributes({"intent": intent, "confidence": conf})
        except Exception:
            pass

    ctx.step()
    return {"intent": intent, "confidence": conf, "step_counter": ctx.step_counter}


def route_decide(state: GraphState) -> str:
    """conditional edge：决定 route 之后去 plan 还是 clarify。"""
    conf = state.get("confidence", 0.0)
    intent = state.get("intent", "")

    # boundary_edge 走 clarify（澄清/拒绝），其他高置信度直接 plan
    if intent == "boundary_edge":
        return "clarify"
    if conf < 0.6:
        return "clarify"
    return "plan"
