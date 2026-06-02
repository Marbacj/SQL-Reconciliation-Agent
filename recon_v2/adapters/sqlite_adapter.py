"""SQLite SQLAdapter 实现。"""

from __future__ import annotations

import sqlite3
import time
from typing import Optional

from recon_v2.adapters.base import ExecResult


class SQLiteAdapter:
    name = "sqlite"
    dialect = "sqlite"

    def __init__(self, db_path: str, timeout: float = 5.0):
        self.db_path = db_path
        self._timeout = timeout

    def _conn(self) -> sqlite3.Connection:
        # 每次 query 短连接：简单可靠；高并发场景可换连接池
        return sqlite3.connect(self.db_path, timeout=self._timeout)

    def explain(self, sql: str) -> ExecResult:
        """EXPLAIN 预校验：SQLite 用 EXPLAIN QUERY PLAN。"""
        t0 = time.time()
        try:
            with self._conn() as conn:
                cur = conn.cursor()
                cur.execute(f"EXPLAIN QUERY PLAN {sql.strip().rstrip(';')}")
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description] if cur.description else []
                return ExecResult(
                    success=True,
                    columns=cols,
                    rows=rows,
                    row_count=len(rows),
                    latency_ms=(time.time() - t0) * 1000,
                )
        except Exception as e:
            return ExecResult(
                success=False,
                error=str(e),
                latency_ms=(time.time() - t0) * 1000,
            )

    def execute(self, sql: str) -> ExecResult:
        t0 = time.time()
        try:
            with self._conn() as conn:
                cur = conn.cursor()
                cur.execute(sql.strip().rstrip(";"))
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description] if cur.description else []
                return ExecResult(
                    success=True,
                    columns=cols,
                    rows=rows,
                    row_count=len(rows),
                    latency_ms=(time.time() - t0) * 1000,
                )
        except Exception as e:
            return ExecResult(
                success=False,
                error=str(e),
                latency_ms=(time.time() - t0) * 1000,
            )

    def close(self) -> None:
        # short-conn 模式无需显式 close
        pass
