# @author mabohui <mabohui@kuaishou.com>
# Created on 2026-06-03
#
# dialect_adapter.py — MySQL → SQLite SQL 后处理转换层
#
# 在 SQL 执行前做一次正则替换，把常见 MySQL 函数转成 SQLite 等价写法。
# 这是系统应完成的一部分（方言兼容），而非依赖用户/prompt 来避免。
#
# 接入点：
#   recon_v2/tools/sql_runner.py 的 _run 方法中，
#   在 apply_limit_guard 之后、EXPLAIN 之前调用。
#
# 用法：
#   from recon_v2.infra.dialect_adapter import adapt_mysql_to_sqlite
#   sql = adapt_mysql_to_sqlite(sql)

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


# ── MySQL → SQLite 替换规则 ──────────────────────────────

# 每条规则：(pattern, replacement, description)
# pattern 使用 re.IGNORECASE + re.DOTALL
# 替换按顺序执行，同一条 SQL 可能需要多轮替换

_REPLACEMENT_RULES: list[tuple[str, str, str]] = [
    # ── 字符串函数 ──
    # IF(cond, a, b) → CASE WHEN cond THEN a ELSE b END
    (
        r"IF\s*\(\s*(.+?)\s*,\s*(.+?)\s*,\s*(.+?)\s*\)",
        r"CASE WHEN \1 THEN \2 ELSE \3 END",
        "IF() → CASE WHEN",
    ),
    # CHAR_LENGTH(s) / CHARACTER_LENGTH(s) → LENGTH(s)
    (
        r"CHAR_LENGTH\s*\(\s*(.+?)\s*\)",
        r"LENGTH(\1)",
        "CHAR_LENGTH() → LENGTH()",
    ),
    (
        r"CHARACTER_LENGTH\s*\(\s*(.+?)\s*\)",
        r"LENGTH(\1)",
        "CHARACTER_LENGTH() → LENGTH()",
    ),
    # SUBSTRING(s, pos, len) → SUBSTR(s, pos, len)
    (
        r"SUBSTRING\s*\(",
        r"SUBSTR(",
        "SUBSTRING() → SUBSTR()",
    ),
    # CONCAT(a, b, ...) → a || b || ...
    # 注意：这个替换较复杂，只处理简单两参数场景
    # 多参数 CONCAT 的情况建议在 prompt 层避免
    (
        r"CONCAT\s*\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)",
        r"\1 || \2",
        "CONCAT() → || (simple 2-arg)",
    ),

    # ── 日期函数 ──
    # DATEDIFF(a, b) = 1 → julianday(a) - julianday(b) = 1
    (
        r"DATEDIFF\s*\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)\s*=\s*1",
        r"julianday(\1) - julianday(\2) = 1",
        "DATEDIFF = 1 → julianday差值 = 1",
    ),
    (
        r"DATEDIFF\s*\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)",
        r"julianday(\1) - julianday(\2)",
        "DATEDIFF() → julianday差值",
    ),
    # DATE_FORMAT(d, fmt) → strftime(fmt_sqlite, d)
    # 处理引号和百分号的各种组合
    (
        r"DATE_FORMAT\s*\(\s*([^,]+?)\s*,\s*['\"](%Y-%m-%d)['\"]\s*\)",
        r"strftime('\2', \1)",
        "DATE_FORMAT('%Y-%m-%d') → strftime()",
    ),
    (
        r"DATE_FORMAT\s*\(\s*([^,]+?)\s*,\s*['\"](%Y-%m)['\"]\s*\)",
        r"strftime('\2', \1)",
        "DATE_FORMAT('%Y-%m') → strftime()",
    ),
    # NOW() → DATETIME('now')
    (
        r"NOW\s*\(\s*\)",
        r"DATETIME('now')",
        "NOW() → DATETIME('now')",
    ),
    # CURDATE() → DATE('now')
    (
        r"CURDATE\s*\(\s*\)",
        r"DATE('now')",
        "CURDATE() → DATE('now')",
    ),
    # YEAR(d) → strftime('%Y', d)
    # 注意：需避免匹配子串中的 YEAR，用 word boundary
    (
        r"\bYEAR\s*\(\s*([^)]+?)\s*\)",
        r"strftime('%Y', \1)",
        "YEAR() → strftime('%Y')",
    ),
    # MONTH(d) → strftime('%m', d)
    (
        r"\bMONTH\s*\(\s*([^)]+?)\s*\)",
        r"strftime('%m', \1)",
        "MONTH() → strftime('%m')",
    ),
    # DAY(d) → strftime('%d', d)
    # 注意：需避免匹配 julianday/weekday 中的 day，用 word boundary
    (
        r"\bDAY\s*\(\s*([^)]+?)\s*\)",
        r"strftime('%d', \1)",
        "DAY() → strftime('%d')",
    ),

    # ── 类型转换 ──
    # CAST(x AS SIGNED) / CAST(x AS UNSIGNED) → CAST(x AS INTEGER)
    (
        r"CAST\s*\(\s*([^)]+?)\s+AS\s+SIGNED\s*\)",
        r"CAST(\1 AS INTEGER)",
        "CAST AS SIGNED → CAST AS INTEGER",
    ),
    (
        r"CAST\s*\(\s*([^)]+?)\s+AS\s+UNSIGNED\s*\)",
        r"CAST(\1 AS INTEGER)",
        "CAST AS UNSIGNED → CAST AS INTEGER",
    ),

    # ── 分页 ──
    # LIMIT n OFFSET m → LIMIT m, n (SQLite 旧语法兼容，两者 SQLite 都支持，保留即可)

    # ── 其他 ──
    # GROUP_CONCAT → SQLite 也是 GROUP_CONCAT，无需替换
    # IFNULL → SQLite 也是 IFNULL，无需替换
    # COALESCE → SQLite 也是 COALESCE，无需替换
]


def adapt_mysql_to_sqlite(sql: str) -> str:
    """将 MySQL 方言 SQL 转换为 SQLite 兼容语法。

    做一轮正则替换，适用于最常见的 MySQL→SQLite 差异。
    复杂场景（如多参数 CONCAT、嵌套 IF）可能需要 prompt 层补充。

    Args:
        sql: 原始 SQL（可能包含 MySQL 方言函数）

    Returns:
        转换后的 SQLite 兼容 SQL
    """
    original = sql
    for pattern, replacement, desc in _REPLACEMENT_RULES:
        new_sql = re.sub(pattern, replacement, sql, flags=re.IGNORECASE)
        if new_sql != sql:
            logger.debug("dialect_adapter: %s → replaced in SQL", desc)
            sql = new_sql

    if sql != original:
        logger.info("dialect_adapter: MySQL→SQLite adaptation applied")

    return sql


def detect_mysql_dialect(sql: str) -> list[str]:
    """检测 SQL 中包含的 MySQL 方言特征，返回特征描述列表。

    用于调试和 prompt 层提示，不执行替换。
    """
    found = []
    for pattern, _, desc in _REPLACEMENT_RULES:
        if re.search(pattern, sql, flags=re.IGNORECASE):
            found.append(desc)
    return found