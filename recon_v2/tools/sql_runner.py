"""sql_runner Tool：调安全护栏 + EXPLAIN + execute。

多数据源支持说明：
- dialect="sqlite"  → 执行前做 MySQL→SQLite 方言转换（兼容 LLM 习惯用 MySQL 语法）
- dialect="mysql"   → 直接执行原生 MySQL SQL，跳过转换
- dialect="postgres"→ 直接执行原生 PostgreSQL SQL，跳过转换
"""

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
        dialect = self.adapter.dialect

        # 1) 安全护栏（AST 解析，传入实际 dialect 以正确识别方言关键字）
        verdict = is_safe(inp.sql, dialect)
        if not verdict.is_safe:
            return SQLRunnerOutput(
                success=False,
                error=f"safety rejected: {verdict.reason}",
                final_sql=inp.sql,
            )

        # 2) 方言适配（仅 SQLite 需要：LLM 倾向于写 MySQL 语法）
        #    MySQL / PostgreSQL 直接执行原生 SQL，无需转换
        if dialect == "sqlite":
            sql = adapt_mysql_to_sqlite(inp.sql)
        else:
            sql = inp.sql

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
            metadata={"limit_added": modified, "dialect": dialect},
        )
