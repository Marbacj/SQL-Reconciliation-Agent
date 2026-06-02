"""AgentContext registry：

由于 LangGraph 的 GraphState 需要可序列化（checkpoint），而 AgentContext
持有 LLMGateway / ToolRegistry / Memory 等不可序列化对象，
我们用一个进程级 registry 通过 ctx_id 索引 AgentContext。
"""

from __future__ import annotations

import threading
from typing import Dict

from recon_v2.core.context import AgentContext

_registry: Dict[str, AgentContext] = {}
_lock = threading.RLock()


def register(ctx: AgentContext) -> str:
    with _lock:
        _registry[ctx.trace_id] = ctx
    return ctx.trace_id


def get(ctx_id: str) -> AgentContext:
    with _lock:
        if ctx_id not in _registry:
            raise KeyError(f"AgentContext {ctx_id} not found in registry")
        return _registry[ctx_id]


def remove(ctx_id: str) -> None:
    with _lock:
        _registry.pop(ctx_id, None)
