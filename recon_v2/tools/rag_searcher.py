"""rag_searcher Tool：通过 HybridRetriever 检索文档（schema / 业务说明）。

实现策略（Stage 1）：
- 接口先稳定：name / input / output schema
- 真实检索后端（Qdrant + BM25 + Cross-Encoder）在 Stage 3 装配
- 缺失后端时返回空结果 + degraded=true 提示，绝不抛异常
"""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import Field

from recon_v2.tools.base import ToolBase, ToolInput, ToolOutput


class RagSearcherInput(ToolInput):
    query: str = Field(..., description="检索 query")
    k: int = Field(3, description="返回 top-k", ge=1, le=20)
    collection: Optional[str] = Field(None, description="可选：指定 collection")


class RagDoc(ToolInput):
    doc_id: str = ""
    text: str = ""
    score: float = 0.0
    metadata: dict = {}


class RagSearcherOutput(ToolOutput):
    docs: List[dict] = []
    degraded: bool = False


class RagSearcherTool(ToolBase[RagSearcherInput, RagSearcherOutput]):
    name = "rag_searcher"
    description = (
        "Retrieve relevant business documentation (table schemas, column descriptions, "
        "or domain rules) using hybrid BM25 + dense retrieval with cross-encoder rerank."
    )
    input_schema = RagSearcherInput
    output_schema = RagSearcherOutput
    intents = ()

    def __init__(self, retriever: Any = None, auto_default: bool = True):
        """retriever 优先注入；否则 auto_default=True 时构造默认 BM25 retriever。"""
        if retriever is None and auto_default:
            try:
                from recon_v2.rag.retriever import get_default_retriever

                retriever = get_default_retriever()
            except Exception:
                retriever = None
        self.retriever = retriever

    def _run(self, ctx: Any, inp: RagSearcherInput) -> RagSearcherOutput:
        if self.retriever is None:
            return RagSearcherOutput(
                success=True,
                docs=[],
                degraded=True,
                metadata={"reason": "retriever not configured"},
            )

        try:
            docs = self.retriever.retrieve(inp.query, k=inp.k, collection=inp.collection)
            return RagSearcherOutput(
                success=True,
                degraded=getattr(self.retriever, "degraded", False),
                docs=[
                    {
                        "doc_id": getattr(d, "doc_id", ""),
                        "text": getattr(d, "text", ""),
                        "score": float(getattr(d, "score", 0.0)),
                        "metadata": getattr(d, "metadata", {}),
                    }
                    for d in docs
                ],
            )
        except Exception as e:
            return RagSearcherOutput(
                success=False,
                error=f"retriever error: {e}",
                degraded=True,
            )
