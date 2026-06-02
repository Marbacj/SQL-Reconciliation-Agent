## ADDED Requirements

### Requirement: Hybrid Retrieval（BM25 + Dense + RRF）
系统 SHALL 实现 `HybridRetriever`，同时执行 BM25 sparse 检索和 Dense（基于 bge-small-zh）检索，使用 Reciprocal Rank Fusion 算法（k=60）融合结果。

#### Scenario: 双通道并行召回
- **WHEN** 调用 retriever.retrieve(query, k=3)
- **THEN** 内部并行执行 BM25 top-20 + Dense top-20，再 RRF 融合

#### Scenario: 单通道服务降级
- **WHEN** Qdrant 服务不可达
- **THEN** 仅返回 BM25 结果并 emit OTel span attribute "rag.degraded=true"，不抛异常

### Requirement: Cross-Encoder Rerank
系统 SHALL 在 RRF 融合后使用 Cross-Encoder（默认 bge-reranker-v2-m3）对 top-10 候选进行二次精排，输出最终 top-k。

#### Scenario: 精排返回 top-3
- **WHEN** RRF 输出 10 个候选 doc，请求 k=3
- **THEN** Reranker 对 10 个 doc 重打分后返回 top-3

### Requirement: RAG-as-Tool
RAG 检索能力 SHALL 以 Tool 形式（`rag_searcher`）暴露给 Agent，由 Agent 在 Act Node 主动决定何时检索，禁止全量自动塞 system prompt。

#### Scenario: Agent 主动调用
- **WHEN** Agent 在 Act Node 通过 Function Calling 请求 rag_searcher
- **THEN** 工具返回检索结果作为 Observation 进入下一轮推理

### Requirement: 离线建库 Indexer
系统 SHALL 提供 `python -m recon_v2.rag.indexer --source <dir> --collection <name>` CLI 工具，将表 schema / 列描述 / 业务文档分块、向量化、写入 Qdrant。

#### Scenario: 全量重建索引
- **WHEN** 执行 indexer --rebuild
- **THEN** 清空目标 collection 后重新建库，输出处理总数和耗时

### Requirement: 检索质量评估
系统 SHALL 提供 `tests/eval/rag_eval.py`，在 Golden Set 子集（含 retrieval label）上计算 MRR@5 和 Recall@10。

#### Scenario: MRR@5 计算
- **WHEN** 对 30 条带 retrieval label 的 case 跑评估
- **THEN** 输出 MRR@5 数值 + 每条 case 的 reciprocal rank
