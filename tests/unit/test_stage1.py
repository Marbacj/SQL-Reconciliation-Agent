"""Stage 1 单元测试：SQL safety / Tools / Adapter / Cache / Cost。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from recon_v2.adapters.sqlite_adapter import SQLiteAdapter
from recon_v2.core import AgentContext, CostBudget
from recon_v2.infra.cache import InMemoryCache
from recon_v2.infra.cost import CallRecord, CostTracker
from recon_v2.infra.sql_safety import apply_limit_guard, is_safe
from recon_v2.tools import (
    DiffCalculatorTool,
    RagSearcherTool,
    ReportGeneratorTool,
    SQLRunnerTool,
    ToolRegistry,
    build_default_registry,
)


DB_PATH = "data/eval_data.sqlite"


# ============== SQL Safety ==============


class TestSQLSafety:
    def test_select_allowed(self):
        assert is_safe("SELECT * FROM orders LIMIT 10").is_safe

    def test_with_cte_allowed(self):
        sql = "WITH o AS (SELECT * FROM orders) SELECT * FROM o LIMIT 5"
        assert is_safe(sql).is_safe

    def test_delete_rejected(self):
        v = is_safe("DELETE FROM orders WHERE id=1")
        assert not v.is_safe
        assert "DELETE" in v.reason.upper()

    def test_drop_rejected(self):
        v = is_safe("DROP TABLE orders")
        assert not v.is_safe

    def test_update_rejected(self):
        assert not is_safe("UPDATE orders SET amount=0").is_safe

    def test_insert_rejected(self):
        assert not is_safe("INSERT INTO orders VALUES (1,'u',1,'paid','2024')").is_safe

    def test_truncate_rejected(self):
        v = is_safe("TRUNCATE TABLE orders")
        assert not v.is_safe

    def test_string_literal_with_delete_passes(self):
        """SQL 注入测试：DELETE 在字符串字面量中不应误杀。"""
        sql = "SELECT id FROM orders WHERE status = 'DELETE FROM users'"
        assert is_safe(sql).is_safe

    def test_comment_bypass_rejected(self):
        """多语句 + 注释 + DDL 仍被拒绝。"""
        sql = "SELECT * FROM orders; /* x */ DROP TABLE x"
        assert not is_safe(sql).is_safe

    def test_injection_pattern_rejected(self):
        assert not is_safe("; DROP TABLE orders; --").is_safe

    def test_empty_rejected(self):
        assert not is_safe("").is_safe
        assert not is_safe("   ").is_safe

    def test_parse_error_rejected(self):
        assert not is_safe("SELEC * FRM").is_safe

    def test_limit_guard_adds(self):
        sql, modified = apply_limit_guard("SELECT * FROM orders", 100)
        assert modified
        assert "LIMIT 100" in sql

    def test_limit_guard_skip_existing(self):
        sql, modified = apply_limit_guard("SELECT * FROM orders LIMIT 5", 100)
        assert not modified


# ============== SQLite Adapter ==============


class TestSQLiteAdapter:
    @pytest.fixture
    def adapter(self):
        assert Path(DB_PATH).exists(), "Please run build_test_db first"
        return SQLiteAdapter(DB_PATH)

    def test_execute_select(self, adapter):
        res = adapter.execute("SELECT COUNT(*) FROM orders")
        assert res.success
        assert res.row_count == 1

    def test_execute_invalid_table(self, adapter):
        res = adapter.execute("SELECT * FROM nonexistent_table_xx")
        assert not res.success
        assert res.error

    def test_explain_passes(self, adapter):
        res = adapter.explain("SELECT * FROM orders WHERE id = 'O000001'")
        assert res.success


# ============== Cache & Cost ==============


class TestCache:
    def test_memory_cache_basic(self):
        c = InMemoryCache(maxsize=10, ttl=60)
        c.set("k1", "v1")
        assert c.get("k1") == "v1"
        assert c.get("missing") is None


class TestCostTracker:
    def test_record_and_summary(self):
        t = CostTracker()
        t.record(CallRecord("trace1", "m", 10, 5, 0.001, 100.0, "live"))
        t.record(CallRecord("trace1", "m", 8, 3, 0.0005, 50.0, "cache"))
        s = t.get_by_trace("trace1")
        assert s.calls == 2
        assert s.total_tokens == 26
        assert s.cache_hits == 1


# ============== Tools ==============


class TestSQLRunnerTool:
    @pytest.fixture
    def ctx(self):
        return AgentContext()

    @pytest.fixture
    def tool(self):
        return SQLRunnerTool(db_path=DB_PATH)

    def test_select_ok(self, tool, ctx):
        out = tool.run(ctx, {"sql": "SELECT COUNT(*) FROM orders"})
        assert out.success
        assert out.row_count == 1

    def test_reject_delete(self, tool, ctx):
        out = tool.run(ctx, {"sql": "DELETE FROM orders"})
        assert not out.success
        assert "safety" in (out.error or "").lower()

    def test_auto_limit_added(self, tool, ctx):
        out = tool.run(ctx, {"sql": "SELECT id FROM orders", "apply_limit": True})
        assert out.success
        assert "LIMIT 1000" in out.final_sql
        assert out.metadata.get("limit_added") is True


class TestDiffCalculatorTool:
    def test_diff_basic(self):
        tool = DiffCalculatorTool()
        ctx = AgentContext()
        out = tool.run(
            ctx,
            {
                "left": [{"id": 1, "amt": 100}, {"id": 2, "amt": 200}],
                "right": [{"id": 1, "amt": 100}, {"id": 3, "amt": 300}],
                "key_columns": ["id"],
                "compare_columns": ["amt"],
            },
        )
        assert out.success
        assert len(out.only_in_left) == 1
        assert len(out.only_in_right) == 1
        assert out.matched_count == 1

    def test_value_mismatch(self):
        tool = DiffCalculatorTool()
        ctx = AgentContext()
        out = tool.run(
            ctx,
            {
                "left": [{"id": 1, "amt": 100.0}],
                "right": [{"id": 1, "amt": 100.5}],
                "key_columns": ["id"],
                "compare_columns": ["amt"],
                "abs_tolerance": 0.01,
            },
        )
        assert out.success
        assert len(out.value_mismatch) == 1


class TestReportTool:
    def test_markdown(self):
        ctx = AgentContext()
        out = ReportGeneratorTool().run(
            ctx,
            {
                "title": "Test",
                "query": "Q",
                "columns": ["a", "b"],
                "rows": [[1, 2]],
                "summary": "ok",
            },
        )
        assert out.success
        assert "# Test" in out.content


class TestRagSearcherTool:
    def test_degraded_no_retriever(self):
        ctx = AgentContext()
        out = RagSearcherTool().run(ctx, {"query": "orders schema", "k": 3})
        assert out.success
        assert out.degraded


class TestRegistry:
    def test_default_registry(self):
        reg = build_default_registry(DB_PATH)
        assert reg.get("sql_runner") is not None
        assert reg.get("diff_calculator") is not None
        assert len(reg.all()) == 5

    def test_openai_function_schema(self):
        reg = build_default_registry(DB_PATH)
        funcs = reg.to_openai_functions()
        for f in funcs:
            assert f["type"] == "function"
            assert "name" in f["function"]
            assert "parameters" in f["function"]


# ============== Budget ==============


class TestBudget:
    def test_step_overflow(self):
        b = CostBudget(max_tokens=1000, max_seconds=60, max_steps=2)
        b.add_step(3)
        assert b.exceeded()

    def test_token_overflow(self):
        b = CostBudget(max_tokens=10, max_seconds=60, max_steps=10)
        b.add_tokens(100)
        assert b.exceeded()
