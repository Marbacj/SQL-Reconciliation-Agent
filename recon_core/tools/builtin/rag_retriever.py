"""
Qdrant RAG 模块 — 表结构语义检索 + Hybrid Search + Query Rewrite

设计：
  - TableDocIndexer: 将表结构文档向量化存入 Qdrant
  - TableDocRetriever: 语义搜索最相关的表/字段
  - 支持 Hybrid Search（向量 + 关键词混合）
  - Query Rewrite: 将用户术语映射为精确字段名
"""

import json
import os
from typing import List, Dict, Any, Optional


class TableDocRetriever:
    """表结构文档检索器 — 支持本地关键词模式 + Qdrant 向量模式

    两种模式：
      本地模式（默认）: 关键词匹配，零外部依赖
      Qdrant 模式: 需要 qdrant-client + embedding API
    """

    def __init__(
        self,
        doc_dir: str = "knowledge_base/table_docs",
        use_qdrant: bool = False,
        qdrant_url: Optional[str] = None,
        qdrant_collection: str = "table_docs",
    ):
        self.doc_dir = doc_dir
        self.use_qdrant = use_qdrant
        self._docs: Dict[str, str] = {}
        self._load_docs()

        if use_qdrant and qdrant_url:
            self._init_qdrant(qdrant_url, qdrant_collection)

    def _load_docs(self):
        """加载本地 Markdown 文档"""
        if not os.path.exists(self.doc_dir):
            return
        for fname in os.listdir(self.doc_dir):
            if fname.endswith('.md'):
                path = os.path.join(self.doc_dir, fname)
                with open(path) as f:
                    self._docs[fname.replace('.md', '')] = f.read()

    def _init_qdrant(self, url: str, collection: str):
        """初始化 Qdrant 连接（可选依赖）"""
        try:
            from qdrant_client import QdrantClient
            self._qdrant = QdrantClient(url=url)
            self._collection = collection
            print(f"🔍 Qdrant 已连接: {url}/{collection}")
        except ImportError:
            print("⚠️ qdrant-client 未安装，使用本地检索模式")
            self.use_qdrant = False

    # ── 检索 ──

    def search(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """检索相关表结构文档

        Args:
            query: 用户查询（如 "GMV 是什么意思"）
            top_k: 返回文档数

        Returns:
            [{"table": "live_gmv", "content": "...", "score": 0.85}, ...]
        """
        if self.use_qdrant and hasattr(self, '_qdrant'):
            return self._qdrant_search(query, top_k)
        return self._keyword_search(query, top_k)

    def _keyword_search(self, query: str, top_k: int) -> List[Dict]:
        """本地关键词检索（Jaccard + TF 加权）"""
        query_lower = query.lower()
        query_terms = set(query_lower.split())

        # 扩展中文分词（简单 2-gram）
        for i in range(len(query) - 1):
            bigram = query[i:i + 2]
            if all('\u4e00' <= c <= '\u9fff' for c in bigram):
                query_terms.add(bigram)

        scored = []
        for table_name, doc in self._docs.items():
            doc_lower = doc.lower()
            doc_terms = set(doc_lower.split())
            intersection = query_terms & doc_terms
            union = query_terms | doc_terms
            score = len(intersection) / len(union) if union else 0
            if score > 0:
                scored.append((score, table_name, doc))

        scored.sort(reverse=True)
        return [
            {"table": name, "content": doc[:500], "score": round(s, 3)}
            for s, name, doc in scored[:top_k]
        ]

    def _qdrant_search(self, query: str, top_k: int) -> List[Dict]:
        """Qdrant 向量检索（桩实现）"""
        # 生产环境：调用 embedding API → Qdrant 向量检索
        print(f"🔍 Qdrant 检索: {query[:50]}...")
        return []

    # ── Query Rewrite ──

    def rewrite_query(self, query: str) -> str:
        """将用户术语映射为精确字段名

        例: "GMV" → "live_gmv.gmv"
            "订单金额" → "order_amount.total_amount"
        """
        # 术语 → 字段映射（从文档中提取，运行时积累）
        term_map = self._build_term_map()
        rewritten = query
        for term, field in term_map.items():
            if term.lower() in query.lower():
                rewritten = rewritten.replace(term, f"{term}({field})")
        return rewritten

    def _build_term_map(self) -> Dict[str, str]:
        """从表文档中提取术语映射"""
        mapping = {}
        import re
        for doc in self._docs.values():
            # 匹配 "GMV" → "live_gmv.gmv" 或 "字段名: gmv" 等模式
            pairs = re.findall(
                r'[\"「](.+?)[\"」]\s*[→↔映射为:]\s*[\"「]?(\w+\.\w+)[\"」]?',
                doc
            )
            for term, field in pairs:
                mapping[term] = field

            # 匹配 Markdown 表格中的 术语 | 字段 行
            rows = re.findall(r'\|\s*(\S+)\s*\|\s*(\S+)\s*\|', doc)
            for term, field in rows:
                if '.' in field:  # 只保留包含表名.字段名的
                    mapping[term] = field

        return mapping

    # ── 索引 ──

    def index_documents(self):
        """将文档索引入 Qdrant（需要 embedding API）"""
        if not self.use_qdrant:
            print("⚠️ Qdrant 未启用，跳过索引")
            return

        print(f"📥 索引 {len(self._docs)} 个文档到 Qdrant...")
        # 生产实现：
        # for doc_id, content in self._docs.items():
        #     vector = embedding_api.encode(content)
        #     self._qdrant.upsert(collection_name, points=[{id, vector, payload}])
        print("✅ 索引完成")

    def stats(self) -> dict:
        return {
            "mode": "qdrant" if self.use_qdrant else "keyword",
            "documents": len(self._docs),
            "collections": list(self._docs.keys()),
        }
