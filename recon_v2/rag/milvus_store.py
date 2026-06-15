"""MilvusVectorStore — Milvus 向量存储封装。

支持两种部署模式：
  - Milvus Lite（本地）：无需服务，存 .db 文件，适合开发/单机
  - Milvus 分布式服务：生产集群，通过 MILVUS_URI + MILVUS_TOKEN 连接

降级策略：
  若 pymilvus 未安装，自动降级到 JSON 文件存储（与 schema_indexer 默认行为一致）。

向量维度由 EMBED_BACKEND 决定（与 schema_indexer 保持一致）：
  dashscope     → 1024 维（text-embedding-v3 真实语义向量）
  bag_of_tokens → 512 维（hash 折叠降级向量，MILVUS_DIM 可覆盖）

配置环境变量：
    MILVUS_URI      : 连接地址，默认 "./data/milvus_schema.db"
    MILVUS_TOKEN    : Zilliz Cloud 认证 token（本地留空）
    EMBED_BACKEND   : "dashscope"（默认）或 "bag_of_tokens"
    MILVUS_DIM      : bag_of_tokens 模式下的降维维度（默认 512）
"""

from __future__ import annotations

import logging
import math
import os
from typing import Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

# ── 配置 ────────────────────────────────────────────────
_MILVUS_URI = os.getenv("MILVUS_URI", "./data/milvus_schema.db")
_MILVUS_TOKEN = os.getenv("MILVUS_TOKEN", "")
_COLLECTION_NAME = "schema_table_index"
_EMBED_BACKEND = os.getenv("EMBED_BACKEND", "dashscope").lower()

# 维度根据后端自动确定：dashscope=1024，bag_of_tokens=env(512)
_DASHSCOPE_DIM = 1024
_SPARSE_DIM = int(os.getenv("MILVUS_DIM", "512"))
_DENSE_DIM = _DASHSCOPE_DIM if _EMBED_BACKEND == "dashscope" else _SPARSE_DIM

try:
    from pymilvus import MilvusClient  # type: ignore
    _HAS_MILVUS = True
except ImportError:
    _HAS_MILVUS = False
    logger.info("pymilvus not installed; MilvusVectorStore will be unavailable")


# ── 向量归一化工具 ───────────────────────────────────────

def _l2_normalize(vec: List[float]) -> List[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm > 0 else vec


def _sparse_to_dense(sparse: Dict[str, float], dim: int = _SPARSE_DIM) -> List[float]:
    """Bag-of-tokens 稀疏向量 hash 折叠为固定维度 dense 向量（降级路径）。"""
    vec = [0.0] * dim
    for token, weight in sparse.items():
        idx = hash(token) % dim
        vec[idx] += weight
    return _l2_normalize(vec)


def _to_dense(vec: Union[List[float], Dict[str, float]], dim: int) -> List[float]:
    """统一向量格式：dense list 直接归一化；sparse dict 先折叠再归一化。"""
    if isinstance(vec, list):
        # 已是 dense（dashscope 输出），维度不匹配时拒绝（说明配置与索引不符）
        if len(vec) != dim:
            raise ValueError(
                f"Vector dim mismatch: got {len(vec)}, expected {dim}. "
                f"Check EMBED_BACKEND / MILVUS_DIM consistency."
            )
        return _l2_normalize(vec)
    # sparse dict → hash-fold
    return _sparse_to_dense(vec, dim)


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
            try:
                dense_vec = _to_dense(entry.vector, self.dim)
            except ValueError as e:
                logger.warning("MilvusVectorStore: skipping entry '%s': %s", entry.table_name, e)
                continue
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
        query_vec: Union[List[float], Dict[str, float]],
        k: int = 5,
        min_score: float = 0.05,
    ) -> List[Tuple[str, float]]:
        """向量检索，返回 [(table_name, score), ...] 按相关性降序。

        query_vec: dense List[float]（dashscope）或 sparse Dict[str,float]（bag_of_tokens），
                   与 schema_indexer._embed() 的输出格式一致。
        """
        try:
            query_dense = _to_dense(query_vec, self.dim)
        except ValueError as e:
            logger.warning("MilvusVectorStore.search: vector error: %s", e)
            return []

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
