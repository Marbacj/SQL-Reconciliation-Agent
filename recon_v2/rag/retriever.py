"""HybridRetriever：BM25-only 降级版（Stage 3 阶段）。

设计原则：
- 接口稳定：retrieve(query, k) -> List[DocChunk]
- 完整版（Stage 3 后续）：BM25 + Dense (Qdrant + bge-small) + RRF + Cross-Encoder
- 当前版本（无 qdrant/sentence-transformers 时）：纯 BM25，degraded=True
- 总是返回结果而非抛异常
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from recon_v2.rag.chunker import DocChunk, build_default_kb

logger = logging.getLogger(__name__)


# ---------------- 简易 BM25 (无外部依赖) ----------------


def _tokenize(text: str) -> List[str]:
    """中英文混合分词：英文按空格，中文按字符。"""
    text = text.lower()
    tokens: List[str] = []
    buf: List[str] = []
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            if buf:
                tokens.append("".join(buf))
                buf = []
            tokens.append(ch)
        elif ch.isalnum() or ch == "_":
            buf.append(ch)
        else:
            if buf:
                tokens.append("".join(buf))
                buf = []
    if buf:
        tokens.append("".join(buf))
    return [t for t in tokens if t.strip()]


@dataclass
class _BM25Index:
    docs: List[DocChunk]
    doc_tokens: List[List[str]] = field(default_factory=list)
    df: Dict[str, int] = field(default_factory=dict)
    n: int = 0
    avgdl: float = 0.0
    k1: float = 1.5
    b: float = 0.75

    def build(self):
        self.n = len(self.docs)
        self.doc_tokens = [_tokenize(d.text) for d in self.docs]
        total_len = sum(len(t) for t in self.doc_tokens)
        self.avgdl = total_len / max(1, self.n)
        self.df = {}
        for tokens in self.doc_tokens:
            seen = set()
            for t in tokens:
                if t in seen:
                    continue
                seen.add(t)
                self.df[t] = self.df.get(t, 0) + 1

    def _idf(self, term: str) -> float:
        # BM25+ idf 防负值
        df = self.df.get(term, 0)
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def score(self, query_tokens: List[str], doc_idx: int) -> float:
        tokens = self.doc_tokens[doc_idx]
        dl = len(tokens)
        if dl == 0:
            return 0.0
        tf: Dict[str, int] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1
        s = 0.0
        for term in set(query_tokens):
            if term not in tf:
                continue
            idf = self._idf(term)
            t_freq = tf[term]
            denom = t_freq + self.k1 * (1 - self.b + self.b * dl / max(1, self.avgdl))
            s += idf * (t_freq * (self.k1 + 1)) / denom
        return s

    def search(self, query: str, k: int = 5) -> List[tuple]:
        q_toks = _tokenize(query)
        if not q_toks:
            return []
        scores = [(i, self.score(q_toks, i)) for i in range(self.n)]
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:k]


# ---------------- Retriever ----------------


@dataclass
class RetrievedDoc:
    doc_id: str
    text: str
    score: float
    metadata: dict = field(default_factory=dict)


class HybridRetriever:
    """完整版：BM25 + Dense + RRF + Rerank；降级版：BM25 only。"""

    def __init__(self, docs: Optional[List[DocChunk]] = None):
        self.docs = docs if docs is not None else build_default_kb()
        self._bm25 = _BM25Index(docs=self.docs)
        self._bm25.build()
        self._dense_available = False  # Stage 3 后续可扩展
        self._reranker_available = False
        self.degraded = True  # 当前是 BM25-only 降级路径

    def retrieve(
        self,
        query: str,
        k: int = 3,
        collection: Optional[str] = None,
    ) -> List[RetrievedDoc]:
        """检索 top-k。collection 当前忽略（单 KB）。"""
        bm25_hits = self._bm25.search(query, k=max(k, 10))
        # Dense / Rerank 暂时跳过（接口预留）
        results: List[RetrievedDoc] = []
        for idx, score in bm25_hits[:k]:
            doc = self.docs[idx]
            results.append(
                RetrievedDoc(
                    doc_id=doc.doc_id,
                    text=doc.text,
                    score=float(score),
                    metadata=doc.metadata,
                )
            )
        return results


# 进程级单例
_default_retriever: Optional[HybridRetriever] = None


def get_default_retriever() -> HybridRetriever:
    global _default_retriever
    if _default_retriever is None:
        _default_retriever = HybridRetriever()
    return _default_retriever
