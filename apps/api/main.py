"""FastAPI 接入层 — POST /query / GET /trace / GET /metrics / GET /health。

最小可运行版（不依赖 fastapi 时优雅退化为 stub）。
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from fastapi import FastAPI, File, HTTPException, UploadFile
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel

    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False

from recon_v2.core.context import AgentContext
from recon_v2.infra.cost import get_default_tracker
from recon_v2.memory.store import MemoryStore
from recon_v2.orchestration import ctx_registry
from recon_v2.orchestration.graph import build_graph
from recon_v2.rag.retriever import get_default_retriever
from recon_v2.rag.schema_indexer import rebuild_index
from recon_v2.tools import build_default_registry

logger = logging.getLogger(__name__)

# ── Sessions SQLite store ─────────────────────────────────────────────────────
_SESSIONS_DB = os.getenv("SESSIONS_DB_PATH", "data/sessions.sqlite")


def _ensure_sessions_db():
    os.makedirs(os.path.dirname(_SESSIONS_DB) if os.path.dirname(_SESSIONS_DB) else ".", exist_ok=True)
    conn = sqlite3.connect(_SESSIONS_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id       TEXT PRIMARY KEY,
            title    TEXT NOT NULL DEFAULT '',
            messages TEXT NOT NULL DEFAULT '[]',
            status   TEXT NOT NULL DEFAULT 'ok',
            ts       INTEGER NOT NULL,
            updated  INTEGER NOT NULL
        )
    """)
    # 兼容旧表：按需添加 messages / title / updated 列
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    for col, ddl in [
        ("messages", "TEXT NOT NULL DEFAULT '[]'"),
        ("title",    "TEXT NOT NULL DEFAULT ''"),
        ("updated",  "INTEGER NOT NULL DEFAULT 0"),
    ]:
        if col not in cols:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} {ddl}")
    conn.commit()
    conn.close()


@contextmanager
def _sessions_conn():
    conn = sqlite3.connect(_SESSIONS_DB)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


_ensure_sessions_db()
# ─────────────────────────────────────────────────────────────────────────────


class QueryRequest(BaseModel):
    query: str
    db_path: Optional[str] = None
    thread_id: Optional[str] = None


class FeedbackRequest(BaseModel):
    trace_id: str
    query: str
    sql: Optional[str] = None
    answer: Optional[str] = None
    correct: bool
    intent: Optional[str] = None
    comment: Optional[str] = None


class SessionRecord(BaseModel):
    id: str
    title: str = ""
    messages: List[Dict[str, Any]] = []   # [{role, html, query}]
    status: str = "ok"
    ts: int
    updated: int = 0


def _build_app():
    if not _HAS_FASTAPI:
        return None

    app = FastAPI(
        title="SQL Reconciliation Agent v2",
        version="0.1.0",
        description="Industrial-grade NL2SQL reconciliation agent.",
    )

    # CORS — allow the static HTML to call the API from any origin
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Serve static UI — 挂载到根路径，所有文件直接用 /xxx.html 访问
    _ui_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "ui"))
    if os.path.isdir(_ui_dir):
        # 显式路由优先（必须在 mount 之前注册）
        @app.get("/", include_in_schema=False)
        def _root():
            landing = os.path.join(_ui_dir, "landing.html")
            if os.path.exists(landing):
                return FileResponse(landing)
            return FileResponse(os.path.join(_ui_dir, "index.html"))

        # 将静态目录挂载到 /static，避免与 API 路由冲突
        # 同时注册常用 HTML 页面的顶层路由
        @app.get("/docs.html", include_in_schema=False)
        def _docs():
            return FileResponse(os.path.join(_ui_dir, "docs.html"))

        @app.get("/index.html", include_in_schema=False)
        def _console():
            return FileResponse(os.path.join(_ui_dir, "index.html"))

        @app.get("/landing.html", include_in_schema=False)
        def _landing():
            return FileResponse(os.path.join(_ui_dir, "landing.html"))

        # 静态资源（CSS/JS/图片等）挂载到 /static
        app.mount("/static", StaticFiles(directory=_ui_dir), name="static")

    db_path = os.getenv("EVAL_DB_PATH", "data/eval_data.sqlite")
    memory = MemoryStore()
    retriever = get_default_retriever()

    # ── Schema 索引：启动时构建/加载，用于 Schema Linking ──
    import threading
    from recon_v2.rag.schema_indexer import get_default_linker

    def _init_schema_index():
        """后台线程构建 schema 向量索引，不阻塞 API 启动。"""
        try:
            get_default_linker(db_path=db_path, auto_build=True)
            logger.info("Schema index ready for db: %s", db_path)
        except Exception as e:
            logger.warning("Schema index init failed (non-fatal): %s", e)

    threading.Thread(target=_init_schema_index, daemon=True).start()

    @app.get("/health")
    def health():
        from recon_v2.rag.schema_indexer import get_default_linker as _get_linker
        try:
            linker = _get_linker(db_path=db_path, auto_build=False)
            schema_index_status = f"{len(linker.indexer.index.entries)} tables" if linker.indexer.is_ready() else "not ready"
        except Exception:
            schema_index_status = "unknown"
        return {
            "status": "ok",
            "deps": {
                "memory_db": memory.db_path,
                "retriever": "bm25-only (degraded)" if retriever.degraded else "hybrid",
                "schema_index": schema_index_status,
            },
        }

    @app.post("/query")
    def query(req: QueryRequest):
        target_db = req.db_path or db_path
        ctx = AgentContext(query=req.query, db_path=target_db)
        ctx.tools = build_default_registry(target_db)
        ctx.memory = memory
        ctx.rag = retriever
        ctx_registry.register(ctx)
        try:
            graph = build_graph()
            cfg = {"configurable": {"thread_id": req.thread_id or ctx.trace_id}}
            t0 = time.time()
            out = graph.invoke(
                {"query": req.query, "db_path": target_db, "ctx_id": ctx.trace_id},
                config=cfg,
            )
            latency = (time.time() - t0) * 1000
            return JSONResponse(
                {
                    "trace_id": ctx.trace_id,
                    "intent": out.get("intent"),
                    "confidence": out.get("confidence"),
                    "sql": out.get("sql"),
                    "answer": out.get("answer"),
                    "status": out.get("final_status"),
                    "latency_ms": latency,
                    "budget": ctx.budget.snapshot(),
                }
            )
        finally:
            ctx_registry.remove(ctx.trace_id)

    @app.post("/admin/reindex", tags=["admin"])
    def reindex(target_db: Optional[str] = None):
        """手动触发 Schema 索引重建（定时任务可调用此接口）。"""
        target = target_db or db_path
        try:
            t0 = time.time()
            idx = rebuild_index(db_path=target)
            elapsed = (time.time() - t0) * 1000
            return {
                "status": "ok",
                "tables": len(idx.entries),
                "db_path": target,
                "elapsed_ms": round(elapsed, 1),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── 用户反馈接口 ────────────────────────────────────────────────

    class FeedbackRequest(BaseModel):
        trace_id: str
        query: str
        sql: Optional[str] = None
        answer: Optional[str] = None
        correct: bool                # True=结果正确, False=结果有误
        intent: Optional[str] = None
        comment: Optional[str] = None  # 用户补充说明（可选）

    @app.post("/feedback", tags=["feedback"])
    def feedback(req: FeedbackRequest):
        """接收用户对查询结果的正误反馈，写入 Memory 触发自我修正。

        - correct=True  → outcome=1，importance 权重高（user_flag=1），强化正确 SQL
        - correct=False → outcome=0，importance 权重高，触发下次重新生成
        """
        outcome = 1 if req.correct else 0
        try:
            result = memory.write(
                trace_id=req.trace_id,
                query=req.query,
                intent=req.intent or "",
                sql=req.sql or "",
                answer=req.answer or "",
                outcome=outcome,
                user_flag=1,          # 用户主动反馈，权重最高
            )
            logger.info(
                "feedback: trace_id=%s correct=%s importance=%.2f promoted=%s",
                req.trace_id,
                req.correct,
                result["importance"],
                result["promoted"],
            )

            # 若标记为错误，触发 evolution 评审（异步，不阻塞响应）
            if not req.correct:
                import threading
                def _review():
                    try:
                        memory.submit_skill_review(
                            trace_id=req.trace_id,
                            query=req.query,
                            sql=req.sql or "",
                            answer=req.answer or "",
                            success=False,
                        )
                    except Exception as e:
                        logger.warning("feedback: skill review failed: %s", e)
                threading.Thread(target=_review, daemon=True).start()

            return {
                "status": "ok",
                "trace_id": req.trace_id,
                "importance": result["importance"],
                "promoted": result["promoted"],
                "message": "感谢反馈，已记录到 Memory 用于后续优化。" if req.correct
                           else "已标记为错误，将触发自动优化分析。",
            }
        except Exception as e:
            logger.error("feedback: write memory failed: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    # ── Sessions CRUD ────────────────────────────────────────────────
    import json as _json

    @app.get("/sessions", tags=["sessions"])
    def list_sessions(limit: int = 100):
        """返回最近 limit 条会话摘要（不含完整 messages），按 updated 倒序。"""
        with _sessions_conn() as conn:
            rows = conn.execute(
                "SELECT id, title, status, ts, updated FROM sessions ORDER BY updated DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    @app.get("/sessions/{session_id}", tags=["sessions"])
    def get_session(session_id: str):
        """返回单个会话完整内容（含 messages）。"""
        with _sessions_conn() as conn:
            row = conn.execute(
                "SELECT id, title, messages, status, ts, updated FROM sessions WHERE id=?",
                (session_id,),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="session not found")
        d = dict(row)
        d["messages"] = _json.loads(d["messages"] or "[]")
        return d

    @app.post("/sessions", tags=["sessions"])
    def create_session(rec: SessionRecord):
        """新建会话。"""
        now = int(time.time() * 1000)
        with _sessions_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sessions (id, title, messages, status, ts, updated) VALUES (?,?,?,?,?,?)",
                (rec.id, rec.title, _json.dumps(rec.messages), rec.status, rec.ts or now, now),
            )
        return {"status": "ok", "id": rec.id}

    @app.put("/sessions/{session_id}/messages", tags=["sessions"])
    def append_message(session_id: str, body: Dict[str, Any]):
        """向会话追加一条消息，并更新 updated 时间。"""
        now = int(time.time() * 1000)
        with _sessions_conn() as conn:
            row = conn.execute(
                "SELECT messages FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="session not found")
            msgs = _json.loads(row["messages"] or "[]")
            msgs.append(body)
            conn.execute(
                "UPDATE sessions SET messages=?, updated=? WHERE id=?",
                (_json.dumps(msgs), now, session_id),
            )
        return {"status": "ok", "count": len(msgs)}

    @app.delete("/sessions/{session_id}", tags=["sessions"])
    def delete_session(session_id: str):
        """删除指定会话。"""
        with _sessions_conn() as conn:
            conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        return {"status": "ok"}

    @app.delete("/sessions", tags=["sessions"])
    def clear_sessions():
        """清空所有会话。"""
        with _sessions_conn() as conn:
            conn.execute("DELETE FROM sessions")
        return {"status": "ok"}

    @app.get("/trace/{trace_id}")
    def trace(trace_id: str):
        summary = get_default_tracker().get_by_trace(trace_id)
        if summary is None:
            raise HTTPException(status_code=404, detail="trace not found")
        return summary.__dict__

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics():
        tracker = get_default_tracker()
        records = tracker.all_records
        live = sum(1 for r in records if r.source == "live")
        cache = sum(1 for r in records if r.source == "cache")
        tokens = sum(r.prompt_tokens + r.completion_tokens for r in records)
        cost = sum(r.cost_usd for r in records)
        lines = [
            "# HELP recon_v2_llm_calls Total LLM calls",
            "# TYPE recon_v2_llm_calls counter",
            f"recon_v2_llm_calls{{source=\"live\"}} {live}",
            f"recon_v2_llm_calls{{source=\"cache\"}} {cache}",
            "# HELP recon_v2_tokens_total Total tokens consumed",
            "# TYPE recon_v2_tokens_total counter",
            f"recon_v2_tokens_total {tokens}",
            "# HELP recon_v2_cost_usd_total Total LLM cost in USD",
            "# TYPE recon_v2_cost_usd_total counter",
            f"recon_v2_cost_usd_total {cost:.6f}",
        ]
        return "\n".join(lines) + "\n"

    # ── Knowledge Base CRUD ─────────────────────────────────────────────────
    _KB_DIR = Path(os.getenv("KB_DIR", "knowledge_base/table_docs"))

    def _safe_name(filename: str) -> str:
        """保证文件名安全（只允许字母数字下划线横线，后缀 .md）"""
        stem = re.sub(r"[^\w\-]", "_", Path(filename).stem)
        return stem + ".md"

    @app.get("/kb/docs", tags=["knowledge-base"])
    def kb_list():
        """列出知识库中的所有 Markdown 文档。"""
        _KB_DIR.mkdir(parents=True, exist_ok=True)
        docs = []
        for p in sorted(_KB_DIR.glob("*.md")):
            stat = p.stat()
            docs.append({
                "name": p.name,
                "size": stat.st_size,
                "updated": int(stat.st_mtime * 1000),
            })
        return {"docs": docs, "total": len(docs)}

    @app.get("/kb/docs/{filename}", tags=["knowledge-base"])
    def kb_get(filename: str):
        """读取单个文档内容。"""
        safe = _safe_name(filename)
        path = _KB_DIR / safe
        if not path.exists():
            raise HTTPException(status_code=404, detail="doc not found")
        return {"name": safe, "content": path.read_text(encoding="utf-8")}

    @app.post("/kb/docs", tags=["knowledge-base"])
    async def kb_upload(file: UploadFile = File(...)):
        """上传 Markdown 文档到知识库（已存在则覆盖）。"""
        if not file.filename or not file.filename.endswith(".md"):
            raise HTTPException(status_code=400, detail="只支持 .md 文件")
        _KB_DIR.mkdir(parents=True, exist_ok=True)
        safe = _safe_name(file.filename)
        content = await file.read()
        (_KB_DIR / safe).write_bytes(content)
        logger.info("kb_upload: saved %s (%d bytes)", safe, len(content))
        return {"status": "ok", "name": safe, "size": len(content)}

    @app.put("/kb/docs/{filename}", tags=["knowledge-base"])
    def kb_update(filename: str, body: Dict[str, Any]):
        """直接更新文档内容（body: {content: str}）。"""
        safe = _safe_name(filename)
        path = _KB_DIR / safe
        content = body.get("content", "")
        if not isinstance(content, str):
            raise HTTPException(status_code=400, detail="content 必须是字符串")
        _KB_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"status": "ok", "name": safe, "size": len(content)}

    @app.delete("/kb/docs/{filename}", tags=["knowledge-base"])
    def kb_delete(filename: str):
        """删除指定文档。"""
        safe = _safe_name(filename)
        path = _KB_DIR / safe
        if not path.exists():
            raise HTTPException(status_code=404, detail="doc not found")
        path.unlink()
        logger.info("kb_delete: removed %s", safe)
        return {"status": "ok", "name": safe}

    return app


app = _build_app()


if __name__ == "__main__":
    if app is None:
        print("FastAPI not installed. `pip install fastapi uvicorn` first.")
    else:
        import uvicorn  # type: ignore

        uvicorn.run(app, host="0.0.0.0", port=8000)
