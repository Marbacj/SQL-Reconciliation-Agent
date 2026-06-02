# ADR-002: Hybrid RAG 设计 (BM25 + Dense + RRF + Rerank)

**Status**: Accepted
**Date**: 2026-06-01

## Context

v1 RAG 是"全量塞 prompt"模式：所有表 schema 字符串拼接到 system prompt，导致：
- 上下文窗口浪费（90% 内容与当次 query 无关）
- 准确率受限（LLM 注意力被噪声分散）
- 无法扩展（业务文档增多就崩）

## Decision

采用四层 Hybrid RAG 管线：

```
Query
  ├─→ BM25 sparse 检索 top-20  ┐
  ├─→ Dense 检索 (bge-small) top-20 ┘
  │                              ↓
  │                            RRF 融合 (k=60)
  │                              ↓
  │                          top-10 候选
  │                              ↓
  └─→ Cross-Encoder Reranker (bge-reranker-v2-m3)
                                 ↓
                            top-3 注入 prompt
```

**关键决策点**：

1. **Sparse + Dense 双通道**：BM25 擅长字面匹配（表名/字段名），Dense 擅长语义（业务描述）
2. **RRF (Reciprocal Rank Fusion)**：参数少（k=60 经验值）、无需训练、对两路结果分布不敏感
3. **Cross-Encoder rerank**：bge-reranker-v2-m3 在中文场景 SOTA，精排 10 → 3 性价比最高
4. **RAG-as-Tool**：暴露为 `rag_searcher` Tool，由 Agent 主动调用而非系统自动注入

## Consequences

**正向**：
- 召回率 + 精度双优
- 上下文 token 大幅压缩（10:1）
- Agent 主动检索 → 可观察、可控、可单测

**负向**：
- 依赖 Qdrant + sentence-transformers + FlagEmbedding 三个外部库
- 需离线 indexer（额外建库流程）
- 首次启动加载 reranker 模型耗时 5-10s

**降级路径**（Stage 3 当前实现）：
- 无 Qdrant → BM25-only，degraded=True
- 无 reranker → 仅 RRF，效果会下降但不挂

## Alternatives Considered

1. **纯 Dense (Qdrant 单通道)**：召回率受限于 embedding 模型
2. **纯 BM25**：召回率最差，业务术语难匹配
3. **LightRAG / GraphRAG**：复杂度高，对小规模业务文档收益不明显
4. **Self-RAG**：依赖训练，工程化成本高
