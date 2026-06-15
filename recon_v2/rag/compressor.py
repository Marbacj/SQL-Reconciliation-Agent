"""ContextCompressor：在 doc.text 放入 prompt 前做提取式压缩。

两种策略：
1. 句子级 BM25 过滤（默认，无额外依赖）：
   - 把 doc.text 拆成句子，用 BM25 对 query 打分
   - 保留得分 top-N 句子，控制每 doc 上限 max_chars 字符
2. LLM 摘要（可选，use_llm=True）：
   - 仅当 doc 超过 llm_threshold 字时调用
   - prompt 极简："从以下文档提取与 {query} 相关的关键信息（≤100字）"

compress() 返回 docs list，text 字段被替换，doc_id / score / metadata 不变。
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── BM25 句子级打分（复用 retriever 中的分词逻辑）────────────────────────────

def _tokenize(text: str) -> List[str]:
    tokens: List[str] = []
    buf: List[str] = []
    for ch in text.lower():
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


def _split_sentences(text: str) -> List[str]:
    """按中英文句子边界分割。"""
    parts = re.split(r"(?<=[。！？\.\!\?])\s*", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _bm25_score(query_tokens: List[str], sent_tokens: List[str], avgdl: float, n: int, df: Dict[str, int]) -> float:
    k1, b = 1.5, 0.75
    dl = len(sent_tokens)
    if dl == 0:
        return 0.0
    tf: Dict[str, int] = {}
    for t in sent_tokens:
        tf[t] = tf.get(t, 0) + 1
    score = 0.0
    for term in set(query_tokens):
        if term not in tf:
            continue
        df_t = df.get(term, 0)
        idf = math.log(1 + (n - df_t + 0.5) / (df_t + 0.5))
        t_freq = tf[term]
        denom = t_freq + k1 * (1 - b + b * dl / max(1, avgdl))
        score += idf * (t_freq * (k1 + 1)) / denom
    return score


def _extract_top_sentences(query: str, text: str, max_chars: int = 200) -> str:
    """BM25 选出最相关的句子，拼接后截断到 max_chars。"""
    sentences = _split_sentences(text)
    if not sentences:
        return text[:max_chars]

    query_tokens = _tokenize(query)
    sent_tokens_list = [_tokenize(s) for s in sentences]

    n = len(sentences)
    total_len = sum(len(t) for t in sent_tokens_list)
    avgdl = total_len / max(1, n)

    df: Dict[str, int] = {}
    for tokens in sent_tokens_list:
        for t in set(tokens):
            df[t] = df.get(t, 0) + 1

    scored = [
        (_bm25_score(query_tokens, sent_tokens_list[i], avgdl, n, df), sentences[i])
        for i in range(n)
    ]
    scored.sort(key=lambda x: x[0], reverse=True)

    result = []
    total = 0
    for _, sent in scored:
        if total + len(sent) > max_chars:
            break
        result.append(sent)
        total += len(sent)

    if not result:
        return text[:max_chars]

    # 按原始顺序还原（提升可读性）
    order = {s: i for i, (_, s) in enumerate(scored)}
    result.sort(key=lambda s: order.get(s, 999))
    return "".join(result)


# ── LLM 摘要 ─────────────────────────────────────────────────────────────────

_COMPRESS_SYSTEM = (
    "Extract only the key information relevant to the query from the document below. "
    "Output in Chinese. Max 100 characters. No extra explanation."
)


def _llm_summarize(llm: Any, query: str, text: str) -> str:
    try:
        result = llm.chat(
            messages=[
                {"role": "system", "content": _COMPRESS_SYSTEM},
                {"role": "user", "content": f"Query: {query}\n\nDocument:\n{text}"},
            ],
            temperature=0.0,
            max_tokens=120,
            use_cache=True,
        )
        return result.content.strip()
    except Exception as e:
        logger.debug("LLM compress failed: %s", e)
        return text[:200]


# ── ContextCompressor ─────────────────────────────────────────────────────────

class ContextCompressor:
    """压缩 doc.text，降低 prompt 噪声，保留关键信息。"""

    def __init__(
        self,
        max_chars: int = 200,
        use_llm: bool = False,
        llm_threshold: int = 800,
        llm: Optional[Any] = None,
    ):
        self.max_chars = max_chars
        self.use_llm = use_llm and llm is not None
        self.llm_threshold = llm_threshold
        self._llm = llm

    def compress_one(self, query: str, text: str) -> str:
        """压缩单个文档文本。"""
        if len(text) <= self.max_chars:
            return text

        if self.use_llm and len(text) > self.llm_threshold:
            return _llm_summarize(self._llm, query, text)

        return _extract_top_sentences(query, text, max_chars=self.max_chars)

    def compress(self, docs: List[dict], query: str) -> List[dict]:
        """压缩 docs 列表中每个 doc 的 text 字段，其余字段不变。"""
        result = []
        for doc in docs:
            text = doc.get("text", "")
            compressed = self.compress_one(query, text)
            result.append({**doc, "text": compressed})
        return result
