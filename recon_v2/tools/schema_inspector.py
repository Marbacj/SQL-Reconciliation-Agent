"""SchemaInspector：从数据库实时获取表结构 + 枚举采样。

取代 RAG 里硬编码的 schema chunk，保证 schema 信息永远最新。

接口：
- inspect(db_path, adapter) → SchemaInfo
- SchemaInfo.to_prompt_str() → 给 LLM 用的 schema 描述文本

枚举检测策略：
- TEXT 类型字段 + 字段名包含枚举关键词（status/type/channel/kind/category）
- SELECT DISTINCT LIMIT 20 采样
- 结果数 <= 20 才认定为枚举字段，高基数字段跳过
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 触发枚举采样的字段名关键词
_ENUM_KEYWORDS = {"status", "type", "channel", "kind", "category", "state", "mode"}

# 枚举最大不同值数（超过则视为高基数，不采样）
_ENUM_MAX_CARDINALITY = 20


@dataclass
class ColumnInfo:
    name: str
    col_type: str
    notnull: bool = False
    default_val: Optional[str] = None
    is_pk: bool = False
    enum_values: List[str] = field(default_factory=list)

    def to_desc(self) -> str:
        parts = [f"{self.name} ({self.col_type})"]
        if self.is_pk:
            parts.append("PK")
        if self.enum_values:
            parts.append(f"values: {', '.join(repr(v) for v in self.enum_values)}")
        return " ".join(parts)


@dataclass
class TableInfo:
    name: str
    columns: List[ColumnInfo] = field(default_factory=list)
    row_estimate: Optional[int] = None

    def to_desc(self) -> str:
        col_descs = ", ".join(c.to_desc() for c in self.columns)
        return f"{self.name}({col_descs})"


@dataclass
class SchemaInfo:
    tables: List[TableInfo] = field(default_factory=list)
    dialect: str = "sqlite"

    def to_prompt_str(self) -> str:
        """生成 LLM prompt 用的 schema 描述，包含枚举值。"""
        lines = [f"Database schema ({self.dialect}):"]
        for t in self.tables:
            lines.append(f"  Table: {t.name}")
            for c in t.columns:
                line = f"    - {c.name} {c.col_type}"
                if c.is_pk:
                    line += " [PK]"
                if c.enum_values:
                    line += f"  -- values: {', '.join(repr(v) for v in c.enum_values)}"
                lines.append(line)
            # 关联关系提示
            fk_cols = [c.name for c in t.columns if c.name.endswith("_id") and not c.is_pk]
            for fk in fk_cols:
                ref_table = fk.replace("_id", "") + "s"
                lines.append(f"    -- {t.name}.{fk} references {ref_table}.id")
        return "\n".join(lines)

    def table_names(self) -> List[str]:
        return [t.name for t in self.tables]


def inspect(db_path: str, adapter: Any = None) -> SchemaInfo:
    """主入口：检查数据库 schema。

    优先使用传入的 adapter（生产环境可传 MySQL/PG adapter）；
    无 adapter 时退回 SQLite 直连。
    """
    if adapter is not None and hasattr(adapter, "dialect"):
        dialect = adapter.dialect
        return _inspect_via_adapter(adapter, dialect)

    # SQLite fallback
    return _inspect_sqlite(db_path)


def _inspect_sqlite(db_path: str) -> SchemaInfo:
    """通过 sqlite3 直连获取 schema，用 PRAGMA table_info + SELECT DISTINCT 枚举采样。"""
    import sqlite3

    try:
        conn = sqlite3.connect(db_path, timeout=5.0)
        try:
            return _do_inspect_sqlite(conn)
        finally:
            conn.close()
    except Exception as e:
        logger.warning("SchemaInspector SQLite failed: %s", e)
        return SchemaInfo(dialect="sqlite")


def _do_inspect_sqlite(conn) -> SchemaInfo:
    import sqlite3

    cur = conn.cursor()

    # 获取所有用户表（排除 sqlite_ 内部表）
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    table_names = [row[0] for row in cur.fetchall()]

    tables: List[TableInfo] = []
    for tname in table_names:
        # PRAGMA table_info(tname) → (cid, name, type, notnull, dflt_value, pk)
        try:
            cur.execute(f"PRAGMA table_info({tname})")
            rows = cur.fetchall()
        except Exception as e:
            logger.warning("PRAGMA table_info(%s) failed: %s", tname, e)
            continue

        cols: List[ColumnInfo] = []
        for cid, cname, ctype, notnull, dflt, pk in rows:
            col = ColumnInfo(
                name=cname,
                col_type=(ctype or "TEXT").upper(),
                notnull=bool(notnull),
                default_val=str(dflt) if dflt is not None else None,
                is_pk=bool(pk),
            )
            # 枚举采样
            if _should_sample_enum(cname, col.col_type):
                col.enum_values = _sample_enum_sqlite(conn, tname, cname)
            cols.append(col)

        # 行数估算（用 COUNT，对大表可换 sqlite_stat1）
        try:
            cur.execute(f"SELECT COUNT(*) FROM {tname}")
            row_count = cur.fetchone()[0]
        except Exception:
            row_count = None

        tables.append(TableInfo(name=tname, columns=cols, row_estimate=row_count))

    return SchemaInfo(tables=tables, dialect="sqlite")


def _should_sample_enum(col_name: str, col_type: str) -> bool:
    """判断是否值得采样枚举值。TEXT 类型 + 名称含枚举关键词。"""
    if "TEXT" not in col_type.upper() and "VARCHAR" not in col_type.upper():
        return False
    name_lower = col_name.lower()
    return any(kw in name_lower for kw in _ENUM_KEYWORDS)


def _sample_enum_sqlite(conn, table: str, col: str) -> List[str]:
    """SELECT DISTINCT col FROM table LIMIT 20，返回枚举值列表（高基数时返回空）。"""
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL LIMIT {_ENUM_MAX_CARDINALITY + 1}"
        )
        rows = [str(r[0]) for r in cur.fetchall()]
        # 超过阈值 → 高基数字段，不作为枚举
        if len(rows) > _ENUM_MAX_CARDINALITY:
            return []
        return sorted(rows)
    except Exception as e:
        logger.debug("enum sample failed %s.%s: %s", table, col, e)
        return []


def _inspect_via_adapter(adapter: Any, dialect: str) -> SchemaInfo:
    """通过 SQLAdapter 协议获取 schema（MySQL/PG 通用路径）。"""
    # MySQL: SHOW TABLES → DESCRIBE table
    # PG: information_schema.columns
    # 这里提供 MySQL/SQLite 通用路径：先查 tables，再 PRAGMA/DESC
    tables: List[TableInfo] = []
    try:
        if dialect == "sqlite":
            res = adapter.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        elif dialect in ("mysql", "tidb"):
            res = adapter.execute("SHOW TABLES")
        elif dialect in ("postgresql", "postgres"):
            res = adapter.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public'"
            )
        else:
            # 兜底：尝试 information_schema
            res = adapter.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema=DATABASE()"
            )

        if not res.success:
            logger.warning("SchemaInspector: list tables failed: %s", res.error)
            return SchemaInfo(dialect=dialect)

        table_names = [r[0] for r in res.rows]
    except Exception as e:
        logger.warning("SchemaInspector via adapter failed: %s", e)
        return SchemaInfo(dialect=dialect)

    for tname in table_names:
        cols = _describe_table_via_adapter(adapter, tname, dialect)
        tables.append(TableInfo(name=tname, columns=cols))

    return SchemaInfo(tables=tables, dialect=dialect)


def _describe_table_via_adapter(adapter: Any, table: str, dialect: str) -> List[ColumnInfo]:
    """通过 adapter 获取单表字段信息。"""
    try:
        if dialect == "sqlite":
            res = adapter.execute(f"PRAGMA table_info({table})")
            if not res.success:
                return []
            cols = []
            for row in res.rows:
                # (cid, name, type, notnull, dflt_value, pk)
                cid, cname, ctype, notnull, dflt, pk = row[:6]
                col = ColumnInfo(
                    name=cname,
                    col_type=(ctype or "TEXT").upper(),
                    notnull=bool(notnull),
                    is_pk=bool(pk),
                )
                cols.append(col)
            return cols

        elif dialect in ("mysql", "tidb"):
            res = adapter.execute(f"DESCRIBE {table}")
            if not res.success:
                return []
            # Field, Type, Null, Key, Default, Extra
            return [
                ColumnInfo(
                    name=row[0],
                    col_type=row[1].upper(),
                    notnull=(row[2] == "NO"),
                    is_pk=(row[3] == "PRI"),
                )
                for row in res.rows
            ]

        elif dialect in ("postgresql", "postgres"):
            res = adapter.execute(
                f"SELECT column_name, data_type, is_nullable "
                f"FROM information_schema.columns WHERE table_name='{table}'"
            )
            if not res.success:
                return []
            return [
                ColumnInfo(name=row[0], col_type=row[1].upper(), notnull=(row[2] == "NO"))
                for row in res.rows
            ]
    except Exception as e:
        logger.warning("describe table %s failed: %s", table, e)
    return []
