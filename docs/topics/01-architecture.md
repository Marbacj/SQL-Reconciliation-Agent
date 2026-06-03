# 系统架构

> SQL Reconciliation Agent v2 — 架构全景

## 整体设计哲学

ReconAgent 基于 **LangGraph** 构建，以有向无环图（DAG）组织对账推理流程。每个节点是纯函数，状态通过 `GraphState` 显式传递，让复杂的多步推理可观测、可调试、可回滚。

---

## 核心分层

```
┌─────────────────────────────────────────────┐
│              用户 / 控制台 UI               │
├─────────────────────────────────────────────┤
│           FastAPI  HTTP / SSE 层            │
├──────────────┬──────────────────────────────┤
│  LangGraph   │  ReconAgent DAG              │
│  Orchestrator│  plan → act → observe → end  │
├──────────────┴──────────────────────────────┤
│  工具层  SQL Executor · Schema Linker · RAG │
├─────────────────────────────────────────────┤
│  存储层  SQLite · BM25 Index · Memory Store │
└─────────────────────────────────────────────┘
```

---

## LangGraph DAG 节点说明

| 节点 | 职责 | 关键输入 / 输出 |
|------|------|----------------|
| `plan` | 将自然语言拆解为并行 SQL 子任务 | `user_query` → `parallel_plan` |
| `parallel_act` | asyncio.gather 并发执行多条 SQL | `parallel_plan` → `parallel_results` |
| `observe` | 汇总结果、做 Range Guard 合理性校验 | `parallel_results` → `observation` |
| `reflect` | 发现异常时生成修正建议或追问 | `observation` → `reflection` |
| `end` | 格式化最终答案返回 | `reflection` → `final_answer` |

路由函数 `route_after_observe` 根据 `observation.has_anomaly` 决定是进入 `reflect` 还是直接 `end`。

---

## 并行执行机制

```python
async def parallel_act(state: GraphState) -> GraphState:
    tasks = [execute_sql(step.sql, step.db) for step in state.parallel_plan]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    ...
```

对账场景往往需要同时查询多张表或多个数据库，并发执行将延迟从 O(N) 降至 O(1)。

---

## 数据流示意

```
用户输入 "比较 A/B 表 6 月差异"
   │
   ▼ plan 节点
   [查询 table_A 6月, 查询 table_B 6月]
   │
   ▼ parallel_act（asyncio.gather）
   [result_A, result_B]
   │
   ▼ observe 节点
   差异 = result_A - result_B → 异常检测
   │
   ▼ reflect（如有异常）→ end
   生成对账报告
```

---

## 技术选型 ADR 索引

- **ADR-001**: 选择 LangGraph 而非 AutoGen — 更易调试、状态显式
- **ADR-002**: Hybrid RAG（BM25 + 向量）— 兼顾精确匹配和语义检索
- **ADR-003**: 三层记忆 — Working / Episodic / Semantic
- **ADR-004**: 三门沙盒 — SQL 安全执行防护
- **ADR-005**: SQLGlot 静态安全检查 — 拦截 DDL/DML

---

## 部署结构

```
Docker Compose
├── recon-agent   (FastAPI + LangGraph)
└── nginx         (反向代理 + 静态文件)

服务器: 47.95.229.48
快速部署: ./deploy.sh ui   # 秒级前端热更
           ./deploy.sh all  # 全量重建
```
