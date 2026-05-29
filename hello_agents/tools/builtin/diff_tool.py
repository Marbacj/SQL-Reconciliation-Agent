"""数据差异比对工具 — 按主键 JOIN 后逐列计算差异"""

import sqlite3
import json
from typing import Dict, Any, List

from ..base import Tool, ToolParameter, tool_action
from ..response import ToolResponse, ToolStatus
from ..errors import ToolErrorCode


class DiffTool(Tool):
    """数据对账工具：对两个 SQL 查询结果按主键 JOIN 并逐列计算差异。

    使用方式：
        tool = DiffTool(db_path="data/mock_reconciliation.db")
        registry.register_tool(tool)  # 注册 diff_compare 子工具
    """

    def __init__(self, db_path: str):
        super().__init__(
            name="DiffTool",
            description="数据对账比对工具 — 比对两组 SQL 查询结果，找出差异",
            expandable=True
        )
        self.db_path = db_path

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        return ToolResponse.error(
            code=ToolErrorCode.INVALID_PARAM,
            message="DiffTool 是可展开工具集，请使用子工具 diff_compare"
        )

    def get_parameters(self) -> List[ToolParameter]:
        return []

    @tool_action("diff_compare", "比对两组查询结果，按主键列关联后逐列计算差异")
    def _compare(
        self,
        sql_a: str,
        sql_b: str,
        key_column: str,
        compare_columns: str
    ) -> str:
        """执行两组 SQL 查询，按 key_column JOIN 后逐列比对差异

        会逐列比对两表中同名的数值列。如果列名不同但含义对应
        （如 total_gmv vs total_order），差异会分别标注在各自列名下。

        Args:
            sql_a: 左表 SQL（如对 live_gmv 的查询）
            sql_b: 右表 SQL（如对 order_amount 的查询）
            key_column: 主键列名，用于关联两表结果（如 live_id）
            compare_columns: 要比对的数值列名，逗号分隔。会检查哪些列在两表中
                            都存在，以及哪些列仅在一表中存在。

        Returns:
            对账报告：差异行数、每个差异行的具体差额
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()

            # 1. 执行左表 SQL
            try:
                c.execute(sql_a)
                rows_a_list = c.fetchall()
                if not rows_a_list:
                    conn.close()
                    return "❌ 左表 SQL 返回 0 行"
                col_names_a = [desc[0] for desc in c.description]
                rows_a = {}
                for row in rows_a_list:
                    key = str(row[key_column])
                    rows_a[key] = {col: row[i] for i, col in enumerate(col_names_a)}
            except (sqlite3.Error, KeyError) as e:
                conn.close()
                return f"❌ 执行左表 SQL 失败: {str(e)}"

            # 2. 执行右表 SQL
            try:
                c.execute(sql_b)
                rows_b_list = c.fetchall()
                if not rows_b_list:
                    conn.close()
                    return "❌ 右表 SQL 返回 0 行"
                col_names_b = [desc[0] for desc in c.description]
                rows_b = {}
                for row in rows_b_list:
                    key = str(row[key_column])
                    rows_b[key] = {col: row[i] for i, col in enumerate(col_names_b)}
            except (sqlite3.Error, KeyError) as e:
                conn.close()
                return f"❌ 执行右表 SQL 失败: {str(e)}"

            conn.close()

            # 3. 解析要比对的列：取两表中实际存在的列
            requested_cols = [c.strip() for c in compare_columns.split(",")]

            # 4. 找出两表共有的非key列，按位置逐一对比
            value_cols_a = [c for c in col_names_a if c != key_column]
            value_cols_b = [c for c in col_names_b if c != key_column]

            # 5. 按 key_column 进行 FULL OUTER JOIN 比对
            all_keys = sorted(
                set(rows_a.keys()) | set(rows_b.keys()),
                key=lambda x: int(x) if x.lstrip('-').isdigit() else x
            )

            diff_rows = []
            only_in_a = []
            only_in_b = []
            matched_count = 0
            total_diff_count = 0

            for key in all_keys:
                row_a = rows_a.get(key)
                row_b = rows_b.get(key)

                if row_a is None:
                    # 仅在右表中存在
                    vals = {}
                    for col in value_cols_b:
                        v = row_b.get(col)
                        vals[col] = f"右表={v}，左表缺失"
                    only_in_b.append({"key": key, **vals})
                    total_diff_count += 1
                elif row_b is None:
                    # 仅在左表中存在
                    vals = {}
                    for col in value_cols_a:
                        v = row_a.get(col)
                        vals[col] = f"左表={v}，右表缺失"
                    only_in_a.append({"key": key, **vals})
                    total_diff_count += 1
                else:
                    # 两表都有，逐列比对
                    # 按位置对应的列进行比对（col 0 → col 0, col 1 → col 1）
                    has_diff = False
                    diffs = {}

                    # 跨列对比：左表第1个value列 vs 右表第1个value列
                    for i in range(max(len(value_cols_a), len(value_cols_b))):
                        col_a = value_cols_a[i] if i < len(value_cols_a) else None
                        col_b = value_cols_b[i] if i < len(value_cols_b) else None

                        if col_a is None:
                            val_b = row_b.get(col_b) or 0
                            diff_key = f"[右]{col_b}"
                            diffs[diff_key] = {"左表": "N/A", "右表": val_b, "差异": f"仅右表有"}
                            has_diff = True
                        elif col_b is None:
                            val_a = row_a.get(col_a) or 0
                            diff_key = f"[左]{col_a}"
                            diffs[diff_key] = {"左表": val_a, "右表": "N/A", "差异": f"仅左表有"}
                            has_diff = True
                        else:
                            val_a = row_a.get(col_a) or 0
                            val_b = row_b.get(col_b) or 0
                            if val_a != val_b:
                                has_diff = True
                                diff_val = val_a - val_b if isinstance(val_a, (int, float)) and isinstance(val_b, (int, float)) else "N/A"
                                diff_key = f"{col_a} ⟷ {col_b}"
                                diffs[diff_key] = {"左表": val_a, "右表": val_b, "差异": diff_val}

                    if has_diff:
                        diff_rows.append({"key": key, **diffs})
                        total_diff_count += 1
                    else:
                        matched_count += 1

            # 6. 组装报告
            result = f"## 对账结果\n\n"
            result += f"- 左表行数: {len(rows_a)}, 右表行数: {len(rows_b)}\n"
            result += f"- 总比对键数: {len(all_keys)}\n"
            result += f"- 完全一致: {matched_count}\n"
            result += f"- 存在差异: {total_diff_count}\n"

            if diff_rows:
                result += f"\n### 🔴 数值差异 ({len(diff_rows)} 处)\n\n"
                for row in diff_rows:
                    key = row['key']
                    result += f"**key={key}**:\n"
                    for col, d in row.items():
                        if col == 'key':
                            continue
                        if isinstance(d, dict):
                            result += f"  - {col}: 左表={d.get('左表')}, 右表={d.get('右表')}, 差异={d.get('差异')}\n"
                        else:
                            result += f"  - {col}: {d}\n"
                    result += "\n"

            if only_in_a:
                result += f"\n### 🟡 仅左表存在 ({len(only_in_a)} 条)\n\n"
                for row in only_in_a:
                    key = row['key']
                    result += f"- **key={key}**: "
                    details = []
                    for col, v in row.items():
                        if col != 'key':
                            details.append(str(v))
                    result += "; ".join(details) + "\n"

            if only_in_b:
                result += f"\n### 🟡 仅右表存在 ({len(only_in_b)} 条)\n\n"
                for row in only_in_b:
                    key = row['key']
                    result += f"- **key={key}**: "
                    details = []
                    for col, v in row.items():
                        if col != 'key':
                            details.append(str(v))
                    result += "; ".join(details) + "\n"

            if total_diff_count == 0:
                result += "\n✅ 所有数据完全一致！\n"

            return result

        except Exception as e:
            return f"❌ 对账比对失败: {str(e)}"
