"""多轮对话澄清功能测试。

验证目标：
1. 低置信度查询 → clarify_node 输出 clarify_context，status=awaiting_clarification
2. 带 clarify_context 的续接请求 → route_node 合并 query，成功路由到 plan
3. 路由成功后 clarify_context 被清除
4. 多轮累计（turn 计数正确递增）
5. 正常高置信度查询不影响（clarify_context 为 None）
"""

from __future__ import annotations

import pytest

from recon_v2.orchestration.state import GraphState
from recon_v2.orchestration.nodes.clarify import clarify_node
from recon_v2.orchestration.nodes.route import route_node, route_decide


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures：构造最小可用的 AgentContext stub
# ─────────────────────────────────────────────────────────────────────────────

class _StepCounter:
    def __init__(self):
        self.step_counter = 0

    def step(self):
        self.step_counter += 1


class _BudgetStub:
    def snapshot(self):
        return {"tokens": 0}


class _CtxStub(_StepCounter):
    """最小化 AgentContext stub，不调 LLM，不访问 DB。"""
    def __init__(self, intent="simple_query", confidence=0.8):
        super().__init__()
        self.trace_id = "test-trace-001"
        self.query = ""
        self.intent = intent
        self.confidence = confidence
        self.llm = None
        self.memory = None
        self.rag = None
        self.budget = _BudgetStub()


# ─────────────────────────────────────────────────────────────────────────────
# Helper：注册 stub ctx 到 registry，返回 ctx_id
# ─────────────────────────────────────────────────────────────────────────────

def _register_ctx(ctx: _CtxStub) -> str:
    from recon_v2.orchestration import ctx_registry
    ctx_registry.register(ctx)
    return ctx.trace_id


def _remove_ctx(ctx: _CtxStub):
    from recon_v2.orchestration import ctx_registry
    try:
        ctx_registry.remove(ctx.trace_id)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Test 1：低置信度 → clarify_node 输出正确的 clarify_context
# ─────────────────────────────────────────────────────────────────────────────

class TestClarifyNodeOutput:
    def test_low_conf_outputs_clarify_context(self):
        ctx = _CtxStub(intent="simple_query", confidence=0.35)
        ctx_id = _register_ctx(ctx)
        try:
            state: GraphState = {
                "query": "帮我看一下数据",
                "db_path": "data/mock_reconciliation.db",
                "ctx_id": ctx_id,
                "intent": "simple_query",
                "confidence": 0.35,
            }
            result = clarify_node(state)

            assert result["final_status"] == "awaiting_clarification", (
                f"Expected 'awaiting_clarification', got '{result['final_status']}'"
            )
            assert result["sql"] == "CLARIFY"
            assert "clarify_context" in result
            cc = result["clarify_context"]
            assert cc["original_query"] == "帮我看一下数据"
            assert cc["turn"] == 1
            assert "clarify_question" in cc
        finally:
            _remove_ctx(ctx)

    def test_boundary_edge_not_awaiting(self):
        """boundary_edge 意图应输出 clarify（范围外），不是 awaiting_clarification。"""
        ctx = _CtxStub(intent="boundary_edge", confidence=0.99)
        ctx_id = _register_ctx(ctx)
        try:
            state: GraphState = {
                "query": "今天有什么菜",
                "db_path": "data/mock_reconciliation.db",
                "ctx_id": ctx_id,
                "intent": "boundary_edge",
                "confidence": 0.99,
            }
            result = clarify_node(state)
            # boundary_edge → clarify（服务范围外），而非 awaiting_clarification
            assert result["final_status"] in ("clarify", "rejected")
        finally:
            _remove_ctx(ctx)

    def test_turn_increments_on_consecutive_clarify(self):
        """若已有 clarify_context（turn=1），再次触发 clarify 时 turn 应增到 2。"""
        ctx = _CtxStub(intent="simple_query", confidence=0.30)
        ctx_id = _register_ctx(ctx)
        try:
            state: GraphState = {
                "query": "还是不知道怎么查",
                "db_path": "data/mock_reconciliation.db",
                "ctx_id": ctx_id,
                "intent": "simple_query",
                "confidence": 0.30,
                # 已有第一轮澄清上下文
                "clarify_context": {
                    "original_query": "帮我看一下数据",
                    "clarify_question": "请提供表名和时间范围",
                    "turn": 1,
                },
            }
            result = clarify_node(state)
            cc = result["clarify_context"]
            assert cc["turn"] == 2
            # original_query 应保持最初的问题
            assert cc["original_query"] == "帮我看一下数据"
        finally:
            _remove_ctx(ctx)


# ─────────────────────────────────────────────────────────────────────────────
# Test 2：route_node 合并 clarify_context + 新 query
# ─────────────────────────────────────────────────────────────────────────────

class TestRouteNodeClarifyMerge:
    def test_clarify_context_merges_query(self, monkeypatch):
        """route_node 检测到 clarify_context 时，应把 original_query + 用户补充合并后路由。"""
        # Monkeypatch LLM 分类器，直接返回高置信度，验证 query 合并逻辑
        captured = {}

        def _mock_llm_classify(ctx, query, episodic_cases=None, knn_hint=None):
            captured["merged_query"] = query
            return "simple_query", 0.90

        import recon_v2.orchestration.nodes.route as route_mod
        monkeypatch.setattr(route_mod, "_llm_classify", _mock_llm_classify)
        monkeypatch.setattr(route_mod, "_fast_path_match", lambda q: None)
        monkeypatch.setattr(route_mod, "_knn_classify", lambda q, cases, k=7: None)
        monkeypatch.setattr(route_mod, "_recall_episodic", lambda ctx, q, k=20: [])

        ctx = _CtxStub()
        ctx_id = _register_ctx(ctx)
        try:
            state: GraphState = {
                "query": "订单表，昨天，总金额",
                "db_path": "data/mock_reconciliation.db",
                "ctx_id": ctx_id,
                "clarify_context": {
                    "original_query": "帮我看一下数据",
                    "clarify_question": "请提供表名和时间范围",
                    "turn": 1,
                },
            }
            result = route_node(state)

            # 合并后的 query 应包含原始问题和补充说明
            merged = captured.get("merged_query", "")
            assert "帮我看一下数据" in merged, f"original_query missing in merged: {merged}"
            assert "订单表" in merged, f"supplement missing in merged: {merged}"

            # 路由成功后 clarify_context 应被清除
            assert result.get("clarify_context") is None, (
                f"clarify_context should be cleared after successful route, got: {result.get('clarify_context')}"
            )
        finally:
            _remove_ctx(ctx)

    def test_no_clarify_context_normal_routing(self, monkeypatch):
        """无 clarify_context 时，正常路由，query 不被修改。"""
        captured = {}

        def _mock_llm_classify(ctx, query, episodic_cases=None, knn_hint=None):
            captured["query"] = query
            return "simple_query", 0.85

        import recon_v2.orchestration.nodes.route as route_mod
        monkeypatch.setattr(route_mod, "_llm_classify", _mock_llm_classify)
        monkeypatch.setattr(route_mod, "_fast_path_match", lambda q: None)
        monkeypatch.setattr(route_mod, "_knn_classify", lambda q, cases, k=7: None)
        monkeypatch.setattr(route_mod, "_recall_episodic", lambda ctx, q, k=20: [])

        ctx = _CtxStub()
        ctx_id = _register_ctx(ctx)
        try:
            original_query = "查一下订单表昨天的总金额"
            state: GraphState = {
                "query": original_query,
                "db_path": "data/mock_reconciliation.db",
                "ctx_id": ctx_id,
            }
            route_node(state)
            assert captured["query"] == original_query
        finally:
            _remove_ctx(ctx)


# ─────────────────────────────────────────────────────────────────────────────
# Test 3：route_decide 路由决策
# ─────────────────────────────────────────────────────────────────────────────

class TestRouteDecide:
    def test_low_conf_goes_clarify(self):
        state: GraphState = {"query": "订单金额对账", "intent": "simple_query", "confidence": 0.40}
        assert route_decide(state) == "clarify"

    def test_high_conf_goes_plan(self):
        # 有业务信号词 + 高置信度 → plan
        state: GraphState = {"query": "查一下今天订单总金额", "intent": "simple_query", "confidence": 0.80}
        assert route_decide(state) == "plan"

    def test_boundary_edge_returns_reject(self):
        # boundary_edge 现在返回 "reject"（graph 里映射到 clarify 节点）
        state: GraphState = {"query": "删除所有订单", "intent": "boundary_edge", "confidence": 0.99}
        assert route_decide(state) == "reject"

    def test_threshold_boundary(self):
        # conf=0.45 边界：>= 0.45 但需有业务信号词才能进 plan
        state_pass: GraphState = {"query": "订单金额统计", "intent": "simple_query", "confidence": 0.45}
        assert route_decide(state_pass) == "plan"

        state_fail: GraphState = {"query": "帮我看下", "intent": "simple_query", "confidence": 0.44}
        assert route_decide(state_fail) == "clarify"

    def test_vague_query_forced_clarify(self):
        """过于模糊的 query（无业务信号词 + 长度 < 10）即使置信度高也要澄清。"""
        vague_cases = [
            {"query": "帮我看数据", "intent": "simple_query", "confidence": 0.75},
            {"query": "查一下", "intent": "simple_query", "confidence": 0.80},
            {"query": "看看", "intent": "simple_query", "confidence": 0.90},
        ]
        for state in vague_cases:
            result = route_decide(state)
            assert result == "clarify", (
                f"Expected clarify for vague query '{state['query']}', got '{result}'"
            )

    def test_specific_query_not_forced_clarify(self):
        """有业务信号词的 query 不应被模糊检测拦截。"""
        specific_cases = [
            {"query": "今天订单", "intent": "simple_query", "confidence": 0.75},
            {"query": "查GMV", "intent": "simple_query", "confidence": 0.80},
            {"query": "看数据差异", "intent": "numeric_diff", "confidence": 0.70},
        ]
        for state in specific_cases:
            result = route_decide(state)
            assert result == "plan", (
                f"Expected plan for specific query '{state['query']}', got '{result}'"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Test 4：GraphState 字段验证
# ─────────────────────────────────────────────────────────────────────────────

class TestGraphStateFields:
    def test_clarify_context_field_exists(self):
        """GraphState 应包含 clarify_context 字段。"""
        from recon_v2.orchestration.state import GraphState
        hints = GraphState.__annotations__
        assert "clarify_context" in hints, "clarify_context not found in GraphState"

    def test_final_status_comment_includes_awaiting(self):
        """验证 final_status 字段存在。"""
        from recon_v2.orchestration.state import GraphState
        assert "final_status" in GraphState.__annotations__
