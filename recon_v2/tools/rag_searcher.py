"""rag_searcher Tool：Query Rewrite → Hybrid Retrieval → Context Compress。

流程：
  1. QueryRewriter 改写 query（有 LLM 时）
  2. HybridRetriever 检索（BM25 + Dense + RRF + Rerank）
  3. ContextCompressor 压缩 doc.text（句子级 BM25 过滤）
  4. 返回压缩后 docs + rewritten_query + degraded 标记
"""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import Field

from recon_v2.tools.base import ToolBase, ToolInput, ToolOutput


class RagSearcherInput(ToolInput):
    query: str = Field(..., description="检索 query")
    k: int = Field(3, description="返回 top-k", ge=1, le=20)
    collection: Optional[str] = Field(None, description="可选：指定 collection")


class RagSearcherOutput(ToolOutput):
    docs: List[dict] = []
    degraded: bool = False
    rewritten_query: Optional[str] = None


class RagSearcherTool(ToolBase[RagSearcherInput, RagSearcherOutput]):
    name = "rag_searcher"
    description = (
        "Retrieve relevant business documentation (table schemas, column descriptions, "
        "or domain rules) using query rewrite + hybrid BM25/dense retrieval + rerank + compression."
    )
    input_schema = RagSearcherInput
    output_schema = RagSearcherOutput
    intents = ()

    def __init__(
        self,
        retriever: Any = None,
        query_rewriter: Any = None,
        compressor: Any = None,
        auto_default: bool = True,
        llm: Any = None,
    ):
        # ── Retriever ──────────────────────────────────────────────────────
        if retriever is None and auto_default:
            try:
                from recon_v2.rag.retriever import get_default_retriever
                retriever = get_default_retriever(llm=llm)
            except Exception:
                retriever = None
        self.retriever = retriever

        # ── Query Rewriter ─────────────────────────────────────────────────
        if query_rewriter is None and llm is not None:
            try:
                from recon_v2.rag.query_rewriter import build_query_rewriter
                query_rewriter = build_query_rewriter(llm=llm)
            except Exception:
                query_rewriter = None
        self.query_rewriter = query_rewriter

        # ── Compressor ─────────────────────────────────────────────────────
        if compressor is None:
            try:
                from recon_v2.rag.compressor import ContextCompressor
                compressor = ContextCompressor(
                    max_chars=200,
                    use_llm=(llm is not None),
                    llm_threshold=800,
                    llm=llm,
                )
            except Exception:
                compressor = None
        self.compressor = compressor

    def _run(self, ctx: Any, inp: RagSearcherInput) -> RagSearcherOutput:
        if self.retriever is None:
            return RagSearcherOutput(
                success=True,
                docs=[],
                degraded=True,
                metadata={"reason": "retriever not configured"},
            )

        try:
            # 1. Query Rewrite
            rewritten = inp.query
            if self.query_rewriter is not None:
                rewritten = self.query_rewriter.rewrite(inp.query)

            # 2. Retrieval
            docs = self.retriever.retrieve(rewritten, k=inp.k, collection=inp.collection)

            raw_docs = [
                {
                    "doc_id": getattr(d, "doc_id", ""),
                    "text": getattr(d, "text", ""),
                    "score": float(getattr(d, "score", 0.0)),
                    "metadata": getattr(d, "metadata", {}),
                }
                for d in docs
            ]

            # 3. Context Compression
            if self.compressor is not None and raw_docs:
                raw_docs = self.compressor.compress(raw_docs, query=rewritten)

            return RagSearcherOutput(
                success=True,
                degraded=getattr(self.retriever, "degraded", False),
                docs=raw_docs,
                rewritten_query=rewritten if rewritten != inp.query else None,
            )
        except Exception as e:
            return RagSearcherOutput(
                success=False,
                error=f"retriever error: {e}",
                degraded=True,
            )
