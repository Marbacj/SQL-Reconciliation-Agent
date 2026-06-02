## ADDED Requirements

### Requirement: 5-Node LangGraph 状态机
系统 SHALL 基于 LangGraph 实现包含 route / clarify / plan / act / observe / reflect 六个节点的状态机（reflect 异步），使用 conditional edges 控制分支跳转。

#### Scenario: 高置信度走完整流程
- **WHEN** 用户提交查询且意图路由 confidence ≥ 0.6
- **THEN** 状态机依次执行 route → plan → act →（必要时多轮 act/observe）→ reflect

#### Scenario: 低置信度走澄清
- **WHEN** 意图路由 confidence < 0.6
- **THEN** 状态机跳转至 clarify 节点反问用户，并以澄清问题作为最终输出结束

#### Scenario: Budget 超限终止
- **WHEN** 任意 Node 入口检测到 ctx.budget.exceeded() == True
- **THEN** 状态机立即跳转至 END，返回当前累积结果与 budget_exceeded 标记

### Requirement: AgentContext 共享上下文
系统 SHALL 定义 `AgentContext` 数据类，持有 trace_id / session_id / query / intent / memory / rag / tools / llm / tracer / budget / step_counter / mode 字段，所有 Node 和 Tool 通过 ctx 参数访问能力。

#### Scenario: Tool 通过 ctx 访问 LLM
- **WHEN** Tool 实现 `run(ctx, inp)` 时需调用 LLM
- **THEN** Tool 必须通过 `ctx.llm.chat(...)` 调用，不允许直接 import litellm

#### Scenario: 模式切换可被观察
- **WHEN** Act Node 检测到 step_counter > REACT_MAX_STEPS（默认 4）
- **THEN** 将 ctx.mode 从 "react" 切换为 "plan_solve" 并 emit OTel span event "mode_switch"

### Requirement: Checkpoint 中断恢复
系统 SHALL 通过 LangGraph 内置 SqliteSaver 提供 checkpointer 能力，支持任意 Node 完成后落盘，并能从该点恢复执行。

#### Scenario: 正常 checkpoint 落盘
- **WHEN** 任一 Node 执行结束
- **THEN** LangGraph 自动将当前 GraphState 序列化写入 checkpointer 后端

#### Scenario: 中断后恢复
- **WHEN** 调用 graph.invoke({"trace_id": X}, config={"configurable": {"thread_id": X}}) 且该 thread_id 已有 checkpoint
- **THEN** 状态机从最近一次 checkpoint 后的节点继续执行，而非从头开始

### Requirement: 意图路由带置信度
系统 SHALL 实现 Route Node 同时返回 `(intent_label, confidence)` 二元组，使用 keyword 规则匹配（快速）+ LLM 分类（兜底）双通道。

#### Scenario: 关键词命中
- **WHEN** Route Node 检测到 query 包含已注册 intent 的关键词
- **THEN** 返回该 intent 及 confidence = 1.0 - epsilon（不直接给 1.0 留校准空间）

#### Scenario: 关键词未命中走 LLM
- **WHEN** 所有 intent 的关键词均未命中
- **THEN** 调用 LLM 输出 (intent, confidence ∈ [0, 1])
