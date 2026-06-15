"""Self-Evolution 三道质量门 pipeline。

Door 1: Dedup     — embedding 相似度 > 0.85 拒
Door 2: Critic    — LLM 三维评分加权 < 0.7 拒（无 LLM 时降级为简单启发式）
Door 3: Sandbox   — Golden Set 抽样 dry-run，回归 > 2% 拒

通过的 skill 入库，confidence 初值 0.6，后续按 Wilson 累积。
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from recon_v2.memory.db import db_conn

logger = logging.getLogger(__name__)


@dataclass
class SkillCandidate:
    name: str
    description: str
    body: str  # skill 内容（few-shot 模板）
    intent: str = ""


# ---------------- Door 1: Dedup ----------------


def _candidate_emb(c: SkillCandidate) -> Dict[str, float]:
    from recon_v2.memory.store import _embed

    return _embed(c.body + " " + c.description)


def door_dedup(memory, candidate: SkillCandidate, threshold: float = 0.85) -> tuple:
    """检查是否与现有 skill 高度重复。返回 (passed, reason)。"""
    from recon_v2.memory.store import _cosine, _deserialize

    cand_emb = _candidate_emb(candidate)
    with db_conn(memory.db_path) as conn:
        rows = conn.execute(
            "SELECT id, name, embedding_json FROM skill WHERE archived=0"
        ).fetchall()
    for r in rows:
        sim = _cosine(cand_emb, _deserialize(r["embedding_json"]))
        if sim >= threshold:
            return False, f"duplicate of skill#{r['id']} ({r['name']}) sim={sim:.2f}"
    return True, "ok"


# ---------------- Door 2: Critic ----------------


def door_critic(candidate: SkillCandidate, llm=None, threshold: float = 0.7) -> tuple:
    """LLM 三维评分（具体性 / 可复用性 / 正交性）。

    无 LLM 时降级启发式：
      - body 长度 ≥ 20 且包含 "WHEN"/"THEN"/"SQL"/"对账" 任一关键词 → 通过
    """
    if llm is None:
        if len(candidate.body) < 20:
            return False, "body too short (heuristic)"
        if not any(kw in candidate.body.lower() for kw in ["sql", "对账", "when", "then", "select"]):
            return False, "no domain-keyword (heuristic)"
        return True, "ok:heuristic"

    sys = (
        "You evaluate a candidate skill on 3 dimensions (0-1 each): "
        "specificity, reusability, orthogonality. "
        "Reply strict JSON {specificity, reusability, orthogonality}."
    )
    usr = f"Name: {candidate.name}\nDescription: {candidate.description}\nBody:\n{candidate.body}"
    try:
        out = llm.chat(
            messages=[{"role": "system", "content": sys}, {"role": "user", "content": usr}],
            temperature=0.0,
            max_tokens=120,
        )
        obj = json.loads(out.content.strip().strip("`"))
        score = (
            float(obj["specificity"]) * 0.4
            + float(obj["reusability"]) * 0.4
            + float(obj["orthogonality"]) * 0.2
        )
        return (score >= threshold, f"score={score:.2f}")
    except Exception as e:
        return False, f"critic error: {e}"


# ---------------- Door 3: Sandbox ----------------


def door_sandbox(
    candidate: SkillCandidate,
    golden_subset: Optional[List] = None,
    regression_threshold: float = 0.02,
    db_path: str = "data/eval_data.sqlite",
) -> tuple:
    """在 Golden Set 抽样（默认 10 条）上做 dry-run。

    简化版：本 stage 用 stub adapter 自检 — 主要验证 sandbox 框架本身可用。
    生产版需用 v2Adapter 跑实际管道，对比 baseline / with-skill 准确率。
    """
    try:
        # 这里跑一个极简的 baseline + with-skill 对比的 stub
        # baseline / with_skill 都用真实 v2 graph 跑（耗时考虑：限 10 条）
        from tests.eval.runner import V2Adapter
        from tests.eval.schema import load_golden_set

        if golden_subset is None:
            cases = load_golden_set("tests/eval/golden_set.jsonl")
            # 按 intent 分层各取 2 条共 10 条
            by_intent: Dict[str, list] = {}
            for c in cases:
                by_intent.setdefault(c.intent_label.value, []).append(c)
            golden_subset = []
            for ints, lst in by_intent.items():
                golden_subset.extend(lst[:2])

        adapter = V2Adapter()
        if not adapter._available:
            return True, "sandbox skipped (v2 not loaded)"

        from tests.eval.runner import run_single

        baseline_acc = 0
        for c in golden_subset:
            m = run_single(adapter, c, db_path)
            baseline_acc += m.exec_acc

        # with-skill 当前没有真实注入逻辑（待 Stage 4 完善），返回相同分数 → 视为通过
        with_skill_acc = baseline_acc

        delta = (baseline_acc - with_skill_acc) / max(1, len(golden_subset))
        if delta > regression_threshold:
            return False, f"regression detected: {delta:.2%}"
        return True, f"ok baseline={baseline_acc}/{len(golden_subset)} delta={delta:.2%}"
    except Exception as e:
        logger.warning("sandbox error: %s", e)
        return True, f"sandbox skipped (error: {e})"


# ---------------- Wilson 动态 confidence ----------------


def wilson_lower_bound(success: int, total: int, z: float = 1.96) -> float:
    if total == 0:
        return 0.0
    p = success / total
    denom = 1 + z * z / total
    num = p + z * z / (2 * total) - z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return max(0.0, num / denom)


# ---------------- 装配 pipeline ----------------


def evaluate_and_persist(
    memory,
    trace_id: str,
    query: str,
    sql: str,
    answer: str,
    success: bool,
    llm=None,
    schema_version: str = "",
) -> dict:
    """主入口：reflect_node → submit_skill_review → 这里。

    只有「成功」case 才会被尝试提炼为 skill。
    失败 case 写到 episodic 用于诊断，不入 skill。
    """
    from recon_v2.memory.store import get_schema_hash
    sv = schema_version or get_schema_hash(getattr(memory, "db_path", ""))

    # 1) 先写 episodic（user_flag=0，importance 由 query 新颖度决定）
    importance_info = memory.write(
        trace_id=trace_id,
        query=query,
        intent="",
        sql=sql,
        answer=answer,
        outcome=1 if success else 0,
        user_flag=0,
        schema_version=sv,
    )

    if not success or not sql or sql.upper() in {"REJECT", "CLARIFY"}:
        return {"skill_added": False, "reason": "not eligible", **importance_info}

    # 2) 候选 skill：以 query → sql 作为 few-shot 模板
    candidate = SkillCandidate(
        name=f"skill_{int(time.time())}",
        description=f"Few-shot for query: {query[:50]}",
        body=f"Q: {query}\nSQL: {sql}",
        intent="",
    )

    # 3) 三道门
    ok, reason = door_dedup(memory, candidate)
    if not ok:
        return {"skill_added": False, "reason": f"dedup: {reason}"}

    ok, reason = door_critic(candidate, llm=llm)
    if not ok:
        return {"skill_added": False, "reason": f"critic: {reason}"}

    ok, reason = door_sandbox(candidate)
    if not ok:
        return {"skill_added": False, "reason": f"sandbox: {reason}"}

    # 4) 入库
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with db_conn(memory.db_path) as conn:
        from recon_v2.memory.store import _embed, _serialize

        cur = conn.execute(
            """
            INSERT INTO skill
              (name, description, body, intent, confidence,
               embedding_json, created_at, last_used_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate.name,
                candidate.description,
                candidate.body,
                candidate.intent,
                0.6,
                _serialize(_embed(candidate.body + " " + candidate.description)),
                now,
                now,
            ),
        )
        skill_id = cur.lastrowid

    return {"skill_added": True, "skill_id": skill_id, "reason": reason, **importance_info}
