# @author mabohui <mabohui@kuaishou.com>
# Created on 2026-06-07
#
# MySQL SQLAdapter 实现（pymysql 软依赖）
#
# 软依赖设计：
#   - import pymysql 失败时不崩溃，在构造时抛出友好 ImportError
#   - 连接为短连接模式（每次 execute 建连 + 关闭），可后续升级为连接池

from __future__ import annotations

import time
from typing import Any, Optional

from recon_v2.adapters.base import ExecResult


class MySQLAdapter:
    name = "mysql"
    dialect = "mysql"

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 3306,
        user: str = "root",
        password: str = "",
        database: str = "",
        charset: str = "utf8mb4",
        timeout: float = 10.0,
        connect_timeout: int = 5,
    ):
        try:
            import pymysql  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "pymysql is required for MySQLAdapter. "
                "Install it with: pip install pymysql"
            ) from e

        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.charset = charset
        self.timeout = timeout
        self.connect_timeout = connect_timeout

    # ── 内部方法 ──────────────────────────────────────────────
    def _connect(self) -> Any:
        import pymysql
        return pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database,
            charset=self.charset,
            connect_timeout=self.connect_timeout,
            read_timeout=int(self.timeout),
            write_timeout=int(self.timeout),
            cursorclass=pymysql.cursors.Cursor,
            autocommit=True,
        )

    # ── 公开接口 ──────────────────────────────────────────────
    def explain(self, sql: str) -> ExecResult:
        """EXPLAIN 预校验：MySQL 使用 EXPLAIN，失败表示 SQL 语法有误。"""
        t0 = time.time()
        conn = None
        try:
            conn = self._connect()
            with conn.cursor() as cur:
                explain_sql = f"EXPLAIN {sql.strip().rstrip(';')}"
                cur.execute(explain_sql)
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
                cols = [d[0] for d in cur.description] if cur.description else []
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
        """列出当前 database 所有表及列信息，用于 schema indexing。"""
        sql = """
            SELECT
                TABLE_NAME       AS table_name,
                COLUMN_NAME      AS column_name,
                DATA_TYPE        AS data_type,
                COLUMN_COMMENT   AS comment,
                ORDINAL_POSITION AS position
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
            ORDER BY TABLE_NAME, ORDINAL_POSITION
        """
        return self.execute(sql)
