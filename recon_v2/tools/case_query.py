"""case_query Tool：从 Episodic Memory 中检索过往相似 case。

Stage 1 留空 stub；Stage 4 接 MemoryStore。
"""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import Field

from recon_v2.tools.base import ToolBase, ToolInput, ToolOutput


class CaseQueryInput(ToolInput):
    query: str = Field(..., description="自然语言 query")
    k: int = Field(3, description="返回 top-k", ge=1, le=10)
    intent_filter: Optional[str] = Field(None, description="可选：限制 intent")


class CaseQueryOutput(ToolOutput):
    cases: List[dict] = []
    degraded: bool = False


class CaseQueryTool(ToolBase[CaseQueryInput, CaseQueryOutput]):
    name = "case_query"
    description = (
        "Retrieve similar historical cases from Episodic Memory. "
        "Returns past (query, sql, answer, outcome) tuples for in-context few-shot."
    )
    input_schema = CaseQueryInput
    output_schema = CaseQueryOutput

    def __init__(self, memory_store: Any = None):
        self.memory_store = memory_store

    def _run(self, ctx: Any, inp: CaseQueryInput) -> CaseQueryOutput:
        if self.memory_store is None:
            return CaseQueryOutput(
                success=True,
                cases=[],
                degraded=True,
                metadata={"reason": "memory_store not configured; will be wired in Stage 4"},
            )
        try:
            cases = self.memory_store.query_episodic(
                inp.query, k=inp.k, intent_filter=inp.intent_filter
            )
            return CaseQueryOutput(success=True, cases=cases)
        except Exception as e:
            return CaseQueryOutput(
                success=False,
                error=f"memory error: {e}",
                degraded=True,
            )
