"""MemoryStore: episodic / skill / semantic-rule persistence with lightweight embedding."""
from __future__ import annotations

import json
import logging
import math
import os
import time
from typing import Any, Dict, List, Optional

from recon_v2.memory.db import db_conn

logger = logging.getLogger(__name__)

_DEFAULT_DB = os.getenv("MEMORY_DB_PATH", "data/memory.sqlite")

# ── Minimal bag-of-tokens embedding ──────────────────────────────────────────

def _tokenize(text: str) -> List[str]:
    import re
    return re.findall(r"[\w一-鿿]+", text.lower())


def _embed(text: str) -> Dict[str, float]:
    tokens = _tokenize(text)
    if not tokens:
        return {}
    freq: Dict[str, float] = {}
    for t in tokens:
        freq[t] = freq.get(t, 0) + 1
    norm = math.sqrt(sum(v * v for v in freq.values()))
    return {k: v / norm for k, v in freq.items()} if norm else freq


def _cosine(a: Dict[str, float], b: Dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    return sum(a.get(k, 0.0) * v for k, v in b.items())


def _serialize(emb: Dict[str, float]) -> str:
    return json.dumps(emb)


def _deserialize(s: str) -> Dict[str, float]:
    try:
        return json.loads(s) if s else {}
    except Exception:
        return {}


# ── Importance scoring ────────────────────────────────────────────────────────

def _importance(query: str, outcome: int, user_flag: int, existing_embs: List[Dict]) -> float:
    emb = _embed(query)
    novelty = 1.0
    if existing_embs:
        max_sim = max(_cosine(emb, e) for e in existing_embs)
        novelty = 1.0 - max_sim
    base = 0.3 + 0.4 * outcome + 0.3 * user_flag
    return round(min(1.0, base * (0.5 + 0.5 * novelty)), 4)


# ── MemoryStore ───────────────────────────────────────────────────────────────

class MemoryStore:
    def __init__(self, db_path: str = _DEFAULT_DB):
        self.db_path = db_path
        # ensure schema exists on init
        with db_conn(self.db_path):
            pass

    # ── Write ─────────────────────────────────────────────────────────────────

    def write(
        self,
        trace_id: str,
        query: str,
        intent: str,
        sql: str,
        answer: str,
        outcome: int,
        user_flag: int = 0,
    ) -> Dict[str, Any]:
        emb = _embed(query)
        with db_conn(self.db_path) as conn:
            existing = conn.execute(
                "SELECT embedding_json FROM episodic_case WHERE archived=0 ORDER BY id DESC LIMIT 200"
            ).fetchall()
        existing_embs = [_deserialize(r["embedding_json"]) for r in existing]
        importance = _importance(query, outcome, user_flag, existing_embs)
        promoted = 1 if importance >= 0.7 and outcome == 1 else 0
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        with db_conn(self.db_path) as conn:
            conn.execute(
                """INSERT INTO episodic_case
                   (trace_id, query, intent, sql, answer, outcome,
                    importance, user_flag, promoted, embedding_json, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (trace_id, query, intent, sql, answer, outcome,
                 importance, user_flag, promoted, _serialize(emb), now),
            )
        return {"importance": importance, "promoted": bool(promoted)}

    # ── Query ─────────────────────────────────────────────────────────────────

    def query(
        self,
        query: str,
        k: int = 5,
        intent_filter: Optional[str] = None,
    ) -> Dict[str, List]:
        episodic = self.query_episodic(query, k=k, intent_filter=intent_filter)
        semantic = self._query_semantic(query, k=k)
        return {"episodic": episodic, "semantic": semantic}

    def query_episodic(
        self,
        query: str,
        k: int = 5,
        intent_filter: Optional[str] = None,
    ) -> List[Dict]:
        emb = _embed(query)
        with db_conn(self.db_path) as conn:
            sql = "SELECT * FROM episodic_case WHERE archived=0"
            params: list = []
            if intent_filter:
                sql += " AND intent=?"
                params.append(intent_filter)
            sql += " ORDER BY id DESC LIMIT 500"
            rows = conn.execute(sql, params).fetchall()
        scored = []
        for r in rows:
            sim = _cosine(emb, _deserialize(r["embedding_json"]))
            scored.append((sim, dict(r)))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:k]]

    def _query_semantic(self, query: str, k: int = 5) -> List[Dict]:
        with db_conn(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM semantic_rule WHERE archived=0 ORDER BY confidence DESC LIMIT ?", (k,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Skill review ──────────────────────────────────────────────────────────

    def submit_skill_review(
        self,
        trace_id: str,
        query: str,
        sql: str,
        answer: str,
        success: bool,
    ) -> Dict[str, Any]:
        try:
            from recon_v2.evolution.pipeline import evaluate_and_persist
            return evaluate_and_persist(
                memory=self,
                trace_id=trace_id,
                query=query,
                sql=sql,
                answer=answer,
                success=success,
            )
        except Exception as e:
            logger.warning("submit_skill_review error: %s", e)
            return {"skill_added": False, "reason": str(e)}

    # ── Evolution ─────────────────────────────────────────────────────────────

    def consolidate(self) -> Dict[str, Any]:
        """Derive semantic rules from high-importance episodic cases."""
        try:
            with db_conn(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT intent, query FROM episodic_case WHERE archived=0 AND importance>=0.7 ORDER BY id DESC LIMIT 100"
                ).fetchall()
            intent_counts: Dict[str, int] = {}
            for r in rows:
                intent_counts[r["intent"]] = intent_counts.get(r["intent"], 0) + 1
            new_rules = 0
            with db_conn(self.db_path) as conn:
                existing = {r["rule"] for r in conn.execute("SELECT rule FROM semantic_rule WHERE archived=0").fetchall()}
                now = time.strftime("%Y-%m-%d %H:%M:%S")
                for intent, count in intent_counts.items():
                    if count >= 3:
                        rule = f"intent:{intent}"
                        if rule not in existing:
                            conn.execute(
                                "INSERT INTO semantic_rule (rule, intent, confidence, created_at) VALUES (?,?,?,?)",
                                (rule, intent, min(0.9, 0.5 + 0.1 * count), now),
                            )
                            new_rules += 1
            return {"new_rules": new_rules}
        except Exception as e:
            logger.error("consolidate error: %s", e)
            return {"new_rules": 0, "error": str(e)}

    def decay(self) -> Dict[str, Any]:
        """Archive old low-importance episodic cases."""
        try:
            cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 30 * 86400))
            with db_conn(self.db_path) as conn:
                cur = conn.execute(
                    "UPDATE episodic_case SET archived=1 WHERE archived=0 AND importance<0.3 AND created_at<?",
                    (cutoff,),
                )
            return {"archived": cur.rowcount}
        except Exception as e:
            logger.error("decay error: %s", e)
            return {"archived": 0, "error": str(e)}
