"""LangGraph GraphState 定义。

GraphState 是 LangGraph 在 Node 之间传递的 dict-like 状态。
所有字段都需可序列化（用于 checkpointer 落盘）。

设计要点：
- AgentContext 通过 `_ctx_id` 在外部 registry 索引（避免直接放入 state，否则 LLM Gateway / Tools 等不可序列化）
- 中间结果（plan / observations / tool_calls）落 state，方便从 checkpoint 恢复
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Optional, TypedDict

import operator


class GraphState(TypedDict, total=False):
    # ---- 输入 ----
    query: str
    db_path: str

    # ---- 上下文索引 ----
    ctx_id: str

    # ---- Route 输出 ----
    intent: str
    confidence: float

    # ---- Clarify 输出 ----
    clarify_question: Optional[str]

    # ---- Plan 输出 ----
    plan_steps: List[str]
    mode: str  # "react" | "plan_solve"

    # ---- Act / Observe ----
    # 用 operator.add 让多次 act 累积
    tool_calls: Annotated[List[Dict[str, Any]], operator.add]
    observations: Annotated[List[Dict[str, Any]], operator.add]
    step_counter: int

    # ---- 并行子任务结果 ----
    # key = alias（如 "order_result"）, value = SQLRunnerOutput-like dict
    parallel_results: Dict[str, Any]

    # ---- Self-Correction（SQL 失败回溯）----
    # 记录最近一次失败的 SQL 及错误，用于 act 节点带上下文重试
    last_failed_sql: Optional[str]
    last_sql_error: Optional[str]
    retry_count: int  # 当前已重试次数，≥ MAX_SQL_RETRIES 时不再重试

    # ---- 终态 ----
    sql: str
    answer: str
    final_status: str  # "ok" | "clarify" | "rejected" | "budget_exceeded" | "error"
    error: Optional[str]

    # ---- 计费 ----
    token_cost: int
    cost_usd: float
