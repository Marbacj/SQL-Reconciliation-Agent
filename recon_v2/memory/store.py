"""MemoryStore: episodic / skill / semantic-rule / discrepancy-pattern persistence.

Embedding strategy (auto-selected at startup):
  1. sentence-transformers paraphrase-multilingual-MiniLM-L12-v2 (dense, multilingual)
  2. bag-of-tokens cosine (fallback, no dependencies)

Stored format: JSON list for dense embeddings, JSON dict for sparse.
Both formats are detected at read time and handled transparently.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
from typing import Any, Dict, List, Optional, Union

from recon_v2.memory.db import db_conn

logger = logging.getLogger(__name__)

_DEFAULT_DB = os.getenv("MEMORY_DB_PATH", "data/memory.sqlite")

# ── Embedding backend (lazy init) ────────────────────────────────────────────

_st_model = None
_st_tried = False


def _get_st_model():
    global _st_model, _st_tried
    if _st_tried:
        return _st_model
    _st_tried = True
    try:
        from sentence_transformers import SentenceTransformer
        _st_model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2", local_files_only=False)
        logger.info("MemoryStore: using dense sentence-transformers embedding")
    except Exception as e:
        logger.info("MemoryStore: sentence-transformers unavailable (%s), using bag-of-tokens", e)
        _st_model = None
    return _st_model


# ── Sparse embedding (bag-of-tokens) ─────────────────────────────────────────

def _tokenize(text: str) -> List[str]:
    return re.findall(r"[\w一-鿿]+", text.lower())


def _sparse_embed(text: str) -> Dict[str, float]:
    tokens = _tokenize(text)
    if not tokens:
        return {}
    freq: Dict[str, float] = {}
    for t in tokens:
        freq[t] = freq.get(t, 0) + 1
    norm = math.sqrt(sum(v * v for v in freq.values()))
    return {k: v / norm for k, v in freq.items()} if norm else freq


# ── Unified embed / similarity ────────────────────────────────────────────────

EmbType = Union[List[float], Dict[str, float]]


def _embed(text: str) -> EmbType:
    model = _get_st_model()
    if model is not None:
        try:
            vec = model.encode(text, normalize_embeddings=True)
            return vec.tolist()
        except Exception as e:
            logger.debug("dense embed failed, fallback: %s", e)
    return _sparse_embed(text)


def _cosine(a: EmbType, b: EmbType) -> float:
    if not a or not b:
        return 0.0
    if isinstance(a, list) and isinstance(b, list):
        # dense dot-product (both already L2-normalised by sentence-transformers)
        return float(sum(x * y for x, y in zip(a, b)))
    if isinstance(a, dict) and isinstance(b, dict):
        return sum(a.get(k, 0.0) * v for k, v in b.items())
    # mixed types (old sparse vs new dense): fallback to 0
    return 0.0


def _serialize(emb: EmbType) -> str:
    return json.dumps(emb)


def _deserialize(s: str) -> EmbType:
    try:
        return json.loads(s) if s else {}
    except Exception:
        return {}


# ── Schema version ────────────────────────────────────────────────────────────

def get_schema_hash(db_path: str = "") -> str:
    """MD5 of schema_index.json (project-level) truncated to 8 chars.
    Falls back to empty string if not found.
    """
    candidates = [
        "data/schema_index.json",
        os.path.join(os.path.dirname(db_path), "schema_index.json") if db_path else "",
    ]
    for path in candidates:
        if path and os.path.exists(path):
            try:
                with open(path, "rb") as f:
                    return hashlib.md5(f.read()).hexdigest()[:8]
            except Exception:
                pass
    return ""


# ── Importance scoring ────────────────────────────────────────────────────────

def _importance(query: str, outcome: int, user_flag: int, existing_embs: List[EmbType]) -> float:
    emb = _embed(query)
    novelty = 1.0
    if existing_embs:
        sims = [_cosine(emb, e) for e in existing_embs if e]
        if sims:
            novelty = 1.0 - max(sims)
    base = 0.3 + 0.4 * outcome + 0.3 * user_flag
    return round(min(1.0, base * (0.5 + 0.5 * novelty)), 4)


# ── MemoryStore ───────────────────────────────────────────────────────────────

class MemoryStore:
    def __init__(self, db_path: str = _DEFAULT_DB):
        self.db_path = db_path
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
        schema_version: str = "",
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
        sv = schema_version or get_schema_hash(self.db_path)
        with db_conn(self.db_path) as conn:
            conn.execute(
                """INSERT INTO episodic_case
                   (trace_id, query, intent, sql, answer, outcome,
                    importance, user_flag, promoted, embedding_json,
                    schema_version, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (trace_id, query, intent, sql, answer, outcome,
                 importance, user_flag, promoted, _serialize(emb),
                 sv, now),
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
        skills = self.query_skills(query, k=3)
        return {"episodic": episodic, "semantic": semantic, "skills": skills}

    def query_episodic(
        self,
        query: str,
        k: int = 5,
        intent_filter: Optional[str] = None,
        current_schema_version: str = "",
    ) -> List[Dict]:
        emb = _embed(query)
        current_sv = current_schema_version or get_schema_hash(self.db_path)
        with db_conn(self.db_path) as conn:
            sql_q = "SELECT * FROM episodic_case WHERE archived=0"
            params: list = []
            if intent_filter:
                sql_q += " AND intent=?"
                params.append(intent_filter)
            sql_q += " ORDER BY id DESC LIMIT 500"
            rows = conn.execute(sql_q, params).fetchall()
        scored = []
        for r in rows:
            sim = _cosine(emb, _deserialize(r["embedding_json"]))
            # boost promoted cases; penalize stale schema cases
            boost = 0.1 if r["promoted"] else 0.0
            schema_penalty = -0.15 if (current_sv and r["schema_version"] and r["schema_version"] != current_sv) else 0.0
            row_dict = dict(r)
            row_dict["similarity"] = round(sim, 4)
            scored.append((sim + boost + schema_penalty, row_dict))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:k]]

    def query_semantic(self, query: str, k: int = 5) -> List[Dict]:
        return self._query_semantic(query, k=k)

    def _query_semantic(self, query: str, k: int = 5) -> List[Dict]:
        with db_conn(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM semantic_rule WHERE archived=0 ORDER BY confidence DESC LIMIT ?", (k,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Skill retrieval & usage tracking ─────────────────────────────────────

    def query_skills(self, query: str, k: int = 3) -> List[Dict]:
        """Return top-k skills ranked by semantic similarity × confidence."""
        emb = _embed(query)
        with db_conn(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM skill WHERE archived=0 ORDER BY confidence DESC LIMIT 200"
            ).fetchall()
        scored = []
        for r in rows:
            sim = _cosine(emb, _deserialize(r["embedding_json"]))
            score = sim * float(r["confidence"])
            scored.append((score, dict(r)))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for score, item in scored[:k] if score > 0.3]

    def update_skill_usage(self, skill_id: int, success: bool) -> None:
        """Increment use_count / success_count and recompute Wilson confidence."""
        try:
            with db_conn(self.db_path) as conn:
                row = conn.execute(
                    "SELECT use_count, success_count FROM skill WHERE id=?", (skill_id,)
                ).fetchone()
                if row is None:
                    return
                use_count = row["use_count"] + 1
                success_count = row["success_count"] + (1 if success else 0)
                confidence = _wilson_lower_bound(success_count, use_count)
                now = time.strftime("%Y-%m-%d %H:%M:%S")
                conn.execute(
                    "UPDATE skill SET use_count=?, success_count=?, confidence=?, last_used_at=? WHERE id=?",
                    (use_count, success_count, confidence, now, skill_id),
                )
        except Exception as e:
            logger.warning("update_skill_usage error: %s", e)

    # ── Discrepancy Pattern ───────────────────────────────────────────────────

    def log_discrepancy_pattern(
        self,
        pattern_text: str,
        tables_involved: List[str],
        category: str,
        example_query: str,
    ) -> None:
        """Upsert a reconciliation discrepancy pattern (merge duplicates by similarity)."""
        emb = _embed(pattern_text)
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        tables_str = ",".join(sorted(tables_involved))
        try:
            with db_conn(self.db_path) as conn:
                existing = conn.execute(
                    "SELECT id, embedding_json FROM discrepancy_pattern WHERE archived=0 LIMIT 100"
                ).fetchall()
            # find existing pattern with similarity > 0.85
            merge_id: Optional[int] = None
            for r in existing:
                if _cosine(emb, _deserialize(r["embedding_json"])) >= 0.85:
                    merge_id = r["id"]
                    break
            with db_conn(self.db_path) as conn:
                if merge_id is not None:
                    conn.execute(
                        "UPDATE discrepancy_pattern SET frequency=frequency+1, last_seen=?, example_query=? WHERE id=?",
                        (now, example_query, merge_id),
                    )
                else:
                    conn.execute(
                        """INSERT INTO discrepancy_pattern
                           (pattern_text, tables_involved, category, frequency,
                            last_seen, example_query, embedding_json, created_at)
                           VALUES (?,?,?,1,?,?,?,?)""",
                        (pattern_text, tables_str, category, now,
                         example_query, _serialize(emb), now),
                    )
        except Exception as e:
            logger.warning("log_discrepancy_pattern error: %s", e)

    def query_discrepancy_patterns(self, query: str, k: int = 3) -> List[Dict]:
        """Return top-k discrepancy patterns most relevant to the current query."""
        emb = _embed(query)
        try:
            with db_conn(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT * FROM discrepancy_pattern WHERE archived=0 ORDER BY frequency DESC LIMIT 100"
                ).fetchall()
            scored = []
            for r in rows:
                sim = _cosine(emb, _deserialize(r["embedding_json"]))
                score = sim * math.log1p(r["frequency"])
                scored.append((score, dict(r)))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [item for score, item in scored[:k] if score > 0.1]
        except Exception as e:
            logger.warning("query_discrepancy_patterns error: %s", e)
            return []

    # ── Skill review ──────────────────────────────────────────────────────────

    def submit_skill_review(
        self,
        trace_id: str,
        query: str,
        sql: str,
        answer: str,
        success: bool,
        schema_version: str = "",
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
                schema_version=schema_version,
            )
        except Exception as e:
            logger.warning("submit_skill_review error: %s", e)
            return {"skill_added": False, "reason": str(e)}

    # ── RAG Feedback ──────────────────────────────────────────────────────────

    def log_retrieval_feedback(
        self,
        trace_id: str,
        query: str,
        rag_sources: List[str],
        final_status: str,
    ) -> None:
        if not rag_sources:
            return
        success = 1 if final_status == "ok" else 0
        doc_ids_json = json.dumps(rag_sources, ensure_ascii=False)
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            with db_conn(self.db_path) as conn:
                conn.execute(
                    """INSERT INTO rag_feedback
                       (trace_id, query, doc_ids, final_status, success, created_at)
                       VALUES (?,?,?,?,?,?)""",
                    (trace_id, query, doc_ids_json, final_status, success, now),
                )
        except Exception as e:
            logger.warning("log_retrieval_feedback error: %s", e)

    def get_doc_quality(self, doc_id: str) -> float:
        try:
            with db_conn(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT success FROM rag_feedback WHERE doc_ids LIKE ?",
                    (f'%"{doc_id}"%',),
                ).fetchall()
            if not rows:
                return 0.5
            return sum(r["success"] for r in rows) / len(rows)
        except Exception:
            return 0.5

    # ── Evolution ─────────────────────────────────────────────────────────────

    def consolidate(self, min_cluster_size: int = 3) -> Dict[str, Any]:
        """Derive semantic rules from successful episodic cases."""
        try:
            with db_conn(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT intent, query FROM episodic_case WHERE archived=0 AND outcome=1 ORDER BY id DESC LIMIT 200"
                ).fetchall()
            intent_counts: Dict[str, int] = {}
            for r in rows:
                intent_counts[r["intent"]] = intent_counts.get(r["intent"], 0) + 1
            new_rules = 0
            with db_conn(self.db_path) as conn:
                existing = {r["rule"] for r in conn.execute("SELECT rule FROM semantic_rule WHERE archived=0").fetchall()}
                now = time.strftime("%Y-%m-%d %H:%M:%S")
                for intent, count in intent_counts.items():
                    if count >= min_cluster_size:
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
        """Archive old low-importance episodic cases and stale skills."""
        try:
            cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 30 * 86400))
            with db_conn(self.db_path) as conn:
                cur = conn.execute(
                    "UPDATE episodic_case SET archived=1 WHERE archived=0 AND importance<0.3 AND created_at<?",
                    (cutoff,),
                )
                archived_cases = cur.rowcount
                # archive skills with Wilson confidence < 0.2 and enough usage data
                cur2 = conn.execute(
                    "UPDATE skill SET archived=1 WHERE archived=0 AND use_count>=5 AND confidence<0.2"
                )
            return {"archived_cases": archived_cases, "archived_skills": cur2.rowcount}
        except Exception as e:
            logger.error("decay error: %s", e)
            return {"archived_cases": 0, "archived_skills": 0, "error": str(e)}


def _wilson_lower_bound(success: int, total: int, z: float = 1.96) -> float:
    if total == 0:
        return 0.6  # optimistic prior for new skills
    p = success / total
    denom = 1 + z * z / total
    num = p + z * z / (2 * total) - z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return round(max(0.0, num / denom), 4)
