"""diff_calculator Tool：对比两个结果集的差异。

典型用途：
- 对账场景：订单 vs 支付，比较两套数据的差额、缺失、多余
- 输入：left/right 两个结果集（每行 dict 列表），按 key_columns 关联

growth_rate_calculator Tool：计算同环比增长率。

典型用途：
- period_comparison 场景：当前周期 vs 对比周期，输出增长率（+/-%）
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from recon_v2.tools.base import ToolBase, ToolInput, ToolOutput


class DiffInput(ToolInput):
    left: List[Dict[str, Any]] = Field(..., description="左侧结果集（每行 dict）")
    right: List[Dict[str, Any]] = Field(..., description="右侧结果集")
    key_columns: List[str] = Field(..., description="用于行匹配的 key（如 order_id）")
    compare_columns: List[str] = Field(
        default_factory=list,
        description="用于值对比的列。空则只看是否存在",
    )
    abs_tolerance: float = Field(0.01, description="数值对比的容差")


class DiffOutput(ToolOutput):
    only_in_left: List[Dict[str, Any]] = []
    only_in_right: List[Dict[str, Any]] = []
    value_mismatch: List[Dict[str, Any]] = []
    matched_count: int = 0


def _build_key(row: Dict[str, Any], cols: List[str]) -> tuple:
    return tuple(row.get(c) for c in cols)


def _values_match(a: Any, b: Any, tol: float) -> bool:
    if a is None and b is None:
        return True
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= tol
    return a == b


class DiffCalculatorTool(ToolBase[DiffInput, DiffOutput]):
    name = "diff_calculator"
    description = (
        "Compare two row sets by key_columns and report rows only in left, only in right, "
        "or with mismatched compare_columns values."
    )
    input_schema = DiffInput
    output_schema = DiffOutput
    intents = ("time_window_recon", "numeric_diff", "multi_table_join")

    def _run(self, ctx: Any, inp: DiffInput) -> DiffOutput:
        left_idx = {_build_key(r, inp.key_columns): r for r in inp.left}
        right_idx = {_build_key(r, inp.key_columns): r for r in inp.right}

        only_left: List[Dict[str, Any]] = []
        only_right: List[Dict[str, Any]] = []
        mismatch: List[Dict[str, Any]] = []
        matched = 0

        for k, lrow in left_idx.items():
            if k not in right_idx:
                only_left.append(lrow)
                continue
            rrow = right_idx[k]
            mismatches: Dict[str, Any] = {}
            for col in inp.compare_columns:
                if not _values_match(lrow.get(col), rrow.get(col), inp.abs_tolerance):
                    mismatches[col] = {"left": lrow.get(col), "right": rrow.get(col)}
            if mismatches:
                mismatch.append({"key": dict[str, Any](zip(inp.key_columns, k)), "fields": mismatches})
            else:
                matched += 1

        for k, rrow in right_idx.items():
            if k not in left_idx:
                only_right.append(rrow)

        return DiffOutput(
            success=True,
            only_in_left=only_left,
            only_in_right=only_right,
            value_mismatch=mismatch,
            matched_count=matched,
            metadata={
                "left_total": len(inp.left),
                "right_total": len(inp.right),
            },
        )


# ─────────────────────────────────────────────────────────────────────────────
# GrowthRateCalculatorTool：同环比增长率计算
# ─────────────────────────────────────────────────────────────────────────────

class GrowthRateInput(ToolInput):
    current_value: float = Field(..., description="当前周期的数值（如本月 GMV）")
    previous_value: float = Field(..., description="对比周期的数值（如上月 GMV）")
    metric_name: str = Field("指标", description="指标名称，用于报告展示（如 GMV、订单数）")
    current_label: str = Field("当前周期", description="当前周期标签（如「本月」「今年Q1」）")
    previous_label: str = Field("对比周期", description="对比周期标签（如「上月」「去年Q1」）")
    # 也支持从 parallel_results dict 直接提取
    current_result: Optional[Dict[str, Any]] = Field(
        None,
        description="来自 parallel_results 的当前周期结果行（自动提取第一个数值列）",
    )
    previous_result: Optional[Dict[str, Any]] = Field(
        None,
        description="来自 parallel_results 的对比周期结果行（自动提取第一个数值列）",
    )


class GrowthRateOutput(ToolOutput):
    current_value: float = 0.0
    previous_value: float = 0.0
    absolute_diff: float = 0.0
    growth_rate_pct: Optional[float] = None   # None 表示对比周期为 0，无法计算
    growth_rate_str: str = ""                  # 如 "+23.5%" 或 "-8.3%" 或 "N/A（对比期为0）"
    metric_name: str = ""
    current_label: str = ""
    previous_label: str = ""


def _extract_first_numeric(row: Dict[str, Any]) -> Optional[float]:
    """从一行 dict 中提取第一个数值类型的值。"""
    for v in row.values():
        if isinstance(v, (int, float)):
            return float(v)
    return None


class GrowthRateCalculatorTool(ToolBase[GrowthRateInput, GrowthRateOutput]):
    name = "growth_rate_calculator"
    description = (
        "Compute period-over-period growth rate (+/-%) between current and previous period values. "
        "Outputs absolute diff and formatted growth rate string like '+23.5%' or '-8.3%'. "
        "Use for period_comparison intent (同比/环比 scenarios)."
    )
    input_schema = GrowthRateInput
    output_schema = GrowthRateOutput
    intents = ("period_comparison",)

    def _run(self, ctx: Any, inp: GrowthRateInput) -> GrowthRateOutput:
        cur = inp.current_value
        prev = inp.previous_value

        # 如果直接传入了 parallel_results 行，优先从行中提取数值
        if inp.current_result is not None:
            extracted = _extract_first_numeric(inp.current_result)
            if extracted is not None:
                cur = extracted
        if inp.previous_result is not None:
            extracted = _extract_first_numeric(inp.previous_result)
            if extracted is not None:
                prev = extracted

        abs_diff = cur - prev

        if prev == 0.0:
            growth_rate_pct = None
            growth_rate_str = "N/A（对比期为 0，无法计算增长率）"
        else:
            rate = (abs_diff / abs(prev)) * 100
            growth_rate_pct = round(rate, 2)
            sign = "+" if rate >= 0 else ""
            growth_rate_str = f"{sign}{rate:.1f}%"

        return GrowthRateOutput(
            success=True,
            current_value=cur,
            previous_value=prev,
            absolute_diff=round(abs_diff, 4),
            growth_rate_pct=growth_rate_pct,
            growth_rate_str=growth_rate_str,
            metric_name=inp.metric_name,
            current_label=inp.current_label,
            previous_label=inp.previous_label,
            metadata={
                "formula": f"({inp.current_label} - {inp.previous_label}) / |{inp.previous_label}| × 100%",
            },
        )


class DiffInput(ToolInput):
    left: List[Dict[str, Any]] = Field(..., description="左侧结果集（每行 dict）")
    right: List[Dict[str, Any]] = Field(..., description="右侧结果集")
    key_columns: List[str] = Field(..., description="用于行匹配的 key（如 order_id）")
    compare_columns: List[str] = Field(
        default_factory=list,
        description="用于值对比的列。空则只看是否存在",
    )
    abs_tolerance: float = Field(0.01, description="数值对比的容差")


class DiffOutput(ToolOutput):
    only_in_left: List[Dict[str, Any]] = []
    only_in_right: List[Dict[str, Any]] = []
    value_mismatch: List[Dict[str, Any]] = []
    matched_count: int = 0


def _build_key(row: Dict[str, Any], cols: List[str]) -> tuple:
    return tuple(row.get(c) for c in cols)


def _values_match(a: Any, b: Any, tol: float) -> bool:
    if a is None and b is None:
        return True
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= tol
    return a == b


class DiffCalculatorTool(ToolBase[DiffInput, DiffOutput]):
    name = "diff_calculator"
    description = (
        "Compare two row sets by key_columns and report rows only in left, only in right, "
        "or with mismatched compare_columns values."
    )
    input_schema = DiffInput
    output_schema = DiffOutput
    intents = ("time_window_recon", "numeric_diff", "multi_table_join")

    def _run(self, ctx: Any, inp: DiffInput) -> DiffOutput:
        left_idx = {_build_key(r, inp.key_columns): r for r in inp.left}
        right_idx = {_build_key(r, inp.key_columns): r for r in inp.right}

        only_left: List[Dict[str, Any]] = []
        only_right: List[Dict[str, Any]] = []
        mismatch: List[Dict[str, Any]] = []
        matched = 0

        for k, lrow in left_idx.items():
            if k not in right_idx:
                only_left.append(lrow)
                continue
            rrow = right_idx[k]
            mismatches: Dict[str, Any] = {}
            for col in inp.compare_columns:
                if not _values_match(lrow.get(col), rrow.get(col), inp.abs_tolerance):
                    mismatches[col] = {"left": lrow.get(col), "right": rrow.get(col)}
            if mismatches:
                mismatch.append({"key": dict[str, Any](zip(inp.key_columns, k)), "fields": mismatches})
            else:
                matched += 1

        for k, rrow in right_idx.items():
            if k not in left_idx:
                only_right.append(rrow)

        return DiffOutput(
            success=True,
            only_in_left=only_left,
            only_in_right=only_right,
            value_mismatch=mismatch,
            matched_count=matched,
            metadata={
                "left_total": len(inp.left),
                "right_total": len(inp.right),
            },
        )
