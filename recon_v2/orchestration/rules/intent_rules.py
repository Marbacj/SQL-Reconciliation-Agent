# @author mabohui <mabohui@kuaishou.com>
# Created on 2026-06-07
#
# intent_rules.py — 意图分类规则注册表（纯数据层，零逻辑）
#
# 职责：
#   只负责定义「意图分类」所需的所有静态知识：
#     1. VALID_INTENTS     — 系统支持的意图枚举
#     2. FAST_PATH_RULES   — Layer 1 快速通道规则（高精度关键词）
#     3. INTENT_DEFINITIONS — Layer 3 LLM 分类器的 system prompt 文本
#     4. STATIC_FEW_SHOTS  — Layer 3 few-shot 样本（可持续追加）
#
# 不包含：
#   - 任何分类算法（KNN / LLM 调用 / 路由决策）→ 在 route.py
#   - 对账判断逻辑 → 在 recon_guard.py
#
# 维护说明：
#   - 新增意图：在 VALID_INTENTS 增加枚举值，INTENT_DEFINITIONS 补充描述，STATIC_FEW_SHOTS 补充样本
#   - 调整关键词：只改这个文件，不需要碰 route.py

from __future__ import annotations

from typing import FrozenSet, List, Tuple

# ── 1. 支持的意图枚举 ─────────────────────────────────────────────

VALID_INTENTS: FrozenSet[str] = frozenset({
    "simple_query",       # 单表聚合 / 过滤 / 查找
    "multi_table_join",   # 多表 JOIN 获取富化结果（非对账）
    "time_window_recon",  # 跨时间窗口对账（找缺失/多余记录）
    "numeric_diff",       # 数值差异对比（两系统/两表金额核对）
    "boundary_edge",      # DDL/DML / 注入 / 离题 → 拒绝
    "trend_analysis",     # 时间序列趋势（折线/柱状）
    "period_comparison",  # 同环比（两周期对比+增长率）
    "topn_ranking",       # TopN 排行榜
})


# ── 2. Fast-Path 规则 (Layer 1) ───────────────────────────────────
# 格式：(intent, [精确关键词], confidence)
# 原则：只保留"高精度、无歧义"的模式，宁可漏，不可错
# 目标覆盖率 < 10%，剩余全部交给 KNN / LLM

FAST_PATH_RULES: List[Tuple[str, List[str], float]] = [
    # DDL/SQL 注入 → 直接拒绝
    (
        "boundary_edge",
        ["drop table", "delete from", "update set", "insert into",
         "truncate ", "alter table", "; --", "/*", "' or '1'='1"],
        0.99,
    ),
    # 完全离题（极端情况）
    (
        "boundary_edge",
        ["今天有什么菜", "外卖怎么点", "天气怎么样"],
        0.90,
    ),
]


# ── 3. LLM 分类器 System Prompt (Layer 3) ─────────────────────────
# 仅包含意图分类所需的语义描述，不含 schema / few-shot（由 route.py 动态拼装）

INTENT_DEFINITIONS: str = """\
You classify a natural language data query into one of these intents:

- simple_query    : Single table aggregation, lookup, filter, or set-difference (EXISTS/NOT EXISTS/LEFT JOIN IS NULL).
                    ALSO used for: time filters, NULL checks, "find orders without refund", "未退款的订单".
                    CRITICAL: "今天/昨天/本月销售额/订单金额/GMV是多少" → simple_query (单表SUM，无需对账).
                    CRITICAL: "销售额是多少" / "销售额有多少" / "今天销售额" → ALWAYS simple_query.
                    Any question asking "X是多少" / "查一下X" / "X有多少" against a SINGLE metric → simple_query.
                    GOLDEN RULE: If query has only ONE subject (one metric, one time range) → simple_query.
- multi_table_join: Requires joining 2+ tables to get enriched results, but NOT for reconciliation.
- time_window_recon: Reconcile data across time windows — find missing/extra records between two datasets.
- numeric_diff    : ONLY when user explicitly asks to COMPARE or DIFF values from TWO different tables/systems.
                    CRITICAL: Do NOT use for single-table SUM/COUNT queries, even if the word "金额/销售额" appears.
                    Requires explicit comparison signal: "对不上", "差多少", "vs", "是否一致", "差异", "不一致".
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
- "今天/昨天/本周销售额是多少" / "本月GMV" / "今日订单金额" / "今天销售额" → simple_query (单表SUM，NOT numeric_diff!)
- "销售额是多少" / "收入有多少" / "金额是多少" (单一指标 + 是多少) → simple_query
- "订单金额和退款金额是否一致" / "差额是多少" → numeric_diff
- "对比两个时间段数据" / "昨天和今天差异" → time_window_recon (NOT period_comparison)
- "每个用户的订单和支付记录" → multi_table_join
- "最近30天GMV走势" / "每天的支付笔数" → trend_analysis
- "本月GMV比上月增长多少" / "今年Q1同比去年" → period_comparison
- "GMV最高的10个直播间" / "退款率前5的商品" → topn_ranking
- NULL checks, future date queries, edge case data → simple_query
"""


# ── 4. Few-shot 样本 (Layer 3) ────────────────────────────────────
# 格式：(query, intent)
# 维护原则：
#   - 每个意图至少 3 条，total 不超过 50 条（避免 prompt 过长）
#   - 优先覆盖"容易混淆"的边界 case
#   - 新加的业务场景按意图分组追加

STATIC_FEW_SHOTS: List[Tuple[str, str]] = [
    # ── simple_query：单表聚合 ──
    ("今天的销售额是多少",                        "simple_query"),
    ("今日GMV是多少",                             "simple_query"),
    ("本月订单总金额",                            "simple_query"),
    ("昨天的支付金额是多少",                      "simple_query"),
    ("查一下今天的收入",                          "simple_query"),
    ("今天销售额",                                "simple_query"),
    ("本周销售额是多少",                          "simple_query"),
    ("今天的收入有多少",                          "simple_query"),
    ("上周 GMV 是多少",                           "simple_query"),
    ("最近7天的销售额",                           "simple_query"),
    ("每个用户的总订单金额",                      "simple_query"),
    ("统计今天的退款笔数",                        "simple_query"),
    # ── simple_query：存在性查找 ──
    ("找出已支付但没有退款的订单",               "simple_query"),
    ("哪些订单没有对应的退款记录",               "simple_query"),
    ("查出 user_id 为 null 的订单",              "simple_query"),
    # ── numeric_diff：数值差异对账 ──
    ("订单表和退款表金额是否一致",               "numeric_diff"),
    ("退款金额比订单金额少多少",                 "numeric_diff"),
    ("每个渠道的成功率和退款率",                 "numeric_diff"),
    # ── time_window_recon：时间窗口对账 ──
    ("昨天的 GMV 和支付系统数据对得上吗",       "time_window_recon"),
    ("对比本月和上月的支付差异",                 "time_window_recon"),
    # ── boundary_edge：拒绝 ──
    ("删除所有订单",                             "boundary_edge"),
    ("今天有什么菜",                             "boundary_edge"),
    # ── trend_analysis：趋势分析 ──
    ("最近30天每天的GMV走势",                   "trend_analysis"),
    ("按月统计过去半年的支付笔数变化",           "trend_analysis"),
    ("每周的退款率趋势",                         "trend_analysis"),
    ("今年各月订单金额折线图",                   "trend_analysis"),
    # ── period_comparison：同环比 ──
    ("本月GMV比上月增长了多少",                 "period_comparison"),
    ("今年Q1和去年Q1的订单数同比变化",          "period_comparison"),
    ("本周退款率比上周高还是低",                "period_comparison"),
    ("11月支付金额环比10月增长率是多少",        "period_comparison"),
    # ── topn_ranking：排行榜 ──
    ("GMV最高的10个直播间排行榜",               "topn_ranking"),
    ("退款率最高的前5个商品类目",               "topn_ranking"),
    ("订单数最多的用户Top10",                   "topn_ranking"),
    ("销售额排名前3的渠道",                     "topn_ranking"),
]
