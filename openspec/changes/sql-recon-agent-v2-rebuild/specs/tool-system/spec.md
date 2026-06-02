## ADDED Requirements

### Requirement: Pydantic Tool Schema
所有工具 MUST 继承 `ToolBase`，输入参数用 Pydantic `BaseModel` 显式声明，输出用 `ToolOutput` 包装。

#### Scenario: Tool 暴露 OpenAI Function Calling schema
- **WHEN** Agent 准备工具列表给 LLM
- **THEN** 每个工具调用 `to_openai_function()` 返回符合 OpenAI Function Calling 规范的 JSON

#### Scenario: 输入校验失败
- **WHEN** Tool.run 收到不符合 input_schema 的参数
- **THEN** 抛出 ValidationError 而非静默调用失败

### Requirement: 5 个内置核心工具
系统 SHALL 提供以下 5 个核心工具：`sql_runner` / `diff_calculator` / `report_generator` / `rag_searcher` / `case_query`，每个工具 name / description / schema 完整。

#### Scenario: sql_runner 执行查询
- **WHEN** Agent 调用 sql_runner(sql="SELECT ...")
- **THEN** 工具先经 sqlglot AST 校验，校验通过后通过 SQLAdapter 执行并返回结果集

#### Scenario: rag_searcher 主动检索
- **WHEN** Agent 在 Act Node 主动调用 rag_searcher(query="订单表 schema", k=3)
- **THEN** 返回 HybridRetriever top-3 检索结果

### Requirement: ToolRegistry 工具注册中心
系统 SHALL 实现 `ToolRegistry` 类，支持注册 / 查询 / 按意图过滤工具。

#### Scenario: 按 intent 过滤工具
- **WHEN** 调用 registry.filter_by_intent("reconciliation")
- **THEN** 返回该 intent 关联的工具子集；未关联则返回全集
