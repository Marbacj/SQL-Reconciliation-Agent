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
    from pydantic import BaseModel, Field

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
from apps.api.auth import (
    TenantInfo,
    create_token,
    create_user,
    get_current_tenant,
    get_tenant_model_config,
    get_user_by_username,
    upsert_tenant_model_config,
    verify_password,
)

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

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
    # 兼容旧表：按需添加 messages / title / updated / tenant_id 列
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    for col, ddl in [
        ("messages",  "TEXT NOT NULL DEFAULT '[]'"),
        ("title",     "TEXT NOT NULL DEFAULT ''"),
        ("updated",   "INTEGER NOT NULL DEFAULT 0"),
        ("tenant_id", "TEXT NOT NULL DEFAULT 'default'"),
    ]:
        if col not in cols:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} {ddl}")
    # 租户索引
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_tenant ON sessions(tenant_id)")
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
    datasource: Optional[str] = None  # 数据源名称，优先于 db_path（兼容旧字段）
    datasource_id: Optional[str] = None  # 新字段：与 datasource 等效，优先使用
    # 多轮对话澄清：客户端在收到 status="awaiting_clarification" 后，
    # 下一轮请求需把上一轮响应里的 clarify_context 原样传回
    clarify_context: Optional[Dict[str, Any]] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    role: str = "user"


class ModelConfigRequest(BaseModel):
    provider: str = ""
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.0


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

    from fastapi import Depends  # noqa: F811 — 在函数内显式引入，确保闭包可见

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
        _NO_CACHE_HEADERS = {
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }

        @app.get("/", include_in_schema=False)
        def _root():
            landing = os.path.join(_ui_dir, "landing.html")
            if os.path.exists(landing):
                return FileResponse(landing, headers=_NO_CACHE_HEADERS)
            return FileResponse(os.path.join(_ui_dir, "index.html"), headers=_NO_CACHE_HEADERS)

        # 将静态目录挂载到 /static，避免与 API 路由冲突
        # 同时注册常用 HTML 页面的顶层路由
        @app.get("/docs.html", include_in_schema=False)
        def _docs():
            return FileResponse(os.path.join(_ui_dir, "docs.html"), headers=_NO_CACHE_HEADERS)

        @app.get("/index.html", include_in_schema=False)
        def _console():
            return FileResponse(os.path.join(_ui_dir, "index.html"), headers=_NO_CACHE_HEADERS)

        @app.get("/landing.html", include_in_schema=False)
        def _landing():
            return FileResponse(os.path.join(_ui_dir, "landing.html"), headers=_NO_CACHE_HEADERS)

        @app.get("/favicon.svg", include_in_schema=False)
        def _favicon():
            return FileResponse(os.path.join(_ui_dir, "favicon.svg"), media_type="image/svg+xml")

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

    # ── FastAPI Depends 工厂 ────────────────────────────────────────────────
    _get_tenant = get_current_tenant()

    # ── 认证接口 ─────────────────────────────────────────────────────────────

    @app.post("/auth/login", tags=["auth"])
    def auth_login(req: LoginRequest):
        """用户登录，返回 JWT Token。"""
        user = get_user_by_username(req.username)
        if not user or not verify_password(req.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        token = create_token(user["tenant_id"], user["role"])
        return {
            "token": token,
            "tenant_id": user["tenant_id"],
            "username": user["username"],
            "role": user["role"],
        }

    @app.post("/auth/register", tags=["auth"])
    def auth_register(req: RegisterRequest, tenant: TenantInfo = Depends(_get_tenant)):
        """注册新用户（需要 admin 权限）。"""
        if tenant.role != "admin":
            raise HTTPException(status_code=403, detail="仅管理员可注册新用户")
        if get_user_by_username(req.username):
            raise HTTPException(status_code=400, detail="用户名已存在")
        user = create_user(req.username, req.password, req.role)
        return {"status": "ok", "tenant_id": user["tenant_id"]}

    # ── 租户模型配置接口 ──────────────────────────────────────────────────────

    @app.get("/tenants/me/model", tags=["tenant"])
    def get_my_model_config(tenant: TenantInfo = Depends(_get_tenant)):
        """获取当前租户的 LLM 配置。"""
        cfg = get_tenant_model_config(tenant.tenant_id)
        return {
            "tenant_id": cfg.tenant_id,
            "provider": cfg.provider,
            "model": cfg.model,
            "api_key": "••••••" if cfg.api_key else "",   # key 脱敏
            "base_url": cfg.base_url,
            "temperature": cfg.temperature,
        }

    @app.put("/tenants/me/model", tags=["tenant"])
    def update_my_model_config(req: ModelConfigRequest, tenant: TenantInfo = Depends(_get_tenant)):
        """更新当前租户的 LLM 配置。"""
        upsert_tenant_model_config(tenant.tenant_id, req.model_dump())
        return {"status": "ok", "tenant_id": tenant.tenant_id}


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
    def query(req: QueryRequest, tenant: TenantInfo = Depends(_get_tenant)):
        # ── 数据源解析：datasource_id > datasource > db_path > 默认 db ──
        from recon_v2.adapters import DataSourceRegistry, build_adapter
        from recon_v2.adapters.sqlite_adapter import SQLiteAdapter
        from recon_v2.tools.sql_runner import SQLRunnerTool

        # 统一 datasource_id（兼容旧字段 datasource）
        _ds_id = req.datasource_id or req.datasource

        _registry = DataSourceRegistry.get_instance()
        _adapter = None
        target_db = db_path  # fallback

        if _ds_id:
            try:
                _adapter = _registry.get_adapter(_ds_id)
                # db_path 仅用于 schema_indexer，datasource 模式保留默认值
                target_db = db_path
            except (KeyError, ValueError) as e:
                raise HTTPException(status_code=400, detail=str(e))
        elif req.db_path:
            target_db = req.db_path

        ctx = AgentContext(query=req.query, db_path=target_db)
        # 若指定了外部 adapter，覆盖默认 sql_runner
        if _adapter:
            _runner = SQLRunnerTool(adapter=_adapter)
            default_registry = build_default_registry(target_db)
            default_registry.register(_runner)
            ctx.tools = default_registry
        else:
            ctx.tools = build_default_registry(target_db)
        ctx.memory = memory
        ctx.rag = retriever
        # ── 注入租户 LLM 配置（优先租户自定义，fallback 到 env）──
        try:
            from recon_v2.infra.llm_gateway import LLMGateway
            tenant_cfg = get_tenant_model_config(tenant.tenant_id)
            ctx.llm = LLMGateway(
                provider=tenant_cfg.provider or None,
                model=tenant_cfg.model or None,
                api_key=tenant_cfg.api_key or None,
                base_url=tenant_cfg.base_url or None,
            )
            logger.debug(
                "LLM Gateway initialized for trace_id=%s tenant=%s: %s/%s",
                ctx.trace_id, tenant.tenant_id, ctx.llm.provider, ctx.llm.model
            )
        except Exception as e:
            logger.warning("LLM Gateway init failed (will use template fallback): %s", e)
        ctx_registry.register(ctx)
        try:
            graph = build_graph()
            cfg = {"configurable": {"thread_id": req.thread_id or ctx.trace_id}}
            t0 = time.time()
            # 构建初始 state，若客户端携带 clarify_context 则注入（多轮澄清续接）
            initial_state: dict = {
                "query": req.query,
                "db_path": target_db,
                "ctx_id": ctx.trace_id,
            }
            # 传递 datasource_id 到 GraphState 供 Schema Linking 使用
            if _ds_id:
                initial_state["datasource_id"] = _ds_id
            if req.clarify_context:
                initial_state["clarify_context"] = req.clarify_context
            out = graph.invoke(initial_state, config=cfg)
            latency = (time.time() - t0) * 1000
            resp: dict = {
                "trace_id": ctx.trace_id,
                "intent": out.get("intent"),
                "confidence": out.get("confidence"),
                "sql": out.get("sql"),
                "answer": out.get("answer"),
                "status": out.get("final_status"),
                "latency_ms": latency,
                "budget": ctx.budget.snapshot(),
            }
            # 若 Agent 等待用户澄清，把澄清上下文返回给客户端
            # 客户端下一轮需将 clarify_context 原样传回
            if out.get("final_status") == "awaiting_clarification":
                resp["clarify_context"] = out.get("clarify_context")
                resp["clarify_question"] = out.get("clarify_question")
            return JSONResponse(resp)
        finally:
            ctx_registry.remove(ctx.trace_id)


    # ── 数据源管理接口 ──────────────────────────────────────────────────────
    import asyncio as _asyncio

    from recon_v2.adapters import DataSourceConfig, DataSourceRegistry, build_adapter
    from recon_v2.rag.schema_indexer import index_datasource

    class DataSourceCreateRequest(BaseModel):
        name: str = Field(..., description="数据源唯一名称，如 prod_mysql")
        type: str = Field(..., description="sqlite | mysql | postgres")
        db_path: Optional[str] = Field(None, description="SQLite 文件路径")
        host: Optional[str] = None
        port: Optional[int] = None
        user: Optional[str] = None
        password: Optional[str] = None
        database: Optional[str] = None
        schema_name: Optional[str] = Field(None, description="PostgreSQL schema，默认 public")
        timeout: float = 10.0
        charset: Optional[str] = "utf8mb4"
        description: Optional[str] = None

    @app.get("/datasources", tags=["datasources"])
    def list_datasources():
        """列出所有已注册数据源（密码脱敏）。"""
        registry = DataSourceRegistry.get_instance()
        all_ds = registry.list_all()
        return {"datasources": all_ds, "total": len(all_ds)}

    @app.post("/datasources", status_code=201, tags=["datasources"])
    async def create_datasource(req: DataSourceCreateRequest):
        """注册新数据源，注册成功后异步触发 Schema 索引构建。"""
        registry = DataSourceRegistry.get_instance()
        if registry.get_entry(req.name) is not None:
            raise HTTPException(status_code=409, detail=f"数据源 '{req.name}' 已存在")
        cfg = DataSourceConfig(
            type=req.type,
            db_path=req.db_path,
            host=req.host,
            port=req.port,
            user=req.user,
            password=req.password,
            database=req.database,
            pg_schema=req.schema_name or "public",
            timeout=req.timeout,
            charset=req.charset,
            description=req.description,
        )
        try:
            registry.register(req.name, cfg)
            # 异步后台触发 Schema 索引
            _asyncio.create_task(index_datasource(req.name))
            return {"status": "registered", "id": req.name, "type": req.type}
        except (ValueError, ImportError) as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.delete("/datasources/{name}", status_code=204, tags=["datasources"])
    def delete_datasource(name: str):
        """删除已注册数据源。"""
        registry = DataSourceRegistry.get_instance()
        if not registry.unregister(name):
            raise HTTPException(status_code=404, detail=f"数据源 '{name}' 不存在")

    @app.get("/datasources/{name}/health", tags=["datasources"])
    def datasource_health(name: str):
        """探测数据源连通性，返回延迟或错误信息。"""
        registry = DataSourceRegistry.get_instance()
        entry = registry.get_entry(name)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"数据源 '{name}' 不存在")
        try:
            adapter = build_adapter(entry.config)
            return adapter.test_connection()
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @app.get("/datasources/{name}/status", tags=["datasources"])
    def datasource_status(name: str):
        """查询数据源的 Schema 索引状态。"""
        registry = DataSourceRegistry.get_instance()
        entry = registry.get_entry(name)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"数据源 '{name}' 不存在")
        return {
            "id": name,
            "index_status": entry.index_status,
            "table_count": entry.table_count,
            "index_error": entry.index_error,
        }

    @app.post("/datasources/{name}/reindex", tags=["datasources"])
    async def reindex_datasource(name: str):
        """手动触发指定数据源的 Schema 重新索引。"""
        registry = DataSourceRegistry.get_instance()
        if registry.get_entry(name) is None:
            raise HTTPException(status_code=404, detail=f"数据源 '{name}' 不存在")
        # 重置状态后异步触发
        registry.update_index_status(name, "pending")
        _asyncio.create_task(index_datasource(name))
        return {"status": "accepted", "message": f"'{name}' 的 Schema 重新索引已触发"}

    @app.post("/datasources/{name}/ping", tags=["datasources"])
    def ping_datasource(name: str):
        """测试指定数据源的连通性（兼容旧接口）。"""
        registry = DataSourceRegistry.get_instance()
        ok = registry.ping(name)
        return {"name": name, "reachable": ok}

    @app.patch("/datasources/{name}/enabled", tags=["datasources"])
    def set_datasource_enabled(name: str, enabled: bool = True):
        """启用或禁用数据源。"""
        registry = DataSourceRegistry.get_instance()
        if not registry.set_enabled(name, enabled):
            raise HTTPException(status_code=404, detail=f"数据源 '{name}' 不存在")
        return {"status": "ok", "name": name, "enabled": enabled}

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

    # ── Schema 预览 + 用户标注 ──────────────────────────────────────────────

    from recon_v2.rag.schema_indexer import get_annotations, set_annotation

    @app.get("/schema/preview", tags=["schema"])
    def schema_preview():
        """返回所有表的向量化文本（doc_text）及用户补注，供前端展示和标注。

        响应格式：
        {
          "tables": [
            {
              "table_name": "orders",
              "doc_text": "orders order id user id amount status 金额 gmv 流水",
              "user_note": "订单主表，记录每笔交易的状态和金额"  // 可为空字符串
            },
            ...
          ],
          "total": 5,
          "index_ready": true
        }
        """
        from recon_v2.rag.schema_indexer import get_default_linker as _get_linker
        try:
            linker = _get_linker(db_path=db_path, auto_build=False)
            annotations = get_annotations()
            if not linker.indexer.is_ready():
                return {"tables": [], "total": 0, "index_ready": False}
            tables = [
                {
                    "table_name": e.table_name,
                    "doc_text": e.doc_text,
                    "user_note": annotations.get(e.table_name, ""),
                    "columns": e.column_names,
                }
                for e in linker.indexer.index.entries
            ]
            return {"tables": tables, "total": len(tables), "index_ready": True}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    class AnnotationRequest(BaseModel):
        table_name: str
        user_note: str  # 空字符串表示清除该表注释

    @app.patch("/schema/annotation", tags=["schema"])
    def update_annotation(req: AnnotationRequest):
        """保存或清除单张表的用户中文补注。

        - user_note 非空 → 保存注释
        - user_note 为空 → 清除注释
        注释在下次 rebuild_index 时生效（注入向量文本）。
        """
        try:
            set_annotation(req.table_name, req.user_note)
            return {
                "status": "ok",
                "table_name": req.table_name,
                "user_note": req.user_note,
                "message": "注释已保存，重建索引后生效" if req.user_note.strip() else "注释已清除",
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/schema/annotation/reindex", tags=["schema"])
    def annotation_reindex():
        """保存注释后立即触发索引重建（一键应用注释）。"""
        try:
            t0 = time.time()
            idx = rebuild_index(db_path=db_path)
            elapsed = (time.time() - t0) * 1000
            return {
                "status": "ok",
                "tables": len(idx.entries),
                "elapsed_ms": round(elapsed, 1),
                "message": f"索引已重建，{len(idx.entries)} 张表的向量已更新",
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
    def list_sessions(limit: int = 100, tenant: TenantInfo = Depends(_get_tenant)):
        """返回当前租户最近 limit 条会话摘要（不含完整 messages），按 updated 倒序。"""
        with _sessions_conn() as conn:
            rows = conn.execute(
                "SELECT id, title, status, ts, updated FROM sessions WHERE tenant_id=? ORDER BY updated DESC LIMIT ?",
                (tenant.tenant_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    @app.get("/sessions/{session_id}", tags=["sessions"])
    def get_session(session_id: str, tenant: TenantInfo = Depends(_get_tenant)):
        """返回当前租户单个会话完整内容（含 messages）。"""
        with _sessions_conn() as conn:
            row = conn.execute(
                "SELECT id, title, messages, status, ts, updated FROM sessions WHERE id=? AND tenant_id=?",
                (session_id, tenant.tenant_id),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="session not found")
        d = dict(row)
        d["messages"] = _json.loads(d["messages"] or "[]")
        return d

    @app.post("/sessions", tags=["sessions"])
    def create_session(rec: SessionRecord, tenant: TenantInfo = Depends(_get_tenant)):
        """新建会话（归属当前租户）。"""
        now = int(time.time() * 1000)
        with _sessions_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sessions (id, title, messages, status, ts, updated, tenant_id) VALUES (?,?,?,?,?,?,?)",
                (rec.id, rec.title, _json.dumps(rec.messages), rec.status, rec.ts or now, now, tenant.tenant_id),
            )
        return {"status": "ok", "id": rec.id}

    @app.put("/sessions/{session_id}/messages", tags=["sessions"])
    def append_message(session_id: str, body: Dict[str, Any], tenant: TenantInfo = Depends(_get_tenant)):
        """向当前租户的会话追加一条消息，并更新 updated 时间。"""
        now = int(time.time() * 1000)
        with _sessions_conn() as conn:
            row = conn.execute(
                "SELECT messages FROM sessions WHERE id=? AND tenant_id=?", (session_id, tenant.tenant_id)
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="session not found")
            msgs = _json.loads(row["messages"] or "[]")
            msgs.append(body)
            conn.execute(
                "UPDATE sessions SET messages=?, updated=? WHERE id=? AND tenant_id=?",
                (_json.dumps(msgs), now, session_id, tenant.tenant_id),
            )
        return {"status": "ok", "count": len(msgs)}

    @app.delete("/sessions/{session_id}", tags=["sessions"])
    def delete_session(session_id: str, tenant: TenantInfo = Depends(_get_tenant)):
        """删除当前租户的指定会话。"""
        with _sessions_conn() as conn:
            conn.execute("DELETE FROM sessions WHERE id=? AND tenant_id=?", (session_id, tenant.tenant_id))
        return {"status": "ok"}

    @app.delete("/sessions", tags=["sessions"])
    def clear_sessions(tenant: TenantInfo = Depends(_get_tenant)):
        """清空当前租户的所有会话。"""
        with _sessions_conn() as conn:
            conn.execute("DELETE FROM sessions WHERE tenant_id=?", (tenant.tenant_id,))
        return {"status": "ok"}

    # ── Evolution 状态与触发 ────────────────────────────────────────────────

    @app.get("/evolution/status", tags=["evolution"])
    def evolution_status(tenant: TenantInfo = Depends(_get_tenant)):
        """返回当前 Memory / Skill 进化状态统计。"""
        from recon_v2.memory.db import db_conn as _db_conn
        try:
            db = memory.db_path
            with _db_conn(db) as conn:
                ep_count = conn.execute(
                    "SELECT COUNT(*) FROM episodic_case WHERE archived=0"
                ).fetchone()[0]
                ep_last = conn.execute(
                    "SELECT MAX(created_at) FROM episodic_case WHERE archived=0"
                ).fetchone()[0]
                sk_count = conn.execute(
                    "SELECT COUNT(*) FROM skill WHERE archived=0"
                ).fetchone()[0]
                sk_avg_conf = conn.execute(
                    "SELECT AVG(confidence) FROM skill WHERE archived=0"
                ).fetchone()[0]
                rule_count = conn.execute(
                    "SELECT COUNT(*) FROM semantic_rule WHERE archived=0"
                ).fetchone()[0]
            state = "running" if ep_count > 0 else "idle"
            return {
                "state": state,
                "episodic_cases": ep_count,
                "episodic_last_at": ep_last or "",
                "skills": sk_count,
                "skill_avg_confidence": round(float(sk_avg_conf or 0.0), 3),
                "semantic_rules": rule_count,
                "log": [
                    f"episodic: {ep_count} cases",
                    f"skills: {sk_count} (avg conf {sk_avg_conf:.2f})" if sk_count else "skills: 0",
                    f"rules: {rule_count}",
                ],
            }
        except Exception as e:
            logger.error("evolution/status error: %s", e)
            return {"state": "error", "error": str(e)}

    @app.post("/evolution/run", tags=["evolution"])
    def evolution_run(tenant: TenantInfo = Depends(_get_tenant)):
        """手动触发一次 consolidation + decay 进化周期。"""
        import threading

        def _run():
            try:
                c_result = memory.consolidate()
                d_result = memory.decay()
                logger.info(
                    "evolution/run finished: new_rules=%s archived=%s",
                    c_result.get("new_rules", 0),
                    d_result.get("archived", 0),
                )
            except Exception as e:
                logger.error("evolution/run error: %s", e)

        threading.Thread(target=_run, daemon=True).start()
        return {"status": "ok", "message": "进化任务已触发（后台执行）"}

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

    # ── Subagent 配置 CRUD ──────────────────────────────────────────────────
    import json as _json_agents

    _AGENTS_DB = os.getenv("AGENTS_DB_PATH", "data/agents.sqlite")

    def _ensure_agents_db():
        os.makedirs(os.path.dirname(_AGENTS_DB) if os.path.dirname(_AGENTS_DB) else ".", exist_ok=True)
        conn = sqlite3.connect(_AGENTS_DB)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                model       TEXT NOT NULL DEFAULT '',
                mode        TEXT NOT NULL DEFAULT '',
                enabled     INTEGER NOT NULL DEFAULT 1,
                system_prompt TEXT NOT NULL DEFAULT '',
                updated     INTEGER NOT NULL DEFAULT 0
            )
        """)
        # 默认内置 Subagent
        defaults = [
            ("sql_gen",  "SQL Generator",   "NL2SQL 生成核心，将自然语言查询转换为 SQL",   "DeepSeek-V3",  "plan_solve", 1, ""),
            ("reflect",  "Reflect Agent",   "汇总多轮查询结果，写入 Episodic Memory",      "DeepSeek-V3",  "reflect",    1, ""),
            ("reviewer", "Skill Reviewer",  "自动分析失败 Case，提炼 Semantic Rule",       "DeepSeek-V3",  "review",     0, ""),
        ]
        for row in defaults:
            conn.execute(
                "INSERT OR IGNORE INTO agents (id, name, description, model, mode, enabled, system_prompt, updated) VALUES (?,?,?,?,?,?,?,?)",
                (*row, int(time.time() * 1000)),
            )
        conn.commit()
        conn.close()

    _ensure_agents_db()

    @contextmanager
    def _agents_conn():
        conn = sqlite3.connect(_AGENTS_DB)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    class AgentConfig(BaseModel):
        id: Optional[str] = None
        name: str
        description: str = ""
        model: str = "DeepSeek-V3"
        mode: str = "plan_solve"
        enabled: bool = True
        system_prompt: str = ""

    @app.get("/agents", tags=["agents"])
    def list_agents():
        """返回所有 Subagent 配置列表。"""
        with _agents_conn() as conn:
            rows = conn.execute(
                "SELECT id, name, description, model, mode, enabled, system_prompt, updated FROM agents ORDER BY updated ASC"
            ).fetchall()
        return {"agents": [dict(r) for r in rows]}

    @app.get("/agents/{agent_id}", tags=["agents"])
    def get_agent(agent_id: str):
        """返回单个 Subagent 配置。"""
        with _agents_conn() as conn:
            row = conn.execute(
                "SELECT id, name, description, model, mode, enabled, system_prompt, updated FROM agents WHERE id=?",
                (agent_id,),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="agent not found")
        return dict(row)

    @app.post("/agents", tags=["agents"])
    def create_agent(cfg: AgentConfig):
        """新建自定义 Subagent 配置。"""
        agent_id = cfg.id or str(uuid.uuid4())[:8]
        now = int(time.time() * 1000)
        with _agents_conn() as conn:
            existing = conn.execute("SELECT id FROM agents WHERE id=?", (agent_id,)).fetchone()
            if existing:
                raise HTTPException(status_code=409, detail="agent id already exists")
            conn.execute(
                "INSERT INTO agents (id, name, description, model, mode, enabled, system_prompt, updated) VALUES (?,?,?,?,?,?,?,?)",
                (agent_id, cfg.name, cfg.description, cfg.model, cfg.mode, int(cfg.enabled), cfg.system_prompt, now),
            )
        logger.info("agents: created %s (%s)", agent_id, cfg.name)
        return {"status": "ok", "id": agent_id}

    @app.put("/agents/{agent_id}", tags=["agents"])
    def update_agent(agent_id: str, cfg: AgentConfig):
        """更新 Subagent 配置。"""
        now = int(time.time() * 1000)
        with _agents_conn() as conn:
            row = conn.execute("SELECT id FROM agents WHERE id=?", (agent_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="agent not found")
            conn.execute(
                "UPDATE agents SET name=?, description=?, model=?, mode=?, enabled=?, system_prompt=?, updated=? WHERE id=?",
                (cfg.name, cfg.description, cfg.model, cfg.mode, int(cfg.enabled), cfg.system_prompt, now, agent_id),
            )
        logger.info("agents: updated %s", agent_id)
        return {"status": "ok", "id": agent_id}

    @app.patch("/agents/{agent_id}/toggle", tags=["agents"])
    def toggle_agent(agent_id: str):
        """切换 Subagent 启用/停用状态。"""
        now = int(time.time() * 1000)
        with _agents_conn() as conn:
            row = conn.execute("SELECT id, enabled FROM agents WHERE id=?", (agent_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="agent not found")
            new_enabled = 0 if row["enabled"] else 1
            conn.execute(
                "UPDATE agents SET enabled=?, updated=? WHERE id=?",
                (new_enabled, now, agent_id),
            )
        logger.info("agents: toggled %s -> enabled=%s", agent_id, new_enabled)
        return {"status": "ok", "id": agent_id, "enabled": bool(new_enabled)}

    @app.delete("/agents/{agent_id}", tags=["agents"])
    def delete_agent(agent_id: str):
        """删除自定义 Subagent（内置 Subagent 不可删除）。"""
        builtin_ids = {"sql_gen", "reflect", "reviewer"}
        if agent_id in builtin_ids:
            raise HTTPException(status_code=403, detail="内置 Subagent 不可删除，可以停用")
        with _agents_conn() as conn:
            row = conn.execute("SELECT id FROM agents WHERE id=?", (agent_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="agent not found")
            conn.execute("DELETE FROM agents WHERE id=?", (agent_id,))
        logger.info("agents: deleted %s", agent_id)
        return {"status": "ok", "id": agent_id}

    return app


app = _build_app()


if __name__ == "__main__":
    if app is None:
        print("FastAPI not installed. `pip install fastapi uvicorn` first.")
    else:
        import uvicorn  # type: ignore

        uvicorn.run(app, host="0.0.0.0", port=8000)
