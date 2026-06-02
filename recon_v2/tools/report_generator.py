"""report_generator Tool：把结构化结果渲染成 Markdown / JSON 报告。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from pydantic import Field

from recon_v2.tools.base import ToolBase, ToolInput, ToolOutput


class ReportInput(ToolInput):
    title: str = Field(..., description="报告标题")
    query: str = Field(..., description="原始用户问题")
    sql: Optional[str] = Field(None, description="实际执行的 SQL")
    columns: List[str] = Field(default_factory=list)
    rows: List[list] = Field(default_factory=list, description="结果行（list of list）")
    summary: str = Field("", description="结论/解读")
    format: str = Field("markdown", description="markdown|json")


class ReportOutput(ToolOutput):
    content: str = ""
    format: str = "markdown"


class ReportGeneratorTool(ToolBase[ReportInput, ReportOutput]):
    name = "report_generator"
    description = "Render a reconciliation result into a Markdown or JSON report."
    input_schema = ReportInput
    output_schema = ReportOutput
    intents = ()

    def _run(self, ctx: Any, inp: ReportInput) -> ReportOutput:
        if inp.format == "json":
            payload = {
                "title": inp.title,
                "query": inp.query,
                "sql": inp.sql,
                "columns": inp.columns,
                "rows": inp.rows,
                "summary": inp.summary,
            }
            return ReportOutput(
                success=True,
                content=json.dumps(payload, ensure_ascii=False, indent=2),
                format="json",
            )

        # Markdown
        lines: List[str] = []
        lines.append(f"# {inp.title}\n")
        lines.append(f"**Query**: {inp.query}\n")
        if inp.sql:
            lines.append(f"**SQL**:\n\n```sql\n{inp.sql}\n```\n")

        if inp.columns and inp.rows:
            lines.append("## Result\n")
            lines.append("| " + " | ".join(inp.columns) + " |")
            lines.append("| " + " | ".join(["---"] * len(inp.columns)) + " |")
            # 只渲染前 50 行避免 prompt 爆炸
            for row in inp.rows[:50]:
                lines.append("| " + " | ".join(str(v) for v in row) + " |")
            if len(inp.rows) > 50:
                lines.append(f"\n_({len(inp.rows) - 50} more rows omitted)_")

        if inp.summary:
            lines.append("\n## Summary\n")
            lines.append(inp.summary)

        return ReportOutput(
            success=True,
            content="\n".join(lines),
            format="markdown",
        )
