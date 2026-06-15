"""AgentContext：贯穿全流程的共享上下文。

所有 Node 和 Tool 都通过 ctx.<field> 访问能力，避免全局副作用。
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from recon_v2.core.budget import CostBudget
from recon_v2.infra.llm_gateway import LLMGateway


@dataclass
class AgentContext:
    """单次 query 的全局上下文。"""

    # ---- 身份 ----
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    query: str = ""

    # ---- 运行时状态 ----
    intent: Optional[str] = None
    confidence: float = 0.0
    mode: str = "react"  # "react" | "plan_solve"
    step_counter: int = 0

    # ---- 能力句柄 ----
    llm: Optional[LLMGateway] = None
    tools: Any = None  # ToolRegistry（避免循环导入）
    memory: Any = None  # MemoryStore（Stage 4）
    rag: Any = None  # HybridRetriever（Stage 3）
    tracer: Any = None  # OTel tracer

    # ---- 守门员 ----
    budget: CostBudget = field(default_factory=CostBudget)

    # ---- 数据源 ----
    db_path: str = field(default_factory=lambda: os.getenv("EVAL_DB_PATH", "data/eval_data.sqlite"))
    datasource_id: Optional[str] = None

    # ---- 租户（用于跨实例重建时恢复 LLM 配置）----
    tenant_id: str = "default"

    # ---- 自由扩展 ----
    extra: Dict[str, Any] = field(default_factory=dict)

    def step(self) -> None:
        self.step_counter += 1
        self.budget.add_step(1)

    def to_log_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "intent": self.intent,
            "confidence": self.confidence,
            "mode": self.mode,
            "step": self.step_counter,
            "budget": self.budget.snapshot(),
        }
