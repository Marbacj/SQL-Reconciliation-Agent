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
from recon_v2.orchestration.state import GraphState

logger = logging.getLogger(__name__)

# 有效意图集合
_VALID_INTENTS = {
    "simple_query",
    "multi_table_join",
    "time_window_recon",
    "numeric_diff",
    "boundary_edge",
    # ── 通用查询意图扩展 ──
    "trend_analysis",      # 趋势分析：按时间聚合折线/柱状数据
    "period_comparison",   # 同环比：两段时期数值对比，输出增长率
    "topn_ranking",        # TopN 排行：输出带序号的排行榜报告
}

# KNN 自动启用所需的最小样本数
_KNN_MIN_SAMPLES = 10

# KNN 置信度阈值
_KNN_HIGH_CONF = 0.85   # >= 此值直接返回，跳过 LLM
_KNN_MID_CONF  = 0.60   # >= 此值作为 hint 注入 LLM，< 此值忽略 KNN


# ──────────────────────────────────────────────────────────────────
# Layer 1：快速通道（Fast-path rules）
# 原则：只保留"高精度、无歧义"的模式，宁可漏，不可错
# 覆盖率目标 < 10%，剩余全部交给 KNN / LLM
# ──────────────────────────────────────────────────────────────────

_FAST_PATH_RULES = [
    # (intent, [exact_patterns], confidence)
    # DDL/SQL注入 → 直接拒绝（几乎不会误判）
    ("boundary_edge", ["drop table", "delete from", "update set", "insert into",
                        "truncate ", "alter table", "; --", "/*", "' or '1'='1"], 0.99),
    # 完全无关话题（极端情况）
    ("boundary_edge", ["今天有什么菜", "外卖怎么点", "天气怎么样"], 0.90),
]


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
        # 兜底：简单分词
        tokens = list[str](query.lower())

    if not tokens:
        return {}
    cnt: Dict[str, int] = {}
    for t in tokens:
        cnt[t] = cnt.get(t, 0) + 1
    norm = math.sqrt(sum(v * v for v in cnt.values()))
    return {k: v / norm for k, v in cnt.items()} if norm > 0 else {}


def _cosine(a: Dict[str, float], b: Dict[str, float]) -> float:
    if not a or not b:
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

    Args:
        query: 用户输入
        episodic_cases: 从 memory 召回的候选（已包含 embedding_json）
        k: 参与投票的邻居数

    Returns:
        (intent, confidence) 或 None（样本不足）
    """
    if len(episodic_cases) < _KNN_MIN_SAMPLES:
        logger.debug(
            "[KNN] skip: only %d cases (need >= %d)", len(episodic_cases), _KNN_MIN_SAMPLES
        )
        return None

    q_emb = _embed_query(query)
    if not q_emb:
        return None

    # 计算与每个 case 的相似度
    scored: List[Tuple[float, str]] = []
    for case in episodic_cases:
        intent = case.get("intent", "")
        if not intent or intent not in _VALID_INTENTS:
            continue
        emb = case.get("_emb") or {}
        if not emb:
            # 尝试从 embedding_json 字段反序列化（来自 DB 查询结果）
            raw_emb = case.get("embedding_json", "")
            if raw_emb:
                try:
                    emb = json.loads(raw_emb) if isinstance(raw_emb, str) else raw_emb
                except Exception:
                    emb = {}
        sim = _cosine(q_emb, emb)
        scored.append((sim, intent))

    if not scored:
        return None

    # 取 top-k 邻居
    scored.sort(key=lambda x: x[0], reverse=True)
    neighbors = scored[:k]

    # 相似度过低的邻居说服力不足，直接过滤（sim < 0.1 视为噪声）
    neighbors = [(sim, intent) for sim, intent in neighbors if sim > 0.1]
    if not neighbors:
        return None

    # 加权投票（权重 = sim^2，放大近邻优势）
    votes: Dict[str, float] = {}
    for sim, intent in neighbors:
        votes[intent] = votes.get(intent, 0.0) + sim * sim

    total_score = sum(votes.values())
    if total_score <= 0:
        return None

    best_intent = max(votes, key=lambda x: votes[x])
    best_score = votes[best_intent]

    # 置信度 = 最高分占比（衡量相对优势）
    conf = best_score / total_score

    # 记录 top 邻居供调试
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
# 包含：意图定义 + 静态 few-shot + 动态 episodic few-shot + KNN hint
# ──────────────────────────────────────────────────────────────────

_INTENT_DEFINITIONS = """You classify a natural language SQL reconciliation query into one of these intents:

- simple_query    : Single table aggregation, lookup, filter, or set-difference (EXISTS/NOT EXISTS/LEFT JOIN IS NULL).
                    Also used for: time filters, NULL checks, "find orders without refund", "未退款的订单".
- multi_table_join: Requires joining 2+ tables to get enriched results, but NOT for reconciliation.
- time_window_recon: Reconcile data across time windows — find missing/extra records between two datasets.
- numeric_diff    : Compute numeric differences/ratios between columns from two tables (金额差, 退款率 vs 支付率).
- boundary_edge   : ONLY for DDL/DML (DROP/DELETE/UPDATE/INSERT), SQL injection, or completely off-topic.
- trend_analysis  : Time-series aggregation to show how a metric changes over time (按天/周/月看走势/趋势/变化).
                    Key signals: "走势", "趋势", "变化", "每天", "按月", "折线", "增长曲线".
- period_comparison: Compare the SAME metric between two specific time periods and compute growth rate (+/-%).
                    Key signals: "同比", "环比", "上月", "上周", "去年", "上个季度", "涨了多少", "增长率".
                    CRITICAL: period_comparison requires explicit two-period comparison + growth rate output.
                    Do NOT confuse with time_window_recon (recon = find missing/extra rows, NOT growth rate).
- topn_ranking    : Rank entities by a metric and return Top-N results with rank numbers and formatted report.
                    Key signals: "排行", "排名", "前N", "Top", "最高的N个", "最多的", "榜单".

Key rules:
- "已支付没有退款" / "找出未退款" / "没有对应退款" → simple_query (NOT EXISTS / LEFT JOIN IS NULL)
- "订单金额和退款金额是否一致" / "差额是多少" → numeric_diff
- "对比两个时间段数据" / "昨天和今天差异" → time_window_recon (NOT period_comparison — no growth rate needed)
- "每个用户的订单和支付记录" → multi_table_join
- "最近30天GMV走势" / "每天的支付笔数" → trend_analysis
- "本月GMV比上月增长多少" / "今年Q1同比去年" → period_comparison
- "GMV最高的10个直播间" / "退款率前5的商品" → topn_ranking
- NULL checks, future date queries, edge case data → simple_query (valid SQL queries)
"""

_STATIC_FEW_SHOTS = [
    ("找出已支付但没有退款的订单",              "simple_query"),
    ("哪些订单没有对应的退款记录",               "simple_query"),
    ("订单表和退款表金额是否一致",               "numeric_diff"),
    ("退款金额比订单金额少多少",                 "numeric_diff"),
    ("昨天的 GMV 和支付系统数据对得上吗",       "time_window_recon"),
    ("每个用户的总订单金额",                     "simple_query"),
    ("统计今天的退款笔数",                       "simple_query"),
    ("对比本月和上月的支付差异",                 "time_window_recon"),
    ("每个渠道的成功率和退款率",                 "numeric_diff"),
    ("查出 user_id 为 null 的订单",              "simple_query"),
    ("删除所有订单",                             "boundary_edge"),
    ("今天有什么菜",                             "boundary_edge"),
    # ── trend_analysis ──
    ("最近30天每天的GMV走势",                   "trend_analysis"),
    ("按月统计过去半年的支付笔数变化",           "trend_analysis"),
    ("每周的退款率趋势",                         "trend_analysis"),
    ("今年各月订单金额折线图",                   "trend_analysis"),
    # ── period_comparison ──
    ("本月GMV比上月增长了多少",                 "period_comparison"),
    ("今年Q1和去年Q1的订单数同比变化",          "period_comparison"),
    ("本周退款率比上周高还是低",                "period_comparison"),
    ("11月支付金额环比10月增长率是多少",        "period_comparison"),
    # ── topn_ranking ──
    ("GMV最高的10个直播间排行榜",               "topn_ranking"),
    ("退款率最高的前5个商品类目",               "topn_ranking"),
    ("订单数最多的用户Top10",                   "topn_ranking"),
    ("销售额排名前3的渠道",                     "topn_ranking"),
]


def _build_few_shot_block(
    episodic_cases: List[dict],
    knn_hint: Optional[str] = None,
) -> str:
    """合并静态 + 动态 episodic few-shot + KNN hint，格式化为 prompt 中的 Examples 块。"""
    lines = ["Examples (Q → intent):"]

    for q, intent in _STATIC_FEW_SHOTS:
        lines.append(f'  Q: {q} → {{"intent": "{intent}"}}')

    if episodic_cases:
        lines.append("  # --- learned from history ---")
        for case in episodic_cases[:5]:
            q = case.get("query", "")
            intent = case.get("intent", "")
            flag = case.get("user_flag", 0)
            if q and intent:
                note = " [user-corrected]" if flag else ""
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

    sys_msg = (
        f"{_INTENT_DEFINITIONS}\n\n"
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
            return "simple_query", 0.5

        return intent, conf

    except Exception as e:
        logger.warning("[ROUTE] LLM classify failed: %s", e)
        return "simple_query", 0.35


# ──────────────────────────────────────────────────────────────────
# Episodic Memory 召回（KNN + LLM 共用）
# ──────────────────────────────────────────────────────────────────

def _recall_episodic(ctx, query: str, k: int = 20) -> List[dict]:
    """从 MemoryStore 召回语义相近的历史 case，同时供 KNN 和 LLM 使用。

    召回 k=20 是为了给 KNN 足够的候选邻居；
    LLM few-shot 只取其中 top-5，不会增加 prompt 长度。
    """
    try:
        if ctx.memory is not None and hasattr(ctx.memory, "query_episodic"):
            return ctx.memory.query_episodic(query, k=k)
    except Exception as e:
        logger.debug("[ROUTE] episodic recall failed: %s", e)
    return []


# ──────────────────────────────────────────────────────────────────
# 主节点
# ──────────────────────────────────────────────────────────────────

def route_node(state: GraphState) -> dict:
    """Route node（四层渐进式架构）：写入 intent / confidence。

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
    ctx.query = query

    with span("route", attributes={"trace_id": ctx.trace_id}) as s:

        # ── Layer 1：快速通道
        fast_result = _fast_path_match(query)
        if fast_result is not None:
            intent, conf = fast_result
            route_path = "fast_path"

        else:
            # 一次性召回 episodic cases，KNN 和 LLM 共享（节省 DB 调用）
            episodic_cases = _recall_episodic(ctx, query, k=20)
            logger.debug("[ROUTE] episodic recall: %d cases", len(episodic_cases))

            # ── Layer 2：KNN 分类器
            knn_result = _knn_classify(query, episodic_cases, k=7)
            knn_hint: Optional[str] = None

            if knn_result is not None:
                knn_intent, knn_conf = knn_result
                logger.debug("[ROUTE] KNN → intent=%s conf=%.2f", knn_intent, knn_conf)

                if knn_conf >= _KNN_HIGH_CONF:
                    # KNN 高置信度：直接采纳，跳过 LLM（省时省钱）
                    intent, conf = knn_intent, knn_conf
                    route_path = "knn_high_conf"

                elif knn_conf >= _KNN_MID_CONF:
                    # KNN 中置信度：作为 hint 注入 LLM，辅助决策
                    knn_hint = f"predicted={knn_intent} (conf={knn_conf:.2f})"
                    intent, conf = _llm_classify(ctx, query, episodic_cases[:5], knn_hint)
                    route_path = "knn_hint_llm"
                else:
                    # KNN 低置信度：忽略，纯 LLM 决策
                    intent, conf = _llm_classify(ctx, query, episodic_cases[:5])
                    route_path = "llm_only"
            else:
                # KNN 不可用（样本不足 / 无 embedding）：纯 LLM
                intent, conf = _llm_classify(ctx, query, episodic_cases[:5])
                route_path = "llm_only"

            # ── Layer 4：兜底
            if conf < 0.35:
                logger.warning(
                    "[ROUTE] conf too low (%.2f), fallback to simple_query", conf
                )
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
    return {"intent": intent, "confidence": conf, "step_counter": ctx.step_counter}


def route_decide(state: GraphState) -> str:
    """Conditional edge：决定 route 之后去 plan 还是 clarify。"""
    conf = state.get("confidence", 0.0)
    intent = state.get("intent", "")

    if intent == "boundary_edge" or conf < 0.55:
        return "clarify"
    return "plan"
