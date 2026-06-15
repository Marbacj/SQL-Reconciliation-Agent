"""Reranker：对召回结果精排。

两种实现（自动降级）：
- CrossEncoderReranker：sentence-transformers CrossEncoder（需要安装）
- LLMReranker：调 LLMGateway 批量打分（无额外依赖，默认降级路径）

使用方：
    reranker = build_reranker(llm=gateway)
    ranked = reranker.rerank(query, docs, k=3)
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import List, Optional

logger = logging.getLogger(__name__)


# ── 抽象基类 ─────────────────────────────────────────────────────────────────

class Reranker(ABC):
    @abstractmethod
    def rerank(self, query: str, docs: list, k: int) -> list:
        """返回按相关性降序排列的 top-k docs（列表元素类型同输入）。"""


# ── Cross-Encoder（sentence-transformers）────────────────────────────────────

class CrossEncoderReranker(Reranker):
    """使用 sentence-transformers CrossEncoder 精排。"""

    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"):
        from sentence_transformers import CrossEncoder  # type: ignore
        self._model = CrossEncoder(model_name)
        logger.info("CrossEncoderReranker loaded: %s", model_name)

    def rerank(self, query: str, docs: list, k: int) -> list:
        if not docs:
            return docs
        pairs = [(query, getattr(d, "text", d.get("text", "") if isinstance(d, dict) else "")) for d in docs]
        scores = self._model.predict(pairs)
        ranked = sorted(zip(scores, docs), key=lambda x: x[0], reverse=True)
        result = [d for _, d in ranked[:k]]
        # 回写 score 字段
        for score, d in ranked[:k]:
            if hasattr(d, "score"):
                d.score = float(score)
        return result


# ── LLM Reranker（无额外依赖）────────────────────────────────────────────────

_RERANK_SYSTEM = (
    "You are a relevance judge. Given a query and a list of documents, "
    "score each document 0-10 for relevance to the query. "
    "Respond with a JSON array of numbers only, e.g. [8, 3, 6]. "
    "Array length MUST equal the number of documents."
)


class LLMReranker(Reranker):
    """用 LLMGateway 批量给文档打相关性分，再按分排序。"""

    def __init__(self, llm: object):
        self._llm = llm

    def rerank(self, query: str, docs: list, k: int) -> list:
        if not docs or self._llm is None:
            return docs[:k]
        try:
            snippets = []
            for i, d in enumerate(docs):
                text = getattr(d, "text", d.get("text", "") if isinstance(d, dict) else "")
                snippets.append(f"[{i}] {text[:200]}")
            user_msg = f"Query: {query}\n\nDocuments:\n" + "\n".join(snippets)
            result = self._llm.chat(
                messages=[
                    {"role": "system", "content": _RERANK_SYSTEM},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.0,
                max_tokens=100,
                use_cache=True,
            )
            scores = json.loads(result.content.strip())
            if not isinstance(scores, list) or len(scores) != len(docs):
                return docs[:k]
            ranked = sorted(zip(scores, docs), key=lambda x: x[0], reverse=True)
            result_docs = [d for _, d in ranked[:k]]
            for score, d in ranked[:k]:
                if hasattr(d, "score"):
                    d.score = float(score)
            return result_docs
        except Exception as e:
            logger.debug("LLMReranker failed (returning original order): %s", e)
            return docs[:k]


# ── PassthroughReranker（无 reranker 时）────────────────────────────────────

class PassthroughReranker(Reranker):
    """不做精排，直接截断 top-k。"""

    def rerank(self, query: str, docs: list, k: int) -> list:
        return docs[:k]


# ── 工厂函数 ─────────────────────────────────────────────────────────────────

def build_reranker(llm: Optional[object] = None) -> Reranker:
    """根据 RERANKER_BACKEND 环境变量选择重排器。

    RERANKER_BACKEND 可选值：
      passthrough  — 保留 RRF 排名，不额外重排（默认；适合中文 + 已有好的语义向量）
      llm          — 调 LLM 打分（需传入 llm 参数；中文友好，有额外延迟）
      crossencoder — sentence-transformers CrossEncoder（仅适合英文查询）
    """
    import os
    backend = os.getenv("RERANKER_BACKEND", "passthrough").lower()

    if backend == "crossencoder":
        try:
            r = CrossEncoderReranker()
            logger.info("Using CrossEncoderReranker")
            return r
        except (ImportError, Exception) as e:
            logger.warning("CrossEncoderReranker unavailable: %s, falling back", e)

    if backend == "llm" or (backend == "crossencoder" and llm is not None):
        if llm is not None:
            logger.info("Using LLMReranker")
            return LLMReranker(llm=llm)
        logger.info("RERANKER_BACKEND=llm but no llm passed, using passthrough")

    logger.info("Using PassthroughReranker (preserving RRF order)")
    return PassthroughReranker()
