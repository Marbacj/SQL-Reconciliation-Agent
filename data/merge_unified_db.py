"""将 enterprise_mock.db 和 leetcode_test.db 合并为统一数据库 unified_test.db。

策略：
- enterprise_mock.db 表名保持不变（保持对账场景兼容）
- leetcode_test.db 的表统一加 lc_ 前缀（避免与对账表冲突）
- 同时更新 leetcode_golden.jsonl 中的 SQL，替换表名引用
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

PROJECT_ROOT = Path(__file__).parents[1]
SRCS = [
    ("enterprise", PROJECT_ROOT / "data/enterprise_mock.db"),
    ("leetcode", PROJECT_ROOT / "data/leetcode_test.db"),
]
OUT = PROJECT_ROOT / "data/unified_test.db"
GOLDEN_IN = PROJECT_ROOT / "tests/eval/leetcode_golden.jsonl"
GOLDEN_OUT = PROJECT_ROOT / "tests/eval/unified_golden.jsonl"


def _get_tables(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """返回 {表名: [{cid, name, type, ...}, ...]}"""
    tables = {}
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall():
        cols = conn.execute(f"PRAGMA table_info({name!r})").fetchall()
        tables[name] = [
            {"cid": c[0], "name": c[1], "type": c[2], "notnull": c[3], "dflt": c[4], "pk": c[5]}
            for c in cols
        ]
    return tables


def _get_row_count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table!r}").fetchone()[0]


def _remap_sql_tables(sql: str) -> str:
    """将 SQL 中冲突的 LeetCode 表名加 lc_ 前缀。"""
    import re

    CONFLICT_TABLES = ["Products", "Users"]
    result = sql
    for t in CONFLICT_TABLES:
        result = re.sub(
            rf'\b(?<!lc_){re.escape(t)}\b',
            f'lc_{t}',
            result,
        )
    return result


def _copy_table(
    src_conn: sqlite3.Connection,
    dst_conn: sqlite3.Connection,
    src_name: str,
    dst_name: str,
):
    """复制一张表的结构 + 数据。"""
    cols = src_conn.execute(f"PRAGMA table_info({src_name!r})").fetchall()
    col_defs = []
    col_names = []
    pk_cols = []
    for c in cols:
        col_names.append(c[1])
        col_def = f"{c[1]} {c[2]}"
        col_defs.append(col_def)
        if c[5]:
            pk_cols.append(c[1])

    # 构建 CREATE，复合主键需要单独声明
    if len(pk_cols) == 1:
        # 单列主键：直接在列定义后加
        pk_col = pk_cols[0]
        for i, c in enumerate(cols):
            if c[1] == pk_col:
                col_defs[i] += " PRIMARY KEY"
                break
    elif len(pk_cols) > 1:
        # 复合主键：单独声明
        col_defs.append(f"PRIMARY KEY ({', '.join(pk_cols)})")

    create_sql = f"CREATE TABLE IF NOT EXISTS {dst_name} ({', '.join(col_defs)})"
    logger.info("  CREATE %s", dst_name)
    dst_conn.execute(create_sql)

    rows = src_conn.execute(f"SELECT * FROM {src_name!r}").fetchall()
    if rows:
        placeholders = ", ".join("?" * len(col_names))
        cols_q = ", ".join(f"{c!r}" for c in col_names)
        insert_sql = f"INSERT OR REPLACE INTO {dst_name} ({cols_q}) VALUES ({placeholders})"
        dst_conn.executemany(insert_sql, rows)
        logger.info("    → %d rows", len(rows))


def main():
    # 1. 收集所有表
    all_src = {}  # {(source_label, table_name): conn}
    conns = {}
    for label, path in SRCS:
        if not path.exists():
            logger.warning("Source not found: %s, skip", path)
            continue
        conn = sqlite3.connect(str(path))
        conns[label] = conn
        for tname in _get_tables(conn):
            all_src[(label, tname)] = conn

    # 2. 检测冲突（SQLite 表名不区分大小写）
    ent_tables = {t for l, t in all_src if l == "enterprise"}
    lc_tables = {t for l, t in all_src if l == "leetcode"}
    ent_lower = {t.lower(): t for t in ent_tables}
    lc_lower = {t.lower(): t for t in lc_tables}
    conflicts = set(ent_lower.keys()) & set(lc_lower.keys())
    if conflicts:
        logger.info("冲突表名: %s", sorted(conflicts))

    # 3. 创建目标库
    if OUT.exists():
        OUT.unlink()
    dst = sqlite3.connect(str(OUT))

    total_tables = 0
    # 仅冲突的 LeetCode 表加 lc_ 前缀，其余保持不变
    # 使用小写判断是否冲突
    for (label, tname), src in sorted(all_src.items()):
        tname_lower = tname.lower()
        if label == "enterprise":
            dst_name = tname  # 企业表名不变
        elif tname_lower in conflicts:
            dst_name = f"lc_{tname}"  # 冲突的 LeetCode 表加前缀
            logger.info("[%s] %s → %s (冲突，添加 lc_ 前缀)", label, tname, dst_name)
        else:
            dst_name = tname  # 无冲突 LeetCode 表名保持不变
            logger.info("[%s] %s → %s", label, tname, dst_name)

        _copy_table(src, dst, tname, dst_name)
        total_tables += 1

    dst.commit()

    # 4. 验证
    final_tables = _get_tables(dst)
    total_rows = sum(_get_row_count(dst, t) for t in final_tables)
    logger.info("统一库: %d 张表, %d 行数据", len(final_tables), total_rows)

    # 5. 生成统一 golden set（SQL 中表名替换）
    if GOLDEN_IN.exists():
        remapped_lines = []
        for raw in GOLDEN_IN.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                remapped_lines.append(raw)
                continue
            try:
                obj = json.loads(line)
                # 替换 expected_sql 中的表名
                if "expected_sql" in obj:
                    obj["expected_sql"] = _remap_sql_tables(obj["expected_sql"])
                # 也替换 mysql_sql 中的表名
                if "mysql_sql" in obj:
                    obj["mysql_sql"] = _remap_sql_tables(obj["mysql_sql"])
                remapped_lines.append(json.dumps(obj, ensure_ascii=False))
            except json.JSONDecodeError:
                remapped_lines.append(raw)

        GOLDEN_OUT.write_text("\n".join(remapped_lines) + "\n", encoding="utf-8")
        logger.info("统一 golden set → %s", GOLDEN_OUT)

    # 6. 关闭连接
    for conn in conns.values():
        conn.close()
    dst.close()

    logger.info("✅ 合并完成: %s", OUT)
    logger.info("   使用: python3 -m tests.eval.runner --target stub --db data/unified_test.db --golden tests/eval/unified_golden.jsonl")
    logger.info("   或设置: EVAL_DB_PATH=data/unified_test.db")


if __name__ == "__main__":
    main()