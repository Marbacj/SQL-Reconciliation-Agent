"""FastAPI 接入层 — POST /query / GET /trace / GET /metrics / GET /health。

最小可运行版（不依赖 fastapi 时优雅退化为 stub）。
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any, Dict, Optional

try:
    from fastapi import FastAPI, HTTPException
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

    # Serve static UI
    _ui_dir = os.path.join(os.path.dirname(__file__), "..", "ui")
    if os.path.isdir(_ui_dir):
        app.mount("/ui", StaticFiles(directory=_ui_dir, html=True), name="ui")

        @app.get("/", include_in_schema=False)
        def _root():
            # 根路由指向 landing page（官网首页）
            landing = os.path.join(_ui_dir, "landing.html")
            if os.path.exists(landing):
                return FileResponse(landing)
            return FileResponse(os.path.join(_ui_dir, "index.html"))

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

    class QueryRequest(BaseModel):
        query: str
        db_path: Optional[str] = None
        thread_id: Optional[str] = None

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

    return app


app = _build_app()


if __name__ == "__main__":
    if app is None:
        print("FastAPI not installed. `pip install fastapi uvicorn` first.")
    else:
        import uvicorn  # type: ignore

        uvicorn.run(app, host="0.0.0.0", port=8000)
