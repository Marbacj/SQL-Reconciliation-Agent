## ADDED Requirements

### Requirement: 三层 Memory 抽象
系统 SHALL 定义 `MemoryStore` 接口，封装三层：Working（内存 LRU 20）/ Episodic（SQLite case 表）/ Semantic（SQLite rule 表）。

#### Scenario: 联合检索
- **WHEN** 调用 memory.query(q, k=5)
- **THEN** 三层各检索 top-5 后去重合并，按 confidence 排序返回 k 条

### Requirement: 重要性打分 Promotion
系统 SHALL 在 memory.write 时计算重要性分数 = 0.4×outcome + 0.3×novelty + 0.3×user_flag；分数高于阈值（默认 0.6）则将 case 提升至 Episodic 层。

#### Scenario: 高重要性自动入 Episodic
- **WHEN** 用户标注 user_flag=1 的 case 触发 memory.write
- **THEN** 计算得分 ≥ 0.6，case 写入 episodic_case 表

#### Scenario: 低重要性仅留 Working
- **WHEN** outcome=0 / novelty<0.3 / user_flag=0
- **THEN** 得分 < 0.6，case 仅进 Working LRU，不持久化

### Requirement: LLM Consolidation Job
系统 SHALL 提供定期任务（默认每日 02:00）扫描 Episodic，对重复 ≥ 5 次的 query pattern 调用 LLM 归纳为 semantic_rule，并经 Critic 评分 > 0.7 才入库。

#### Scenario: 重复模式归纳
- **WHEN** Episodic 中存在 ≥ 5 条相同 intent 且 query embedding 相似度 ≥ 0.85 的 case
- **THEN** Consolidation Job 调用 LLM 输出抽象规则，入 semantic_rule 表 confidence=0.7

### Requirement: Confidence 衰减 / 归档
系统 SHALL 对 30 天未使用且 confidence < 0.5 的 semantic_rule 自动标记 archived=1。

#### Scenario: 30 天未用规则归档
- **WHEN** 衰减任务执行时发现规则 last_used_at < now - 30d 且 confidence < 0.5
- **THEN** 该规则 archived=1，后续 query 不再召回

### Requirement: SQLite 持久化 schema
Episodic 与 Semantic 持久化 MUST 使用 SQLite，schema 包含 success_count / fail_count / last_used_at / embedding 字段。

#### Scenario: 写入 case 含成功统计
- **WHEN** Episodic 写入新 case
- **THEN** success_count 默认 0，每次 Agent 用此 case 成功后 ++
