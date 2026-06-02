"""CostBudget：守门员，控制单次 trace 的 token / 时间 / step 上限。"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field


@dataclass
class CostBudget:
    max_tokens: int = field(default_factory=lambda: int(os.getenv("BUDGET_MAX_TOKENS", "50000")))
    max_seconds: float = field(default_factory=lambda: float(os.getenv("BUDGET_MAX_SECONDS", "120")))
    max_steps: int = field(default_factory=lambda: int(os.getenv("BUDGET_MAX_STEPS", "15")))

    # 运行时累计
    tokens_used: int = 0
    steps_used: int = 0
    _started_at: float = field(default_factory=time.time)

    def add_tokens(self, n: int) -> None:
        self.tokens_used += max(0, int(n))

    def add_step(self, n: int = 1) -> None:
        self.steps_used += n

    def elapsed(self) -> float:
        return time.time() - self._started_at

    def exceeded(self) -> bool:
        return (
            self.tokens_used > self.max_tokens
            or self.steps_used > self.max_steps
            or self.elapsed() > self.max_seconds
        )

    def reason(self) -> str:
        if self.tokens_used > self.max_tokens:
            return f"tokens {self.tokens_used} > {self.max_tokens}"
        if self.steps_used > self.max_steps:
            return f"steps {self.steps_used} > {self.max_steps}"
        if self.elapsed() > self.max_seconds:
            return f"elapsed {self.elapsed():.1f}s > {self.max_seconds}s"
        return "ok"

    def snapshot(self) -> dict:
        return {
            "tokens_used": self.tokens_used,
            "steps_used": self.steps_used,
            "elapsed_s": round(self.elapsed(), 2),
            "exceeded": self.exceeded(),
            "reason": self.reason() if self.exceeded() else "ok",
        }
