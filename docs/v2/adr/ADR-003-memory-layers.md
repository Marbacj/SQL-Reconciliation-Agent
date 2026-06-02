# ADR-003: Memory v2 三层架构

**Status**: Accepted
**Date**: 2026-06-01

## Context

v1 Memory 是单层 SQLite case 表，所有 case 平等存储，导致：
- 检索性能差：每次 query 都遍历全表
- 高质量 case 与噪声 case 混在一起
- 无遗忘机制，库越长越脏
- 无抽象能力（只有原始 case，没有规则）

## Decision

借鉴认知心理学的三层记忆模型：

| Layer | 存储 | 容量 | TTL | 用途 |
| --- | --- | --- | --- | --- |
| **Working** | 内存 LRU | 20 | 进程级 | 当前/最近 case 快速访问 |
| **Episodic** | SQLite | 无限 | 30 天 | 单次执行 trace 持久化 |
| **Semantic** | SQLite | 无限 | 永久 | 抽象规则（从 episodic 归纳） |

**关键机制**：

1. **Promotion**：write 时计算 importance = 0.4×outcome + 0.3×novelty + 0.3×user_flag；≥ 0.6 才进 Episodic
2. **Consolidation**：周期任务，对 Episodic 中重复 ≥ 5 次的同 intent 高成功率模式，归纳为 Semantic rule
3. **Decay**：30 天未用 + confidence < 0.5 → 自动 archived

## Consequences

**正向**：
- Working 提供 O(1) 缓存命中
- Episodic 索引按 intent 分桶，检索快
- Semantic rule 是 RAG 的高价值素材
- 自动清理机制保持库健康

**负向**：
- 三层联合查询需要拼装逻辑（已封装在 MemoryStore.query）
- Consolidation 需 LLM 调用（成本 ~ $0.01/天）

**v1 → v2 迁移**：v1 的 case_store 可一次性导入 Episodic 表，按当前 importance 公式重算

## Alternatives Considered

1. **单层 Postgres + jsonb**：性能可以，但与"分层认知"心智模型不符
2. **Redis only**：失去持久化和复杂查询能力
3. **向量库 only (Qdrant)**：检索好，但失去结构化分析能力（如统计某 intent 成功率）
