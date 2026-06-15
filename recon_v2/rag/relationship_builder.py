"""RelationshipChunkBuilder：自动从 DB schema 推断表间关联，生成关联 chunk。

推断策略（按优先级）：
  1. PRAGMA foreign_key_list — 显式 FK 声明（最可靠）
  2. 命名推断 — col 名为 `{other_table}_id` 或 `{other_table_singular}_id`，
     且 other_table 存在于同一 DB
  3. 手动补充 — 通过 extra_relations 参数传入（应对非标准命名）

每对有关联的表生成一个 RelationChunk，描述：
  - 连接键（join key）
  - 业务含义
  - 典型 JOIN SQL 模板
  - 常见查询场景

同时生成一个全局 overview chunk（所有表 + 关联一览）。
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class Relation:
    from_table: str
    from_col: str
    to_table: str
    to_col: str
    source: str = "inferred"          # "fk" | "inferred" | "manual"
    cardinality: str = "N:1"          # 从 from_table 看：N:1 表示多对一
    business_hint: str = ""


@dataclass
class RelationChunk:
    doc_id: str
    text: str
    relations: List[Relation] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


# ── 关联推断 ──────────────────────────────────────────────────────────────────

def _get_all_tables(conn: sqlite3.Connection) -> Dict[str, List[str]]:
    """返回 {table_name: [col_name, ...]}。"""
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    result = {}
    for (tname,) in tables:
        cols = conn.execute(f"PRAGMA table_info({tname})").fetchall()
        result[tname] = [c[1] for c in cols]
    return result


def _detect_fk_relations(conn: sqlite3.Connection, tables: Dict[str, List[str]]) -> List[Relation]:
    """从 PRAGMA foreign_key_list 提取显式 FK。"""
    relations = []
    for tname in tables:
        fks = conn.execute(f"PRAGMA foreign_key_list({tname})").fetchall()
        for fk in fks:
            # fk: (id, seq, table, from, to, on_update, on_delete, match)
            relations.append(Relation(
                from_table=tname,
                from_col=fk[3],
                to_table=fk[2],
                to_col=fk[4],
                source="fk",
                cardinality="N:1",
            ))
    return relations


def _singular(name: str) -> str:
    """粗略单数化：orders→order, payments→payment, refunds→refund。"""
    if name.endswith("ies"):
        return name[:-3] + "y"
    if name.endswith("s") and not name.endswith("ss"):
        return name[:-1]
    return name


def _detect_naming_relations(
    tables: Dict[str, List[str]],
    existing: Set[Tuple[str, str]],
) -> List[Relation]:
    """命名推断：col 名为 {other_table}_id 或 {other_table_singular}_id。"""
    table_set = set(tables.keys())
    singular_map = {_singular(t): t for t in table_set}  # order→orders
    relations = []

    for tname, cols in tables.items():
        for col in cols:
            if not col.endswith("_id") or col == "id":
                continue
            prefix = col[:-3]  # order_id → order
            # 查整体名和单数名
            target = None
            if prefix in table_set:
                target = prefix
            elif prefix in singular_map:
                target = singular_map[prefix]
            if target is None or target == tname:
                continue
            key = (tname, target)
            if key in existing:
                continue
            # 检查 target 表有 id 或同名列
            target_cols = tables[target]
            ref_col = "id" if "id" in target_cols else (col if col in target_cols else None)
            if ref_col is None:
                continue
            existing.add(key)
            relations.append(Relation(
                from_table=tname,
                from_col=col,
                to_table=target,
                to_col=ref_col,
                source="inferred",
                cardinality="N:1",
            ))
    return relations


# ── Chunk 生成 ────────────────────────────────────────────────────────────────

# 预置的业务含义描述（命中时补充到 chunk）
_BUSINESS_HINTS: Dict[Tuple[str, str], str] = {
    ("payments", "orders"): "一笔订单可对应多条支付记录（正常支付、重复支付、补单等）。",
    ("refunds", "orders"): "一笔订单可对应多条退款记录（分批退款、部分退款等）。",
    ("order_amount", "live_gmv"): "order_amount 是订单明细，live_gmv 是直播间汇总，通过 live_id 关联用于 GMV 对账。",
}

# 预置的常见查询场景（按表对）
_COMMON_QUERIES: Dict[Tuple[str, str], List[Tuple[str, str]]] = {
    ("payments", "orders"): [
        ("对账差异", "SELECT o.id, o.amount, p.amount AS paid, ABS(o.amount-p.amount) AS diff\nFROM orders o\nJOIN payments p ON o.id = p.order_id\nWHERE ABS(o.amount - p.amount) > 0.01"),
        ("漏支付检测", "SELECT o.id, o.amount FROM orders o\nLEFT JOIN payments p ON o.id = p.order_id AND p.status='success'\nWHERE o.status='paid' AND p.order_id IS NULL"),
        ("重复支付检测", "SELECT order_id, COUNT(*) AS cnt FROM payments\nWHERE status='success'\nGROUP BY order_id HAVING cnt > 1"),
    ],
    ("refunds", "orders"): [
        ("孤儿退款", "SELECT r.id, r.order_id FROM refunds r\nLEFT JOIN orders o ON r.order_id = o.id\nWHERE o.id IS NULL"),
        ("净收入", "SELECT SUM(o.amount) - COALESCE(SUM(r.amount),0) AS net\nFROM orders o\nLEFT JOIN refunds r ON o.id = r.order_id\nWHERE o.status='paid'"),
    ],
    ("order_amount", "live_gmv"): [
        ("GMV 对账", "SELECT g.live_id, g.gmv, SUM(a.total_amount) AS order_total,\n       g.gmv - SUM(a.total_amount) AS diff\nFROM live_gmv g\nJOIN order_amount a ON g.live_id = a.live_id\nGROUP BY g.live_id\nHAVING ABS(diff) > 0.01"),
    ],
}


def _build_pair_chunk(rel: Relation) -> RelationChunk:
    """为一对表生成关联 chunk。"""
    ft, fc = rel.from_table, rel.from_col
    tt, tc = rel.to_table, rel.to_col
    doc_id = f"relation:{ft}__{tt}"

    hint = _BUSINESS_HINTS.get((ft, tt)) or _BUSINESS_HINTS.get((tt, ft)) or ""
    queries = _COMMON_QUERIES.get((ft, tt)) or _COMMON_QUERIES.get((tt, ft)) or []

    lines = [
        f"# 关联关系：{ft} ↔ {tt}",
        "",
        "## 连接键",
        f"- `{ft}.{fc}` = `{tt}.{tc}`",
        f"- 来源：{'显式 FK 声明' if rel.source == 'fk' else '字段命名推断'}",
        f"- 基数：{ft} 中多条记录对应 {tt} 中一条（{rel.cardinality}）",
        "",
    ]

    if hint:
        lines += ["## 业务含义", hint, ""]

    lines += [
        "## 标准 JOIN 写法",
        f"```sql",
        f"SELECT * FROM {ft} f",
        f"JOIN {tt} t ON f.{fc} = t.{tc}",
        f"-- 或 LEFT JOIN 保留无匹配的 {ft} 行",
        f"```",
        "",
    ]

    if queries:
        lines.append("## 常见查询场景")
        for name, sql in queries:
            lines += [f"### {name}", "```sql", sql, "```", ""]

    return RelationChunk(
        doc_id=doc_id,
        text="\n".join(lines),
        relations=[rel],
        metadata={"type": "relation", "tables": f"{ft},{tt}"},
    )


def _build_overview_chunk(
    tables: Dict[str, List[str]],
    relations: List[Relation],
) -> RelationChunk:
    """生成全局 schema overview chunk（所有表 + 关联一览）。"""
    lines = [
        "# 数据库 Schema 总览",
        "",
        "## 表清单",
    ]
    for tname, cols in sorted(tables.items()):
        lines.append(f"- `{tname}`：{', '.join(cols)}")

    if relations:
        lines += ["", "## 表关联关系"]
        for r in relations:
            lines.append(f"- `{r.from_table}.{r.from_col}` → `{r.to_table}.{r.to_col}`")

    return RelationChunk(
        doc_id="relation:overview",
        text="\n".join(lines),
        relations=relations,
        metadata={"type": "relation", "tables": "all"},
    )


# ── 主入口 ────────────────────────────────────────────────────────────────────

class RelationshipChunkBuilder:
    """从 SQLite DB 自动推断表关联，生成 RelationChunk 列表。"""

    def __init__(
        self,
        db_path: str,
        extra_relations: Optional[List[Relation]] = None,
    ):
        self.db_path = db_path
        self.extra_relations = extra_relations or []

    def build(self) -> List[RelationChunk]:
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            tables = _get_all_tables(conn)
            if not tables:
                return []

            # 推断关联
            existing: Set[Tuple[str, str]] = set()
            relations: List[Relation] = []

            fk_rels = _detect_fk_relations(conn, tables)
            for r in fk_rels:
                existing.add((r.from_table, r.to_table))
            relations.extend(fk_rels)

            naming_rels = _detect_naming_relations(tables, existing)
            relations.extend(naming_rels)
            relations.extend(self.extra_relations)

            conn.close()

            if not relations:
                logger.info("RelationshipChunkBuilder: 未检测到表关联")
                return [_build_overview_chunk(tables, [])]

            chunks: List[RelationChunk] = []
            for rel in relations:
                chunks.append(_build_pair_chunk(rel))
                logger.debug("built relation chunk: %s ↔ %s", rel.from_table, rel.to_table)

            chunks.append(_build_overview_chunk(tables, relations))
            logger.info("RelationshipChunkBuilder: 生成 %d 个关联 chunk", len(chunks))
            return chunks

        except Exception as e:
            logger.warning("RelationshipChunkBuilder failed: %s", e)
            return []
