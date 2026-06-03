"""Tests for DiffTool — diff_compare with known discrepancies.

Per the mock data generator:
- live_id=105: GMV=12500, Order=11800 → diff=+700 (GMV虚高)
- live_id=208: only in order_amount table, GMV table missing → data gap
- live_id=312: GMV=8900, Order=9200 → diff=-300 (order虚高)
"""

import os
import pytest

from recon_core.tools.builtin.diff_tool import DiffTool
from recon_core.tools.registry import ToolRegistry
from recon_core.tools.response import ToolResponse, ToolStatus


DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "mock_reconciliation.db")

# Two SQL queries that produce comparable results keyed by live_id
SQL_A = "SELECT live_id, gmv FROM live_gmv"
SQL_B = "SELECT live_id, total_amount FROM order_amount"


class TestDiffToolDirect:
    """Test DiffTool._compare directly (bypassing registry)."""

    @classmethod
    def setup_class(cls):
        cls.tool = DiffTool(db_path=DB_PATH)

    # --- basic functionality ---

    def test_compare_returns_result(self):
        result = self.tool._compare(
            sql_a=SQL_A,
            sql_b=SQL_B,
            key_column="live_id",
            compare_columns="gmv,total_amount",
        )
        assert isinstance(result, str)
        assert "对账结果" in result
        assert "左表行数" in result
        assert "右表行数" in result

    def test_left_table_row_count(self):
        result = self.tool._compare(
            sql_a=SQL_A, sql_b=SQL_B,
            key_column="live_id",
            compare_columns="gmv,total_amount",
        )
        # live_gmv has 26 rows
        assert "左表行数: 26" in result

    def test_right_table_row_count(self):
        result = self.tool._compare(
            sql_a=SQL_A, sql_b=SQL_B,
            key_column="live_id",
            compare_columns="gmv,total_amount",
        )
        # order_amount has 27 rows
        assert "右表行数: 27" in result

    # --- known discrepancy: live_id=105 (diff=+700) ---

    def test_live_105_diff_700(self):
        result = self.tool._compare(
            sql_a=SQL_A, sql_b=SQL_B,
            key_column="live_id",
            compare_columns="gmv,total_amount",
        )
        assert "key=105" in result
        # diff should be 12500 - 11800 = 700
        assert "差异=700" in result

    # --- known discrepancy: live_id=312 (diff=-300) ---

    def test_live_312_diff_negative_300(self):
        result = self.tool._compare(
            sql_a=SQL_A, sql_b=SQL_B,
            key_column="live_id",
            compare_columns="gmv,total_amount",
        )
        assert "key=312" in result
        # diff should be 8900 - 9200 = -300
        assert "差异=-300" in result

    # --- known discrepancy: live_id=208 (only in right table) ---

    def test_live_208_only_in_right(self):
        result = self.tool._compare(
            sql_a=SQL_A, sql_b=SQL_B,
            key_column="live_id",
            compare_columns="gmv,total_amount",
        )
        assert "key=208" in result
        assert "仅右表存在" in result or "右表缺失" in result

    # --- diff counts ---

    def test_total_diff_count(self):
        result = self.tool._compare(
            sql_a=SQL_A, sql_b=SQL_B,
            key_column="live_id",
            compare_columns="gmv,total_amount",
        )
        # We expect at least 3 differences (105, 208, 312) plus possibly minor
        # float rounding differences from the random multiplier
        assert "存在差异:" in result

    def test_numbers_are_consistent(self):
        """Run twice and verify diff for live_id=105 is consistently 700."""
        r1 = self.tool._compare(
            sql_a=SQL_A, sql_b=SQL_B,
            key_column="live_id",
            compare_columns="gmv,total_amount",
        )
        r2 = self.tool._compare(
            sql_a=SQL_A, sql_b=SQL_B,
            key_column="live_id",
            compare_columns="gmv,total_amount",
        )
        assert "差异=700" in r1
        assert "差异=700" in r2

    # --- error cases ---

    def test_empty_left_sql(self):
        result = self.tool._compare(
            sql_a="SELECT * FROM live_gmv WHERE 1=0",
            sql_b=SQL_B,
            key_column="live_id",
            compare_columns="gmv,total_amount",
        )
        assert result.startswith("❌")
        assert "左表 SQL 返回 0 行" in result

    def test_empty_right_sql(self):
        result = self.tool._compare(
            sql_a=SQL_A,
            sql_b="SELECT * FROM order_amount WHERE 1=0",
            key_column="live_id",
            compare_columns="gmv,total_amount",
        )
        assert result.startswith("❌")
        assert "右表 SQL 返回 0 行" in result

    def test_bad_key_column(self):
        result = self.tool._compare(
            sql_a=SQL_A, sql_b=SQL_B,
            key_column="no_such_column",
            compare_columns="gmv,total_amount",
        )
        assert result.startswith("❌")
        assert "失败" in result


# ---------- ToolRegistry integration ----------


class TestDiffToolViaRegistry:
    """Test diff_compare sub-tool registered and executable via ToolRegistry."""

    @classmethod
    def setup_class(cls):
        cls.tool = DiffTool(db_path=DB_PATH)
        cls.registry = ToolRegistry()
        cls.registry.register_tool(cls.tool)

    def test_sub_tool_registered(self):
        names = self.registry.list_tools()
        assert "diff_compare" in names

    def test_execute_via_registry(self):
        import json

        resp = self.registry.execute_tool(
            "diff_compare",
            json.dumps({
                "sql_a": SQL_A,
                "sql_b": SQL_B,
                "key_column": "live_id",
                "compare_columns": "gmv,total_amount",
            }),
        )
        assert resp.status == ToolStatus.SUCCESS
        assert "key=105" in resp.text
        assert "差异=700" in resp.text
