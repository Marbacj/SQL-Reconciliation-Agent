"""SchemaInspector：从数据库实时获取表结构 + 枚举采样 + 列级数据剖析 + JOIN 关系。

接口：
- inspect(db_path, adapter, profile=True) → SchemaInfo
- SchemaInfo.to_prompt_str() → 给 LLM 用的 schema 描述（含值域、统计、JOIN 关系）

枚举检测策略：
- TEXT 类型字段 + 字段名包含枚举关键词 → SELECT DISTINCT LIMIT 20

列级数据剖析（profile=True 时）：
- 数值列：min / max / avg / null_rate
- 日期列：date range（min/max）
- 文本 ID 列：sample values（top 3）
- 大表（>500k 行）跳过剖析，避免全表扫描

JOIN 关系：
- 优先 PRAGMA foreign_key_list（显式 FK）
- fallback 命名推断（{other_table}_id 模式）
- 关系信息注入 to_prompt_str() 减少 LLM 猜测 JOIN key
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 触发枚举采样的字段名关键词
_ENUM_KEYWORDS = {"status", "type", "channel", "kind", "category", "state", "mode"}
_ENUM_MAX_CARDINALITY = 20

# 触发数值剖析的列类型
_NUMERIC_TYPES = {"INTEGER", "REAL", "NUMERIC", "FLOAT", "DOUBLE", "DECIMAL", "BIGINT", "INT"}
# 触发日期剖析的列名关键词
_DATE_KEYWORDS = {"created_at", "updated_at", "paid_at", "order_time", "pay_time", "date", "time"}
# 大表阈值：超过此行数跳过剖析（避免全表扫描）
_PROFILE_MAX_ROWS = 500_000
# 高基数文本列（_id 后缀）不做 sample
_ID_SUFFIX = "_id"


@dataclass
class ColumnStats:
    """列级统计信息（数值列 / 日期列）。"""
    null_rate: float = 0.0        # 0.0-1.0
    min_val: Optional[str] = None
    max_val: Optional[str] = None
    avg_val: Optional[str] = None  # 数值列专用
    sample_vals: List[str] = field(default_factory=list)  # 低基数文本列的样本


@dataclass
class ColumnInfo:
    name: str
    col_type: str
    notnull: bool = False
    default_val: Optional[str] = None
    is_pk: bool = False
    enum_values: List[str] = field(default_factory=list)
    stats: Optional[ColumnStats] = None  # 数值/日期列剖析结果

    def to_desc(self) -> str:
        parts = [f"{self.name} ({self.col_type})"]
        if self.is_pk:
            parts.append("PK")
        if self.enum_values:
            parts.append(f"values: {', '.join(repr(v) for v in self.enum_values)}")
        return " ".join(parts)


@dataclass
class Relation:
    """表间 JOIN 关系。"""
    from_table: str
    from_col: str
    to_table: str
    to_col: str
    source: str = "inferred"  # "fk" | "inferred"


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
    relations: List[Relation] = field(default_factory=list)  # JOIN 关系图

    def to_prompt_str(self) -> str:
        """生成 LLM prompt 用的 schema 描述，包含枚举值、列统计、JOIN 关系。"""
        lines = [f"Database schema ({self.dialect}):"]
        for t in self.tables:
            row_hint = f" ({t.row_estimate:,} rows)" if t.row_estimate else ""
            lines.append(f"  Table: {t.name}{row_hint}")
            for c in t.columns:
                line = f"    - {c.name} {c.col_type}"
                if c.is_pk:
                    line += " [PK]"
                if c.enum_values:
                    line += f"  -- values: {', '.join(repr(v) for v in c.enum_values)}"
                elif c.stats:
                    hints = []
                    if c.stats.null_rate > 0.01:
                        hints.append(f"null_rate={c.stats.null_rate:.0%}")
                    if c.stats.min_val is not None and c.stats.max_val is not None:
                        hints.append(f"range=[{c.stats.min_val}, {c.stats.max_val}]")
                    if c.stats.avg_val is not None:
                        hints.append(f"avg={c.stats.avg_val}")
                    if c.stats.sample_vals:
                        hints.append(f"e.g. {', '.join(repr(v) for v in c.stats.sample_vals[:3])}")
                    if hints:
                        line += "  -- " + ", ".join(hints)
                lines.append(line)

        # JOIN 关系段：帮助 LLM 知道如何 JOIN，不再依靠猜测
        if self.relations:
            lines.append("\nJOIN relationships:")
            seen = set()
            for r in self.relations:
                key = (r.from_table, r.to_table)
                if key in seen:
                    continue
                seen.add(key)
                src_tag = "[FK]" if r.source == "fk" else "[inferred]"
                lines.append(
                    f"  {r.from_table}.{r.from_col} = {r.to_table}.{r.to_col}  {src_tag}"
                )

        return "\n".join(lines)

    def table_names(self) -> List[str]:
        return [t.name for t in self.tables]


def inspect(db_path: str, adapter: Any = None, profile: bool = True) -> SchemaInfo:
    """主入口：检查数据库 schema。

    profile=True 时额外剖析数值/日期列统计 + 推断 JOIN 关系。
    优先使用传入的 adapter（生产环境可传 MySQL/PG adapter）；
    无 adapter 时退回 SQLite 直连。
    """
    if adapter is not None and hasattr(adapter, "dialect"):
        dialect = adapter.dialect
        return _inspect_via_adapter(adapter, dialect)

    # SQLite fallback
    return _inspect_sqlite(db_path, profile=profile)


def _inspect_sqlite(db_path: str, profile: bool = True) -> SchemaInfo:
    """通过 sqlite3 直连获取 schema，用 PRAGMA table_info + SELECT DISTINCT 枚举采样。"""
    import sqlite3

    try:
        conn = sqlite3.connect(db_path, timeout=5.0)
        try:
            return _do_inspect_sqlite(conn, profile=profile)
        finally:
            conn.close()
    except Exception as e:
        logger.warning("SchemaInspector SQLite failed: %s", e)
        return SchemaInfo(dialect="sqlite")


def _do_inspect_sqlite(conn, profile: bool = True) -> SchemaInfo:
    cur = conn.cursor()

    # 获取所有用户表
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    table_names = [row[0] for row in cur.fetchall()]

    tables: List[TableInfo] = []
    for tname in table_names:
        try:
            cur.execute(f"PRAGMA table_info(\"{tname}\")")
            rows = cur.fetchall()
        except Exception as e:
            logger.warning("PRAGMA table_info(%s) failed: %s", tname, e)
            continue

        # 行数估算（用于决定是否跳过剖析）
        try:
            cur.execute(f'SELECT COUNT(*) FROM "{tname}"')
            row_count = cur.fetchone()[0]
        except Exception:
            row_count = None

        skip_profile = (not profile) or (row_count is not None and row_count > _PROFILE_MAX_ROWS)

        cols: List[ColumnInfo] = []
        for cid, cname, ctype, notnull, dflt, pk in rows:
            col_type_upper = (ctype or "TEXT").upper()
            col = ColumnInfo(
                name=cname,
                col_type=col_type_upper,
                notnull=bool(notnull),
                default_val=str(dflt) if dflt is not None else None,
                is_pk=bool(pk),
            )
            if _should_sample_enum(cname, col_type_upper):
                col.enum_values = _sample_enum_sqlite(conn, tname, cname)
            elif not skip_profile and not bool(pk):
                col.stats = _profile_column_sqlite(conn, tname, cname, col_type_upper, row_count or 0)
            cols.append(col)

        tables.append(TableInfo(name=tname, columns=cols, row_estimate=row_count))

    # JOIN 关系推断
    relations = _infer_relations_sqlite(conn, table_names)

    return SchemaInfo(tables=tables, dialect="sqlite", relations=relations)


def _profile_column_sqlite(
    conn, table: str, col: str, col_type: str, row_count: int
) -> Optional[ColumnStats]:
    """对单列执行轻量统计查询，返回 ColumnStats。

    数值列 (INTEGER/REAL/…): MIN / MAX / AVG + null_rate
    日期列 (列名含 date/time 关键词): MIN / MAX
    低基数文本列 (非 enum, 非 id, 非 pk): sample top-3 values
    其他：跳过返回 None
    """
    try:
        cur = conn.cursor()
        col_q = f'"{col}"'
        tbl_q = f'"{table}"'
        is_numeric = any(t in col_type for t in _NUMERIC_TYPES)
        is_date = any(kw in col.lower() for kw in _DATE_KEYWORDS)
        is_id = col.lower().endswith(_ID_SUFFIX) or col.lower() == "id"

        # 计算 null_rate
        null_count = 0
        if row_count > 0:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {tbl_q} WHERE {col_q} IS NULL")
                null_count = cur.fetchone()[0]
            except Exception:
                pass

        null_rate = null_count / row_count if row_count > 0 else 0.0

        if is_numeric:
            try:
                cur.execute(f"SELECT MIN({col_q}), MAX({col_q}), AVG({col_q}) FROM {tbl_q}")
                mn, mx, avg = cur.fetchone()
                return ColumnStats(
                    null_rate=null_rate,
                    min_val=_fmt_num(mn),
                    max_val=_fmt_num(mx),
                    avg_val=_fmt_num(avg),
                )
            except Exception:
                return None

        if is_date:
            try:
                cur.execute(f"SELECT MIN({col_q}), MAX({col_q}) FROM {tbl_q}")
                mn, mx = cur.fetchone()
                if mn or mx:
                    return ColumnStats(
                        null_rate=null_rate,
                        min_val=str(mn)[:10] if mn else None,
                        max_val=str(mx)[:10] if mx else None,
                    )
            except Exception:
                return None

        # 低基数文本列：取 top-3 出现最多的值作为样本
        if not is_id and "TEXT" in col_type:
            try:
                cur.execute(
                    f"SELECT {col_q}, COUNT(*) AS cnt FROM {tbl_q} "
                    f"WHERE {col_q} IS NOT NULL GROUP BY {col_q} ORDER BY cnt DESC LIMIT 5"
                )
                rows = cur.fetchall()
                if rows and len(rows) <= 20:  # 低基数才有意义
                    return ColumnStats(
                        null_rate=null_rate,
                        sample_vals=[str(r[0]) for r in rows[:3]],
                    )
            except Exception:
                pass

        return None
    except Exception as e:
        logger.debug("_profile_column_sqlite %s.%s: %s", table, col, e)
        return None


def _fmt_num(v) -> Optional[str]:
    if v is None:
        return None
    try:
        f = float(v)
        if f == int(f) and abs(f) < 1e12:
            return str(int(f))
        return f"{f:.2f}"
    except (TypeError, ValueError):
        return str(v)


def _infer_relations_sqlite(conn, table_names: List[str]) -> List[Relation]:
    """从显式 FK 和命名规则推断表间 JOIN 关系。"""
    relations: List[Relation] = []
    seen: set = set()
    table_set = set(table_names)

    for tname in table_names:
        # Layer 1：PRAGMA foreign_key_list（显式 FK）
        try:
            fks = conn.execute(f'PRAGMA foreign_key_list("{tname}")').fetchall()
            for fk in fks:
                # (id, seq, table, from_col, to_col, ...)
                to_table, from_col, to_col = fk[2], fk[3], fk[4]
                key = (tname, to_table)
                if key not in seen:
                    seen.add(key)
                    relations.append(Relation(tname, from_col, to_table, to_col, source="fk"))
        except Exception:
            pass

        # Layer 2：命名推断 {other_table}_id
        try:
            cols = conn.execute(f'PRAGMA table_info("{tname}")').fetchall()
            for row in cols:
                cname = row[1]
                if not cname.endswith("_id") or cname == "id":
                    continue
                prefix = cname[:-3]  # order_id → order
                # 尝试复数形式（order → orders）
                candidates = [prefix, prefix + "s"]
                for cand in candidates:
                    if cand in table_set and cand != tname:
                        key = (tname, cand)
                        if key not in seen:
                            seen.add(key)
                            # 目标表的 PK（通常是 id）
                            to_cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{cand}")').fetchall()}
                            to_col = "id" if "id" in to_cols else cname
                            relations.append(Relation(tname, cname, cand, to_col, source="inferred"))
                        break
        except Exception:
            pass

    return relations


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
