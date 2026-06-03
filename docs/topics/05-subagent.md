# Subagent 体系

> 专职 Agent 协同：SQL 生成 / 反思 / Skill 审查

## 设计动机

单个 LLM 调用难以同时做好"生成 SQL"和"审查 SQL"——两个目标存在内在冲突（生成者倾向于为自己的输出辩护）。引入独立 Subagent，实现职责隔离、相互校验。

---

## Subagent 角色定义

```
┌─────────────────────────────────────────────────────┐
│                   ReconAgent 主流程                   │
│  plan ──► parallel_act ──► observe ──► reflect ──► end│
└──────────────────┬──────────────────────────────────┘
                   │ 委托
        ┌──────────┼──────────┐
        ▼          ▼          ▼
  SQL Generator  Reflect    Skill
   Subagent      Agent     Reviewer
   （生成）      （反思）   （审查）
```

---

## SQL Generator Subagent

**职责**：将 plan 节点的子任务转化为可执行 SQL

**System Prompt 要素**：
- 当前 Schema（经过权限过滤）
- RAG 检索到的业务文档
- 历史相似案例（Episodic Memory）
- 语义规则（Semantic Memory）

**输出格式**：
```json
{
  "sql": "SELECT ...",
  "confidence": 0.87,
  "tables_used": ["orders", "payments"],
  "reasoning": "使用 LEFT JOIN 因为 payments 可能存在未匹配记录"
}
```

---

## Reflect Agent Subagent

**职责**：在 observe 节点发现异常时，分析根因并提出修正建议

**触发条件**：
- `observation.has_anomaly = True`
- SQL 执行错误（SyntaxError / DataError）
- Range Guard 检测到数值不合理

**输出结构**：
```json
{
  "anomaly_type": "data_mismatch",
  "root_cause": "A 表使用 event_date，B 表使用 settle_date，时间口径不一致",
  "suggestion": "修改 B 表查询条件为 event_date BETWEEN ...",
  "action": "retry_sql"
}
```

---

## Skill Reviewer Subagent

**职责**：定期审查 skill_library 中的 Skill 是否仍然有效、安全

**审查维度**：

| 维度 | 检查项 |
|------|--------|
| 安全性 | 是否包含危险操作（文件删除、网络请求） |
| 有效性 | 依赖的 API / 数据源是否仍可访问 |
| 质量 | 输出格式是否规范、是否有测试用例 |
| 时效性 | 上次更新时间，是否需要同步最新口径 |

---

## Subagent 通信协议

所有 Subagent 通过结构化 JSON 与主 Agent 通信，禁止自由文本：

```python
class SubagentResponse(BaseModel):
    status: Literal["DONE", "DONE_WITH_CONCERNS", "BLOCKED", "NEEDS_CONTEXT"]
    output: dict
    concerns: list[str] = []
    context_needed: list[str] = []
```

`BLOCKED` 和 `NEEDS_CONTEXT` 状态会暂停主流程，等待人工干预或补充信息。

---

## 控制台配置

在控制台右下角 **设置 → 功能** 面板中可配置每个 Subagent：

- **模型选择**：每个 Subagent 可独立指定 LLM（低成本任务用小模型）
- **温度**：SQL Generator 低温（0.1），Reflect Agent 中温（0.5）
- **超时**：防止单个 Subagent 阻塞整体流程

---

## 扩展新 Subagent

在 `skill_library/` 下新建 `.md` 文件，格式：

```markdown
---
name: my-subagent
description: 做什么用途，何时触发
---

[System Prompt 内容]
```

主 Agent 启动时自动扫描 `skill_library/`，无需修改代码。
