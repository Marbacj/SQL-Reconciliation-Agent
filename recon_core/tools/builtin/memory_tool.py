"""
三层记忆系统 — Working / Episodic / Semantic Memory

Working Memory:  当前对账会话的上下文（SQL、中间结果、状态）
Episodic Memory: 历史对账案例（完整交互记录，通过 CaseStore 持久化）
Semantic Memory: 表结构知识（Schema、字段映射、业务术语 → 字段对应关系）
"""

import json
import os
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class MemoryEntry:
    """记忆条目"""
    key: str
    value: Any
    memory_type: str  # "working" | "episodic" | "semantic"
    importance: float = 0.5  # 0.0-1.0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    ttl: Optional[int] = None  # 秒，None=永不过期


class MemoryTool:
    """三层记忆工具

    Working Memory:   内存 dict，会话级别，关掉就丢
    Episodic Memory:  文件持久化，跨会话保留（委托给 CaseStore）
    Semantic Memory:  结构化知识库，手动/自动积累的表结构和术语映射
    """

    def __init__(self, store_dir: str = "memory_store"):
        self.store_dir = store_dir
        os.makedirs(store_dir, exist_ok=True)

        # Working Memory — 纯内存
        self._working: Dict[str, MemoryEntry] = {}

        # Episodic Memory — JSON 文件
        self._episodic_path = os.path.join(store_dir, "episodic.json")
        self._episodic: List[MemoryEntry] = self._load_episodic()

        # Semantic Memory — JSON 文件
        self._semantic_path = os.path.join(store_dir, "semantic.json")
        self._semantic: Dict[str, MemoryEntry] = self._load_semantic()

    # ── Working Memory（会话级） ──

    def working_set(self, key: str, value: Any):
        """写入工作记忆"""
        self._working[key] = MemoryEntry(key=key, value=value, memory_type="working")

    def working_get(self, key: str) -> Optional[Any]:
        """读取工作记忆"""
        entry = self._working.get(key)
        return entry.value if entry else None

    def working_all(self) -> Dict[str, Any]:
        """获取所有工作记忆"""
        return {k: v.value for k, v in self._working.items()}

    def working_clear(self):
        """清空工作记忆"""
        self._working.clear()

    # ── Episodic Memory（跨会话） ──

    def episodic_add(self, key: str, value: Any, importance: float = 0.5):
        """添加情景记忆"""
        entry = MemoryEntry(
            key=key, value=value, memory_type="episodic", importance=importance
        )
        self._episodic.append(entry)
        self._save_episodic()

    def episodic_search(self, query: str, top_k: int = 5) -> List[MemoryEntry]:
        """关键词搜索情景记忆"""
        query_lower = query.lower()
        scored = []
        for entry in self._episodic:
            content = str(entry.value).lower()
            score = sum(1 for word in query_lower.split() if word in content)
            score += entry.importance
            if score > 0:
                scored.append((score, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:top_k]]

    def episodic_stats(self) -> dict:
        return {"total": len(self._episodic)}

    # ── Semantic Memory（知识库） ──

    def semantic_set(self, key: str, value: Any, importance: float = 0.8):
        """写入语义记忆（术语→字段映射、表结构文档等）"""
        self._semantic[key] = MemoryEntry(
            key=key, value=value, memory_type="semantic", importance=importance
        )
        self._save_semantic()

    def semantic_get(self, key: str) -> Optional[Any]:
        entry = self._semantic.get(key)
        return entry.value if entry else None

    def semantic_search(self, query: str) -> List[MemoryEntry]:
        """搜索语义记忆"""
        query_lower = query.lower()
        results = []
        for key, entry in self._semantic.items():
            if query_lower in key.lower() or query_lower in str(entry.value).lower():
                results.append(entry)
        return results

    def semantic_all(self) -> Dict[str, Any]:
        return {k: v.value for k, v in self._semantic.items()}

    # ── 初始化语义知识（表结构） ──

    def bootstrap_schema_knowledge(self, db_path: str):
        """从 SQLite 数据库自动提取表结构作为语义记忆"""
        import sqlite3
        try:
            conn = sqlite3.connect(db_path)
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()

            for (table,) in tables:
                cols = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
                schema = {
                    "table": table,
                    "columns": [
                        {"name": c[1], "type": c[2], "nullable": not c[3]}
                        for c in cols
                    ]
                }
                self.semantic_set(f"schema:{table}", schema, importance=0.9)

                # 术语映射
                for col in cols:
                    col_name = col[1]
                    self.semantic_set(
                        f"term:{col_name}",
                        {"field": col_name, "table": table, "type": col[2]},
                        importance=0.7,
                    )

            conn.close()
            print(f"🧠 语义记忆已初始化: {len(tables)} 张表")
        except Exception as e:
            print(f"⚠️ 语义记忆初始化失败: {e}")

    # ── 持久化 ──

    def _load_episodic(self) -> List[MemoryEntry]:
        if os.path.exists(self._episodic_path):
            data = json.loads(open(self._episodic_path).read())
            return [MemoryEntry(**item) for item in data]
        return []

    def _save_episodic(self):
        with open(self._episodic_path, 'w') as f:
            json.dump(
                [e.__dict__ for e in self._episodic[-500:]],  # 保留最近 500 条
                f, ensure_ascii=False, indent=2
            )

    def _load_semantic(self) -> Dict[str, MemoryEntry]:
        if os.path.exists(self._semantic_path):
            data = json.loads(open(self._semantic_path).read())
            return {k: MemoryEntry(**v) for k, v in data.items()}
        return {}

    def _save_semantic(self):
        with open(self._semantic_path, 'w') as f:
            json.dump(
                {k: v.__dict__ for k, v in self._semantic.items()},
                f, ensure_ascii=False, indent=2
            )

    # ── 统计 ──

    def stats(self) -> dict:
        return {
            "working": len(self._working),
            "episodic": len(self._episodic),
            "semantic": len(self._semantic),
        }

    def summary(self) -> str:
        s = self.stats()
        return (
            f"Memory[工作={s['working']}, 情景={s['episodic']}, 语义={s['semantic']}]"
        )
