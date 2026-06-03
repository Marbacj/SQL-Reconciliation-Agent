# ADR-001: 选择 LangGraph 作为 Orchestration 框架

**Status**: Accepted
**Date**: 2026-06-01

## Context

v1 自研 `recon_core` 框架带来三个核心问题：
1. ReAct loop 边界靠 `step_counter > N` 硬编码，缺乏可观察的状态机视角
2. 中断恢复能力缺失，长流程一旦失败必须从头跑
3. 自研代码必须自己维护，对面试简历是减分项（"自己写了一个 ReAct" vs "用 LangGraph 实现 stateful agent"）

## Decision

采用 LangGraph (`langgraph>=0.2.0`) 作为编排框架，理由：

1. **stateful**：GraphState 自动序列化、Checkpointer 持久化，原生支持中断恢复
2. **conditional edges**：状态机分支显式、可视化、可单测
3. **生态背书**：LangChain 团队官方维护，2025 年成为 LLM Agent 编排事实标准
4. **简历价值**：业界主流框架，面试官立刻可识别能力
5. **避险**：Day 1 PoC 阀门：若 LangGraph minimal hello-world 跑不通，切换到自研 mini state machine（仍保留 AgentContext 模式）

## Consequences

**正向**：
- Node 之间状态自动持久化，trace 可恢复
- 自动获得 LangSmith 等生态工具兼容
- 状态机定义清晰，新人易上手

**负向**：
- 增加一个外部依赖（~30MB）
- LangGraph API 仍在迭代，需关注版本兼容（已锁定 >=0.2.0）
- GraphState 必须可序列化 → AgentContext 通过 ctx_id 间接挂载（增加一层间接）

## Alternatives Considered

1. **LangChain Expression Language (LCEL)**：链式表达力强，但状态机能力弱，不适合多轮 ReAct
2. **AutoGen (Microsoft)**：多 agent 协作好，但单 agent 复杂度高
3. **Llama Index Workflow**：相对新，社区还在成长
4. **自研 mini state machine**：完全可控，但持久化/恢复要自己写，简历减分
