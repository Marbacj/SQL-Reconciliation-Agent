"""AgentContext registry with SQLite-backed cross-instance support.

Two-layer lookup:
  1. In-process dict (fast path, same instance)
  2. CtxSnapshotStore (SQLite, shared across instances via shared volume / NFS)
     → rebuilds AgentContext from stored config on cache miss

Register also persists a CtxSnapshot so other instances can rebuild.
Remove cleans up both layers.
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, Optional

from recon_v2.core.context import AgentContext
from recon_v2.orchestration.ctx_store import CtxSnapshot, get_store

logger = logging.getLogger(__name__)

_registry: Dict[str, AgentContext] = {}
_lock = threading.RLock()


def register(ctx: AgentContext) -> str:
    with _lock:
        _registry[ctx.trace_id] = ctx

    # Persist snapshot for cross-instance recovery
    try:
        snap = _snapshot_from_ctx(ctx)
        get_store().save(snap)
    except Exception as e:
        logger.warning("ctx_registry: failed to persist snapshot %s: %s", ctx.trace_id, e)

    return ctx.trace_id


def get(ctx_id: str) -> AgentContext:
    with _lock:
        if ctx_id in _registry:
            return _registry[ctx_id]

    # Cache miss: try to rebuild from snapshot store
    logger.info("ctx_registry: local miss for %s, attempting snapshot rebuild", ctx_id)
    try:
        snap = get_store().load(ctx_id)
        if snap is not None:
            ctx = _rebuild_from_snapshot(snap)
            with _lock:
                _registry[ctx_id] = ctx
            logger.info("ctx_registry: rebuilt ctx %s from snapshot", ctx_id)
            return ctx
    except Exception as e:
        logger.warning("ctx_registry: snapshot rebuild failed for %s: %s", ctx_id, e)

    raise KeyError(f"AgentContext {ctx_id} not found in registry or snapshot store")


def remove(ctx_id: str) -> None:
    with _lock:
        _registry.pop(ctx_id, None)
    try:
        get_store().delete(ctx_id)
    except Exception as e:
        logger.debug("ctx_registry: snapshot delete failed for %s: %s", ctx_id, e)


# ── Snapshot helpers ──────────────────────────────────────────────────────────

def _snapshot_from_ctx(ctx: AgentContext) -> CtxSnapshot:
    llm_provider = llm_model = llm_api_key = llm_base_url = ""
    if ctx.llm is not None:
        llm_provider = getattr(ctx.llm, "provider", "") or ""
        llm_model = getattr(ctx.llm, "model", "") or ""
        llm_api_key = getattr(ctx.llm, "api_key", "") or ""
        llm_base_url = getattr(ctx.llm, "base_url", "") or ""
    return CtxSnapshot(
        trace_id=ctx.trace_id,
        session_id=ctx.session_id,
        query=ctx.query,
        db_path=ctx.db_path,
        tenant_id=ctx.tenant_id,
        datasource_id=ctx.datasource_id or "",
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_api_key=llm_api_key,
        llm_base_url=llm_base_url,
        mode=ctx.mode,
    )


def _rebuild_from_snapshot(snap: CtxSnapshot) -> AgentContext:
    """Reconstruct a full AgentContext from a CtxSnapshot."""
    from recon_v2.infra.llm_gateway import LLMGateway
    from recon_v2.memory.store import MemoryStore
    from recon_v2.rag.retriever import get_default_retriever
    from recon_v2.tools import build_default_registry

    ctx = AgentContext(
        trace_id=snap.trace_id,
        session_id=snap.session_id,
        query=snap.query,
        db_path=snap.db_path,
        tenant_id=snap.tenant_id,
        datasource_id=snap.datasource_id or None,
        mode=snap.mode,
    )

    # Reconstruct LLM
    if snap.llm_provider or snap.llm_model:
        try:
            ctx.llm = LLMGateway(
                provider=snap.llm_provider or None,
                model=snap.llm_model or None,
                api_key=snap.llm_api_key or None,
                base_url=snap.llm_base_url or None,
            )
        except Exception as e:
            logger.warning("ctx_registry: LLM rebuild failed for %s: %s", snap.trace_id, e)

    # Reconstruct tools / memory / rag
    try:
        ctx.tools = build_default_registry(snap.db_path)
    except Exception as e:
        logger.warning("ctx_registry: tools rebuild failed: %s", e)

    try:
        ctx.memory = MemoryStore()
    except Exception as e:
        logger.warning("ctx_registry: memory rebuild failed: %s", e)

    try:
        ctx.rag = get_default_retriever()
    except Exception as e:
        logger.warning("ctx_registry: rag rebuild failed: %s", e)

    return ctx
