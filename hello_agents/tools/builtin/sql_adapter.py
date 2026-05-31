"""
SQL 多引擎适配器 — 统一接口支持 SQLite / Hive / ClickHouse

设计：
  - DataSourceConnector 抽象基类
  - SQLiteConnector（Demo 用，实际执行）
  - HiveConnector / ClickHouseConnector（生产扩展桩）
  - SQLAdapter 统一路由
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Tuple


class DataSourceConnector(ABC):
    """数据源连接器抽象基类"""

    @abstractmethod
    def get_tables(self) -> List[str]:
        """获取所有表名"""
        ...

    @abstractmethod
    def get_schema(self, table_name: str) -> Dict[str, Any]:
        """获取表结构

        Returns:
            {"table": "...", "columns": [{"name":..., "type":..., "nullable":...}]}
        """
        ...

    @abstractmethod
    def execute(self, sql: str, limit: int = 50) -> Tuple[List[str], List[List[Any]]]:
        """执行查询

        Returns:
            (列名列表, 数据行列表)
        """
        ...

    @abstractmethod
    def validate(self, sql: str) -> bool:
        """校验 SQL 语法"""
        ...


class SQLiteConnector(DataSourceConnector):
    """SQLite 连接器（Demo 用）"""

    def __init__(self, db_path: str):
        import sqlite3
        self.db_path = db_path

    def _connect(self):
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_tables(self) -> List[str]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]

    def get_schema(self, table_name: str) -> Dict[str, Any]:
        conn = self._connect()
        cols = conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
        conn.close()
        return {
            "table": table_name,
            "columns": [
                {"name": c[1], "type": c[2], "nullable": not c[3]}
                for c in cols
            ]
        }

    def execute(self, sql: str, limit: int = 50) -> Tuple[List[str], List[List[Any]]]:
        conn = self._connect()
        c = conn.cursor()
        c.execute(sql)
        col_names = [d[0] for d in c.description] if c.description else []
        rows = c.fetchmany(limit)
        conn.close()
        return col_names, [list(r) for r in rows]

    def validate(self, sql: str) -> bool:
        try:
            conn = self._connect()
            conn.execute(f"EXPLAIN {sql}")
            conn.close()
            return True
        except Exception:
            return False


class HiveConnector(DataSourceConnector):
    """Hive 连接器桩 — 生产环境对接 HiveServer2

    实际使用时替换为 PyHive 或 impyla 连接。
    """

    def __init__(self, host: str, port: int = 10000, database: str = "default"):
        self.host = host
        self.port = port
        self.database = database
        self._connected = False
        print(f"🐝 Hive 连接器已配置: {host}:{port}/{database}")

    def get_tables(self) -> List[str]:
        return []  # 生产环境通过 SHOW TABLES 获取

    def get_schema(self, table_name: str) -> Dict[str, Any]:
        return {"table": table_name, "note": "Hive 连接器桩，需替换为实际实现"}

    def execute(self, sql: str, limit: int = 50) -> Tuple[List[str], List[List[Any]]]:
        return [], []  # 桩实现

    def validate(self, sql: str) -> bool:
        return True  # 桩实现，总是返回 True


class ClickHouseConnector(DataSourceConnector):
    """ClickHouse 连接器桩 — 生产环境对接 ClickHouse HTTP/TCP

    实际使用时替换为 clickhouse-driver 或 clickhouse-connect。
    """

    def __init__(self, host: str, port: int = 8123, database: str = "default"):
        self.host = host
        self.port = port
        self.database = database
        print(f"🏠 ClickHouse 连接器已配置: {host}:{port}/{database}")

    def get_tables(self) -> List[str]:
        return []

    def get_schema(self, table_name: str) -> Dict[str, Any]:
        return {"table": table_name, "note": "ClickHouse 连接器桩，需替换为实际实现"}

    def execute(self, sql: str, limit: int = 50) -> Tuple[List[str], List[List[Any]]]:
        return [], []

    def validate(self, sql: str) -> bool:
        return True


class SQLAdapter:
    """SQL 多引擎适配器 — 根据配置路由到不同连接器"""

    def __init__(self):
        self._connectors: Dict[str, DataSourceConnector] = {}

    def register(self, name: str, connector: DataSourceConnector):
        """注册数据源"""
        self._connectors[name] = connector

    def get(self, name: str) -> Optional[DataSourceConnector]:
        return self._connectors.get(name)

    def list_sources(self) -> List[str]:
        return list(self._connectors.keys())

    # Sugar: 代理到默认连接器
    def get_tables(self, source: str = "default") -> List[str]:
        if conn := self._connectors.get(source):
            return conn.get_tables()
        return []

    def get_schema(self, table_name: str, source: str = "default") -> Dict[str, Any]:
        if conn := self._connectors.get(source):
            return conn.get_schema(table_name)
        return {}

    def execute(self, sql: str, source: str = "default", limit: int = 50):
        if conn := self._connectors.get(source):
            return conn.execute(sql, limit)
        return [], []

    def validate(self, sql: str, source: str = "default") -> bool:
        if conn := self._connectors.get(source):
            return conn.validate(sql)
        return False
