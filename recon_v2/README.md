# SQL Reconciliation Agent v2

工业级 NL2SQL 对账 Agent，基于 LangGraph + Hybrid RAG + Memory v2 + Self-Evolution Sandbox + Eval-Driven。

## 目录结构

```
recon_v2/
├── core/           # AgentContext / Budget / 共享类型
├── orchestration/  # LangGraph state machine + nodes
├── tools/          # Pydantic 工具（sql_runner / rag_searcher / ...）
├── memory/         # Working / Episodic / Semantic 三层记忆
├── rag/            # Hybrid Retriever (BM25 + Dense + Rerank)
├── evolution/      # Self-Evolution Sandbox + 三道质量门
├── infra/          # LLM Gateway / SQL Safety / OTel
└── adapters/       # SQLAdapter 抽象 + sqlite 实现
```

详见 [docs/v2/architecture.md](../docs/v2/architecture.md)。

## v1 代码

v1 自研 `hello_agents/` 框架代码仍保留在仓库根目录作为对照基线，
将在 Stage 5 收尾时统一迁移至 `legacy/` 目录。
