"""SQL 安全护栏：sqlglot AST 解析 + verb 白名单 + 危险节点扫描。

设计原则（v1 失败的教训）：
- 拒绝关键字黑名单：正则容易被注释 / Unicode / 大小写 / 字符串字面量绕过
- 改用 AST：把 SQL 真的解析成树，禁止 Delete/Update/Insert/Drop/Alter/Create/TruncateTable 节点
- verb 白名单：根节点仅允许 SELECT / WITH
- 多 statement：sqlglot.parse 解析所有 statement，任一不合规就拒
- 解析失败：直接拒绝（宁可误杀也不放过）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

import sqlglot
from sqlglot import expressions as exp

# 允许的根 verb
ALLOWED_ROOT = (exp.Select, exp.With)

# 危险节点（任何子树出现就拒绝）
DANGEROUS_NODES = (
    exp.Delete,
    exp.Update,
    exp.Insert,
    exp.Drop,
    exp.Alter,
    exp.Create,
    exp.TruncateTable,
    exp.Command,  # 比如 SHUTDOWN / VACUUM 之类
)


@dataclass
class SafetyVerdict:
    is_safe: bool
    reason: str

    def __bool__(self) -> bool:
        return self.is_safe


def is_safe(sql: str, dialect: str = "sqlite") -> SafetyVerdict:
    """单次安全检查入口。"""
    sql = (sql or "").strip()
    if not sql:
        return SafetyVerdict(False, "empty sql")

    # parse_all -> 所有 statement
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except Exception as e:
        return SafetyVerdict(False, f"parse error: {e}")

    if not statements:
        return SafetyVerdict(False, "parse error: no statements")

    for i, stmt in enumerate(statements):
        if stmt is None:
            return SafetyVerdict(False, f"statement #{i} parsed as None")

        # 根节点必须是允许的 verb
        if not isinstance(stmt, ALLOWED_ROOT):
            return SafetyVerdict(False, f"verb {type(stmt).__name__.upper()} not allowed")

        # 子树扫描
        for node in stmt.walk():
            if isinstance(node, DANGEROUS_NODES):
                return SafetyVerdict(
                    False,
                    f"dangerous node {type(node).__name__} found in statement #{i}",
                )

    return SafetyVerdict(True, "ok")


def assert_safe(sql: str, dialect: str = "sqlite") -> None:
    """便捷断言风格：不安全则抛 ValueError。"""
    verdict = is_safe(sql, dialect)
    if not verdict.is_safe:
        raise ValueError(f"Unsafe SQL: {verdict.reason}")


def apply_limit_guard(sql: str, default_limit: int = 1000) -> Tuple[str, bool]:
    """如果是 SELECT 且没有 LIMIT，自动附加 LIMIT 防全表。

    返回 (new_sql, modified)。
    """
    s = sql.strip().rstrip(";")
    if not s.lower().lstrip().startswith("select"):
        return sql, False
    if "limit" in s.lower():
        return sql, False
    return f"{s} LIMIT {default_limit}", True
