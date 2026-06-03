# 自进化机制

> Agent 如何从错误中学习，持续提升准确率

## 什么是自进化

自进化（Self-Evolution）是指 Agent 在运行过程中，自动识别自身的失败模式、提炼经验规则、更新内部知识，从而在不重新训练模型的前提下持续提升性能。

---

## 进化闭环

```
用户提问
    │
    ▼ Agent 执行
    │
    ▼ 结果评估（正确/错误/用户反馈）
    │
    ├─ 正确 ──► 将成功案例写入 Episodic Memory
    │
    └─ 错误 ──► 错误分析 ──► 提炼规则 ──► 写入 Semantic Memory
                                              │
                                              ▼
                                     下次遇到类似问题时
                                     规则自动注入 Prompt
```

---

## 四个进化维度

### 1. SQL 生成质量进化

当 SQL 执行连续失败，Reflect Agent 分析失败原因：

```python
async def evolve_sql_patterns(error_cases: list[ErrorCase]):
    # 聚类相似错误
    clusters = cluster_errors(error_cases)
    
    for cluster in clusters:
        # 提炼规则
        rule = await llm.extract_rule(
            errors=cluster.cases,
            prompt="总结这类错误的共同原因和避免方法"
        )
        
        # 存入语义记忆
        await memory_store.save_rule(rule, confidence=cluster.confidence)
```

### 2. Schema Linking 进化

记录哪些表名/字段名经常被错误映射：

```json
{
  "user_say": "退款金额",
  "correct_column": "payments.refund_amount",
  "wrong_guesses": ["orders.amount", "returns.money"],
  "learned_at": "R42"
}
```

### 3. Skill 进化

Skill Reviewer 定期检查 `skill_library/`，标记过时 Skill，建议更新或删除：

```
[SKILL REVIEW] payments-query-skill
  状态: 过时
  原因: payments 表新增 settle_status 字段，原 SQL 缺少过滤
  建议: 在 WHERE 子句添加 settle_status = 'confirmed'
  操作: [更新] [忽略] [删除]
```

### 4. 记忆健康度进化

定期清理低置信度规则，防止记忆"污染"：

```python
async def memory_hygiene():
    rules = await memory_store.get_all_rules()
    for rule in rules:
        if rule.confidence < THRESHOLD or rule.age_days > MAX_AGE:
            await memory_store.deprecate_rule(rule.id)
```

---

## 进化触发时机

| 触发条件 | 进化动作 |
|---------|---------|
| 会话结束 | 成功案例写入 Episodic Memory |
| SQL 连续失败 3 次 | 提炼错误模式为语义规则 |
| 用户显式纠正 | 高权重规则立即写入 |
| 每日定时任务 | Memory Hygiene + Skill Review |
| 用户点击"触发自进化" | 手动启动全量优化 |

---

## 控制台操作

在 **设置 → 功能** 面板中可手动触发：

- **Memory 优化**：扫描并清理低质量记忆条目
- **Skill 审查**：批量检查 skill_library 有效性
- **知识库更新**：重建 BM25 + 向量索引

---

## 可观测性

每次进化动作记录到审计日志：

```json
{
  "event": "semantic_rule_created",
  "rule_id": "rule_047",
  "trigger": "consecutive_sql_failures",
  "confidence": 0.88,
  "session_ids": ["s_001", "s_004"],
  "timestamp": "2026-06-03T09:00:00Z"
}
```

通过 `/api/evolution/history` 接口可查询完整进化历史，了解 Agent "学到了什么"。

---

## 进化边界与防护

自进化不意味着无约束：

- 规则置信度低于 0.7 不生效（仅记录）
- 涉及权限的规则变更需人工确认
- 所有自动写入的规则标记 `auto=true`，可一键回滚
- 进化不修改模型权重，仅修改 prompt 上下文
