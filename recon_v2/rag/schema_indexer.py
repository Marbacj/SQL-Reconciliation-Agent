"""SchemaIndexer — Schema Linking 的离线索引构建 + 在线检索。

工作流程（两阶段）：

离线（定时任务 / 启动时）：
    1. SchemaInspector.inspect() → SchemaInfo（所有表结构）
    2. 每张表拼接文档文本（表名 + 描述 + 字段名 + 枚举值）
    3. Bag-of-tokens 向量化（与 retriever._tokenize 共用，无需额外依赖）
    4. 写入向量存储（Milvus 或 本地 JSON 降级）

在线（每次查询 act 节点）：
    query → 向量化 → cosine 相似度排序 → Top-K 相关表
    → 只把 Top-K 表的 schema 注入 LLM prompt（而非全量）

向量存储选择（SCHEMA_STORE 环境变量）：
    "milvus"  : 使用 Milvus（需安装 pymilvus，支持本地 Lite 和远程集群）
    "json"    : 本地 JSON 文件降级（默认，无需额外依赖）

生产升级路径：
    - 设置 SCHEMA_STORE=milvus + MILVUS_URI=http://milvus-server:19530
    - 将 Bag-of-tokens embedding 替换为 sentence-transformers dense embedding
    - 文档文本加入资产平台的业务注释，提升召回率

配置：
    SCHEMA_STORE       : 存储后端，"milvus" 或 "json"（默认 "json"）
    SCHEMA_INDEX_PATH  : JSON 模式的落盘路径（默认 data/schema_index.json）
    SCHEMA_TOP_K       : 检索返回的最大候选表数（默认 5）
    SCHEMA_MIN_SCORE   : 最低相关性阈值，低于此分数的表被过滤（默认 0.05）
    MILVUS_URI         : Milvus 连接地址（默认 ./data/milvus_schema.db 本地 Lite）
    MILVUS_TOKEN       : Milvus/Zilliz Cloud 认证 token（本地留空）
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from recon_v2.rag.retriever import _tokenize
from recon_v2.tools.schema_inspector import SchemaInfo, TableInfo, inspect as inspect_schema

logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────
_DEFAULT_INDEX_PATH = os.getenv("SCHEMA_INDEX_PATH", "data/schema_index.json")
_DEFAULT_TOP_K = int(os.getenv("SCHEMA_TOP_K", "5"))
_DEFAULT_MIN_SCORE = float(os.getenv("SCHEMA_MIN_SCORE", "0.05"))
_SCHEMA_STORE = os.getenv("SCHEMA_STORE", "json").lower()  # "milvus" 或 "json"


# ── 向量工具（与 memory/store.py 共用逻辑）──────────────

def _embed(text: str) -> Dict[str, float]:
    """Bag-of-tokens 归一化向量（可替换为 dense embedding）。"""
    tokens = _tokenize(text)
    if not tokens:
        return {}
    cnt: Dict[str, int] = {}
    for t in tokens:
        cnt[t] = cnt.get(t, 0) + 1
    norm = math.sqrt(sum(v * v for v in cnt.values()))
    return {k: v / norm for k, v in cnt.items()} if norm > 0 else {}


def _cosine(a: Dict[str, float], b: Dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    keys = set(a.keys()) & set(b.keys())
    return sum(a[k] * b[k] for k in keys)


# ── 文档构建 ──────────────────────────────────────────

def _build_table_doc(table: TableInfo) -> str:
    """把一张表的 schema 信息拼成适合向量化的文本。

    包含：表名 + 字段名 + 枚举值 + 隐式业务词扩展。
    生产环境可在此拼入资产平台的中文业务注释。
    """
    parts = [table.name]

    for col in table.columns:
        # 字段名（下划线拆分，让 "order_id" → "order id" 两个 token 都能匹配）
        col_tokens = col.name.replace("_", " ")
        parts.append(col_tokens)
        # 枚举值作为关键词（如 status: paid cancelled → 用户可能直接说"已支付"）
        if col.enum_values:
            parts.extend(col.enum_values)

    # 隐式业务词扩展（基于字段名规律，无需人工维护）
    # 含 amount → 扩展"金额 GMV 流水 收入"
    col_names = {c.name.lower() for c in table.columns}
    if any(k in col_names for k in ("amount", "price", "fee", "cost")):
        parts.extend(["金额", "gmv", "流水", "收入", "交易额"])
    if any(k in col_names for k in ("created_at", "create_time", "order_time")):
        parts.extend(["时间", "日期", "时段", "月份", "按天", "按月"])
    if any(k in col_names for k in ("status", "state")):
        parts.extend(["状态", "筛选"])
    if any(k in col_names for k in ("user_id", "member_id", "customer_id")):
        parts.extend(["用户", "会员", "客户"])

    return " ".join(parts)


# ── 索引数据结构 ──────────────────────────────────────

@dataclass
class TableEntry:
    table_name: str
    doc_text: str
    vector: Dict[str, float]
    column_names: List[str] = field(default_factory=list)
    enum_summary: Dict[str, List[str]] = field(default_factory=dict)
    built_at: float = field(default_factory=time.time)


@dataclass
class SchemaIndex:
    entries: List[TableEntry] = field(default_factory=list)
    db_path: str = ""
    dialect: str = "sqlite"
    built_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "db_path": self.db_path,
            "dialect": self.dialect,
            "built_at": self.built_at,
            "entries": [
                {
                    "table_name": e.table_name,
                    "doc_text": e.doc_text,
                    "vector": e.vector,
                    "column_names": e.column_names,
                    "enum_summary": e.enum_summary,
                    "built_at": e.built_at,
                }
                for e in self.entries
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SchemaIndex":
        entries = [
            TableEntry(
                table_name=e["table_name"],
                doc_text=e["doc_text"],
                vector=e["vector"],
                column_names=e.get("column_names", []),
                enum_summary=e.get("enum_summary", {}),
                built_at=e.get("built_at", 0.0),
            )
            for e in d.get("entries", [])
        ]
        return cls(
            entries=entries,
            db_path=d.get("db_path", ""),
            dialect=d.get("dialect", "sqlite"),
            built_at=d.get("built_at", 0.0),
        )


# ── SchemaIndexer ─────────────────────────────────────

class SchemaIndexer:
    """构建和持久化 Schema 向量索引。

    使用：
        indexer = SchemaIndexer(db_path="data/eval_data.sqlite")
        indexer.build()          # 全量重建
        indexer.save()           # 落盘
        indexer.load()           # 从文件加载（启动时调用）
    """

    def __init__(
        self,
        db_path: str = "",
        index_path: str = _DEFAULT_INDEX_PATH,
        adapter: Any = None,
    ):
        self.db_path = db_path
        self.index_path = index_path
        self.adapter = adapter
        self._index: Optional[SchemaIndex] = None

    def build(self) -> SchemaIndex:
        """全量重建索引：inspect → 文档 → 向量。"""
        t0 = time.time()
        logger.info("SchemaIndexer: building index for %s ...", self.db_path)

        schema_info: SchemaInfo = inspect_schema(db_path=self.db_path, adapter=self.adapter)
        entries: List[TableEntry] = []

        for table in schema_info.tables:
            doc = _build_table_doc(table)
            vec = _embed(doc)
            enum_summary = {
                c.name: c.enum_values
                for c in table.columns
                if c.enum_values
            }
            entries.append(TableEntry(
                table_name=table.name,
                doc_text=doc,
                vector=vec,
                column_names=[c.name for c in table.columns],
                enum_summary=enum_summary,
            ))

        self._index = SchemaIndex(
            entries=entries,
            db_path=self.db_path,
            dialect=schema_info.dialect,
        )
        elapsed = (time.time() - t0) * 1000
        logger.info(
            "SchemaIndexer: built %d tables in %.1fms",
            len(entries), elapsed,
        )
        return self._index

    def save(self, path: Optional[str] = None) -> None:
        """序列化索引到 JSON 文件。"""
        if self._index is None:
            logger.warning("SchemaIndexer: nothing to save, call build() first")
            return
        dest = path or self.index_path
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(self._index.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info("SchemaIndexer: saved index to %s", dest)

    def load(self, path: Optional[str] = None) -> bool:
        """从文件加载索引，返回是否成功。"""
        src = path or self.index_path
        if not os.path.exists(src):
            logger.info("SchemaIndexer: no index file at %s", src)
            return False
        try:
            with open(src, encoding="utf-8") as f:
                self._index = SchemaIndex.from_dict(json.load(f))
            logger.info(
                "SchemaIndexer: loaded %d tables from %s",
                len(self._index.entries), src,
            )
            return True
        except Exception as e:
            logger.warning("SchemaIndexer: load failed: %s", e)
            return False

    def build_and_save(self) -> SchemaIndex:
        """全量重建 + 落盘（定时任务入口）。

        根据 SCHEMA_STORE 环境变量决定写入 Milvus 或本地 JSON。
        """
        idx = self.build()

        if _SCHEMA_STORE == "milvus":
            self._save_to_milvus(idx)
        else:
            self.save()

        return idx

    def _save_to_milvus(self, idx: SchemaIndex) -> None:
        """将索引写入 Milvus（需安装 pymilvus）。"""
        try:
            from recon_v2.rag.milvus_store import get_milvus_store, is_milvus_available
            if not is_milvus_available():
                logger.warning("pymilvus not installed, falling back to JSON save")
                self.save()
                return
            store = get_milvus_store()
            store.drop_collection()       # 先清空旧数据
            store.upsert_batch(idx.entries)
            logger.info("SchemaIndexer: wrote %d entries to Milvus", len(idx.entries))
            # 同时保存 JSON 作为备份（Milvus 宕机时 fallback）
            self.save()
        except Exception as e:
            logger.error("SchemaIndexer: Milvus save failed: %s, falling back to JSON", e)
            self.save()

    @property
    def index(self) -> Optional[SchemaIndex]:
        return self._index

    def is_ready(self) -> bool:
        return self._index is not None and len(self._index.entries) > 0


# ── SchemaLinker ──────────────────────────────────────

class SchemaLinker:
    """在线 Schema Linking：把查询映射到最相关的 Top-K 张表。

    使用：
        linker = SchemaLinker(indexer)
        tables = linker.link(query="五月份GMV", k=3)
        # → ["orders"] 或 ["orders", "payments"]
    """

    def __init__(
        self,
        indexer: SchemaIndexer,
        top_k: int = _DEFAULT_TOP_K,
        min_score: float = _DEFAULT_MIN_SCORE,
    ):
        self.indexer = indexer
        self.top_k = top_k
        self.min_score = min_score

    def link(self, query: str, k: Optional[int] = None) -> List[str]:
        """返回与 query 最相关的表名列表（按相关性降序）。

        优先使用 Milvus（SCHEMA_STORE=milvus），失败则 fallback 到本地索引。
        若索引未就绪，返回空列表（调用方需 fallback 到全量 schema）。
        """
        k = k or self.top_k
        query_vec = _embed(query)
        if not query_vec:
            return []

        # 优先尝试 Milvus 检索
        if _SCHEMA_STORE == "milvus":
            milvus_result = self._link_via_milvus(query_vec, k)
            if milvus_result is not None:
                return milvus_result
            logger.debug("SchemaLinker: Milvus unavailable, falling back to local index")

        # 本地内存索引检索（fallback）
        if not self.indexer.is_ready():
            logger.debug("SchemaLinker: index not ready, skip linking")
            return []

        scores: List[Tuple[str, float]] = []
        for entry in self.indexer.index.entries:
            score = _cosine(query_vec, entry.vector)
            if score >= self.min_score:
                scores.append((entry.table_name, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        result = [name for name, _ in scores[:k]]
        logger.debug(
            "SchemaLinker: query=%r → top scores: %s",
            query[:50],
            [(n, f"{s:.3f}") for n, s in scores[:k]],
        )
        return result

    def _link_via_milvus(self, query_vec: Dict[str, float], k: int) -> Optional[List[str]]:
        """通过 Milvus 检索，返回表名列表；失败返回 None 触发 fallback。"""
        try:
            from recon_v2.rag.milvus_store import get_milvus_store, is_milvus_available
            if not is_milvus_available():
                return None
            store = get_milvus_store()
            hits = store.search(query_vec, k=k, min_score=self.min_score)
            return [name for name, _ in hits]
        except Exception as e:
            logger.debug("SchemaLinker: Milvus search failed: %s", e)
            return None

    def link_with_scores(self, query: str, k: Optional[int] = None) -> List[Tuple[str, float]]:
        """返回 (table_name, score) 列表，方便调试。"""
        if not self.indexer.is_ready():
            return []
        k = k or self.top_k
        query_vec = _embed(query)
        if not query_vec:
            return []
        scores = [
            (e.table_name, _cosine(query_vec, e.vector))
            for e in self.indexer.index.entries
        ]
        scores.sort(key=lambda x: x[1], reverse=True)
        return [(n, s) for n, s in scores[:k] if s >= self.min_score]


# ── 全局单例（供 act.py 使用）────────────────────────

_global_indexer: Optional[SchemaIndexer] = None
_global_linker: Optional[SchemaLinker] = None


def get_default_linker(
    db_path: str = "",
    index_path: str = _DEFAULT_INDEX_PATH,
    adapter: Any = None,
    auto_build: bool = True,
) -> SchemaLinker:
    """获取全局 SchemaLinker 单例。

    首次调用时：尝试加载索引文件，若不存在且 auto_build=True 则实时构建。
    """
    global _global_indexer, _global_linker

    if _global_linker is not None:
        return _global_linker

    _global_indexer = SchemaIndexer(
        db_path=db_path,
        index_path=index_path,
        adapter=adapter,
    )

    loaded = _global_indexer.load()
    if not loaded and auto_build and db_path:
        logger.info("SchemaIndexer: no cached index, building now...")
        _global_indexer.build_and_save()

    _global_linker = SchemaLinker(_global_indexer)
    return _global_linker


def rebuild_index(
    db_path: str = "",
    index_path: str = _DEFAULT_INDEX_PATH,
    adapter: Any = None,
) -> SchemaIndex:
    """强制重建索引并更新全局单例（用于 /admin/reindex 接口）。"""
    global _global_indexer, _global_linker

    indexer = SchemaIndexer(db_path=db_path, index_path=index_path, adapter=adapter)
    idx = indexer.build_and_save()

    _global_indexer = indexer
    _global_linker = SchemaLinker(indexer)
    return idx
