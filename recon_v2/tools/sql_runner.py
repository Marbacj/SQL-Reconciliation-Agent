"""sql_runner Tool：调安全护栏 + EXPLAIN + execute。"""

from __future__ import annotations

from typing import Any, List

from pydantic import Field

from recon_v2.adapters.base import SQLAdapter
from recon_v2.adapters.sqlite_adapter import SQLiteAdapter
from recon_v2.infra.dialect_adapter import adapt_mysql_to_sqlite
from recon_v2.infra.sql_safety import apply_limit_guard, is_safe
from recon_v2.tools.base import ToolBase, ToolInput, ToolOutput


class SQLRunnerInput(ToolInput):
    sql: str = Field(..., description="待执行的 SQL（仅允许 SELECT/WITH）")
    apply_limit: bool = Field(True, description="是否自动给 SELECT 加 LIMIT 1000 防全表")


class SQLRunnerOutput(ToolOutput):
    columns: List[str] = []
    rows: List[list] = []
    row_count: int = 0
    final_sql: str = ""


class SQLRunnerTool(ToolBase[SQLRunnerInput, SQLRunnerOutput]):
    name = "sql_runner"
    description = (
        "Execute a read-only SQL query (SELECT/WITH only). "
        "Returns columns and rows. Unsafe SQL will be rejected before execution."
    )
    input_schema = SQLRunnerInput
    output_schema = SQLRunnerOutput
    intents = ()  # 所有 intent 通用

    def __init__(self, adapter: SQLAdapter | None = None, db_path: str | None = None):
        if adapter is None:
            assert db_path is not None, "Must provide adapter or db_path"
            adapter = SQLiteAdapter(db_path)
        self.adapter = adapter

    def _run(self, ctx: Any, inp: SQLRunnerInput) -> SQLRunnerOutput:
        # 1) 安全护栏
        verdict = is_safe(inp.sql, self.adapter.dialect)
        if not verdict.is_safe:
            return SQLRunnerOutput(
                success=False,
                error=f"safety rejected: {verdict.reason}",
                final_sql=inp.sql,
            )

        # 2) MySQL→SQLite 方言适配（后处理转换层）
        sql = adapt_mysql_to_sqlite(inp.sql)

        # 3) 自动加 LIMIT
        modified = False
        if inp.apply_limit:
            sql, modified = apply_limit_guard(sql, default_limit=1000)

        # 4) EXPLAIN 预校验
        explain_res = self.adapter.explain(sql)
        if not explain_res.success:
            return SQLRunnerOutput(
                success=False,
                error=f"explain failed: {explain_res.error}",
                final_sql=sql,
            )

        # 5) 实际执行
        res = self.adapter.execute(sql)
        if not res.success:
            return SQLRunnerOutput(
                success=False,
                error=f"execute failed: {res.error}",
                final_sql=sql,
                latency_ms=res.latency_ms,
            )

        return SQLRunnerOutput(
            success=True,
            columns=res.columns,
            rows=[list(r) for r in res.rows],
            row_count=res.row_count,
            final_sql=sql,
            latency_ms=res.latency_ms,
            metadata={"limit_added": modified},
        )
