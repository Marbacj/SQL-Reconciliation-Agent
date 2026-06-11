# @author mabohui <mabohui@kuaishou.com>
# Created on 2026-06-07
#
# recon_guard.py — 对账路径守卫（纯函数层，零副作用）
#
# 职责：
#   判断一个查询"是否应该走对账路径（多表并行规划）"
#   输出：bool + reason（可用于日志/debug）
#
# 不包含：
#   - LLM 调用
#   - 数据库操作
#   - 路由/规划逻辑 → 在 plan.py / route.py
#
# 使用方式：
#   from recon_v2.orchestration.rules.recon_guard import should_enter_recon, RECON_INTENTS
#
#   if not should_enter_recon(query, intent).ok:
#       return None  # 不走对账路径

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, List, Tuple

# ── 需要走并行对账路径的意图 ──────────────────────────────────────
# plan 节点据此决定是否调 _build_recon_plan_with_llm / _build_parallel_steps_with_llm

RECON_INTENTS: FrozenSet[str] = frozenset({
    "numeric_diff",       # 数值差异：两表金额核对
    "time_window_recon",  # 时间窗口对账：找缺失/多余记录
    "multi_table_join",   # 多表 JOIN 富化（需要并行取数）
    "period_comparison",  # 同环比：当前周期 vs 对比周期（天然并行）
})


# ── 单表聚合信号词 ───────────────────────────────────────────────
# 出现这些词时，查询几乎一定是单表聚合，不应走对账路径
# 维护原则：只加"高精度、低误判"的词，宁可漏判也不错判

SINGLE_METRIC_SIGNALS: List[str] = [
    # 中文量词
    "是多少", "有多少", "共多少", "总共多少",
    # 查询动词（无比对含义）
    "查一下", "查询", "告诉我",
    # 统计动词（单表）
    "统计", "汇总", "合计", "总计",
]

# 对账强信号词（出现这些词才应走对账路径）
RECON_SIGNAL_WORDS: List[str] = [
    "对账", "对不上", "差异", "不一致", "核查", "核对",
    "差多少", "是否一致", "有没有差", "少了", "多了",
    "vs", "比对", "reconcil",
]


@dataclass(frozen=True)
class GuardResult:
    """守卫判断结果，frozen 确保不可变。"""
    ok: bool          # True = 可以走对账路径；False = 不应走
    reason: str       # 判断依据（用于日志）


def should_enter_recon(query: str, intent: str) -> GuardResult:
    """判断查询是否应进入多表并行对账路径。

    规则优先级（从高到低）：
    1. intent 不在 RECON_INTENTS → 直接拒绝（最强约束）
    2. 包含单表信号词 且 不包含对账信号词 → 拒绝（防穿透）
    3. 否则 → 允许

    Args:
        query: 原始用户查询
        intent: route 节点输出的意图标签

    Returns:
        GuardResult(ok=True/False, reason=...)
    """
    # Rule 1：intent 级别守卫（最强，无例外）
    if intent not in RECON_INTENTS:
        return GuardResult(
            ok=False,
            reason=f"intent '{intent}' not in RECON_INTENTS",
        )

    # Rule 2：查询内容守卫（防止 LLM route 误判后穿透到对账路径）
    q_lower = query.lower()
    has_single_signal = any(sig in q_lower for sig in SINGLE_METRIC_SIGNALS)
    has_recon_signal = any(sig in q_lower for sig in RECON_SIGNAL_WORDS)

    if has_single_signal and not has_recon_signal:
        return GuardResult(
            ok=False,
            reason=(
                f"query contains single-metric signal but no recon signal "
                f"(matched: {[s for s in SINGLE_METRIC_SIGNALS if s in q_lower]})"
            ),
        )

    # Rule 3：通过所有守卫
    return GuardResult(ok=True, reason="passed all guards")


def build_recon_planner_prompt_prefix(query: str, intent: str) -> str:
    """生成对账规划器 prompt 的前缀约束段。

    把"单表聚合 → 返回空 tables"的判断规则注入 LLM prompt，
    作为对 should_enter_recon 的二次兜底（应对 LLM 幻觉）。

    Returns:
        str: 可直接拼接到 prompt 的规则文本
    """
    return (
        "【重要判断标准 - MUST READ FIRST】\n"
        "如果查询属于以下任一情况，必须返回空 tables: {\"tables\":[]}\n"
        f"  - 查询包含 {SINGLE_METRIC_SIGNALS[:4]} 等单一指标词语\n"
        "  - 查询只涉及一个时间段的单表汇总（如今天销售额、本月GMV）\n"
        f"  - 查询不包含 {RECON_SIGNAL_WORDS[:6]} 等对账信号词\n\n"
        "错误示例（下面这些不应生成对账计划）:\n"
        "  - '今天的销售额是多少' → 单表 SUM，返回 {\"tables\":[]}\n"
        "  - '本月 GMV 是多少' → 单表 SUM，返回 {\"tables\":[]}\n\n"
        "正确对账示例（下面这些才需要对账计划）:\n"
        "  - '订单金额和支付金额是否一致' → 订单表 vs 支付表，需要对账\n"
        "  - '订单和退款对不上' → 订单表 vs 退款表，需要对账\n\n"
    )
