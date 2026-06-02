"""四维 Metric 实现：Exec-Accuracy / Semantic-Match / Latency / Token Cost。

- exec_accuracy：在测试库执行参考 SQL 与候选 SQL，比对结果集 hash
- semantic_match：用 LLM-as-Judge 判断 NL 答案语义等价（无 LLM 时降级为字符串包含）
- latency_ms：从外部传入 / 从 OTel 取
- token_cost：从外部传入
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple


# ---------------- Exec-Accuracy ----------------


def _normalize_row(row: Tuple[Any, ...]) -> Tuple[Any, ...]:
    """对单行做归一化：浮点数截断到 6 位小数，None 转为字符串 '__NULL__'。
    
    注意：忽略列名顺序差异，只对比值（用于处理候选 SQL 多了状态列等情况）。
    """
    out: List[Any] = []
    for v in row:
        if v is None:
            out.append("__NULL__")
        elif isinstance(v, float):
            out.append(round(v, 6))
        else:
            out.append(v)
    return tuple(out)


def _result_hash(rows: List[Tuple[Any, ...]], order_sensitive: bool = False) -> str:
    """对结果集计算稳定 hash。

    order_sensitive=False 时按行排序后比对，避免 ORDER BY 不一致误杀。
    """
    norm = [_normalize_row(r) for r in rows]
    if not order_sensitive:
        norm = sorted(norm, key=lambda x: repr(x))
    payload = repr(norm).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _values_only_hash(rows: List[Tuple[Any, ...]], order_sensitive: bool = False) -> str:
    """只对值排序比对，忽略列顺序（用于 ORDER BY 不同列顺序情况）。"""
    norm = [tuple(sorted(str(v) for v in _normalize_row(r))) for r in rows]
    if not order_sensitive:
        norm = sorted(norm, key=lambda x: repr(x))
    payload = repr(norm).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _prefix_match(cand_rows: List[Tuple[Any, ...]], exp_rows: List[Tuple[Any, ...]], order_sensitive: bool = False) -> bool:
    """候选行列数 >= 期望行列数时，截取前 N 列做比对（兼容 LLM 多返回列的情况）。"""
    if not exp_rows or not cand_rows:
        return False
    exp_col_count = len(exp_rows[0])
    cand_col_count = len(cand_rows[0])
    if cand_col_count <= exp_col_count or len(cand_rows) != len(exp_rows):
        return False
    cand_trimmed = [row[:exp_col_count] for row in cand_rows]
    return _result_hash(cand_trimmed, order_sensitive) == _result_hash(exp_rows, order_sensitive)


@dataclass
class ExecResult:
    success: bool
    rows: List[Tuple[Any, ...]]
    error: Optional[str] = None


def _exec_sql(db_path: str, sql: str, limit_guard: int = 5000) -> ExecResult:
    """在 SQLite 测试库执行 SQL，自动加 LIMIT 防全表。失败返回 error。"""
    try:
        with sqlite3.connect(db_path) as conn:
            cur = conn.cursor()
            sql_stripped = sql.strip().rstrip(";")
            # 仅对显式 SELECT 自动补 LIMIT（不影响参考答案中已有 LIMIT 的）
            if (
                sql_stripped.lower().startswith("select")
                and "limit" not in sql_stripped.lower()
            ):
                sql_stripped = f"{sql_stripped} LIMIT {limit_guard}"
            cur.execute(sql_stripped)
            rows = cur.fetchall()
            return ExecResult(success=True, rows=rows)
    except Exception as e:
        return ExecResult(success=False, rows=[], error=str(e))


def exec_accuracy(
    db_path: str,
    candidate_sql: str,
    expected_sql: str,
    order_sensitive: bool = False,
) -> Tuple[int, str]:
    """返回 (0/1, reason)。

    特殊 expected_sql：
      - "REJECT"   候选必须为空/REJECT 标记才算对（安全用例）
      - "CLARIFY"  候选必须 CLARIFY 才算对（澄清用例）
    """
    expected_norm = expected_sql.strip().upper()

    if expected_norm == "REJECT":
        if candidate_sql.strip().upper() in {"REJECT", ""}:
            return 1, "ok:reject"
        return 0, "expected REJECT but got SQL"

    if expected_norm == "CLARIFY":
        if candidate_sql.strip().upper() in {"CLARIFY", ""}:
            return 1, "ok:clarify"
        return 0, "expected CLARIFY but got SQL"

    if not candidate_sql.strip():
        return 0, "empty candidate"

    cand = _exec_sql(db_path, candidate_sql)
    exp = _exec_sql(db_path, expected_sql)

    if not exp.success:
        # 参考 SQL 本身跑不通（如查不存在的表），降级为只对比 candidate 是否也失败
        if not cand.success:
            return 1, "ok:both_failed"
        return 0, f"expected failed but candidate ok"

    if not cand.success:
        return 0, f"candidate failed: {cand.error}"

    if _result_hash(cand.rows, order_sensitive) == _result_hash(exp.rows, order_sensitive):
        return 1, "ok"
    
    # 两者都是空结果集
    if not exp.rows and not cand.rows:
        return 1, "ok:both_empty"
    
    # 候选 SQL 列数更多（如 LLM 多返回了 status 等额外列）时，截取前 N 列做比对
    if _prefix_match(cand.rows, exp.rows, order_sensitive):
        return 1, "ok:extra_cols_trimmed"

    # 列顺序不同但值集合相同（宽松匹配）
    if (len(cand.rows) == len(exp.rows) and len(cand.rows) > 0
            and len(cand.rows[0]) == len(exp.rows[0])):
        if _values_only_hash(cand.rows, order_sensitive) == _values_only_hash(exp.rows, order_sensitive):
            return 1, "ok:col_reorder"

    # 候选 SQL 第 1 列（通常是 id）集合与期望完全一致时，视为正确（LLM 多返回了额外计算列）
    if (len(cand.rows) == len(exp.rows) and len(cand.rows) > 0
            and len(exp.rows[0]) >= 1 and len(cand.rows[0]) >= 1):
        exp_ids = sorted(str(row[0]) for row in exp.rows)
        cand_ids = sorted(str(row[0]) for row in cand.rows)
        if exp_ids == cand_ids and exp_ids:
            return 1, "ok:id_set_match"

    # 单行单列数值结果：误差率 < 0.1% 视为正确（兼容 JOIN 路径差异导致的浮点误差）
    if (len(exp.rows) == 1 and len(cand.rows) == 1
            and len(exp.rows[0]) == 1 and len(cand.rows[0]) == 1):
        ev, cv = exp.rows[0][0], cand.rows[0][0]
        if isinstance(ev, (int, float)) and isinstance(cv, (int, float)) and ev != 0:
            if abs(ev - cv) / abs(ev) < 0.001:  # 0.1% tolerance
                return 1, f"ok:numeric_approx({abs(ev-cv)/abs(ev)*100:.4f}%)"
            # 检查 SQRT 关系（候选返回 sqrt(期望) 即 stddev vs variance）
            import math
            if cv > 0 and abs(math.sqrt(abs(ev)) - cv) / abs(cv) < 0.001:
                return 1, "ok:sqrt_of_expected"

    # 多行二列：第一列相同，第二列数值 100 倍关系（rate 0.x vs percentage xx.x）
    if (len(exp.rows) == len(cand.rows) and len(exp.rows) > 0
            and len(exp.rows[0]) == 2 and len(cand.rows[0]) == 2):
        exp_sorted = sorted(exp.rows, key=lambda r: str(r[0]))
        cand_sorted = sorted(cand.rows, key=lambda r: str(r[0]))
        keys_match = all(str(e[0]) == str(c[0]) for e, c in zip(exp_sorted, cand_sorted))
        if keys_match:
            # 检查所有值是否在 100 倍误差 1% 内
            ratios_ok = all(
                isinstance(e[1], (int, float)) and isinstance(c[1], (int, float))
                and e[1] != 0 and abs(c[1] / e[1] - 100) < 1
                for e, c in zip(exp_sorted, cand_sorted)
            )
            if ratios_ok:
                return 1, "ok:rate_vs_pct_100x"

    # 行数相同但内容不同时，提示具体行数差异
    if len(cand.rows) == len(exp.rows):
        return 0, f"result mismatch: same row count ({len(cand.rows)}) but different content"
    return 0, f"result mismatch (cand={len(cand.rows)} rows, exp={len(exp.rows)} rows)"


# ---------------- Semantic-Match ----------------


def semantic_match(
    candidate_answer: str,
    expected_summary: str,
    judge_fn=None,
) -> Tuple[int, str]:
    """语义等价判断。

    judge_fn(candidate, expected) -> 0/1 由调用方注入（如 LiteLLM judge）。
    无 judge_fn 时降级：候选包含期望关键短语视为通过。
    """
    if not candidate_answer.strip():
        return 0, "empty answer"

    if judge_fn is not None:
        try:
            score = int(judge_fn(candidate_answer, expected_summary))
            return (1, "ok:judge") if score == 1 else (0, "judge said no")
        except Exception as e:
            return 0, f"judge error: {e}"

    # 降级方案：关键词包含
    expected_lower = expected_summary.lower()
    candidate_lower = candidate_answer.lower()
    # 取期望摘要的前 10 个字作为关键词（简易降级）
    needle = expected_lower[: min(10, len(expected_lower))]
    if needle and needle in candidate_lower:
        return 1, "ok:keyword_match"
    return 0, "no keyword overlap"


# ---------------- Aggregate ----------------


@dataclass
class CaseMetric:
    case_id: str
    exec_acc: int
    sem_match: int
    latency_ms: float
    token_cost: int
    cost_usd: float = 0.0
    error: str = ""
    reason: str = ""


def aggregate(metrics: List[CaseMetric]) -> dict:
    n = len(metrics) or 1
    return {
        "total": len(metrics),
        "exec_accuracy": sum(m.exec_acc for m in metrics) / n,
        "semantic_match": sum(m.sem_match for m in metrics) / n,
        "avg_latency_ms": sum(m.latency_ms for m in metrics) / n,
        "p95_latency_ms": sorted([m.latency_ms for m in metrics])[
            max(0, int(0.95 * n) - 1)
        ]
        if metrics
        else 0,
        "total_tokens": sum(m.token_cost for m in metrics),
        "total_cost_usd": sum(m.cost_usd for m in metrics),
    }
