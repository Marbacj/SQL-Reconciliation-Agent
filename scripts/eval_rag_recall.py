"""RAG Recall 评估脚本。

Ground truth 来源（两种，自动合并）：
  1. data/sessions.sqlite — 历史对话中 query + 最终 SQL（用 sqlglot 解析出表名）
  2. --cases 手动传入 JSON 文件：[{"query": "...", "expected_tables": ["orders", "payments"]}]

指标：
  Recall@k   = |retrieved_tables ∩ expected_tables| / |expected_tables|
  Hit@k      = 1 if any expected_table in retrieved_tables else 0
  MRR        = 1 / rank_of_first_hit（无命中 = 0）

用法：
  python scripts/eval_rag_recall.py
  python scripts/eval_rag_recall.py --k 5 --limit 50
  python scripts/eval_rag_recall.py --cases my_cases.json
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

# 保证 recon_v2 可导入
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Ground Truth 构建 ─────────────────────────────────────────────────────────

def _extract_tables_from_sql(sql: str) -> Set[str]:
    """用 sqlglot 解析 SQL，提取所有引用的表名（小写）。"""
    try:
        import sqlglot
        from sqlglot import expressions as exp
        stmts = sqlglot.parse(sql or "")
        tables: Set[str] = set()
        for stmt in stmts:
            if stmt is None:
                continue
            for node in stmt.walk():
                if isinstance(node, exp.Table) and node.name:
                    tables.add(node.name.lower())
        return tables
    except Exception:
        return set()


def load_cases_from_sessions(
    db_path: str = "data/sessions.sqlite",
    limit: int = 100,
) -> List[Dict]:
    """从 sessions.sqlite 加载历史 query+SQL 作为评估 case。"""
    cases = []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT messages FROM sessions WHERE status='ok' ORDER BY ts DESC LIMIT ?",
            (limit * 3,),
        ).fetchall()
        conn.close()
    except Exception as e:
        print(f"[WARN] 无法读取 sessions.sqlite: {e}")
        return []

    seen: Set[str] = set()
    for row in rows:
        try:
            msgs = json.loads(row["messages"])
            for m in msgs:
                q = (m.get("query") or "").strip()
                sql = (m.get("sql") or "").strip()
                if not q or not sql or q in seen:
                    continue
                tables = _extract_tables_from_sql(sql)
                if not tables:
                    continue
                seen.add(q)
                cases.append({"query": q, "expected_tables": sorted(tables)})
                if len(cases) >= limit:
                    break
        except Exception:
            continue
        if len(cases) >= limit:
            break

    return cases


def load_cases_from_file(path: str) -> List[Dict]:
    with open(path) as f:
        return json.load(f)


# ── 检索 ──────────────────────────────────────────────────────────────────────

def retrieve_doc_ids(retriever, query: str, k: int) -> List[str]:
    """调用 retriever，返回 doc_id 列表（保持排名顺序）。"""
    try:
        docs = retriever.retrieve(query, k=k)
        return [d.doc_id for d in docs]
    except Exception as e:
        print(f"  [ERR] retrieve failed: {e}")
        return []


def doc_ids_to_tables(doc_ids: List[str]) -> List[str]:
    """从 doc_id 提取表名。

    支持格式：
      'table:orders'    → 'orders'
      'doc:orders#0'    → 'orders'
      'doc:order_amount#1' → 'order_amount'
    """
    import re
    tables = []
    for did in doc_ids:
        if did.startswith("table:"):
            tables.append(did[len("table:"):].lower())
        elif did.startswith("doc:"):
            # doc:orders#0 → orders
            name = did[len("doc:"):]
            name = re.sub(r"#\d+$", "", name).lower()
            # 跳过明显的非表名 chunk（业务规则/方言文档）
            skip = {"reconciliation_rules", "sqlite_dialect", "business_terms"}
            if name and not any(name.startswith(p) for p in ["lc_", "lc "]) and name not in skip:
                tables.append(name)
    return tables


def get_kb_table_names(retriever) -> Set[str]:
    """返回 KB 中覆盖的所有表名集合。"""
    tables = set()
    for doc in retriever.docs:
        tables.update(doc_ids_to_tables([doc.doc_id]))
    return tables


# ── 指标计算 ──────────────────────────────────────────────────────────────────

@dataclass
class CaseResult:
    query: str
    expected: Set[str]
    retrieved_tables: List[str]  # 有序
    retrieved_doc_ids: List[str]

    @property
    def recall(self) -> float:
        if not self.expected:
            return 1.0
        hit = len(self.expected & set(self.retrieved_tables))
        return hit / len(self.expected)

    @property
    def hit(self) -> int:
        return 1 if self.expected & set(self.retrieved_tables) else 0

    @property
    def mrr(self) -> float:
        for rank, t in enumerate(self.retrieved_tables, start=1):
            if t in self.expected:
                return 1.0 / rank
        return 0.0


def compute_metrics(results: List[CaseResult]) -> Dict:
    if not results:
        return {}
    recall_scores = [r.recall for r in results]
    hit_scores = [r.hit for r in results]
    mrr_scores = [r.mrr for r in results]
    return {
        "n": len(results),
        "Recall@k": round(sum(recall_scores) / len(recall_scores), 4),
        "Hit@k": round(sum(hit_scores) / len(hit_scores), 4),
        "MRR": round(sum(mrr_scores) / len(mrr_scores), 4),
        "perfect_recall_pct": round(sum(1 for s in recall_scores if s == 1.0) / len(recall_scores) * 100, 1),
        "zero_recall_pct": round(sum(1 for s in recall_scores if s == 0.0) / len(recall_scores) * 100, 1),
    }


# ── 主函数 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="评估 RAG Recall 效果")
    parser.add_argument("--k", type=int, default=3, help="top-k（默认 3）")
    parser.add_argument("--limit", type=int, default=50, help="从 sessions 加载的最大 case 数（默认 50）")
    parser.add_argument("--cases", type=str, default=None, help="手动 case JSON 文件路径")
    parser.add_argument("--sessions-db", type=str, default="data/sessions.sqlite")
    parser.add_argument("--verbose", action="store_true", help="打印每条 case 详情")
    args = parser.parse_args()

    # ── 加载 cases ────────────────────────────────────────────────────────────
    cases = []
    if args.cases:
        cases = load_cases_from_file(args.cases)
        print(f"[Cases] 从文件加载 {len(cases)} 条")
    else:
        cases = load_cases_from_sessions(args.sessions_db, limit=args.limit)
        print(f"[Cases] 从 sessions.sqlite 加载 {len(cases)} 条（有效 SQL 的 case）")

    if not cases:
        print("[ERROR] 没有可用的评估 case，退出")
        sys.exit(1)

    # ── 初始化 Retriever ──────────────────────────────────────────────────────
    from recon_v2.rag.retriever import HybridRetriever
    retriever = HybridRetriever()
    kb_tables = get_kb_table_names(retriever)
    print(f"[Retriever] degraded={retriever.degraded}, "
          f"dense={retriever._dense_available}, "
          f"reranker={retriever._reranker_available}")
    print(f"[Retriever] KB size={len(retriever.docs)} chunks")
    print(f"[Retriever] KB 覆盖表: {sorted(kb_tables)}\n")

    # ── 逐条评估 ─────────────────────────────────────────────────────────────
    results: List[CaseResult] = []
    skipped_no_coverage = 0
    for i, case in enumerate(cases):
        query = case["query"]
        expected_tables = set(t.lower() for t in case.get("expected_tables", []))

        if not expected_tables:
            continue

        # 只评估 KB 能覆盖的 case（有至少一个 expected 表在 KB 中）
        covered = expected_tables & kb_tables
        if not covered:
            skipped_no_coverage += 1
            if args.verbose:
                print(f"  - [{i+1:02d}] SKIP（KB 未覆盖）query={query[:40]!r} expected={sorted(expected_tables)}")
            continue

        doc_ids = retrieve_doc_ids(retriever, query, k=args.k)
        retrieved_tables = doc_ids_to_tables(doc_ids)

        result = CaseResult(
            query=query,
            expected=covered,           # 只考核 KB 覆盖的部分
            retrieved_tables=retrieved_tables,
            retrieved_doc_ids=doc_ids,
        )
        results.append(result)

        if args.verbose:
            status = "✓" if result.hit else "✗"
            print(f"  {status} [{i+1:02d}] query={query[:40]!r}")
            print(f"       expected={sorted(expected_tables)}")
            print(f"       retrieved={retrieved_tables}")
            print(f"       Recall={result.recall:.2f}  Hit={result.hit}  MRR={result.mrr:.2f}")

    # ── 汇总指标 ──────────────────────────────────────────────────────────────
    metrics = compute_metrics(results)
    print("\n" + "=" * 55)
    print(f"  RAG Recall 评估结果  (k={args.k})")
    print("=" * 55)
    print(f"  总 case 数     : {len(cases)}")
    print(f"  KB 未覆盖跳过  : {skipped_no_coverage}  ← KB 覆盖率问题")
    print(f"  实际评估样本数 : {metrics.get('n', 0)}")
    if metrics:
        print(f"  Recall@{args.k}       : {metrics['Recall@k']:.4f}  ({metrics['Recall@k']*100:.1f}%)")
        print(f"  Hit@{args.k}          : {metrics['Hit@k']:.4f}  ({metrics['Hit@k']*100:.1f}%)")
        print(f"  MRR            : {metrics['MRR']:.4f}")
        print(f"  完美召回 (1.0) : {metrics['perfect_recall_pct']}%")
        print(f"  零召回 (0.0)  : {metrics['zero_recall_pct']}%")
    print("=" * 55)

    # ── 零召回 case 分析 ──────────────────────────────────────────────────────
    zero_recall = [r for r in results if r.recall == 0.0]
    if zero_recall:
        print(f"\n[零召回 Case 分析] 共 {len(zero_recall)} 条：")
        for r in zero_recall[:5]:
            print(f"  query: {r.query[:50]!r}")
            print(f"  expected: {sorted(r.expected)}  retrieved: {r.retrieved_tables}")

    return metrics


if __name__ == "__main__":
    main()
