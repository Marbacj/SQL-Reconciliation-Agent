"""RAG 检索质量评估：MRR@5 / Recall@10。

需要 GoldenCase.retrieval_label 字段（哪些 doc_id 应被召回）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from recon_v2.rag.retriever import HybridRetriever
from tests.eval.schema import GoldenCase, load_golden_set


def mrr_at_k(ranked_doc_ids: List[str], gold: List[str], k: int) -> float:
    for i, did in enumerate(ranked_doc_ids[:k], start=1):
        if did in gold:
            return 1.0 / i
    return 0.0


def recall_at_k(ranked_doc_ids: List[str], gold: List[str], k: int) -> float:
    if not gold:
        return 0.0
    hit = sum(1 for did in ranked_doc_ids[:k] if did in gold)
    return hit / len(gold)


def run_rag_eval(
    golden_path: str = "tests/eval/golden_set.jsonl",
    k_mrr: int = 5,
    k_recall: int = 10,
) -> dict:
    cases = [c for c in load_golden_set(golden_path) if c.retrieval_label]
    if not cases:
        return {"note": "No cases with retrieval_label; please annotate golden_set first."}

    retriever = HybridRetriever()
    mrr_scores: List[float] = []
    recall_scores: List[float] = []
    for case in cases:
        ranked = [d.doc_id for d in retriever.retrieve(case.query, k=k_recall)]
        gold = case.retrieval_label or []
        mrr_scores.append(mrr_at_k(ranked, gold, k_mrr))
        recall_scores.append(recall_at_k(ranked, gold, k_recall))

    return {
        "cases_evaluated": len(cases),
        f"mrr@{k_mrr}": sum(mrr_scores) / len(mrr_scores),
        f"recall@{k_recall}": sum(recall_scores) / len(recall_scores),
    }


if __name__ == "__main__":
    import json

    print(json.dumps(run_rag_eval(), indent=2))
