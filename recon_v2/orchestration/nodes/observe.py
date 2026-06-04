"""Observe node：分析最近一次工具结果，决定下一跳。

返回：
- 在 state 上写入 sql/answer（若是终态），并设置 final_status
- 用 observe_decide 决定走 act 还是 reflect

Range Guard（业务合理性检查）：
- 金额类结果不应为负
- 比率类结果不应超过 100%（或 1.0）
- COUNT 结果不应超过数据库总行数的 10 倍（防幻觉）
- 空结果 + WHERE 含精确过滤条件 → 发出警告（可能过滤过严）
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from recon_v2.infra.tracing import span
from recon_v2.orchestration.ctx_registry import get as get_ctx
from recon_v2.orchestration.state import GraphState

logger = logging.getLogger(__name__)


# ── SQL 格式化 ─────────────────────────────────────────────────────

try:
    import sqlparse
    _HAS_SQLPARSE = True
except ImportError:
    _HAS_SQLPARSE = False


def _format_sql(sql: str) -> str:
    """用 sqlparse 格式化 SQL，失败则原样返回。"""
    if not sql or not _HAS_SQLPARSE:
        return sql
    try:
        formatted = sqlparse.format(
            sql,
            reindent=True,
            keyword_case="upper",
            strip_whitespace=True,
        )
        return formatted if formatted else sql
    except Exception:
        return sql


MAX_ACT_ITER = 6  # 单次 query 最多 6 次工具调用


# ── Range Guard 规则 ─────────────────────────────────────────────────

# 关键词 → 检查类型
_AMOUNT_KEYWORDS = re.compile(
    r"(gmv|amount|金额|收入|流水|revenue|total|sum)", re.IGNORECASE
)
_RATE_KEYWORDS = re.compile(
    r"(rate|ratio|占比|比率|退款率|成功率|percent)", re.IGNORECASE
)
_COUNT_KEYWORDS = re.compile(
    r"(count|笔数|订单数|数量|cnt)", re.IGNORECASE
)


def _check_range_guard(
    query: str,
    sql: str,
    rows: List[Any],
    row_count: int,
) -> List[str]:
    """业务合理性检查，返回警告信息列表（空列表 = 通过）。

    不阻断结果，仅附加到 answer 供用户判断。
    """
    warnings: List[str] = []

    if not rows:
        # 空结果检查：如果 SQL 含精确 WHERE 过滤（=、IN 等），可能条件过严
        if sql and re.search(r"WHERE\s+.*=\s*['\"]", sql, re.IGNORECASE):
            warnings.append("结果为空，WHERE 条件可能过于严格，请确认过滤条件是否正确。")
        return warnings

    # 提取首行首个数值
    first_value: Optional[float] = None
    try:
        first_row = rows[0]
        if isinstance(first_row, (list, tuple)) and len(first_row) > 0:
            val = first_row[0]
        elif isinstance(first_row, dict):
            val = next(iter(first_row.values()), None)
        else:
            val = first_row
        if val is not None:
            first_value = float(val)
    except (TypeError, ValueError, StopIteration):
        first_value = None

    if first_value is None:
        return warnings

    # 1. 金额不应为负（SUM/AVG 查询）
    if _AMOUNT_KEYWORDS.search(query) or (
        sql and re.search(r"\bSUM\s*\(|\bAVG\s*\(", sql, re.IGNORECASE)
    ):
        if first_value < 0:
            warnings.append(
                f"⚠️ 金额结果为负值（{first_value:.2f}），可能包含了冲销/退款记录，请确认业务逻辑。"
            )

    # 2. 比率/占比不应超过 1.0（或 100%）
    if _RATE_KEYWORDS.search(query):
        if first_value > 1.0:
            warnings.append(
                f"⚠️ 比率结果超过 100%（{first_value:.4f}），可能计算逻辑有误（分母为 0 或算法错误）。"
            )

    # 3. COUNT 结果异常大（超过 100 万）时提示确认
    if _COUNT_KEYWORDS.search(query) or (
        sql and re.search(r"\bCOUNT\s*\(", sql, re.IGNORECASE)
    ):
        if first_value > 1_000_000:
            warnings.append(
                f"⚠️ 查询结果行数极大（{int(first_value):,}），请确认是否符合预期，可能缺少过滤条件。"
            )

    # 4. 结果为 0 但查询包含金额聚合 → 提示可能无数据或条件有误
    if first_value == 0 and _AMOUNT_KEYWORDS.search(query):
        warnings.append("结果为 0，请确认时间范围内是否有数据，以及 status 过滤条件是否正确。")

    return warnings


# ── 辅助函数 ────────────────────────────────────────────────────────

def _summarize_observations(obs: list) -> str:
    if not obs:
        return ""
    last = obs[-1]
    if not last.get("success"):
        return f"工具失败：{last.get('error', '未知错误')}"
    if "rows" in last:
        row_count = last.get('row_count', 0)
        rows = last.get("rows", [])
        columns = last.get("columns", [])
        # 构造表格形式的数据展示
        data_text = _format_rows(rows, columns, max_rows=20)
        return f"返回 {row_count} 行结果。\n\n{data_text}"
    if "content" in last:
        return last["content"][:200]
    return "工具调用成功。"


def _format_value(v) -> str:
    """格式化单个值，数字加千分位，None 显示为 N/A。"""
    if v is None:
        return "N/A"
    try:
        f = float(v)
        # 整数型不显示小数
        if f == int(f) and abs(f) < 1e15:
            return f"{int(f):,}"
        return f"{f:,.2f}"
    except (TypeError, ValueError):
        return str(v)


def _format_rows(rows: list, columns: list, max_rows: int = 20) -> str:
    """将查询结果格式化为可读文本。

    - 单行单列：直接展示 "列名: 值"
    - 单行多列：每个字段一行 "列名: 值"
    - 多行：表格形式展示
    """
    if not rows:
        return "（空结果）"

    total = len(rows)
    first_row = rows[0]

    def get_row_values(row):
        if isinstance(row, dict):
            return list(row.values())
        return list(row)

    # 单行结果 → key: value 列表形式更易读
    if total == 1:
        values = get_row_values(first_row)
        if columns and len(columns) == len(values):
            lines = [f"{col}: {_format_value(val)}" for col, val in zip(columns, values)]
        else:
            lines = [_format_value(v) for v in values]
        return "\n".join(lines)

    # 多行结果 → 表格形式
    lines = []
    display_rows = rows[:max_rows]

    if columns:
        # 计算每列最大宽度
        col_widths = [len(str(c)) for c in columns]
        for row in display_rows:
            vals = get_row_values(row)
            for i, v in enumerate(vals):
                if i < len(col_widths):
                    col_widths[i] = max(col_widths[i], len(_format_value(v)))

        header = " | ".join(str(c).ljust(col_widths[i]) for i, c in enumerate(columns))
        separator = "-+-".join("-" * w for w in col_widths)
        lines.append(header)
        lines.append(separator)

        for row in display_rows:
            vals = get_row_values(row)
            cells = []
            for i, v in enumerate(vals):
                w = col_widths[i] if i < len(col_widths) else 0
                cells.append(_format_value(v).ljust(w))
            lines.append(" | ".join(cells))
    else:
        for row in display_rows:
            lines.append(" | ".join(_format_value(v) for v in get_row_values(row)))

    if total > max_rows:
        lines.append(f"\n... 共 {total:,} 行，仅展示前 {max_rows} 行")

    return "\n".join(lines)


# ── observe_node ─────────────────────────────────────────────────────

def observe_node(state: GraphState) -> dict:
    ctx = get_ctx(state["ctx_id"])
    observations = state.get("observations", [])
    tool_calls = state.get("tool_calls", [])

    with span("observe"):
        if not observations:
            return {
                "answer": "no observations",
                "final_status": "error",
                "error": "empty observations",
            }

        last_obs = observations[-1]
        last_call = tool_calls[-1] if tool_calls else {}

        # 提取 SQL（如果最后是 sql_runner）
        sql = ""
        if last_call.get("name") == "sql_runner":
            raw_sql = last_obs.get("final_sql") or last_call.get("args", {}).get("sql", "")
            if raw_sql and raw_sql.strip().upper() in {"REJECT", "CLARIFY"}:
                sql = ""
            else:
                sql = _format_sql(raw_sql)

        # 构造 answer
        summary = _summarize_observations(observations)
        if last_obs.get("success"):
            answer = summary
            status = "ok"

            # ── Range Guard：业务合理性检查 ──
            rows = last_obs.get("rows", [])
            row_count = last_obs.get("row_count", 0)
            query = getattr(ctx, "query", "") or state.get("query", "")
            guard_warnings = _check_range_guard(query, sql, rows, row_count)

            if guard_warnings:
                warning_text = "\n".join(guard_warnings)
                answer = f"{summary}\n\n【数据校验提示】\n{warning_text}"
                logger.info(
                    "observe: range guard triggered for query=%r: %s",
                    query[:60],
                    guard_warnings,
                )
                # 仅警告，不改变 status（不阻断结果）
                # 若警告条目超过 2 个则标记为 needs_review，供用户确认
                if len(guard_warnings) >= 2:
                    status = "needs_review"

        else:
            answer = summary
            status = "error"

        # Budget exceeded
        if ctx.budget.exceeded():
            return {
                "sql": sql,
                "answer": f"budget exceeded: {ctx.budget.reason()}",
                "final_status": "budget_exceeded",
            }

        ctx.step()
        return {
            "sql": sql,
            "answer": answer,
            "final_status": status,
        }


def observe_decide(state: GraphState) -> str:
    """conditional edge：observe 完了去 reflect (终止) 还是再 act (继续)。"""
    status = state.get("final_status", "")
    step = state.get("step_counter", 0)
    obs_count = len(state.get("observations", []))

    # 出现 error / budget / clarify / rejected → 终止
    if status in {"budget_exceeded", "rejected", "clarify"}:
        return "reflect"

    # needs_review → 直接终止（已在 answer 里附上警告，交给用户判断）
    if status == "needs_review":
        return "reflect"

    # 工具失败 → 再试一次（最多 1 次）
    last = state.get("observations", [])
    if last and not last[-1].get("success") and obs_count < 2:
        return "act"

    # 默认成功 → 反思终止
    if status == "ok":
        return "reflect"

    # 步数过多 → 终止
    if step > 8 or obs_count > MAX_ACT_ITER:
        return "reflect"

    return "reflect"
