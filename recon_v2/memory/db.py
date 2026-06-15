"""SQLite persistence layer for MemoryStore."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path


_DDL = """
CREATE TABLE IF NOT EXISTS episodic_case (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id    TEXT NOT NULL,
    query       TEXT NOT NULL DEFAULT '',
    intent      TEXT NOT NULL DEFAULT '',
    sql         TEXT NOT NULL DEFAULT '',
    answer      TEXT NOT NULL DEFAULT '',
    outcome     INTEGER NOT NULL DEFAULT 0,
    importance  REAL NOT NULL DEFAULT 0.5,
    user_flag   INTEGER NOT NULL DEFAULT 0,
    promoted    INTEGER NOT NULL DEFAULT 0,
    archived    INTEGER NOT NULL DEFAULT 0,
    embedding_json TEXT NOT NULL DEFAULT '{}',
    schema_version TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now'))
);

CREATE TABLE IF NOT EXISTS skill (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    body            TEXT NOT NULL DEFAULT '',
    intent          TEXT NOT NULL DEFAULT '',
    confidence      REAL NOT NULL DEFAULT 0.6,
    use_count       INTEGER NOT NULL DEFAULT 0,
    success_count   INTEGER NOT NULL DEFAULT 0,
    archived        INTEGER NOT NULL DEFAULT 0,
    embedding_json  TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now')),
    last_used_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now'))
);

CREATE TABLE IF NOT EXISTS semantic_rule (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    rule        TEXT NOT NULL DEFAULT '',
    intent      TEXT NOT NULL DEFAULT '',
    confidence  REAL NOT NULL DEFAULT 0.5,
    archived    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now'))
);

CREATE TABLE IF NOT EXISTS rag_feedback (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id     TEXT NOT NULL,
    query        TEXT NOT NULL DEFAULT '',
    doc_ids      TEXT NOT NULL DEFAULT '[]',
    final_status TEXT NOT NULL DEFAULT '',
    success      INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now'))
);

CREATE TABLE IF NOT EXISTS discrepancy_pattern (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_text    TEXT NOT NULL DEFAULT '',
    tables_involved TEXT NOT NULL DEFAULT '',
    category        TEXT NOT NULL DEFAULT '',
    frequency       INTEGER NOT NULL DEFAULT 1,
    last_seen       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now')),
    example_query   TEXT NOT NULL DEFAULT '',
    embedding_json  TEXT NOT NULL DEFAULT '{}',
    archived        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now'))
);

CREATE INDEX IF NOT EXISTS idx_skill_confidence ON skill(confidence DESC);
CREATE INDEX IF NOT EXISTS idx_discrepancy_freq ON discrepancy_pattern(frequency DESC);
"""

# 列迁移：为旧库补列（列不存在时才执行，已存在时 OperationalError 被吞掉）
_MIGRATIONS = [
    "ALTER TABLE episodic_case ADD COLUMN schema_version TEXT NOT NULL DEFAULT ''",
]

# 依赖迁移列的索引必须在 migrations 之后才能建
_POST_MIGRATION_DDL = """
CREATE INDEX IF NOT EXISTS idx_episodic_schema_version ON episodic_case(schema_version);
"""


@contextmanager
def db_conn(db_path: str):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_DDL)
    for migration in _MIGRATIONS:
        try:
            conn.execute(migration)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists
    # 建依赖新列的索引（必须在 migration 之后）
    conn.executescript(_POST_MIGRATION_DDL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
