"""Golden Set 数据 schema 与加载器。

每条 case 描述一次 NL2SQL 任务的标准答案，用于 Eval Harness 自动评估。
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class Difficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class IntentLabel(str, Enum):
    """与 Route Node 的 intent 枚举保持一致。"""

    SIMPLE_QUERY = "simple_query"
    MULTI_TABLE_JOIN = "multi_table_join"
    TIME_WINDOW_RECON = "time_window_recon"
    NUMERIC_DIFF = "numeric_diff"
    BOUNDARY_EDGE = "boundary_edge"


class GoldenCase(BaseModel):
    """单条评测样例。"""

    id: str = Field(..., description="全局唯一 ID，建议 kebab-case")
    query: str = Field(..., description="自然语言查询")
    intent_label: Optional[IntentLabel] = Field(default=None, description="期望命中的 intent（LeetCode 题目可为空）")
    difficulty: Difficulty = Field(...)
    expected_sql: str = Field(..., description="参考 SQL（结果集 hash 用）")
    expected_result_summary: str = Field(..., description="参考自然语言答案，用于 LLM-as-Judge")
    # 可选：检索质量评估专用
    retrieval_label: Optional[List[str]] = Field(
        default=None,
        description="RAG 评估应被召回的 doc id 列表（30 条带标签的子集填）",
    )
    # 可选：用于过滤/分群分析
    tags: List[str] = Field(default_factory=list)
    notes: str = Field(default="", description="人工备注，不参与评测")

    @field_validator("expected_sql")
    @classmethod
    def _strip_sql(cls, v: str) -> str:
        return v.strip().rstrip(";")


def load_golden_set(path: str | Path) -> List[GoldenCase]:
    """从 jsonl 文件加载 GoldenCase 列表。

    每行一个 JSON 对象，空行/`#` 开头注释行忽略。
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Golden set not found: {path}")

    cases: List[GoldenCase] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                obj = json.loads(line)
                cases.append(GoldenCase(**obj))
            except Exception as e:
                raise ValueError(f"Invalid case at line {line_no}: {e}") from e

    return cases


def stats(cases: List[GoldenCase]) -> dict:
    """统计 Golden Set 构成（用于 coverage 守门）。"""
    intent_count: dict = {}
    diff_count: dict = {}
    for c in cases:
        if c.intent_label is not None:
            intent_count[c.intent_label.value] = intent_count.get(c.intent_label.value, 0) + 1
        diff_count[c.difficulty.value] = diff_count.get(c.difficulty.value, 0) + 1
    return {
        "total": len(cases),
        "by_intent": intent_count,
        "by_difficulty": diff_count,
        "with_retrieval_label": sum(1 for c in cases if c.retrieval_label),
    }
