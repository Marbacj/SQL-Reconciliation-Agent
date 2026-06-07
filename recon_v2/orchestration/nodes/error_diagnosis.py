"""错误诊断模块 — 把底层系统错误翻译成用户可理解的业务语言。

职责：
1. _classify_error()    → 识别错误类型
2. _diagnose_error()    → 翻译为业务语言 + 给出行动建议
3. _diagnose_empty()    → 空结果时的原因分析
4. build_retry_exhausted_message() → 重试耗尽后的引导信息
5. build_rejected_message()        → 安全拒绝的解释信息

所有函数均为纯函数，无外部 I/O，方便单测。
"""

from __future__ import annotations

import re
from typing import List, Optional


# ── 错误类型枚举 ─────────────────────────────────────────────────────

class ErrorType:
    TABLE_NOT_FOUND   = "table_not_found"
    COLUMN_NOT_FOUND  = "column_not_found"
    SYNTAX_ERROR      = "syntax_error"
    TYPE_ERROR        = "type_error"
    DB_LOCKED         = "db_locked"
    PERMISSION_DENIED = "permission_denied"
    DATE_ERROR        = "date_error"
    AMBIGUOUS_COLUMN  = "ambiguous_column"
    UNKNOWN           = "unknown"


# ── 错误分类 ─────────────────────────────────────────────────────────

_TABLE_NOT_FOUND_PATTERNS = [
    re.compile(r"no such table[:\s]+(\S+)", re.IGNORECASE),
    re.compile(r"table[:\s]+['\"]?(\S+?)['\"]? does not exist", re.IGNORECASE),
    re.compile(r"relation[:\s]+['\"]?(\S+?)['\"]? does not exist", re.IGNORECASE),
]

_COLUMN_NOT_FOUND_PATTERNS = [
    re.compile(r"no such column[:\s]+(\S+)", re.IGNORECASE),
    re.compile(r"table (\S+) has no column named (\S+)", re.IGNORECASE),
    re.compile(r"unknown column[:\s]+['\"]?(\S+?)['\"]?", re.IGNORECASE),
]

_SYNTAX_ERROR_PATTERNS = [
    re.compile(r"syntax error", re.IGNORECASE),
    re.compile(r"near ['\"](.+?)['\"].*syntax error", re.IGNORECASE),
    re.compile(r"incomplete input", re.IGNORECASE),
    re.compile(r"unrecognized token", re.IGNORECASE),
]

_TYPE_ERROR_PATTERNS = [
    re.compile(r"datatype mismatch", re.IGNORECASE),
    re.compile(r"could not convert string to", re.IGNORECASE),
    re.compile(r"invalid literal for .* with base", re.IGNORECASE),
]

_DATE_ERROR_PATTERNS = [
    re.compile(r"date.*invalid", re.IGNORECASE),
    re.compile(r"invalid date", re.IGNORECASE),
    re.compile(r"time data .* does not match format", re.IGNORECASE),
]

_LOCKED_PATTERNS = [
    re.compile(r"database is locked", re.IGNORECASE),
    re.compile(r"table is locked", re.IGNORECASE),
    re.compile(r"locked", re.IGNORECASE),
]

_AMBIGUOUS_PATTERNS = [
    re.compile(r"ambiguous column name[:\s]+(\S+)", re.IGNORECASE),
]


def classify_error(error_msg: str) -> tuple[str, dict]:
    """识别错误类型，返回 (ErrorType, extracted_info)。

    extracted_info 根据错误类型不同包含不同字段：
    - table_not_found: {"table": "xxx"}
    - column_not_found: {"column": "xxx", "table": "xxx"}
    - syntax_error: {"near": "xxx"}
    - ambiguous_column: {"column": "xxx"}
    """
    if not error_msg:
        return ErrorType.UNKNOWN, {}

    for pat in _TABLE_NOT_FOUND_PATTERNS:
        m = pat.search(error_msg)
        if m:
            return ErrorType.TABLE_NOT_FOUND, {"table": m.group(1).strip("'\";,")}

    for pat in _COLUMN_NOT_FOUND_PATTERNS:
        m = pat.search(error_msg)
        if m:
            groups = m.groups()
            if len(groups) >= 2:
                return ErrorType.COLUMN_NOT_FOUND, {"table": groups[0], "column": groups[1]}
            return ErrorType.COLUMN_NOT_FOUND, {"column": groups[0].strip("'\";,")}

    for pat in _AMBIGUOUS_PATTERNS:
        m = pat.search(error_msg)
        if m:
            return ErrorType.AMBIGUOUS_COLUMN, {"column": m.group(1).strip("'\";,")}

    for pat in _DATE_ERROR_PATTERNS:
        if pat.search(error_msg):
            return ErrorType.DATE_ERROR, {}

    for pat in _SYNTAX_ERROR_PATTERNS:
        m = pat.search(error_msg)
        if m:
            near = m.group(1) if m.lastindex and m.lastindex >= 1 else ""
            return ErrorType.SYNTAX_ERROR, {"near": near}

    for pat in _TYPE_ERROR_PATTERNS:
        if pat.search(error_msg):
            return ErrorType.TYPE_ERROR, {}

    for pat in _LOCKED_PATTERNS:
        if pat.search(error_msg):
            return ErrorType.DB_LOCKED, {}

    return ErrorType.UNKNOWN, {}


# ── 相似表名推断（Levenshtein 简化版）────────────────────────────────

def _similar_names(target: str, candidates: List[str], max_dist: int = 3) -> List[str]:
    """返回与 target 编辑距离 <= max_dist 的候选名，按距离排序。"""
    def edit_dist(a: str, b: str) -> int:
        a, b = a.lower(), b.lower()
        if a == b:
            return 0
        if len(a) > len(b):
            a, b = b, a
        row = list(range(len(a) + 1))
        for c in b:
            new_row = [row[0] + 1]
            for j, d in enumerate(a):
                new_row.append(min(row[j + 1] + 1, new_row[-1] + 1, row[j] + (c != d)))
            row = new_row
        return row[-1]

    results = []
    for name in candidates:
        dist = edit_dist(target, name)
        if dist <= max_dist:
            results.append((dist, name))
    results.sort()
    return [name for _, name in results[:3]]


def _extract_known_tables(sql: str) -> List[str]:
    """从 SQL 里提取出现的表名（FROM/JOIN 子句后的标识符）。"""
    pattern = re.compile(
        r'(?:FROM|JOIN)\s+[`"]?([A-Za-z_][A-Za-z0-9_]*)[`"]?',
        re.IGNORECASE,
    )
    return list(dict.fromkeys(pattern.findall(sql)))


# ── 核心：把错误翻译成用户友好语言 ──────────────────────────────────

def diagnose_error(
    error_msg: str,
    sql: str,
    query: str,
    known_tables: Optional[List[str]] = None,
    retry_count: int = 0,
    max_retries: int = 3,
) -> str:
    """把底层错误翻译成业务语言 + 行动建议。

    Args:
        error_msg:    原始错误信息
        sql:          执行失败的 SQL
        query:        用户原始查询
        known_tables: 数据库中实际存在的表名列表（用于相似匹配）
        retry_count:  当前已重试次数
        max_retries:  最大重试次数

    Returns:
        用户友好的错误信息字符串（Markdown 格式）
    """
    error_type, info = classify_error(error_msg)

    # ── 失败 SQL 展示块（总是附上，方便高级用户）
    sql_block = ""
    if sql and sql.strip():
        sql_block = f"\n\n<details>\n<summary>🔍 查看尝试过的 SQL（点击展开）</summary>\n\n```sql\n{sql.strip()}\n```\n\n</details>"

    # 重试次数提示
    retry_hint = ""
    if retry_count > 0:
        retry_hint = f"（已自动重试 {retry_count} 次）"

    # ── 按错误类型生成引导信息
    if error_type == ErrorType.TABLE_NOT_FOUND:
        table_name = info.get("table", "未知表")
        suggestions = []
        if known_tables:
            similar = _similar_names(table_name, known_tables)
            if similar:
                suggestions.append(f"你是否想查这些表：**{' / '.join(similar)}**？")
        if not suggestions:
            suggestions.append("可以先问：「列出所有可用的表名」来确认表名")

        return (
            f"❌ 找不到表 `{table_name}` {retry_hint}\n\n"
            f"**可能的原因：**\n"
            f"- 表名拼写有误，数据库中不存在 `{table_name}`\n"
            f"- 表名大小写不匹配（部分数据库区分大小写）\n\n"
            f"**建议：**\n"
            + "\n".join(f"- {s}" for s in suggestions)
            + sql_block
        )

    if error_type == ErrorType.COLUMN_NOT_FOUND:
        col_name = info.get("column", "未知字段")
        table_name = info.get("table", "")
        table_hint = f"（表 `{table_name}`）" if table_name else ""

        return (
            f"❌ 找不到字段 `{col_name}` {table_hint} {retry_hint}\n\n"
            f"**可能的原因：**\n"
            f"- 字段名拼写有误\n"
            f"- 该字段在其他表中，不在你查询的表里\n\n"
            f"**建议：**\n"
            f"- 换个说法，如把「{col_name}」改为该字段的中文业务含义\n"
            f"- 先问：「{table_name or '这张表'} 有哪些字段？」\n"
            + sql_block
        )

    if error_type == ErrorType.AMBIGUOUS_COLUMN:
        col_name = info.get("column", "未知字段")
        return (
            f"❌ 字段 `{col_name}` 在多张表中都存在，产生了歧义 {retry_hint}\n\n"
            f"**建议：**\n"
            f"- 明确指定表名，如「订单表里的 {col_name}」或「支付表里的 {col_name}」\n"
            + sql_block
        )

    if error_type == ErrorType.DATE_ERROR:
        return (
            f"❌ 日期/时间格式错误 {retry_hint}\n\n"
            f"**可能的原因：**\n"
            f"- 时间条件的格式不符合数据库要求\n\n"
            f"**建议：**\n"
            f"- 用更自然的表达，如「昨天」「上个月」「最近 7 天」，系统会自动转换格式\n"
            f"- 避免直接写日期字符串，如「2024/01/15」（应写「2024-01-15」）\n"
            + sql_block
        )

    if error_type == ErrorType.SYNTAX_ERROR:
        near = info.get("near", "")
        near_hint = f"（错误位置附近：`{near}`）" if near else ""
        return (
            f"❌ SQL 语法错误 {near_hint} {retry_hint}\n\n"
            f"**建议：**\n"
            f"- 换一种更简单的说法，减少复杂条件\n"
            f"- 把复合问题拆开：先查一张表，再查另一张\n"
            + sql_block
        )

    if error_type == ErrorType.TYPE_ERROR:
        return (
            f"❌ 数据类型不匹配 {retry_hint}\n\n"
            f"**可能的原因：**\n"
            f"- 用数字条件过滤了文本字段，或反之\n\n"
            f"**建议：**\n"
            f"- 检查过滤条件的值是否正确，如 status 的值是 `'paid'` 还是 `1`\n"
            + sql_block
        )

    if error_type == ErrorType.DB_LOCKED:
        return (
            f"❌ 数据库暂时繁忙 {retry_hint}\n\n"
            f"**建议：**\n"
            f"- 稍等几秒后重新查询\n"
            f"- 如果持续出现，请检查是否有其他程序正在占用数据库\n"
        )

    # 兜底：原始错误 + 通用建议
    return (
        f"❌ 查询执行失败 {retry_hint}\n\n"
        f"**错误详情：** `{error_msg[:200]}`\n\n"
        f"**建议：**\n"
        f"- 换一种更简单的说法重新提问\n"
        f"- 把复杂问题拆成多个小问题分步查询\n"
        f"- 先确认表名：「有哪些可用的表？」\n"
        + sql_block
    )


# ── 空结果诊断 ───────────────────────────────────────────────────────

def diagnose_empty_result(
    sql: str,
    query: str,
    row_count_without_filter: Optional[int] = None,
) -> str:
    """空结果时分析可能原因并给出建议。

    Args:
        sql:                      执行的 SQL
        query:                    用户原始查询
        row_count_without_filter: 去掉 WHERE 后查到的行数（None 表示未执行诊断查询）
    """
    sql_upper = sql.upper() if sql else ""

    suggestions = []

    # 分析 WHERE 子句
    has_date_filter = bool(re.search(r"DATE\s*\(|created_at|order_time|pay_time", sql, re.IGNORECASE))
    has_status_filter = bool(re.search(r"status\s*=|state\s*=", sql, re.IGNORECASE))
    has_amount_filter = bool(re.search(r"amount\s*[><=]|price\s*[><=]", sql, re.IGNORECASE))

    if has_date_filter:
        suggestions.append("时间范围内可能没有数据，试试扩大时间范围（如「最近一个月」）")
    if has_status_filter:
        # 提取 status 值
        m = re.search(r"status\s*=\s*['\"]?(\w+)['\"]?", sql, re.IGNORECASE)
        status_val = m.group(1) if m else "该状态"
        suggestions.append(f"状态过滤条件 `{status_val}` 可能不存在，试试去掉状态条件先查全量")
    if has_amount_filter:
        suggestions.append("金额过滤范围可能过于严格，试试去掉金额条件")

    if row_count_without_filter is not None:
        if row_count_without_filter > 0:
            suggestions.insert(
                0,
                f"去掉过滤条件后，表中有 **{row_count_without_filter:,}** 条记录，说明是过滤条件导致无结果"
            )
        else:
            suggestions.insert(0, "表本身就没有数据，请确认数据库是否已导入数据")

    if not suggestions:
        suggestions.append("当前查询条件下没有匹配的数据，可以尝试放宽查询条件")
        suggestions.append("先确认表里有数据：「xxx 表里有多少条记录？」")

    result = "📭 查询返回空结果\n\n**可能的原因：**\n"
    result += "\n".join(f"- {s}" for s in suggestions)
    return result


# ── 重试耗尽引导 ─────────────────────────────────────────────────────

def build_retry_exhausted_message(
    query: str,
    attempted_sqls: List[str],
    last_error: str,
    known_tables: Optional[List[str]] = None,
) -> str:
    """生成重试耗尽后的引导信息。

    Args:
        query:          用户原始查询
        attempted_sqls: 所有尝试过的 SQL 列表
        last_error:     最后一次错误信息
        known_tables:   数据库中存在的表名列表
    """
    error_type, info = classify_error(last_error)

    # 构建尝试过的 SQL 列表
    sql_history = ""
    if attempted_sqls:
        sqls_text = "\n\n".join(
            f"第 {i+1} 次尝试:\n```sql\n{s.strip()}\n```"
            for i, s in enumerate(attempted_sqls[-3:])  # 最多展示最近 3 次
        )
        sql_history = (
            f"\n\n<details>\n"
            f"<summary>📋 查看全部尝试记录（{len(attempted_sqls)} 次）</summary>\n\n"
            f"{sqls_text}\n\n"
            f"</details>"
        )

    # 根据错误类型给出针对性建议
    specific_suggestions = []
    if error_type == ErrorType.TABLE_NOT_FOUND:
        table = info.get("table", "")
        if known_tables:
            similar = _similar_names(table, known_tables)
            if similar:
                specific_suggestions.append(f"指定正确的表名，如：「查 **{similar[0]}** 表的数据」")
        specific_suggestions.append("先询问「有哪些表？」确认表名后再提问")
    elif error_type == ErrorType.COLUMN_NOT_FOUND:
        specific_suggestions.append("描述你想要的业务含义而不是字段名，如「订单金额」而非「total_amount」")
        specific_suggestions.append("先询问「这张表有哪些字段？」确认字段名")
    elif error_type == ErrorType.SYNTAX_ERROR:
        specific_suggestions.append("简化问题，拆分成多个小问题分步查询")
        specific_suggestions.append("避免使用复杂的嵌套条件")
    else:
        specific_suggestions.append("换一种说法重新描述需求")
        specific_suggestions.append("把问题拆成更小的步骤，如先查单张表")

    suggestions_text = "\n".join(f"- {s}" for s in specific_suggestions)

    return (
        f"❌ 自动 SQL 生成失败，已尝试 {len(attempted_sqls)} 次仍未解决\n\n"
        f"**最后的错误原因：** `{last_error[:150]}`\n\n"
        f"**你可以尝试：**\n"
        f"{suggestions_text}\n"
        f"- 联系管理员确认数据库结构\n"
        + sql_history
    )


# ── 安全拒绝解释 ─────────────────────────────────────────────────────

_DDL_KEYWORDS = re.compile(
    r"\b(DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE|CREATE|REPLACE)\b",
    re.IGNORECASE,
)

_INJECTION_PATTERNS = re.compile(
    r"(;--|/\*|'\s*or\s*'1'\s*=\s*'1|union\s+select)",
    re.IGNORECASE,
)


def build_rejected_message(query: str) -> str:
    """生成安全拒绝时的友好解释。

    Args:
        query: 用户原始查询
    """
    # 检测是 DDL/DML 还是注入
    ddl_match = _DDL_KEYWORDS.search(query)
    injection_match = _INJECTION_PATTERNS.search(query)

    if injection_match:
        return (
            "🛡️ 该请求包含不安全的 SQL 模式，已被系统拦截\n\n"
            "如果你是在正常查询数据，请用自然语言描述需求，系统会自动生成安全的 SQL。\n\n"
            "**示例：**\n"
            "- ✅ 「查一下昨天的订单总金额」\n"
            "- ✅ 「对比订单表和支付表的差异」\n"
            "- ❌ 不要直接输入 SQL 语句"
        )

    if ddl_match:
        keyword = ddl_match.group(1).upper()
        keyword_explain = {
            "DROP": "删除表",
            "DELETE": "删除数据",
            "UPDATE": "修改数据",
            "INSERT": "插入数据",
            "ALTER": "修改表结构",
            "TRUNCATE": "清空表",
            "CREATE": "创建表",
            "REPLACE": "替换数据",
        }.get(keyword, "修改数据库")

        return (
            f"🛡️ 检测到数据修改操作（{keyword} = {keyword_explain}），系统仅支持只读查询\n\n"
            f"**原因：** 为保护数据安全，本系统只允许 SELECT 查询，不支持任何写入操作。\n\n"
            f"**如果你想：**\n"
            f"- **查看数据** → 改用查询语言，如「查看 xxx 表的最新 10 条数据」\n"
            f"- **修改/删除数据** → 请通过数据库管理工具（如 DBeaver、MySQL Workbench）直接操作\n"
            f"- **了解表结构** → 「xxx 表有哪些字段？」"
        )

    # 通用拒绝
    return (
        "🛡️ 该请求不在支持范围内\n\n"
        "**系统支持：** 数据查询、对账分析、差异比对\n\n"
        "**不支持：** 数据修改、删除、系统操作等\n\n"
        "请用自然语言描述你的数据查询需求。"
    )
