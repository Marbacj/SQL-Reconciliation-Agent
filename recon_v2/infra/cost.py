"""Cost Tracker：按 trace_id 累计每次 LLM 调用的 token / 美元成本。"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class CallRecord:
    trace_id: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: float
    source: str = "live"  # "live" | "cache"


@dataclass
class TraceSummary:
    trace_id: str
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    cache_hits: int = 0


class CostTracker:
    def __init__(self):
        self._lock = threading.RLock()
        self._records: List[CallRecord] = []
        self._by_trace: Dict[str, TraceSummary] = {}

    def record(self, rec: CallRecord) -> None:
        with self._lock:
            self._records.append(rec)
            s = self._by_trace.setdefault(rec.trace_id, TraceSummary(trace_id=rec.trace_id))
            s.calls += 1
            s.prompt_tokens += rec.prompt_tokens
            s.completion_tokens += rec.completion_tokens
            s.total_tokens = s.prompt_tokens + s.completion_tokens
            s.cost_usd += rec.cost_usd
            if rec.source == "cache":
                s.cache_hits += 1

    def get_by_trace(self, trace_id: str) -> Optional[TraceSummary]:
        with self._lock:
            return self._by_trace.get(trace_id)

    def reset(self) -> None:
        with self._lock:
            self._records.clear()
            self._by_trace.clear()

    @property
    def all_records(self) -> List[CallRecord]:
        with self._lock:
            return list(self._records)


# 进程级单例（也可以注入到 AgentContext，避免全局副作用）
_default_tracker = CostTracker()


def get_default_tracker() -> CostTracker:
    return _default_tracker
