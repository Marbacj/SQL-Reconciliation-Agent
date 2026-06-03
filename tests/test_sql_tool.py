"""Tests for SQLTool — the 3 sub-tools (sql_schema, sql_execute, sql_validate)."""

import os
import pytest

from recon_core.tools.builtin.sql_tool import SQLTool
from recon_core.tools.registry import ToolRegistry
from recon_core.tools.response import ToolResponse, ToolStatus


# ---------- helpers ----------

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "mock_reconciliation.db")


def _assert_ok(result: str):
    """Fail if the string result indicates an error (starts with ❌)."""
    assert not result.startswith("❌"), f"Unexpected error: {result}"


# ---------- direct method tests (bypass registry) ----------


class TestSQLToolDirect:
    """Test SQLTool sub-tool methods called directly on the parent instance."""

    @classmethod
    def setup_class(cls):
        cls.tool = SQLTool(db_path=DB_PATH)

    # --- sql_schema ---

    def test_schema_existing_table(self):
        result = self.tool._get_schema("live_gmv")
        _assert_ok(result)
        assert "live_gmv" in result
        assert "live_id" in result
        assert "gmv" in result
        assert "总行数" in result
        assert "DDL" in result

    def test_schema_example_data_present(self):
        result = self.tool._get_schema("live_gmv")
        assert "示例数据" in result
        assert "李佳琦" in result  # first row

    def test_schema_nonexistent_table(self):
        result = self.tool._get_schema("no_such_table")
        assert result.startswith("❌")
        assert "不存在" in result

    def test_schema_order_amount_table(self):
        result = self.tool._get_schema("order_amount")
        _assert_ok(result)
        assert "order_amount" in result
        assert "total_amount" in result
        assert "order_status" in result

    # --- sql_execute ---

    def test_execute_simple_select(self):
        result = self.tool._execute("SELECT live_id, gmv FROM live_gmv WHERE live_id = 105")
        _assert_ok(result)
        assert "105" in result
        assert "12500" in result

    def test_execute_count(self):
        result = self.tool._execute("SELECT COUNT(*) AS cnt FROM live_gmv")
        _assert_ok(result)
        assert "26" in result  # 26 rows in live_gmv

    def test_execute_order_table(self):
        result = self.tool._execute("SELECT * FROM order_amount WHERE live_id = 208")
        _assert_ok(result)
        assert "208" in result
        assert "3500" in result

    def test_execute_security_blocks_dangerous(self):
        for keyword in ["DROP", "DELETE", "UPDATE", "INSERT"]:
            result = self.tool._execute(f"{keyword} TABLE live_gmv")
            assert result.startswith("❌")
            assert "安全限制" in result

    def test_execute_bad_syntax(self):
        result = self.tool._execute("SELECTT * FORM live_gmv")
        assert result.startswith("❌ SQL 执行错误")

    # --- sql_validate ---

    def test_validate_good_sql(self):
        result = self.tool._validate("SELECT * FROM live_gmv WHERE live_id = 101")
        assert result.startswith("✅")
        assert "语法校验通过" in result

    def test_validate_bad_sql(self):
        result = self.tool._validate("SELECTT * FRM live_gmv")
        assert result.startswith("❌ SQL 语法错误")

    def test_validate_dangerous_rejected(self):
        result = self.tool._validate("DROP TABLE live_gmv")
        assert result.startswith("❌")
        assert "安全限制" in result


# ---------- ToolRegistry integration tests ----------


class TestSQLToolViaRegistry:
    """Test that sub-tools are correctly expanded and executable via ToolRegistry."""

    @classmethod
    def setup_class(cls):
        cls.tool = SQLTool(db_path=DB_PATH)
        cls.registry = ToolRegistry()
        cls.registry.register_tool(cls.tool)

    def test_sub_tools_registered(self):
        names = self.registry.list_tools()
        assert "sql_schema" in names
        assert "sql_execute" in names
        assert "sql_validate" in names

    def test_execute_sql_schema_via_registry(self):
        resp = self.registry.execute_tool(
            "sql_schema", '{"table_name": "live_gmv"}'
        )
        assert resp.status == ToolStatus.SUCCESS
        assert "live_gmv" in resp.text
        assert "live_id" in resp.text

    def test_execute_sql_execute_via_registry(self):
        resp = self.registry.execute_tool(
            "sql_execute",
            '{"sql": "SELECT live_id, gmv FROM live_gmv WHERE live_id = 105"}',
        )
        assert resp.status == ToolStatus.SUCCESS
        assert "105" in resp.text
        assert "12500" in resp.text

    def test_execute_sql_validate_via_registry(self):
        resp = self.registry.execute_tool(
            "sql_validate",
            '{"sql": "SELECT * FROM live_gmv WHERE live_id = 101"}',
        )
        assert resp.status == ToolStatus.SUCCESS
        assert "语法校验通过" in resp.text

    def test_parent_tool_expanded_not_in_registry(self):
        # When auto_expand=True (default), the parent "SQLTool" is expanded
        # into sub-tools and not registered under its own name.
        assert "SQLTool" not in self.registry.list_tools()

    def test_parent_tool_gives_error_when_registered_directly(self):
        # When registered with auto_expand=False, the parent tool IS registered
        # and returns an error directing users to sub-tools.
        registry2 = ToolRegistry()
        registry2.register_tool(self.tool, auto_expand=False)
        resp = registry2.execute_tool("SQLTool", '{}')
        assert resp.status == ToolStatus.ERROR
        assert "请使用子工具" in resp.text
