"""
对账案例库 — 积累每次对账经验，实现 Agent 能力持续增长

核心设计：
  - 每次对账完成后自动保存案例（SQL 模式 + 差异结论）
  - 新查询时检索相似历史案例，作为 few-shot 注入 Prompt
  - 纯本地 JSON 存储，零外部依赖
"""

import json
import os
import re
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path


class CaseStore:
    """对账案例持久化存储 & 相似案例检索"""

    def __init__(self, store_dir: str = "recon_cases"):
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.store_dir / "_index.json"
        self._index = self._load_index()

    # ── 索引管理 ──

    def _load_index(self) -> List[dict]:
        if self._index_path.exists():
            return json.loads(self._index_path.read_text())
        return []

    def _save_index(self):
        self._index_path.write_text(json.dumps(self._index, ensure_ascii=False, indent=2))

    # ── 保存案例 ──

    def save(
        self,
        query: str,
        sql_a: str,
        sql_b: str,
        key_column: str,
        compare_columns: str,
        diff_summary: str,
        conclusion: str,
        raw_output: str = "",
    ) -> str:
        """保存一次对账案例，返回案例 ID"""
        case_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        case_file = self.store_dir / f"{case_id}.json"

        # 提取关键词（表名、列名、操作类型）
        keywords = self._extract_keywords(query, sql_a, sql_b)

        case = {
            "id": case_id,
            "timestamp": datetime.now().isoformat(),
            "query": query,
            "sql_a": sql_a,
            "sql_b": sql_b,
            "key_column": key_column,
            "compare_columns": compare_columns,
            "diff_summary": diff_summary,
            "conclusion": conclusion,
            "keywords": keywords,
        }

        case_file.write_text(json.dumps(case, ensure_ascii=False, indent=2))

        # 更新索引
        self._index.append({
            "id": case_id,
            "query": query[:100],
            "keywords": keywords,
            "timestamp": case["timestamp"],
        })
        self._save_index()

        return case_id

    # ── 检索相似案例 ──

    def find_similar(self, query: str, top_k: int = 3) -> List[dict]:
        """根据查询文本检索最相似的历史案例"""
        query_tokens = set(self._tokenize(query))

        scored = []
        for entry in self._index:
            case_tokens = set(entry.get("keywords", []))
            if not case_tokens:
                continue

            # Jaccard 相似度
            intersection = query_tokens & case_tokens
            union = query_tokens | case_tokens
            score = len(intersection) / len(union) if union else 0

            # 表名匹配加权
            table_bonus = sum(
                2.0 for t in intersection
                if t in ("live_gmv", "order_amount", "gmv", "订单", "直播")
            )
            score += table_bonus * 0.1

            if score > 0:
                scored.append((score, entry["id"]))

        scored.sort(reverse=True)
        top_ids = [case_id for _, case_id in scored[:top_k]]

        return [self._load_case(cid) for cid in top_ids]

    def _load_case(self, case_id: str) -> dict:
        case_file = self.store_dir / f"{case_id}.json"
        if case_file.exists():
            return json.loads(case_file.read_text())
        return {}

    # ── 工具方法 ──

    def _tokenize(self, text: str) -> List[str]:
        """中文+英文混合分词"""
        # 提取中文词组
        cn = re.findall(r"[\u4e00-\u9fff]{2,}", text)
        # 提取英文/下划线词
        en = re.findall(r"[a-zA-Z_]{2,}", text)
        return cn + en

    def _extract_keywords(self, query: str, sql_a: str, sql_b: str) -> List[str]:
        """从查询和 SQL 中提取关键词"""
        combined = f"{query} {sql_a} {sql_b}".lower()
        tokens = set(self._tokenize(combined))

        # 提取 SQL 中的表名和列名
        tables = set(re.findall(r"FROM\s+(\w+)", combined, re.IGNORECASE))
        tables |= set(re.findall(r"JOIN\s+(\w+)", combined, re.IGNORECASE))

        return sorted(tokens | tables)

    # ── 统计 ──

    def stats(self) -> dict:
        return {
            "total_cases": len(self._index),
            "latest": self._index[-1]["timestamp"] if self._index else None,
            "top_tables": self._top_tables(),
        }

    def _top_tables(self) -> List[str]:
        from collections import Counter
        table_counter = Counter()
        for entry in self._index:
            for kw in entry.get("keywords", []):
                if "_" in kw and not any("\u4e00" <= c <= "\u9fff" for c in kw):
                    table_counter[kw] += 1
        return [t for t, _ in table_counter.most_common(5)]

    def list_all(self) -> List[dict]:
        return sorted(self._index, key=lambda x: x["timestamp"], reverse=True)


# ── Few-Shot Prompt 构建器 ──

def build_few_shot_prompt(cases: List[dict], max_cases: int = 2) -> str:
    """将历史案例转换为 few-shot 示例注入 system prompt"""
    if not cases:
        return ""

    parts = ["\n## 历史对账经验（Few-Shot 参考）\n"]
    parts.append("以下是你过去完成的对账案例，可以参考其中的 SQL 模式和分析思路：\n")

    for i, case in enumerate(cases[:max_cases], 1):
        parts.append(f"### 案例 {i}：{case.get('query', '')[:80]}")
        parts.append(f"- **SQL A**: `{case.get('sql_a', '')}`")
        parts.append(f"- **SQL B**: `{case.get('sql_b', '')}`")
        parts.append(f"- **关联键**: `{case.get('key_column', '')}`")
        parts.append(f"- **比对列**: `{case.get('compare_columns', '')}`")
        parts.append(f"- **差异摘要**: {case.get('diff_summary', '')}")
        parts.append(f"- **结论**: {case.get('conclusion', '')}")
        parts.append("")

    parts.append("你可以参考以上案例的 SQL 结构和分析思路来完成当前的对账任务。\n")
    return "\n".join(parts)
