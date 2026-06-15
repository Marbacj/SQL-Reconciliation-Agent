"""KB 文档向量存储：Dashscope text-embedding-v3 + Milvus。

与 milvus_store.py（schema 表索引）完全分离：
  - 独立 collection：kb_docs
  - 字段：doc_id（业务主键）+ vector（1024 维）+ text（原文）
  - embedding：Dashscope text-embedding-v3，稳定、跨进程一致
  - 降级：pymilvus 未装 或 DASHSCOPE_API_KEY 未配置 → 返回 unavailable

用法：
    store = KBVectorStore()
    store.index(chunks)                  # 离线建索引
    hits = store.search("对账差异", k=5) # 在线检索 → [(doc_id, score), ...]
"""

from __future__ import annotations

import logging
import math
import os
import time
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

_COLLECTION_NAME = "kb_docs"
_DIM = 1024                        # text-embedding-v3 固定输出维度
_MILVUS_URI = os.getenv("MILVUS_URI", "./data/milvus_kb.db")
_MILVUS_TOKEN = os.getenv("MILVUS_TOKEN", "")
_DASHSCOPE_KEY = os.getenv("DASHSCOPE_API_KEY", "")

# ── 依赖检测 ──────────────────────────────────────────────────────────────────

try:
    from pymilvus import MilvusClient  # type: ignore
    _HAS_MILVUS = True
except ImportError:
    _HAS_MILVUS = False

try:
    import dashscope  # type: ignore  # noqa: F401
    from dashscope import TextEmbedding  # type: ignore
    _HAS_DASHSCOPE = True
except ImportError:
    _HAS_DASHSCOPE = False


def is_available() -> bool:
    return _HAS_MILVUS and _HAS_DASHSCOPE and bool(_DASHSCOPE_KEY)


# ── Embedding ─────────────────────────────────────────────────────────────────

_embed_cache: dict[str, List[float]] = {}


def _embed(text: str, api_key: str = _DASHSCOPE_KEY) -> Optional[List[float]]:
    """调用 Dashscope text-embedding-v3，带内存缓存，失败返回 None。"""
    if not _HAS_DASHSCOPE or not api_key:
        return None
    cache_key = text[:512]
    if cache_key in _embed_cache:
        return _embed_cache[cache_key]
    try:
        resp = TextEmbedding.call(
            model=TextEmbedding.Models.text_embedding_v3,
            input=text[:2048],     # API 单次最大 2048 字符
            api_key=api_key,
        )
        if resp.status_code != 200:
            logger.warning("Dashscope embed failed: %s %s", resp.code, resp.message)
            return None
        vec: List[float] = resp.output["embeddings"][0]["embedding"]
        _embed_cache[cache_key] = vec
        return vec
    except Exception as e:
        logger.warning("Dashscope embed exception: %s", e)
        return None


def _embed_batch(texts: List[str], api_key: str = _DASHSCOPE_KEY, batch_size: int = 10) -> List[Optional[List[float]]]:
    """批量 embedding，每批最多 25 条（API 限制），带进度日志。"""
    results: List[Optional[List[float]]] = [None] * len(texts)
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        # 检查缓存
        need_api: List[int] = []
        for j, t in enumerate(batch):
            ck = t[:512]
            if ck in _embed_cache:
                results[i + j] = _embed_cache[ck]
            else:
                need_api.append(j)

        if need_api:
            batch_input = [batch[j][:2048] for j in need_api]
            try:
                resp = TextEmbedding.call(
                    model=TextEmbedding.Models.text_embedding_v3,
                    input=batch_input,
                    api_key=api_key,
                )
                if resp.status_code == 200:
                    for k, emb in enumerate(resp.output["embeddings"]):
                        j = need_api[k]
                        vec = emb["embedding"]
                        _embed_cache[batch[j][:512]] = vec
                        results[i + j] = vec
                else:
                    logger.warning("Dashscope batch embed failed: %s", resp.message)
            except Exception as e:
                logger.warning("Dashscope batch embed exception: %s", e)

        logger.info("kb_vector_store: embedded %d/%d", min(i + batch_size, len(texts)), len(texts))
        if i + batch_size < len(texts):
            time.sleep(0.3)     # 避免触发 QPS 限制

    return results


# ── KBVectorStore ─────────────────────────────────────────────────────────────

class KBVectorStore:
    """KB 文档向量存储，collection = kb_docs，dim = 1024。"""

    def __init__(
        self,
        uri: str = _MILVUS_URI,
        token: str = _MILVUS_TOKEN,
        api_key: str = _DASHSCOPE_KEY,
    ):
        if not _HAS_MILVUS:
            raise ImportError("pymilvus not installed")
        if not _HAS_DASHSCOPE:
            raise ImportError("dashscope not installed")
        if not api_key:
            raise ValueError("DASHSCOPE_API_KEY not set")

        self._api_key = api_key
        if uri.endswith(".db"):
            os.makedirs(os.path.dirname(uri) or ".", exist_ok=True)
        self._client = MilvusClient(uri=uri, token=token or None)
        self._ensure_collection()

    # ── Collection 管理 ──────────────────────────────────────────────────────

    def _ensure_collection(self) -> None:
        if not self._client.has_collection(_COLLECTION_NAME):
            logger.info("KBVectorStore: creating collection '%s' dim=%d", _COLLECTION_NAME, _DIM)
            self._client.create_collection(
                collection_name=_COLLECTION_NAME,
                dimension=_DIM,
                primary_field_name="id",
                vector_field_name="vector",
                auto_id=True,
                metric_type="COSINE",
            )
        # Milvus Lite 查询前必须显式 load
        try:
            self._client.load_collection(_COLLECTION_NAME)
        except Exception:
            pass

    def count(self) -> int:
        try:
            stats = self._client.get_collection_stats(_COLLECTION_NAME)
            return int(stats.get("row_count", 0))
        except Exception:
            return 0

    def drop_and_recreate(self) -> None:
        if self._client.has_collection(_COLLECTION_NAME):
            self._client.drop_collection(_COLLECTION_NAME)
        self._ensure_collection()   # 内部已含 load_collection

    # ── 索引 ─────────────────────────────────────────────────────────────────

    def index(self, chunks, force: bool = False) -> int:
        """把 DocChunk 列表 embed 后写入 Milvus。

        force=True：先清空再重建；force=False：collection 有数据则跳过。
        返回实际写入数量。
        """
        from recon_v2.rag.chunker import DocChunk  # 避免循环 import

        if not force and self.count() > 0:
            logger.info("KBVectorStore: collection already has %d docs, skip indexing (use force=True to rebuild)", self.count())
            return 0

        if force:
            self.drop_and_recreate()

        texts = [c.text for c in chunks]
        logger.info("KBVectorStore: embedding %d chunks via Dashscope...", len(texts))
        vecs = _embed_batch(texts, api_key=self._api_key)

        data = []
        skipped = 0
        for chunk, vec in zip(chunks, vecs):
            if vec is None:
                skipped += 1
                continue
            data.append({
                "doc_id": chunk.doc_id[:256],
                "vector": vec,
                "text": chunk.text[:4096],
            })

        if skipped:
            logger.warning("KBVectorStore: %d chunks skipped (embedding failed)", skipped)

        if not data:
            return 0

        result = self._client.insert(collection_name=_COLLECTION_NAME, data=data)
        count = result.get("insert_count", len(data))
        logger.info("KBVectorStore: indexed %d docs", count)
        return count

    # ── 检索 ─────────────────────────────────────────────────────────────────

    def search(self, query: str, k: int = 10, min_score: float = 0.0) -> List[Tuple[str, float]]:
        """语义检索，返回 [(doc_id, cosine_score), ...] 降序。"""
        vec = _embed(query, api_key=self._api_key)
        if vec is None:
            return []

        results = self._client.search(
            collection_name=_COLLECTION_NAME,
            data=[vec],
            limit=k,
            output_fields=["doc_id"],
            search_params={"metric_type": "COSINE"},
        )

        if not results or not results[0]:
            return []

        hits = []
        for hit in results[0]:
            score = float(hit.get("distance", 0.0))
            doc_id = hit.get("entity", {}).get("doc_id", "")
            if score >= min_score and doc_id:
                hits.append((doc_id, score))
        return hits


# ── 全局单例 ──────────────────────────────────────────────────────────────────

_global_kb_store: Optional[KBVectorStore] = None


def get_kb_store() -> Optional[KBVectorStore]:
    """获取全局 KBVectorStore，不可用时返回 None。"""
    global _global_kb_store
    if _global_kb_store is not None:
        return _global_kb_store
    if not is_available():
        return None
    try:
        _global_kb_store = KBVectorStore()
        return _global_kb_store
    except Exception as e:
        logger.info("KBVectorStore unavailable: %s", e)
        return None
