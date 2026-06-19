"""Intent route node：四层渐进式架构
    Layer 1 - Fast-Path（精确关键词，覆盖率 < 10%，DDL/注入/离题专用）
    Layer 2 - KNN 分类器（embedding 相似度投票，样本 >= 10 条自动启用）
              - conf >= 0.85 → 直接返回，跳过 LLM
              - 0.60 <= conf < 0.85 → 结果作为 hint 注入 LLM prompt
              - conf < 0.60 → 忽略 KNN，纯 LLM
    Layer 3 - LLM 分类器（主路径，Few-shot + Episodic Memory 样本）
    Layer 4 - 兜底（LLM 不可用时降级到 simple_query）

随着 episodic memory 积累，KNN 命中率自动上升，LLM 调用比例自然下降。
这是真正的"自进化 router"——不需要人工干预，数据驱动路由决策。

返回 (intent, confidence) 写入 GraphState。
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Dict, List, Optional, Tuple

from recon_v2.infra.tracing import span
from recon_v2.orchestration.ctx_registry import get as get_ctx
from recon_v2.orchestration.rules import (
    VALID_INTENTS,
    FAST_PATH_RULES,
    INTENT_DEFINITIONS,
    STATIC_FEW_SHOTS,
)
from recon_v2.orchestration.state import GraphState

logger = logging.getLogger(__name__)

# ── KNN 超参数（分类器算法配置，保留在此）────────────────────────
_KNN_MIN_SAMPLES = 10
_KNN_HIGH_CONF   = 0.85  # >= 此值直接返回，跳过 LLM
_KNN_MID_CONF    = 0.60  # >= 此值作为 hint 注入 LLM，< 此值忽略 KNN

# 内部别名（保持后续代码引用不变）
_VALID_INTENTS   = VALID_INTENTS
_FAST_PATH_RULES = FAST_PATH_RULES


# ──────────────────────────────────────────────────────────────────
# Layer 1：快速通道
# 规则数据已迁移至 recon_v2/orchestration/rules/intent_rules.py
# ──────────────────────────────────────────────────────────────────

def _fast_path_match(query: str) -> Optional[Tuple[str, float]]:
    """Layer 1：精确 pattern 命中 → 直接返回，否则 None。"""
    q = query.lower()
    for intent, patterns, conf in _FAST_PATH_RULES:
        if any(p in q for p in patterns):
            logger.debug("[ROUTE] fast_path hit: intent=%s conf=%.2f", intent, conf)
            return intent, conf
    return None


# ──────────────────────────────────────────────────────────────────
# Layer 2：KNN 分类器
# 完全复用 episodic memory 中的 embedding_json，零额外依赖
# ──────────────────────────────────────────────────────────────────

def _embed_query(query: str) -> Dict[str, float]:
    """Bag-of-tokens 归一 embedding（与 MemoryStore 保持一致）。"""
    import math
    try:
        from recon_v2.rag.retriever import _tokenize
        tokens = _tokenize(query)
    except Exception:
        tokens = list(query.lower())

    if not tokens:
        return {}
    cnt: Dict[str, int] = {}
    for t in tokens:
        cnt[t] = cnt.get(t, 0) + 1
    norm = math.sqrt(sum(v * v for v in cnt.values()))
    return {k: v / norm for k, v in cnt.items()} if norm > 0 else {}


def _cosine(a: Dict[str, float], b: Dict[str, float]) -> float:
    if not a or not b or not isinstance(b, dict) or not isinstance(a, dict):
        return 0.0
    keys = set(a.keys()) & set(b.keys())
    return sum(a[k] * b[k] for k in keys)


def _knn_classify(
    query: str,
    episodic_cases: List[dict],
    k: int = 7,
) -> Optional[Tuple[str, float]]:
    """Layer 2：KNN 投票分类器。

    算法：
    1. 计算 query 与所有 episodic case 的 cosine 相似度
    2. 取 top-k 案例
    3. 按相似度加权投票（权重 = sim^2，惩罚远邻）
    4. 返回得票最高的 intent 及置信度

    置信度计算：winning_score / total_score（相对优势，非绝对分数）
    """
    if len(episodic_cases) < _KNN_MIN_SAMPLES:
        logger.debug(
            "[KNN] skip: only %d cases (need >= %d)", len(episodic_cases), _KNN_MIN_SAMPLES
        )
        return None

    q_emb = _embed_query(query)
    if not q_emb:
        return None

    scored: List[Tuple[float, str]] = []
    for case in episodic_cases:
        intent = case.get("intent", "")
        if not intent or intent not in _VALID_INTENTS:
            continue
        emb = case.get("_emb") or {}
        if not emb:
            raw_emb = case.get("embedding_json", "")
            if raw_emb:
                try:
                    parsed = json.loads(raw_emb) if isinstance(raw_emb, str) else raw_emb
                    emb = parsed if isinstance(parsed, dict) else {}
                except Exception:
                    emb = {}
        sim = _cosine(q_emb, emb)
        scored.append((sim, intent))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    neighbors = scored[:k]
    neighbors = [(sim, intent) for sim, intent in neighbors if sim > 0.1]
    if not neighbors:
        return None

    votes: Dict[str, float] = {}
    for sim, intent in neighbors:
        votes[intent] = votes.get(intent, 0.0) + sim * sim

    total_score = sum(votes.values())
    if total_score <= 0:
        return None

    best_intent = max(votes, key=lambda x: votes[x])
    best_score = votes[best_intent]
    conf = best_score / total_score

    logger.debug(
        "[KNN] top3=%s votes=%s → intent=%s conf=%.2f",
        [(f"{sim:.2f}", intent) for sim, intent in neighbors[:3]],
        {k: f"{v:.3f}" for k, v in sorted(votes.items(), key=lambda x: -x[1])},
        best_intent,
        conf,
    )
    return best_intent, conf


# ──────────────────────────────────────────────────────────────────
# Layer 3：LLM 分类器
# 意图定义和 few-shot 已迁移至 recon_v2/orchestration/rules/intent_rules.py
# ──────────────────────────────────────────────────────────────────

def _build_few_shot_block(
    episodic_cases: List[dict],
    knn_hint: Optional[str] = None,
) -> str:
    """合并静态 + 动态 episodic few-shot + KNN hint，格式化为 prompt 中的 Examples 块。"""
    lines = ["Examples (Q → intent):"]

    # 静态样本来自 rules/intent_rules.py
    for q, intent in STATIC_FEW_SHOTS:
        lines.append(f'  Q: {q} → {{"intent": "{intent}"}}')

    if episodic_cases:
        # promoted=1 案例优先（高重要性且 outcome=1 自动晋升），其次按相似度排序
        promoted = [c for c in episodic_cases if c.get("promoted")]
        others = [c for c in episodic_cases if not c.get("promoted")]
        top_cases = (promoted + others)[:5]
        if top_cases:
            lines.append("  # --- learned from history ---")
            for case in top_cases:
                q = case.get("query", "")
                intent = case.get("intent", "")
                flag = case.get("user_flag", 0)
                is_promoted = bool(case.get("promoted"))
                if q and intent:
                    if flag:
                        note = " [user-verified]"
                    elif is_promoted:
                        note = " [auto-learned]"
                    else:
                        note = ""
                    lines.append(f'  Q: {q} → {{"intent": "{intent}"}}{note}')

    if knn_hint:
        lines.append(f"\n# KNN classifier hint (based on {_KNN_MIN_SAMPLES}+ historical cases): {knn_hint}")
        lines.append("# Consider this hint but make your own judgment based on the query semantics.")

    return "\n".join(lines)


def _llm_classify(
    ctx,
    query: str,
    episodic_cases: Optional[List[dict]] = None,
    knn_hint: Optional[str] = None,
) -> Tuple[str, float]:
    """Layer 3：LLM 分类器（KNN 无法直接决策时调用）。

    knn_hint: KNN 的软建议（置信度中等时注入），帮助 LLM 做更好的决策
    """
    if ctx.llm is None:
        return "simple_query", 0.4

    few_shot_block = _build_few_shot_block(episodic_cases or [], knn_hint)

    # INTENT_DEFINITIONS 来自 rules/intent_rules.py
    sys_msg = (
        f"{INTENT_DEFINITIONS}\n\n"
        f"{few_shot_block}\n\n"
        'Respond JSON only: {"intent": <label>, "confidence": <0.0-1.0>}'
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
        raw = out.content.strip()
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            raw = raw[start:end]
        obj = json.loads(raw)
        intent = obj.get("intent", "simple_query")
        conf = float(obj.get("confidence", 0.5))

        if intent not in _VALID_INTENTS:
            logger.warning("[ROUTE] LLM returned unknown intent '%s', fallback to simple_query", intent)
            intent = "simple_query"
            conf = 0.4

        ctx.budget.add_tokens(out.prompt_tokens + out.completion_tokens)
        return intent, conf

    except Exception as e:
        logger.warning("[ROUTE] LLM classify failed: %s", e)
        return "simple_query", 0.4


def _recall_episodic(ctx, query: str, k: int = 20) -> List[dict]:
    """从 episodic memory 召回历史案例（KNN + LLM 共享）。"""
    if ctx.memory is None:
        return []
    try:
        result = ctx.memory.query(query, k=k)
        return result.get("episodic", [])
    except Exception as e:
        logger.debug("[ROUTE] episodic recall failed: %s", e)
        return []


def route_node(state: GraphState) -> dict:
    """Route node（四层渐进式架构）：写入 intent / confidence。

    多轮澄清支持：
    - 若 state 携带 clarify_context（上一轮澄清留下的），则把用户新回复与原始 query 合并后再做意图识别
    - 合并格式："<原始问题> <用户补充说明>"，让 LLM 有完整上下文

    数据流：
    query
      → Layer1 fast_path (精确关键词)
      → Layer2 KNN       (embedding 投票，自动随样本增长)
          ├─ conf >= 0.85 → 直接返回
          ├─ conf 0.60~0.85 → hint 注入 Layer3
          └─ conf < 0.60 → 忽略，纯 Layer3
      → Layer3 LLM       (few-shot + episodic + knn_hint)
      → Layer4 fallback  (LLM 不可用 / conf 极低)
    """
    ctx = get_ctx(state["ctx_id"])
    query = state["query"]

    # ── 多轮澄清上下文合并 ──────────────────────────────────────────────
    clarify_ctx = state.get("clarify_context")
    if clarify_ctx and isinstance(clarify_ctx, dict):
        original_query = clarify_ctx.get("original_query", "")
        suggestions = clarify_ctx.get("suggestions", [])
        if query in suggestions:
            # User clicked a suggestion → treat as a fresh standalone query, don't merge
            logger.info(
                "[ROUTE] suggestion selected (turn=%d), using as fresh query: '%s'",
                clarify_ctx.get("turn", 1),
                query[:80],
            )
        elif original_query and original_query != query:
            merged_query = f"{original_query}。（补充说明：{query}）"
            logger.info(
                "[ROUTE] clarify_context detected (turn=%d), merged query: %s",
                clarify_ctx.get("turn", 1),
                merged_query[:100],
            )
            query = merged_query

    ctx.query = query

    with span("route", attributes={"trace_id": ctx.trace_id}) as s:

        # ── Layer 1：快速通道
        fast_result = _fast_path_match(query)
        if fast_result is not None:
            intent, conf = fast_result
            route_path = "fast_path"

        else:
            episodic_cases = _recall_episodic(ctx, query, k=20)
            logger.debug("[ROUTE] episodic recall: %d cases", len(episodic_cases))

            # ── Layer 2：KNN 分类器
            knn_result = _knn_classify(query, episodic_cases, k=7)
            knn_hint: Optional[str] = None

            if knn_result is not None:
                knn_intent, knn_conf = knn_result
                logger.debug("[ROUTE] KNN → intent=%s conf=%.2f", knn_intent, knn_conf)

                if knn_conf >= _KNN_HIGH_CONF:
                    intent, conf = knn_intent, knn_conf
                    route_path = "knn_high_conf"
                elif knn_conf >= _KNN_MID_CONF:
                    knn_hint = f"predicted={knn_intent} (conf={knn_conf:.2f})"
                    intent, conf = _llm_classify(ctx, query, episodic_cases[:5], knn_hint)
                    route_path = "knn_hint_llm"
                else:
                    intent, conf = _llm_classify(ctx, query, episodic_cases[:5])
                    route_path = "llm_only"
            else:
                intent, conf = _llm_classify(ctx, query, episodic_cases[:5])
                route_path = "llm_only"

            # ── Layer 4：兜底
            if conf < 0.35:
                logger.warning("[ROUTE] conf too low (%.2f), fallback to simple_query", conf)
                intent = "simple_query"
                conf = 0.5
                route_path = "fallback"

        logger.info(
            "[ROUTE] query='%s' path=%s intent=%s conf=%.2f",
            query[:80], route_path, intent, conf,
        )

        ctx.intent = intent
        ctx.confidence = conf

        try:
            s.set_attributes({"intent": intent, "confidence": conf, "route_path": route_path})
        except Exception:
            pass

    ctx.step()
    clear_clarify = conf >= 0.55 and intent != "boundary_edge"
    return {
        "intent": intent,
        "confidence": conf,
        "step_counter": ctx.step_counter,
        "clarify_context": None if clear_clarify else state.get("clarify_context"),
    }


# 不需要 Plan 节点的意图：单步 SQL，直接进 act（ReAct 模式）
# plan_node 对这些意图只会生成单行模板步骤，是纯开销
_SKIP_PLAN_INTENTS = frozenset({
    "simple_query",    # 单表聚合/查询，1 条 SQL
    "topn_ranking",    # ORDER BY LIMIT，1 条 SQL
    "trend_analysis",  # GROUP BY 时间，1 条 SQL
    "boundary_edge",   # 安全拒绝，0 条 SQL（clarify_node 处理）
})


def route_decide(state: GraphState) -> str:
    """Conditional edge：决定 route 之后去 plan / act / clarify。

    路由优先级：
    1. boundary_edge → reject（clarify_node 内处理安全拒绝）
    2. 置信度 < 0.45 / query 过于模糊 → clarify
    3. 意图在 _SKIP_PLAN_INTENTS → act（跳过 plan，直接 ReAct）
    4. 其他复杂意图 → plan（Plan & Solve）
    """
    conf = state.get("confidence", 0.0)
    intent = state.get("intent", "simple_query")
    query = (state.get("query") or "").strip()

    if intent == "boundary_edge":
        return "reject"

    # Break infinite clarification loops: after 3 turns force a plan attempt
    clarify_ctx = state.get("clarify_context")
    if clarify_ctx and isinstance(clarify_ctx, dict):
        if clarify_ctx.get("turn", 0) >= 3 and intent != "boundary_edge":
            logger.info(
                "[ROUTE] clarify turn=%d >= 3, forcing plan", clarify_ctx.get("turn")
            )
            return "plan"

    if conf < 0.45:
        return "clarify"

    # ── 模糊 query 检测：即使 LLM 给了高置信度，也应澄清 ──────────────
    # 判断依据：query 过短（< 8字）且不包含任何业务信号词
    _BUSINESS_SIGNALS = [
        # 表/实体名（注意："数据" 太泛，不列入信号词）
        "订单", "支付", "退款", "GMV", "gmv", "收入", "销售", "金额", "笔数",
        "渠道", "用户", "商品", "直播", "结算", "流水", "账单",
        "order", "payment", "refund", "sales", "amount", "revenue",
        # 时间词
        "今天", "昨天", "本周", "上周", "本月", "上月", "今年", "去年",
        "最近", "7天", "30天", "季度", "年度", "daily", "weekly", "monthly",
        # 操作词
        "对比", "差异", "不一致", "对账", "统计", "查询", "排行", "趋势",
        "走势", "增长", "同比", "环比", "合计", "汇总", "找出", "筛选",
        "compare", "diff", "check", "total", "count", "sum",
    ]
    # query.lower() 用于英文不区分大小写匹配；中文直接匹配原始字符串
    query_lower = query.lower()
    has_signal = any(sig in query_lower or sig in query for sig in _BUSINESS_SIGNALS)
    if not has_signal and len(query) < 10:
        logger.info(
            "[ROUTE] vague query (len=%d, no business signal), forcing clarify: '%s'",
            len(query), query,
        )
        return "clarify"

    # 单步意图不需要 Plan 节点，直接进 act（ReAct 模式，省一次 LLM 调用）
    if intent in _SKIP_PLAN_INTENTS:
        logger.info("[ROUTE] intent=%s → skip plan, direct to act (ReAct)", intent)
        return "act"

    return "plan"
