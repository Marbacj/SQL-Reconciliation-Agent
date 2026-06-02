"""Stage 4 单元测试：Memory v2 + Self-Evolution。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from recon_v2.evolution.pipeline import (
    SkillCandidate,
    door_critic,
    door_dedup,
    door_sandbox,
    evaluate_and_persist,
    wilson_lower_bound,
)
from recon_v2.memory.store import MemoryStore


@pytest.fixture
def tmp_memory(tmp_path):
    db = tmp_path / "mem.sqlite"
    return MemoryStore(db_path=str(db))


# ============== Memory ==============


class TestMemoryWrite:
    def test_low_importance_only_working(self, tmp_memory):
        info = tmp_memory.write(
            trace_id="t1",
            query="查询订单",
            intent="simple_query",
            sql="SELECT 1",
            answer="ok",
            outcome=0,
            user_flag=0,
        )
        assert info["promoted"] is False

    def test_high_importance_promoted(self, tmp_memory):
        info = tmp_memory.write(
            trace_id="t2",
            query="非常具体的某查询",
            intent="simple_query",
            sql="SELECT 1",
            answer="ok",
            outcome=1,
            user_flag=1,  # 强制高 importance
        )
        # outcome=1, novelty 高（首次），user_flag=1 → importance = 0.4 + 0.3 + 0.3 = 1.0
        assert info["promoted"] is True
        assert info["importance"] >= 0.6

    def test_query_episodic(self, tmp_memory):
        tmp_memory.write(
            trace_id="t3",
            query="对账昨日订单",
            intent="time_window_recon",
            sql="SELECT count(*) FROM orders",
            answer="ok",
            outcome=1,
            user_flag=1,
        )
        hits = tmp_memory.query_episodic("对账", k=3)
        assert len(hits) >= 1
        assert hits[0]["similarity"] > 0


class TestConsolidation:
    def test_consolidate_generates_rules(self, tmp_memory):
        # 注入 5 条 time_window_recon 高成功率 case
        for i in range(5):
            tmp_memory.write(
                trace_id=f"c{i}",
                query=f"对账昨日订单 {i}",
                intent="time_window_recon",
                sql="SELECT 1",
                answer="ok",
                outcome=1,
                user_flag=1,
            )
        res = tmp_memory.consolidate(min_cluster_size=5)
        assert res["new_rules"] >= 1

        rules = tmp_memory.query_semantic("对账", k=3)
        assert len(rules) >= 1


# ============== Self-Evolution 3 doors ==============


class TestDedupDoor:
    def test_first_unique(self, tmp_memory):
        cand = SkillCandidate(
            name="s1", description="d", body="SELECT * FROM orders WHERE 1=1"
        )
        ok, reason = door_dedup(tmp_memory, cand)
        assert ok

    def test_dedup_rejects_duplicate(self, tmp_memory):
        # 先写一条
        evaluate_and_persist(
            memory=tmp_memory,
            trace_id="t1",
            query="查询昨天订单总数",
            sql="SELECT COUNT(*) FROM orders",
            answer="ok",
            success=True,
        )
        # 再尝试一条完全一样的
        cand = SkillCandidate(
            name="s2",
            description="Few-shot for query: 查询昨天订单总数",
            body="Q: 查询昨天订单总数\nSQL: SELECT COUNT(*) FROM orders",
        )
        ok, reason = door_dedup(tmp_memory, cand, threshold=0.5)
        assert not ok
        assert "duplicate" in reason.lower()


class TestCriticDoor:
    def test_heuristic_pass(self):
        cand = SkillCandidate(
            name="s",
            description="d",
            body="SELECT * FROM orders WHERE status='paid' AND DATE(created_at)=DATE('now')",
        )
        ok, _ = door_critic(cand)
        assert ok

    def test_heuristic_reject_too_short(self):
        cand = SkillCandidate(name="s", description="d", body="x")
        ok, _ = door_critic(cand)
        assert not ok


class TestWilson:
    def test_wilson_basic(self):
        # 18 success / 20 total 应该 > 简单 18/20=0.9 的 lower bound
        lb = wilson_lower_bound(18, 20)
        assert 0.6 < lb < 0.9

    def test_wilson_zero(self):
        assert wilson_lower_bound(0, 0) == 0.0


class TestEvaluateAndPersist:
    def test_unique_success_creates_skill(self, tmp_memory):
        info = evaluate_and_persist(
            memory=tmp_memory,
            trace_id="t",
            query="对账昨日支付与订单",
            sql="SELECT o.id FROM orders o JOIN payments p ON o.id=p.order_id WHERE DATE(o.created_at)=DATE('now','-1 day')",
            answer="完成对账",
            success=True,
        )
        assert info["skill_added"] is True

    def test_failed_case_not_persisted(self, tmp_memory):
        info = evaluate_and_persist(
            memory=tmp_memory,
            trace_id="t",
            query="x",
            sql="REJECT",
            answer="rejected",
            success=False,
        )
        assert info["skill_added"] is False
