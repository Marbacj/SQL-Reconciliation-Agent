"""QueryRewriter：在检索前对用户 query 做 LLM 改写。

目标：
- 扩充关键词（中文口语 → 数据库术语）
- 标准化模糊表达（"欠款最多" → "最大未还金额"）
- 失败时原样返回，绝不抛异常
"""

from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """\
You are a query rewriter for a SQL database assistant.
Given a user query in Chinese or English, rewrite it to improve retrieval of relevant table schemas and business rules.

Rules:
- Expand abbreviations and colloquialisms into standard database terminology
- Keep the core intent unchanged
- Output JSON: {"rewritten": "<improved query>", "keywords": ["kw1", "kw2", "kw3"]}
- If the query is already clear, still return the same structure
- Respond with JSON only, no explanation
"""


class QueryRewriter:
    """用 LLMGateway 对 query 做一次轻量改写。"""

    def __init__(self, llm: object):
        self._llm = llm

    def rewrite(self, query: str) -> str:
        """返回改写后的 query；失败时返回原始 query。"""
        if not query or self._llm is None:
            return query
        try:
            result = self._llm.chat(
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": f"Query: {query}"},
                ],
                temperature=0.0,
                max_tokens=150,
                use_cache=True,
            )
            parsed = json.loads(result.content.strip())
            rewritten = parsed.get("rewritten", "").strip()
            if rewritten:
                logger.debug("QueryRewriter: %r → %r", query, rewritten)
                return rewritten
        except Exception as e:
            logger.debug("QueryRewriter failed (using original): %s", e)
        return query


# ── 工厂 ─────────────────────────────────────────────────────────────────────

def build_query_rewriter(llm: Optional[object] = None) -> Optional[QueryRewriter]:
    """有 llm 时返回 QueryRewriter，否则返回 None（调用方需做 None 检查）。"""
    if llm is None:
        return None
    return QueryRewriter(llm=llm)
