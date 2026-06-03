# RAG 检索增强

> Hybrid RAG：BM25 精确匹配 + 向量语义检索

## RAG 在对账场景的定位

对账 Agent 需要理解业务方言——"GMV"、"退款率"、"T+1 结算"这些词在不同业务线有不同口径。RAG 让 Agent 在生成 SQL 前先检索相关业务文档，将口径定义注入 prompt，从而减少幻觉。

---

## 两种检索方式对比

| 特性 | BM25（关键词） | 向量（语义） |
|------|--------------|------------|
| 精确词匹配 | 极强 | 弱 |
| 同义词/近义词 | 弱 | 强 |
| 速度 | 极快（ms 级） | 较慢（需 embedding） |
| 部署复杂度 | 低（纯 Python） | 高（需向量数据库） |
| 适合场景 | 表名、字段名查找 | 业务概念、模糊问法 |

**结论**：两者互补，Hybrid = max(BM25 score, vector score)，取最高分文档。

---

## 系统架构

```
用户问题
    │
    ├─► BM25 检索  ──►┐
    │                  ├─► RRF 融合排序 ──► Top-K 文档 ──► 注入 Prompt
    └─► 向量检索  ──►┘
```

RRF（Reciprocal Rank Fusion）公式：

```
score(d) = Σ 1 / (k + rank_i(d))
```

k=60 为经验值，平衡两路排名权重。

---

## 知识库组织

```
knowledge_base/
├── schema/          # 表结构描述、字段注释
│   ├── orders.md
│   └── payments.md
├── business/        # 业务口径定义
│   ├── metrics.md   # GMV、DAU 等指标定义
│   └── rules.md     # 结算规则、对账规则
└── examples/        # 历史对账 SQL 示例（few-shot）
    └── golden_set.jsonl
```

---

## 检索触发时机

```python
# plan 节点中，生成 SQL 前先检索
async def plan(state: GraphState) -> GraphState:
    # 1. 检索相关文档
    docs = await rag_retriever.search(state.user_query, top_k=5)
    
    # 2. 将文档注入 system prompt
    context = format_docs(docs)
    
    # 3. LLM 生成并行计划
    plan = await llm.plan(state.user_query, context=context)
    ...
```

---

## RAG vs Memory 的区别

| 维度 | RAG | Memory |
|------|-----|--------|
| 存储内容 | 静态业务文档 | 动态会话记录 |
| 更新频率 | 低（手动更新） | 高（每次对话） |
| 检索粒度 | 文档段落 | 对话片段 / 规则 |
| 适合场景 | 口径定义、schema 描述 | 用户偏好、历史错误 |

两者协同：RAG 提供"知识"，Memory 提供"经验"。

---

## 降级策略

当向量数据库不可用时，自动降级为纯 BM25 模式：

```python
try:
    vector_results = await milvus_store.search(query)
except MilvusException:
    logger.warning("Milvus 不可用，降级为 BM25 模式")
    vector_results = []

results = bm25_results if not vector_results else hybrid_merge(bm25_results, vector_results)
```

BM25 索引在内存中常驻，零外部依赖，保证基础可用性。

---

## 评估指标

| 指标 | 当前值 | 目标 |
|------|--------|------|
| Recall@5 | 82% | >85% |
| MRR | 0.71 | >0.75 |
| SQL 执行准确率（有 RAG） | 78% | >80% |
| SQL 执行准确率（无 RAG） | 61% | — |
