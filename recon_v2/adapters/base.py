"""SQLAdapter 抽象：屏蔽不同数据源（SQLite / MySQL / Postgres）。

接口设计：
- explain(sql)：执行 EXPLAIN 预校验
- execute(sql)：执行查询，返回 columns + rows
- close()：释放资源

错误处理：所有方法返回 ExecResult，不直接抛异常，便于上层 Agent 决策。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Protocol


@dataclass
class ExecResult:
    success: bool
    columns: List[str] = field(default_factory=list)
    rows: List[tuple] = field(default_factory=list)
    error: Optional[str] = None
    row_count: int = 0
    latency_ms: float = 0.0

    def to_dict_rows(self) -> List[dict]:
        return [dict(zip(self.columns, r)) for r in self.rows]


class SQLAdapter(Protocol):
    name: str
    dialect: str

    def explain(self, sql: str) -> ExecResult:  # pragma: no cover
        ...

    def execute(self, sql: str) -> ExecResult:  # pragma: no cover
        ...

    def close(self) -> None:  # pragma: no cover
        ...

    def test_connection(self) -> dict:  # pragma: no cover
        """测试数据库连通性，返回 {"status": "ok", "latency_ms": float} 或 {"status": "error", "message": str}。"""
        ...
