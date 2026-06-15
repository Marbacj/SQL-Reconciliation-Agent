"""HybridRetriever：BM25 + Dense（Milvus）+ RRF + Rerank。

降级链（越往下越宽松）：
  BM25 + Dense + Rerank  →  BM25 + Dense  →  BM25 + Rerank  →  BM25 only
degraded=True 当且仅当 Dense 不可用。
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from recon_v2.rag.chunker import DocChunk, build_default_kb

logger = logging.getLogger(__name__)


# ── 简易 BM25（无外部依赖）──────────────────────────────────────────────────


def _tokenize(text: str) -> List[str]:
    """中英文混合分词：英文按空格，中文按字符。"""
    text = text.lower()
    tokens: List[str] = []
    buf: List[str] = []
    for ch in text:
        if "一" <= ch <= "鿿":
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

    def search(self, query: str, k: int = 5) -> List[Tuple[int, float]]:
        q_toks = _tokenize(query)
        if not q_toks:
            return []
        scores = [(i, self.score(q_toks, i)) for i in range(self.n)]
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:k]


# ── RetrievedDoc ──────────────────────────────────────────────────────────────


@dataclass
class RetrievedDoc:
    doc_id: str
    text: str
    score: float
    metadata: dict = field(default_factory=dict)


# ── RRF 合并 ─────────────────────────────────────────────────────────────────


def _rrf_merge(
    bm25_hits: List[Tuple[int, float]],
    dense_hits: List[Tuple[str, float]],
    docs: List[DocChunk],
    k: int,
    rrf_k: int = 60,
) -> List[Tuple[int, float]]:
    """Reciprocal Rank Fusion：合并 BM25（by index）和 Dense（by doc_id）排名。

    返回 (doc_idx, rrf_score) 列表，按 rrf_score 降序。
    """
    scores: Dict[int, float] = {}

    # BM25 贡献
    for rank, (idx, _) in enumerate(bm25_hits):
        scores[idx] = scores.get(idx, 0.0) + 1.0 / (rank + rrf_k)

    # Dense 贡献（doc_id → idx 映射）
    id_to_idx = {doc.doc_id: i for i, doc in enumerate(docs)}
    for rank, (doc_id, _) in enumerate(dense_hits):
        idx = id_to_idx.get(doc_id)
        if idx is not None:
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (rank + rrf_k)

    merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return merged[:k]


# ── HybridRetriever ──────────────────────────────────────────────────────────


class HybridRetriever:
    """完整版：BM25 + Dense + RRF + Rerank；降级版：BM25 only。"""

    def __init__(
        self,
        docs: Optional[List[DocChunk]] = None,
        llm: Optional[object] = None,
    ):
        self.docs = docs if docs is not None else build_default_kb()
        self._bm25 = _BM25Index(docs=self.docs)
        self._bm25.build()

        # ── Dense（KB 语义向量：Dashscope + Milvus）──────────────────────────
        self._kb_store = None
        self._dense_available = False
        try:
            from recon_v2.rag.kb_vector_store import get_kb_store
            kb = get_kb_store()
            if kb is not None:
                # 首次启动：collection 为空则自动索引
                if kb.count() == 0:
                    logger.info("HybridRetriever: KB collection 为空，自动建索引...")
                    kb.index(self.docs)
                self._kb_store = kb
                self._dense_available = True
                logger.info("HybridRetriever: Dense 已启用（%d docs in Milvus）", kb.count())
        except Exception as e:
            logger.info("HybridRetriever: Dense 不可用，降级 BM25-only（%s）", e)

        # ── Reranker ──────────────────────────────────────────────────────
        from recon_v2.rag.reranker import build_reranker, PassthroughReranker
        self._reranker = build_reranker(llm=llm)
        self._reranker_available = not isinstance(self._reranker, PassthroughReranker)

        self.degraded = not self._dense_available

    # ── Dense 查询辅助 ────────────────────────────────────────────────────────

    def _dense_search(self, query: str, k: int) -> List[Tuple[str, float]]:
        """语义向量检索，返回 [(doc_id, score), ...]；失败返回空列表。"""
        if not self._dense_available or self._kb_store is None:
            return []
        try:
            return self._kb_store.search(query, k=k)
        except Exception as e:
            logger.debug("Dense search failed: %s", e)
            return []

    # ── 核心检索 ─────────────────────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        k: int = 3,
        collection: Optional[str] = None,
    ) -> List[RetrievedDoc]:
        """检索 top-k，自动选择 BM25+Dense+Rerank 或降级路径。"""
        recall = max(k * 3, 10)

        # BM25 召回
        bm25_hits = self._bm25.search(query, k=recall)

        # Dense 召回 + RRF 合并
        if self._dense_available:
            dense_hits = self._dense_search(query, k=recall)
            merged = _rrf_merge(bm25_hits, dense_hits, self.docs, k=recall)
        else:
            merged = bm25_hits[:recall]

        # 构建 RetrievedDoc 列表
        candidates: List[RetrievedDoc] = []
        for idx, score in merged:
            if 0 <= idx < len(self.docs):
                doc = self.docs[idx]
                candidates.append(
                    RetrievedDoc(
                        doc_id=doc.doc_id,
                        text=doc.text,
                        score=float(score),
                        metadata=doc.metadata,
                    )
                )

        # Rerank
        if self._reranker_available and len(candidates) > k:
            candidates = self._reranker.rerank(query, candidates, k=k)
        else:
            candidates = candidates[:k]

        return candidates


# ── 进程级单例 ────────────────────────────────────────────────────────────────

_default_retriever: Optional[HybridRetriever] = None


def get_default_retriever(llm: Optional[object] = None) -> HybridRetriever:
    global _default_retriever
    if _default_retriever is None:
        _default_retriever = HybridRetriever(llm=llm)
    return _default_retriever
