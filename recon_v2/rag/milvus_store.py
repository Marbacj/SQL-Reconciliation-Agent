"""MilvusVectorStore — Milvus 向量存储封装。

支持两种部署模式：
  - Milvus Lite（本地）：无需服务，存 .db 文件，适合开发/单机
  - Milvus 分布式服务：生产集群，通过 MILVUS_URI + MILVUS_TOKEN 连接

降级策略：
  若 pymilvus 未安装，自动降级到 JSON 文件存储（与 schema_indexer 默认行为一致）。

配置环境变量：
    MILVUS_URI    : 连接地址，默认 "./data/milvus_schema.db"（Milvus Lite 本地文件）
                   生产示例: "http://milvus-server:19530"
    MILVUS_TOKEN  : Zilliz Cloud 等托管服务的认证 token（本地部署留空）
    MILVUS_DIM    : 向量维度，默认 512（Bag-of-tokens 稀疏向量展开为 dense 时的维度）

使用：
    store = MilvusVectorStore()
    store.upsert_batch(entries)          # 批量写入
    results = store.search(query_vec, k=5)   # → [(table_name, score), ...]
    store.drop_collection()              # 清空（重建前调用）
"""

from __future__ import annotations

import logging
import math
import os
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 配置 ────────────────────────────────────────────────
_MILVUS_URI = os.getenv("MILVUS_URI", "./data/milvus_schema.db")
_MILVUS_TOKEN = os.getenv("MILVUS_TOKEN", "")
_COLLECTION_NAME = "schema_table_index"

# Bag-of-tokens 向量 hash 到 dense 维度时使用
# 如果换用 sentence-transformers，改为对应模型输出维度（如 768 / 1536）
_DENSE_DIM = int(os.getenv("MILVUS_DIM", "512"))

try:
    from pymilvus import (  # type: ignore
        CollectionSchema,
        DataType,
        FieldSchema,
        MilvusClient,
    )
    _HAS_MILVUS = True
except ImportError:
    _HAS_MILVUS = False
    logger.info("pymilvus not installed; MilvusVectorStore will be unavailable")


# ── 向量转换：稀疏 dict → dense list ─────────────────────

def _sparse_to_dense(sparse: Dict[str, float], dim: int = _DENSE_DIM) -> List[float]:
    """把 Bag-of-tokens 稀疏向量 hash 折叠为固定维度 dense 向量。

    原理：对每个 token 用 hash(token) % dim 确定落点，累加权重，最后 L2 归一化。
    这是一种简单的 Random Projection 降维，相似度保持性（Johnson-Lindenstrauss）近似有效。

    生产升级路径：换为 sentence-transformers.encode() 直接返回 dense embedding。
    """
    vec = [0.0] * dim
    for token, weight in sparse.items():
        idx = hash(token) % dim
        vec[idx] += weight

    # L2 归一化
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


# ── MilvusVectorStore ────────────────────────────────────

class MilvusVectorStore:
    """Milvus 向量存储，封装 collection 的 create/upsert/search/drop。

    表结构（collection schema）：
        id          INT64  (auto_id)
        table_name  VARCHAR(256)   — 业务主键
        vector      FLOAT_VECTOR   — dense 向量
        doc_text    VARCHAR(4096)  — 原始文档文本（调试用）
    """

    def __init__(
        self,
        uri: str = _MILVUS_URI,
        token: str = _MILVUS_TOKEN,
        collection_name: str = _COLLECTION_NAME,
        dim: int = _DENSE_DIM,
    ):
        if not _HAS_MILVUS:
            raise ImportError(
                "pymilvus is not installed. Run: pip install 'pymilvus>=2.4'"
            )
        self.collection_name = collection_name
        self.dim = dim

        # 自动创建本地目录（Milvus Lite 模式）
        if uri.endswith(".db"):
            os.makedirs(os.path.dirname(uri) or ".", exist_ok=True)

        self._client = MilvusClient(uri=uri, token=token or None)
        self._ensure_collection()

    # ── Collection 管理 ──────────────────────────────────

    def _ensure_collection(self) -> None:
        """若 collection 不存在则创建（幂等）。"""
        if self._client.has_collection(self.collection_name):
            return

        logger.info("MilvusVectorStore: creating collection '%s' dim=%d", self.collection_name, self.dim)
        self._client.create_collection(
            collection_name=self.collection_name,
            dimension=self.dim,
            primary_field_name="id",
            vector_field_name="vector",
            auto_id=True,
            metric_type="COSINE",
        )

    def drop_collection(self) -> None:
        """删除整个 collection（重建索引前调用）。"""
        if self._client.has_collection(self.collection_name):
            self._client.drop_collection(self.collection_name)
            logger.info("MilvusVectorStore: dropped collection '%s'", self.collection_name)
        # 重新创建
        self._ensure_collection()

    # ── 写入 ─────────────────────────────────────────────

    def upsert_batch(self, entries: list) -> int:
        """批量写入 TableEntry 列表，返回写入数量。

        entries: List[TableEntry]（来自 schema_indexer）
        """
        if not entries:
            return 0

        # 先删旧数据（按 table_name 过滤），再插入新数据
        # Milvus Lite 支持 delete by filter
        existing_names = [e.table_name for e in entries]
        try:
            filter_expr = " || ".join(
                f'table_name == "{n}"' for n in existing_names
            )
            self._client.delete(
                collection_name=self.collection_name,
                filter=filter_expr,
            )
        except Exception as e:
            logger.debug("MilvusVectorStore: delete old entries failed (ok for first insert): %s", e)

        data = []
        for entry in entries:
            dense_vec = _sparse_to_dense(entry.vector, self.dim)
            data.append({
                "table_name": entry.table_name[:256],
                "vector": dense_vec,
                "doc_text": entry.doc_text[:4096],
            })

        result = self._client.insert(
            collection_name=self.collection_name,
            data=data,
        )
        count = result.get("insert_count", len(data))
        logger.info("MilvusVectorStore: upserted %d entries", count)
        return count

    # ── 检索 ─────────────────────────────────────────────

    def search(
        self,
        query_sparse: Dict[str, float],
        k: int = 5,
        min_score: float = 0.05,
    ) -> List[Tuple[str, float]]:
        """向量检索，返回 [(table_name, score), ...] 按相关性降序。

        query_sparse: Bag-of-tokens 向量（与 schema_indexer._embed() 输出格式一致）
        """
        query_dense = _sparse_to_dense(query_sparse, self.dim)

        results = self._client.search(
            collection_name=self.collection_name,
            data=[query_dense],
            limit=k,
            output_fields=["table_name", "doc_text"],
            search_params={"metric_type": "COSINE"},
        )

        if not results or not results[0]:
            return []

        hits = []
        for hit in results[0]:
            score = hit.get("distance", 0.0)
            table_name = hit.get("entity", {}).get("table_name", "")
            if score >= min_score and table_name:
                hits.append((table_name, float(score)))

        return hits

    def count(self) -> int:
        """返回 collection 中的向量数量。"""
        try:
            stats = self._client.get_collection_stats(self.collection_name)
            return int(stats.get("row_count", 0))
        except Exception:
            return -1


# ── 工厂函数（供外部直接使用）──────────────────────────

_global_store: Optional[MilvusVectorStore] = None


def get_milvus_store(
    uri: str = _MILVUS_URI,
    token: str = _MILVUS_TOKEN,
    dim: int = _DENSE_DIM,
) -> MilvusVectorStore:
    """获取全局 MilvusVectorStore 单例。"""
    global _global_store
    if _global_store is None:
        _global_store = MilvusVectorStore(uri=uri, token=token, dim=dim)
    return _global_store


def is_milvus_available() -> bool:
    """检测 pymilvus 是否可用（用于 fallback 判断）。"""
    return _HAS_MILVUS
