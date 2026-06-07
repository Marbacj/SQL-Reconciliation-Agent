"""SQL 工具集 — 表结构查询 + SQL 执行 + 语法校验

基于 HelloAgents Tool 基类，使用 @tool_action 装饰器自动展开为 3 个子工具。
"""

import sqlite3
from typing import Dict, Any, List

from ..base import Tool, ToolParameter, tool_action
from ..response import ToolResponse, ToolStatus
from ..errors import ToolErrorCode


class SQLTool(Tool):
    """SQL 工具集：提供表结构查询、SQL 执行、语法校验三个子工具。

    使用方式：
        tool = SQLTool(db_path="data/mock_reconciliation.db")
        registry.register_tool(tool)  # 自动展开为 sql_schema, sql_execute, sql_validate
    """

    def __init__(self, db_path: str):
        super().__init__(
            name="SQLTool",
            description="SQL 工具集 — 查询表结构、执行 SQL、校验语法",
            expandable=True
        )
        self.db_path = db_path

    # ============ 父工具接口（不直接调用，由子工具代理） ============

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        """父工具不直接执行，请使用展开的子工具。"""
        return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM,
            message="SQLTool 是一个可展开工具集，请使用子工具：sql_schema / sql_execute / sql_validate"
        )

    def get_parameters(self) -> List[ToolParameter]:
        return []  # 父工具无参数

    # ============ 子工具：sql_schema ============

    @tool_action("sql_schema", "查询数据表的结构信息（字段名、类型、示例数据）")
    def _get_schema(self, table_name: str) -> str:
        """查询指定数据表的结构信息

        Args:
            table_name: 要查询的表名（如 live_gmv, order_amount）

        Returns:
            包含 CREATE TABLE 语句 + 前 3 行示例数据的格式化文本
        """
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()

            # 1. 获取 CREATE TABLE 语句
            c.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,)
            )
            row = c.fetchone()
            if not row:
                conn.close()
                return f"❌ 表 '{table_name}' 不存在"

            create_sql = row[0]

            # 2. 获取列信息
            c.execute(f"PRAGMA table_info('{table_name}')")
            columns = c.fetchall()

            # 3. 获取前 3 行示例数据
            try:
                c.execute(f"SELECT * FROM '{table_name}' LIMIT 3")
                sample_rows = c.fetchall()
            except sqlite3.Error:
                sample_rows = []

            # 4. 获取总行数
            c.execute(f"SELECT COUNT(*) FROM '{table_name}'")
            total_rows = c.fetchone()[0]

            conn.close()

            # 构建输出
            result = f"## 表: {table_name}\n\n"
            result += f"**总行数**: {total_rows}\n\n"
            result += "### 字段列表\n\n"
            result += "| 字段名 | 类型 | 可否为空 | 默认值 |\n"
            result += "|--------|------|----------|--------|\n"
            for col in columns:
                # col: (cid, name, type, notnull, dflt_value, pk)
                nullable = "YES" if not col[3] else "NO"
                default = str(col[4]) if col[4] is not None else "-"
                result += f"| {col[1]} | {col[2]} | {nullable} | {default} |\n"

            if sample_rows:
                result += "\n### 示例数据（前 3 行）\n\n"
                col_names = [col[1] for col in columns]
                result += "| " + " | ".join(col_names) + " |\n"
                result += "|" + "|".join(["---"] * len(col_names)) + "|\n"
                for row_data in sample_rows:
                    result += "| " + " | ".join(str(v) for v in row_data) + " |\n"

            result += f"\n### DDL\n```sql\n{create_sql}\n```\n"

            return result

        except sqlite3.Error as e:
            return f"❌ 数据库错误: {str(e)}"

    # ============ 子工具：sql_execute ============

    @tool_action("sql_execute", "执行 SQL 查询并返回结果")
    def _execute(self, sql: str) -> str:
        """执行 SQL 查询，返回前 50 行结果的 Markdown 表格

        Args:
            sql: 要执行的 SQL SELECT 语句（仅允许 SELECT/PRAGMA）

        Returns:
            查询结果的 Markdown 表格，包含行数统计
        """
        # 安全检查：只允许读操作
        sql_upper = sql.strip().upper()
        dangerous_keywords = ['DROP', 'DELETE', 'UPDATE', 'INSERT', 'ALTER', 'CREATE']
        for keyword in dangerous_keywords:
            if sql_upper.startswith(keyword) or f' {keyword} ' in f' {sql_upper} ':
                return f"❌ 安全限制：不允许执行 {keyword} 操作。仅支持 SELECT 查询。"

        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute(sql)

            # 尝试获取结果
            try:
                rows = c.fetchmany(50)  # 最多 50 行
                col_names = [desc[0] for desc in c.description] if c.description else []
            except sqlite3.Error:
                conn.close()
                return "✅ SQL 执行成功（无返回行，可能非 SELECT 语句）"

            # 获取总行数
            total = len(rows)
            if total == 50:
                # 可能还有更多行
                try:
                    remaining = c.fetchall()
                    total += len(remaining)
                    has_more = len(remaining) > 0
                except sqlite3.Error:
                    has_more = False
            else:
                has_more = False

            conn.close()

            if not col_names:
                return "✅ 查询执行成功，但无数据列返回。"

            # 构建 Markdown 表格
            result = f"**查询结果**: {total} 行, {len(col_names)} 列\n\n"
            result += "| " + " | ".join(col_names) + " |\n"
            result += "|" + "|".join(["---"] * len(col_names)) + "|\n"

            for row in rows:
                result += "| " + " | ".join(str(v) if v is not None else "NULL" for v in row) + " |\n"

            if has_more:
                result += f"\n⚠️ 结果超过 50 行，仅显示前 50 行。总行数: {total}\n"

            return result

        except sqlite3.Error as e:
            return f"❌ SQL 执行错误: {str(e)}"

    # ============ 子工具：sql_validate ============

    @tool_action("sql_validate", "校验 SQL 语法是否正确，返回错误信息或确认")
    def _validate(self, sql: str) -> str:
        """使用 SQLite EXPLAIN 做语法校验，不实际执行查询

        Args:
            sql: 要校验的 SQL 语句

        Returns:
            校验结果：成功时返回确认信息，失败时返回具体错误
        """
        sql_upper = sql.strip().upper()
        dangerous_keywords = ['DROP', 'DELETE', 'UPDATE', 'INSERT', 'ALTER', 'CREATE']
        for keyword in dangerous_keywords:
            if sql_upper.startswith(keyword) or f' {keyword} ' in f' {sql_upper} ':
                return f"❌ 安全限制：不允许执行 {keyword} 操作"

        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            # EXPLAIN 会解析并生成执行计划，但不会实际执行
            c.execute(f"EXPLAIN {sql}")
            plan_rows = c.fetchall()
            conn.close()
            return f"✅ SQL 语法校验通过。执行计划包含 {len(plan_rows)} 个操作码。"
        except sqlite3.Error as e:
            return f"❌ SQL 语法错误: {str(e)}\n\n请检查并修正 SQL 语句后重试。"

    # ============ 子工具：sql_schema_search ============

    @tool_action("sql_schema_search", "按关键词搜索相关的表和字段（支持逗号分隔多个关键词）")
    def _search_schema(self, keyword: str) -> str:
        """在数据库所有表名和字段名中模糊搜索，返回相关表的摘要

        Args:
            keyword: 搜索关键词，支持逗号分隔多个词（如 "订单,日志" 或 "order"）

        Returns:
            命中表的字段列表摘要；命中分最高的表额外附带前 3 行示例数据
        """
        keywords = [k.strip().lower() for k in keyword.split(",") if k.strip()]
        if not keywords:
            return "❌ 请提供搜索关键词"

        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()

            # 获取所有表名
            c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            all_tables = [row[0] for row in c.fetchall()]

            if not all_tables:
                conn.close()
                return "❌ 数据库中没有任何表"

            scores: List[tuple] = []  # (score, table_name, columns)

            for table in all_tables:
                score = 0
                # 表名匹配（权重更高）
                for kw in keywords:
                    if kw in table.lower():
                        score += 3

                # 字段名匹配
                c.execute(f"PRAGMA table_info('{table}')")
                columns = c.fetchall()  # (cid, name, type, notnull, dflt_value, pk)
                for col in columns:
                    col_name = col[1].lower()
                    for kw in keywords:
                        if kw in col_name:
                            score += 1

                if score > 0:
                    scores.append((score, table, columns))

            if not scores:
                conn.close()
                return (
                    f"❌ 未找到与关键词 '{keyword}' 相关的表。\n\n"
                    f"数据库中共有 {len(all_tables)} 张表：{', '.join(all_tables)}"
                )

            # 按分数降序排列
            scores.sort(key=lambda x: x[0], reverse=True)

            result = f"## Schema 搜索结果（关键词: {keyword}）\n\n"
            result += f"共命中 **{len(scores)}** 张表：\n\n"

            for rank, (score, table, columns) in enumerate(scores):
                result += f"### {table}（匹配度: {score}）\n\n"
                result += "| 字段名 | 类型 | 主键 |\n"
                result += "|--------|------|------|\n"
                for col in columns:
                    pk_mark = "✓" if col[5] else ""
                    result += f"| {col[1]} | {col[2]} | {pk_mark} |\n"

                # 命中分最高的表（rank=0）额外附带示例数据
                if rank == 0:
                    try:
                        c.execute(f"SELECT * FROM '{table}' LIMIT 3")
                        sample_rows = c.fetchall()
                        if sample_rows:
                            col_names = [col[1] for col in columns]
                            result += "\n**示例数据（前 3 行）**\n\n"
                            result += "| " + " | ".join(col_names) + " |\n"
                            result += "|" + "|".join(["---"] * len(col_names)) + "|\n"
                            for row_data in sample_rows:
                                result += "| " + " | ".join(str(v) for v in row_data) + " |\n"
                    except sqlite3.Error:
                        pass

                result += "\n"

            conn.close()
            return result

        except sqlite3.Error as e:
            return f"❌ 数据库错误: {str(e)}"
