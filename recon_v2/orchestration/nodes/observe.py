"""Observe node：分析最近一次工具结果，决定下一跳。

返回：
- 在 state 上写入 sql/answer（若是终态），并设置 final_status
- 用 observe_decide 决定走 act 还是 reflect

Range Guard（业务合理性检查）：
- 金额类结果不应为负
- 比率类结果不应超过 100%（或 1.0）
- COUNT 结果不应超过数据库总行数的 10 倍（防幻觉）
- 空结果 + WHERE 含精确过滤条件 → 发出警告（可能过滤过严）

错误恢复引导（error_diagnosis）：
- SQL 执行失败时，翻译底层错误为业务语言 + 行动建议
- 结果为空时，分析过滤条件、推断原因
- 重试耗尽时，提供上下文齐全的引导信息
- 安全拒绝时，给出友好解释和正确做法
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from recon_v2.infra.tracing import span
from recon_v2.orchestration.ctx_registry import get as get_ctx
from recon_v2.orchestration.state import GraphState
from recon_v2.orchestration.nodes.error_diagnosis import (
    diagnose_error,
    diagnose_empty_result,
    build_retry_exhausted_message,
    build_rejected_message,
)

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

def _nway_diff_engine(parallel_results: dict, recon_plan: dict) -> str:
    """N-way Diff Engine：支持任意 N 张表的对账差异分析。

    接收 parallel_results（每个 alias 对应子查询结果）和 recon_plan（tables 数组规划），
    按 join_keys 对齐所有表，输出三类异常：
      ① 金额差异：所有表都有记录但数值不等
      ② 单边缺失：某表有记录但其他表没有

    跨表 key 映射：
      使用 recon_plan["_join_validation"] 中的 {alias_pair: {col_a, col_b, match_count}}
      自动处理 order_no(A) ↔ order_id(B) 这类跨表列名不同的情况
    """
    tables = recon_plan.get("tables", [])
    tolerance = float(recon_plan.get("tolerance", 0.01))
    join_validation = recon_plan.get("_join_validation", {})  # "a-b" → {col_a, col_b, match_count}
    plan_warning = recon_plan.get("_warning", "")

    if not tables or len(tables) < 2:
        return _diff_parallel_results(parallel_results, "")

    # ── 若验证明确说明表不可关联，提前返回警告
    if plan_warning and not recon_plan.get("_plan_valid", True):
        return (
            f"⚠️ 对账表关联验证失败，无法执行差异分析。\n\n"
            f"{plan_warning}\n\n"
            "建议：请明确指定需要对账的表名（如「order_amount 和 settlements 的金额差异」）。"
        )

    # ── 构建跨表 key 映射：alias → 本表侧的 key 列名
    # 逻辑：从 _join_validation 汇总每个 alias 实际使用的 key 列名
    alias_to_key_col: dict = {}
    for pair_str, vinfo in join_validation.items():
        if vinfo.get("match_count", 0) > 0:
            parts = pair_str.split("-", 1)
            if len(parts) == 2:
                alias_a, alias_b = parts
                if vinfo.get("col_a"):
                    alias_to_key_col[alias_a] = vinfo["col_a"]
                if vinfo.get("col_b"):
                    alias_to_key_col[alias_b] = vinfo["col_b"]

    # fallback：若没有验证结果，用 tables 里的 key_cols
    for t in tables:
        if t["alias"] not in alias_to_key_col and t.get("key_cols"):
            alias_to_key_col[t["alias"]] = t["key_cols"][0]

    # ── 为了 N 路对齐，需要一个"全局 key 空间"
    # 策略：以第一张表的 key 列值作为主键，其他表的 key 列值映射到同一空间
    # 这里每张表独立建立 key_value → row_dict 索引，对齐时用各自的 key 列名

    table_indexes: dict = {}  # alias → {key_value → row_dict}
    all_key_values: set = set()

    for t in tables:
        alias = t["alias"]
        result = parallel_results.get(alias, {})
        if not result.get("success"):
            logger.warning("nway_diff: table %s failed: %s", alias, result.get("error"))
            continue

        rows = result.get("rows", [])
        cols = result.get("columns", [])
        key_col = alias_to_key_col.get(alias)
        if not key_col:
            logger.warning("nway_diff: no key col for alias %s, skip", alias)
            continue

        idx: dict = {}
        for row in rows:
            row_dict = row if isinstance(row, dict) else dict(zip(cols, row))
            key_val = row_dict.get(key_col)
            if key_val is not None:
                idx[key_val] = row_dict
                all_key_values.add(key_val)

        table_indexes[alias] = idx

    if not all_key_values:
        return "所有子查询结果均为空，无数据可对比。"

    available_aliases = list(table_indexes.keys())
    if len(available_aliases) < 2:
        msg = f"只有 {available_aliases} 有数据，其余表查询失败，无法对比。"
        if plan_warning:
            msg += f"\n\n{plan_warning}"
        return msg

    # ── 遍历所有 key 值，分类差异
    amount_diffs = []
    existence_diffs = []
    first_key_col = alias_to_key_col.get(available_aliases[0], "key")

    for key_val in sorted(all_key_values, key=lambda x: str(x)):
        present_in = {alias for alias in available_aliases if key_val in table_indexes[alias]}
        missing_in = set(available_aliases) - present_in

        if missing_in:
            existence_diffs.append({
                first_key_col: key_val,
                "出现在": ", ".join(sorted(present_in)),
                "缺失于": ", ".join(sorted(missing_in)),
            })
            continue

        # 所有表都有此记录 → 比较数值列
        value_map: dict = {}
        for t in tables:
            alias = t["alias"]
            if alias not in table_indexes:
                continue
            row = table_indexes[alias].get(key_val, {})
            for vc in t.get("value_cols", []):
                try:
                    val = float(row.get(vc) or 0)
                    value_map[f"{alias}.{vc}"] = val
                except (TypeError, ValueError):
                    pass

        val_names = list(value_map.keys())
        has_diff = any(
            abs(value_map[val_names[i]] - value_map[val_names[j]]) > tolerance
            for i in range(len(val_names))
            for j in range(i + 1, len(val_names))
        )

        if has_diff:
            rec = {first_key_col: key_val}
            rec.update(value_map)
            if len(val_names) >= 2:
                rec["diff"] = round(value_map[val_names[0]] - value_map[val_names[-1]], 4)
            amount_diffs.append(rec)

    # ── 生成报告
    parts = []
    table_names = " vs ".join(t["alias"] for t in tables if t["alias"] in available_aliases)
    total_keys = len(all_key_values)

    if plan_warning:
        parts.append(f"⚠️ {plan_warning}\n")

    if not amount_diffs and not existence_diffs:
        base = (
            f"✅ {len(available_aliases)} 张表数据完全一致，共核对 {total_keys:,} 条记录。\n"
            f"对账表: {table_names}"
        )
        return f"{parts[0]}\n{base}" if parts else base

    if amount_diffs:
        parts.append(f"【金额差异】发现 {len(amount_diffs)} 条记录金额不一致：")
        parts.append(_format_rows_from_dicts(amount_diffs[:50]))
        if len(amount_diffs) > 50:
            parts.append(f"... 共 {len(amount_diffs)} 条，仅展示前 50 条")

    if existence_diffs:
        parts.append(f"\n【存在性差异】发现 {len(existence_diffs)} 条记录在部分表中缺失：")
        parts.append(_format_rows_from_dicts(existence_diffs[:50]))
        if len(existence_diffs) > 50:
            parts.append(f"... 共 {len(existence_diffs)} 条，仅展示前 50 条")

    parts.append(f"\n对账范围：{table_names}，共核对 {total_keys:,} 条记录")
    return "\n".join(parts)
    tables = recon_plan.get("tables", [])
    join_keys = recon_plan.get("join_keys", [])
    tolerance = float(recon_plan.get("tolerance", 0.01))

    if not tables or len(tables) < 2:
        return _diff_parallel_results(parallel_results, "")

    # ── 构建每张表的 {join_key_tuple → row_dict} 索引
    table_indexes: dict[str, dict] = {}  # alias → {key_tuple → row_dict}
    all_key_tuples: set = set()

    for t in tables:
        alias = t["alias"]
        result = parallel_results.get(alias, {})
        if not result.get("success"):
            logger.warning("nway_diff: table %s failed: %s", alias, result.get("error"))
            continue

        rows = result.get("rows", [])
        cols = result.get("columns", [])
        key_cols = t["key_cols"]

        idx: dict = {}
        for row in rows:
            if isinstance(row, dict):
                row_dict = row
            else:
                row_dict = dict(zip(cols, row))

            # 构建 join key tuple
            key = tuple(row_dict.get(k) for k in key_cols)
            idx[key] = row_dict
            all_key_tuples.add(key)

        table_indexes[alias] = idx

    if not all_key_tuples:
        return "所有子查询结果均为空，无数据可对比。"

    available_aliases = list(table_indexes.keys())
    if len(available_aliases) < 2:
        return f"只有 {available_aliases} 有数据，其余表查询失败，无法对比。"

    # ── 遍历所有 key，分类差异
    amount_diffs = []       # 金额不等
    existence_diffs = []    # 存在性缺失

    for key in sorted(all_key_tuples, key=lambda x: str(x)):
        present_in = {alias for alias in available_aliases if key in table_indexes[alias]}
        missing_in = set(available_aliases) - present_in

        if missing_in:
            # 存在性差异：某些表缺失此记录
            rec = {kc: key[i] for i, kc in enumerate(join_keys[:len(key)])}
            rec["出现在"] = ", ".join(sorted(present_in))
            rec["缺失于"] = ", ".join(sorted(missing_in))
            existence_diffs.append(rec)
            continue

        # 所有表都有此记录 → 比较数值列
        value_map: dict[str, float] = {}
        for t in tables:
            alias = t["alias"]
            if alias not in table_indexes:
                continue
            row = table_indexes[alias].get(key, {})
            for vc in t.get("value_cols", []):
                try:
                    val = float(row.get(vc) or 0)
                    value_map[f"{alias}.{vc}"] = val
                except (TypeError, ValueError):
                    pass

        # 两两比较所有数值列
        val_names = list(value_map.keys())
        has_diff = False
        for i in range(len(val_names)):
            for j in range(i + 1, len(val_names)):
                va, vb = value_map[val_names[i]], value_map[val_names[j]]
                if abs(va - vb) > tolerance:
                    has_diff = True
                    break
            if has_diff:
                break

        if has_diff:
            rec = {kc: key[i] for i, kc in enumerate(join_keys[:len(key)])}
            rec.update(value_map)
            # 计算差值（第一列减最后一列）
            if len(val_names) >= 2:
                rec["diff"] = round(value_map[val_names[0]] - value_map[val_names[-1]], 4)
            amount_diffs.append(rec)

    # ── 生成报告
    parts = []
    table_names = " vs ".join(t["alias"] for t in tables)
    total_keys = len(all_key_tuples)

    if not amount_diffs and not existence_diffs:
        return (
            f"✅ {len(available_aliases)} 张表数据完全一致，共核对 {total_keys:,} 条记录。\n"
            f"对账表: {table_names}"
        )

    if amount_diffs:
        parts.append(f"【金额差异】发现 {len(amount_diffs)} 条记录金额不一致：")
        parts.append(_format_rows_from_dicts(amount_diffs[:50]))
        if len(amount_diffs) > 50:
            parts.append(f"... 共 {len(amount_diffs)} 条，仅展示前 50 条")

    if existence_diffs:
        parts.append(f"\n【存在性差异】发现 {len(existence_diffs)} 条记录在部分表中缺失：")
        parts.append(_format_rows_from_dicts(existence_diffs[:50]))
        if len(existence_diffs) > 50:
            parts.append(f"... 共 {len(existence_diffs)} 条，仅展示前 50 条")

    parts.append(f"\n对账范围：{table_names}，共核对 {total_keys:,} 条记录")
    return "\n".join(parts)


def _format_rows_from_dicts(rows: list) -> str:
    """将 dict 列表格式化为对齐表格。"""
    if not rows:
        return "（空）"
    cols = list(rows[0].keys())
    col_widths = [len(str(c)) for c in cols]
    for row in rows:
        for i, c in enumerate(cols):
            col_widths[i] = max(col_widths[i], len(_format_value(row.get(c))))

    header = " | ".join(str(c).ljust(col_widths[i]) for i, c in enumerate(cols))
    sep = "-+-".join("-" * w for w in col_widths)
    lines = [header, sep]
    for row in rows:
        cells = [_format_value(row.get(c)).ljust(col_widths[i]) for i, c in enumerate(cols)]
        lines.append(" | ".join(cells))
    return "\n".join(lines)


def _diff_parallel_results(parallel_results: dict, query: str) -> str:
    """对 parallel_results 中的两个子查询结果做差值对比（diff_calculator 核心逻辑）。

    策略：
    1. 找两表共同列（作为 join key）
    2. 找各自的数值列（金额/数量等）
    3. LEFT JOIN 对齐后找出差异行
    4. 格式化输出
    """
    if not parallel_results or len(parallel_results) < 2:
        return ""

    aliases = list(parallel_results.keys())
    left_alias, right_alias = aliases[0], aliases[1]
    left  = parallel_results[left_alias]
    right = parallel_results[right_alias]

    if not left.get("success") or not right.get("success"):
        failed = [a for a in aliases if not parallel_results[a].get("success")]
        return f"子查询失败（{', '.join(failed)}），无法进行差异对比。"

    left_rows  = left.get("rows", [])
    right_rows = right.get("rows", [])
    left_cols  = left.get("columns", [])
    right_cols = right.get("columns", [])

    if not left_rows or not right_rows:
        empty = []
        if not left_rows:  empty.append(left_alias)
        if not right_rows: empty.append(right_alias)
        return f"子查询结果为空（{', '.join(empty)}），无差异数据可对比。"

    # ── 推断 join key（共同列中非数值的文本 key，优先选 *_no / *_id / order_no 等）
    def _is_id_col(name: str) -> bool:
        name_lower = name.lower()
        return any(k in name_lower for k in ("_no", "_id", "id", "no", "code", "key"))

    common_cols = [c for c in left_cols if c in right_cols]
    join_keys = [c for c in common_cols if _is_id_col(c)]
    if not join_keys:
        join_keys = common_cols[:1]  # fallback: 用第一个共同列
    if not join_keys:
        return f"两个子查询无共同列（{left_cols} vs {right_cols}），无法对比差异。"

    # ── 推断金额列（数值型列，优先 amount / total / sum / fee / gmv 等）
    def _is_amount_col(name: str) -> bool:
        n = name.lower()
        return any(k in n for k in ("amount", "total", "sum", "fee", "gmv", "revenue", "价格", "金额", "价值"))

    def _get_numeric_cols(cols):
        amount_cols = [c for c in cols if _is_amount_col(c)]
        return amount_cols if amount_cols else [c for c in cols if c not in join_keys]

    left_val_cols  = _get_numeric_cols([c for c in left_cols  if c not in join_keys])
    right_val_cols = _get_numeric_cols([c for c in right_cols if c not in join_keys])

    # ── 转换为 dict，以 join key 索引
    def _rows_to_dict(rows, cols, key_cols):
        result = {}
        for row in rows:
            if isinstance(row, dict):
                vals = row
            else:
                vals = dict(zip(cols, row))
            k = tuple(vals.get(kc) for kc in key_cols)
            result[k] = vals
        return result

    left_dict  = _rows_to_dict(left_rows,  left_cols,  join_keys)
    right_dict = _rows_to_dict(right_rows, right_cols, join_keys)

    all_keys = set(left_dict.keys()) | set(right_dict.keys())

    # ── 找差异
    diff_rows = []
    for k in sorted(all_keys, key=lambda x: str(x)):
        lv = left_dict.get(k)
        rv = right_dict.get(k)

        if lv is None:
            row = {jk: k[i] for i, jk in enumerate(join_keys)}
            for vc in right_val_cols:
                row[f"{right_alias}.{vc}"] = rv.get(vc)
            for vc in left_val_cols:
                row[f"{left_alias}.{vc}"] = None
            row["diff"] = None
            diff_rows.append(row)
        elif rv is None:
            row = {jk: k[i] for i, jk in enumerate(join_keys)}
            for vc in left_val_cols:
                row[f"{left_alias}.{vc}"] = lv.get(vc)
            for vc in right_val_cols:
                row[f"{right_alias}.{vc}"] = None
            row["diff"] = None
            diff_rows.append(row)
        else:
            # 同时存在，比较数值
            lval = None
            rval = None
            if left_val_cols:
                try: lval = float(lv.get(left_val_cols[0]) or 0)
                except: pass
            if right_val_cols:
                try: rval = float(rv.get(right_val_cols[0]) or 0)
                except: pass

            if lval is not None and rval is not None:
                diff_val = round(lval - rval, 4)
                if abs(diff_val) > 0.001:  # 有差异才记录
                    row = {jk: k[i] for i, jk in enumerate(join_keys)}
                    if left_val_cols:
                        row[f"{left_alias}.{left_val_cols[0]}"] = lval
                    if right_val_cols:
                        row[f"{right_alias}.{right_val_cols[0]}"] = rval
                    row["diff"] = diff_val
                    diff_rows.append(row)

    if not diff_rows:
        return f"✅ 两表数据完全一致，无差异记录。\n（{left_alias}: {len(left_rows)} 行，{right_alias}: {len(right_rows)} 行）"

    # ── 格式化差异表
    col_names = list(diff_rows[0].keys())
    col_widths = [len(c) for c in col_names]
    for row in diff_rows:
        for i, c in enumerate(col_names):
            col_widths[i] = max(col_widths[i], len(_format_value(row.get(c))))

    header    = " | ".join(c.ljust(col_widths[i]) for i, c in enumerate(col_names))
    separator = "-+-".join("-" * w for w in col_widths)
    lines = [
        f"发现 {len(diff_rows)} 条差异记录（{left_alias} vs {right_alias}）：",
        "",
        header,
        separator,
    ]
    for row in diff_rows[:50]:
        cells = [_format_value(row.get(c)).ljust(col_widths[i]) for i, c in enumerate(col_names)]
        lines.append(" | ".join(cells))
    if len(diff_rows) > 50:
        lines.append(f"\n... 共 {len(diff_rows)} 条差异，仅展示前 50 条")

    return "\n".join(lines)


def _summarize_observations(obs: list, state: dict = None) -> str:
    if not obs:
        return ""

    # ── 优先：检测是否来自 parallel_act（多子任务结果）
    parallel_obs = [o for o in obs if o.get("source") == "parallel_act"]
    if parallel_obs and state is not None:
        parallel_results = state.get("parallel_results", {})
        query = state.get("query", "")
        if parallel_results and len(parallel_results) >= 2:
            # 检查是否有 ReconPlanner 的结构化计划
            plan_steps = state.get("plan_steps", [])
            recon_plan = None
            for step in plan_steps:
                if isinstance(step, dict) and "_recon_plan" in step:
                    recon_plan = step["_recon_plan"]
                    break

            if recon_plan and recon_plan.get("tables"):
                # 使用 N-way Diff Engine
                diff_text = _nway_diff_engine(parallel_results, recon_plan)
            else:
                # fallback：旧的启发式 diff
                diff_text = _diff_parallel_results(parallel_results, query)

            if diff_text:
                sqls = []
                for alias, r in parallel_results.items():
                    sql = r.get("sql", "")
                    if sql:
                        sqls.append(f"[{alias}]\n{_format_sql(sql)}")
                sql_block = "\n\n".join(sqls)
                return f"{diff_text}\n\n─── 执行的 SQL ───\n{sql_block}" if sql_block else diff_text

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


# ── 已知表名获取（用于错误诊断中的相似匹配）───────────────────────

def _get_known_tables(ctx) -> List[str]:
    """从 schema_info 缓存或 SQL 中提取已知表名列表。"""
    try:
        schema_info = getattr(ctx, "_schema_info", None)
        if schema_info and schema_info.tables:
            return [t.name for t in schema_info.tables]
    except Exception:
        pass
    return []


# ── observe_node ─────────────────────────────────────────────────────

def observe_node(state: GraphState) -> dict:
    ctx = get_ctx(state["ctx_id"])
    observations = state.get("observations", [])
    tool_calls = state.get("tool_calls", [])
    query = getattr(ctx, "query", "") or state.get("query", "")
    retry_count = state.get("retry_count", 0)
    max_retries = 3  # 与 act.py 中的 MAX_SQL_RETRIES 保持一致

    with span("observe"):
        if not observations:
            return {
                "answer": "no observations",
                "final_status": "error",
                "error": "empty observations",
            }

        last_obs = observations[-1]
        last_call = tool_calls[-1] if tool_calls else {}

        # 提取 SQL（如果最后是 sql_runner，或从 parallel_results 提取）
        sql = ""
        if last_call.get("name") == "sql_runner":
            raw_sql = last_obs.get("final_sql") or last_call.get("args", {}).get("sql", "")
            if raw_sql and raw_sql.strip().upper() in {"REJECT", "CLARIFY"}:
                sql = ""
            else:
                sql = _format_sql(raw_sql)
        elif last_obs.get("source") == "parallel_act":
            # parallel 路径：拼接所有子查询 SQL
            parallel_results = state.get("parallel_results", {})
            sqls = [_format_sql(r.get("sql", "")) for r in parallel_results.values() if r.get("sql")]
            sql = "\n\n".join(sqls) if sqls else ""

        # ── Budget exceeded ─────────────────────────────────────────
        if ctx.budget.exceeded():
            ctx.step()
            return {
                "sql": sql,
                "answer": (
                    "⚠️ 本次查询消耗的 Token 已超出预算上限\n\n"
                    "**建议：**\n"
                    "- 简化查询，减少需要分析的表或条件\n"
                    "- 把复合问题拆成多个小问题分步查询\n"
                    f"\n*详情：{ctx.budget.reason()}*"
                ),
                "final_status": "budget_exceeded",
            }

        # ── 成功路径 ─────────────────────────────────────────────────
        if last_obs.get("success"):
            summary = _summarize_observations(observations, state=state)
            answer = summary
            status = "ok"

            # ── Range Guard：业务合理性检查 ──
            rows = last_obs.get("rows", [])
            row_count = last_obs.get("row_count", 0)
            guard_warnings = _check_range_guard(query, sql, rows, row_count)

            # ── 空结果增强诊断 ──
            if not rows and last_call.get("name") == "sql_runner":
                empty_diag = diagnose_empty_result(sql=sql, query=query)
                answer = f"{summary}\n\n{empty_diag}"
                logger.info("observe: empty result detected for query=%r", query[:60])
                # 空结果仍是 "ok" 状态（SQL 成功执行），不改变 status

            if guard_warnings:
                warning_text = "\n".join(guard_warnings)
                answer = f"{answer}\n\n【数据校验提示】\n{warning_text}"
                logger.info(
                    "observe: range guard triggered for query=%r: %s",
                    query[:60],
                    guard_warnings,
                )
                if len(guard_warnings) >= 2:
                    status = "needs_review"

        # ── 失败路径 ─────────────────────────────────────────────────
        else:
            error_msg = last_obs.get("error", "未知错误")
            known_tables = _get_known_tables(ctx)
            obs_count = len(observations)

            # 收集所有尝试过的 SQL（用于重试耗尽时的历史展示）
            attempted_sqls = []
            for tc in tool_calls:
                if tc.get("name") == "sql_runner":
                    attempted_sqls.append(tc.get("args", {}).get("sql", ""))
            attempted_sqls = [s for s in attempted_sqls if s]

            # 判断是否重试耗尽
            is_retry_exhausted = retry_count >= max_retries or obs_count >= MAX_ACT_ITER

            if is_retry_exhausted and attempted_sqls:
                # 重试耗尽：给出完整的引导信息
                answer = build_retry_exhausted_message(
                    query=query,
                    attempted_sqls=attempted_sqls,
                    last_error=error_msg,
                    known_tables=known_tables,
                )
                logger.info(
                    "observe: retry exhausted (retry_count=%d) for query=%r, error=%r",
                    retry_count, query[:60], error_msg[:80],
                )
            else:
                # 单次失败：翻译错误为业务语言
                answer = diagnose_error(
                    error_msg=error_msg,
                    sql=sql,
                    query=query,
                    known_tables=known_tables,
                    retry_count=retry_count,
                    max_retries=max_retries,
                )
                logger.info(
                    "observe: SQL error diagnosed for query=%r: %r",
                    query[:60], error_msg[:80],
                )

            status = "error"

        ctx.step()
        return {
            "sql": sql,
            "answer": answer,
            "final_status": status,
            # 新增：把原始错误也带回，方便 reflect/前端使用
            "error": last_obs.get("error") if not last_obs.get("success") else None,
        }


# ── observe_decide ────────────────────────────────────────────────────

def observe_decide(state: GraphState) -> str:
    """conditional edge：observe 完了去 reflect (终止) 还是再 act (继续)。"""
    status = state.get("final_status", "")
    step = state.get("step_counter", 0)
    obs_count = len(state.get("observations", []))
    retry_count = state.get("retry_count", 0)
    max_retries = 3

    # Budget 超限 / 安全拒绝 / 待澄清 → 直接终止
    if status in {"budget_exceeded", "rejected", "clarify"}:
        return "reflect"

    # needs_review → 直接终止（已在 answer 里附上警告，交给用户判断）
    if status == "needs_review":
        return "reflect"

    # 工具失败 → 判断是否还有重试机会
    last_obs_list = state.get("observations", [])
    if last_obs_list and not last_obs_list[-1].get("success"):
        # 重试耗尽 → 终止（answer 中已有引导信息）
        if retry_count >= max_retries or obs_count >= MAX_ACT_ITER:
            return "reflect"
        # 还有重试机会 → 继续 act
        return "act"

    # 默认成功 → 反思终止
    if status == "ok":
        return "reflect"

    # 步数过多 → 终止
    if step > 8 or obs_count > MAX_ACT_ITER:
        return "reflect"

    return "reflect"
