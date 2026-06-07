# @author mabohui <mabohui@kuaishou.com>
# Created on 2026-06-07
#
# PostgreSQL SQLAdapter 实现（psycopg2 软依赖）
#
# 软依赖设计：
#   - import psycopg2 失败时不崩溃，在构造时抛出友好 ImportError
#   - 连接为短连接模式（每次 execute 建连 + 关闭）
#   - 读取 search_path 用于 schema 限定

from __future__ import annotations

import time
from typing import Any, Optional

from recon_v2.adapters.base import ExecResult


class PostgreSQLAdapter:
    name = "postgres"
    dialect = "postgres"

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5432,
        user: str = "postgres",
        password: str = "",
        database: str = "postgres",
        schema: str = "public",
        timeout: float = 10.0,
        connect_timeout: int = 5,
        options: str = "",
    ):
        try:
            import psycopg2  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "psycopg2-binary is required for PostgreSQLAdapter. "
                "Install it with: pip install psycopg2-binary"
            ) from e

        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.schema = schema
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.options = options

    # ── 内部方法 ──────────────────────────────────────────────
    def _connect(self) -> Any:
        import psycopg2
        import psycopg2.extras

        conn = psycopg2.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            dbname=self.database,
            connect_timeout=self.connect_timeout,
            options=self.options or f"-c statement_timeout={int(self.timeout * 1000)}",
        )
        conn.autocommit = True
        # 设置 search_path，确保不带 schema 前缀的表名能正确解析
        with conn.cursor() as cur:
            cur.execute(f"SET search_path TO {self.schema}")
        return conn

    # ── 公开接口 ──────────────────────────────────────────────
    def explain(self, sql: str) -> ExecResult:
        """EXPLAIN 预校验：PostgreSQL 使用 EXPLAIN (FORMAT TEXT)。"""
        t0 = time.time()
        conn = None
        try:
            conn = self._connect()
            with conn.cursor() as cur:
                clean_sql = sql.strip().rstrip(";")
                cur.execute(f"EXPLAIN {clean_sql}")
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description] if cur.description else []
                return ExecResult(
                    success=True,
                    columns=cols,
                    rows=list(rows),
                    row_count=len(rows),
                    latency_ms=(time.time() - t0) * 1000,
                )
        except Exception as e:
            return ExecResult(
                success=False,
                error=str(e),
                latency_ms=(time.time() - t0) * 1000,
            )
        finally:
            if conn:
                conn.close()

    def execute(self, sql: str) -> ExecResult:
        t0 = time.time()
        conn = None
        try:
            conn = self._connect()
            with conn.cursor() as cur:
                cur.execute(sql.strip().rstrip(";"))
                rows = cur.fetchall()
                # psycopg2 description 是 Column 对象，取 .name 属性
                cols = [d.name for d in cur.description] if cur.description else []
                return ExecResult(
                    success=True,
                    columns=cols,
                    rows=[list(r) for r in rows],
                    row_count=len(rows),
                    latency_ms=(time.time() - t0) * 1000,
                )
        except Exception as e:
            return ExecResult(
                success=False,
                error=str(e),
                latency_ms=(time.time() - t0) * 1000,
            )
        finally:
            if conn:
                conn.close()

    def close(self) -> None:
        # 短连接模式，无持久连接需要释放
        pass

    def ping(self) -> bool:
        """连通性检测，返回 True 表示连接成功。"""
        try:
            conn = self._connect()
            conn.close()
            return True
        except Exception:
            return False

    def get_schemas(self) -> ExecResult:
        """列出指定 schema 下所有表及列信息，用于 schema indexing。"""
        sql = """
            SELECT
                c.table_name,
                c.column_name,
                c.data_type,
                pgd.description AS comment,
                c.ordinal_position AS position
            FROM information_schema.columns c
            LEFT JOIN pg_catalog.pg_statio_all_tables AS st
                ON st.schemaname = c.table_schema
                AND st.relname = c.table_name
            LEFT JOIN pg_catalog.pg_description pgd
                ON pgd.objoid = st.relid
                AND pgd.objsubid = c.ordinal_position
            WHERE c.table_schema = %(schema)s
            ORDER BY c.table_name, c.ordinal_position
        """
        t0 = time.time()
        conn = None
        try:
            conn = self._connect()
            with conn.cursor() as cur:
                cur.execute(sql, {"schema": self.schema})
                rows = cur.fetchall()
                cols = [d.name for d in cur.description] if cur.description else []
                return ExecResult(
                    success=True,
                    columns=cols,
                    rows=[list(r) for r in rows],
                    row_count=len(rows),
                    latency_ms=(time.time() - t0) * 1000,
                )
        except Exception as e:
            return ExecResult(
                success=False,
                error=str(e),
                latency_ms=(time.time() - t0) * 1000,
            )
        finally:
            if conn:
                conn.close()
