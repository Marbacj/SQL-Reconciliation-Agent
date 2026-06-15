"""CtxSnapshotStore: SQLite-backed persistent AgentContext config.

On multi-instance deployment the in-process _registry dict is instance-scoped.
This store persists the minimal serializable fields needed to reconstruct an
AgentContext on *any* instance that receives a request for a known trace_id.

TTL: snapshots are auto-purged after CTX_SNAPSHOT_TTL_S seconds (default 300).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

_DB_PATH = os.getenv("CTX_SNAPSHOT_DB", "data/ctx_snapshots.sqlite")
_TTL_S = int(os.getenv("CTX_SNAPSHOT_TTL_S", "300"))


@dataclass
class CtxSnapshot:
    trace_id: str
    session_id: str
    query: str
    db_path: str
    tenant_id: str = ""
    datasource_id: str = ""
    # LLM config – stored directly so any instance can reconstruct without auth layer
    llm_provider: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    llm_base_url: str = ""
    mode: str = "react"
    created_at: float = field(default_factory=time.time)


class CtxSnapshotStore:
    """Thread-safe SQLite store for CtxSnapshot objects."""

    def __init__(self, db_path: str = _DB_PATH) -> None:
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
        self._ensure_table()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_table(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ctx_snapshot (
                    trace_id     TEXT PRIMARY KEY,
                    payload      TEXT NOT NULL,
                    created_at   REAL NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ctx_created ON ctx_snapshot(created_at)"
            )

    def save(self, snap: CtxSnapshot) -> None:
        payload = json.dumps(asdict(snap), ensure_ascii=False)
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ctx_snapshot (trace_id, payload, created_at) VALUES (?,?,?)",
                (snap.trace_id, payload, snap.created_at),
            )

    def load(self, trace_id: str) -> Optional[CtxSnapshot]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT payload, created_at FROM ctx_snapshot WHERE trace_id=?",
                (trace_id,),
            ).fetchone()
        if row is None:
            return None
        age = time.time() - row["created_at"]
        if age > _TTL_S:
            self.delete(trace_id)
            logger.debug("ctx_store: snapshot %s expired (age=%.0fs)", trace_id, age)
            return None
        try:
            data = json.loads(row["payload"])
            return CtxSnapshot(**data)
        except Exception as e:
            logger.warning("ctx_store: failed to deserialize snapshot %s: %s", trace_id, e)
            return None

    def delete(self, trace_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM ctx_snapshot WHERE trace_id=?", (trace_id,))

    def cleanup_expired(self) -> int:
        cutoff = time.time() - _TTL_S
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM ctx_snapshot WHERE created_at < ?", (cutoff,)
            )
            return cur.rowcount


# Module-level singleton
_store: Optional[CtxSnapshotStore] = None


def get_store() -> CtxSnapshotStore:
    global _store
    if _store is None:
        _store = CtxSnapshotStore()
    return _store
