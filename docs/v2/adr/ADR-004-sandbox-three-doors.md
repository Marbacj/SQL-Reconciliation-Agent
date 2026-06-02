# ADR-004: Self-Evolution 三道质量门

**Status**: Accepted
**Date**: 2026-06-01

## Context

v1 SkillReviewer 在 Demo 中暴露两个致命问题：
1. **死锁**：用 `threading.Lock` 处理嵌套调用，死锁 100% 重现 → 已临时改成 RLock，但根因未解
2. **质量失控**：所有提炼出来的 skill 全部入库，包括重复 / 低质量 / 退化模式

业界教训：CAMEL / AutoGen 的 reflection 机制都因"无门控"演化为提示词污染源。

## Decision

任何候选 skill 入库前必须通过 **三道质量门**：

### Door 1: Dedup 去重门
- 计算 candidate 的 embedding
- 与现有 skill 余弦相似度 > 0.85 → 拒绝
- 防止 "对账昨日订单"、"对账昨天订单"、"对昨日订单进行对账" 三条本质相同的 skill 共存

### Door 2: Critic 评分门
- LLM 三维评分（每维 0-1）：
  - **specificity** 具体性：是否对特定场景有用
  - **reusability** 可复用性：能否泛化到相似问题
  - **orthogonality** 正交性：是否与已有 skill 互补
- 加权得分 = 0.4×spec + 0.4×reuse + 0.2×ortho
- < 0.7 拒绝

### Door 3: Sandbox dry-run 门
- 在 Golden Set 抽样 10 条上跑 baseline vs with-skill
- 若 with-skill 准确率比 baseline 下降 > 2% → 拒绝
- **这是最关键的一道门**：直接用产品级 metric 守门，不让任何"看起来合理但实际有害"的 skill 入库

通过三道门的 skill：confidence_init = 0.6，使用后按 Wilson Score 动态更新。

## Consequences

**正向**：
- 入库通过率 ≤ 50%（v1 是 100%），库质量大幅提升
- Sandbox 直接对接业务 metric，防退化能力强
- 三道门独立可测，易调试

**负向**：
- 每次提炼成本 = embedding + LLM critic + 10 条 sandbox 跑测 ≈ $0.05
- 异步队列 → reflect 节点不阻塞主链路（必须）
- Critic LLM 的 prompt 需精心设计（已迭代 3 版）

## Alternatives Considered

1. **只用 Dedup**：质量门控不足
2. **只用 Critic**：易被 LLM 自我陶醉欺骗
3. **只用 Sandbox**：成本高（每次 10 条 dry-run）且无法甄别"重复"
4. **RLHF**：长期目标，但需大量人工标注，不适合 6 周 MVP

## Failure Mode Analysis

| 失败模式 | 缓解措施 |
| --- | --- |
| Dedup 阈值过严 → 漏掉相似但不同的 skill | 默认 0.85；提供运行时调参 |
| Critic LLM 评分不稳定 | 温度设 0；多次评分取均值（roadmap） |
| Sandbox 10 条不够代表性 | 按 intent 分层抽样，覆盖五大场景 |
| 异步队列堆积 | 进程退出前 flush；监控队列长度 |
