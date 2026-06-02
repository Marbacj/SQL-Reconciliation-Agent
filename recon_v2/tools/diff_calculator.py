"""diff_calculator Tool：对比两个结果集的差异。

典型用途：
- 对账场景：订单 vs 支付，比较两套数据的差额、缺失、多余
- 输入：left/right 两个结果集（每行 dict 列表），按 key_columns 关联
"""

from __future__ import annotations

from typing import Any, Dict, List

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
                mismatch.append({"key": dict(zip(inp.key_columns, k)), "fields": mismatches})
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
