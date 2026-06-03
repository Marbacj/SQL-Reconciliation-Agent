# 记忆系统

> 三层记忆架构：Working / Episodic / Semantic

## 为什么 Agent 需要记忆

无状态的 LLM 每次对话都从零开始。对账场景中，用户可能连续追问、修正错误、积累偏好——这些"经验"如果每次都丢失，Agent 会反复犯同样的错误，用户体验极差。

---

## 三层记忆模型

```
┌─────────────────────────────────────────────┐
│          Working Memory（工作记忆）           │
│  当前会话上下文，存活于单次对话，会话结束清除   │
├─────────────────────────────────────────────┤
│          Episodic Memory（情节记忆）          │
│  历史对账案例，按 session_id 索引，可检索回放  │
├─────────────────────────────────────────────┤
│          Semantic Memory（语义记忆）          │
│  提炼的业务规则、用户偏好，长期有效           │
└─────────────────────────────────────────────┘
```

---

## Working Memory

```python
class GraphState(TypedDict):
    user_query: str
    parallel_plan: list[SQLStep]
    parallel_results: list[QueryResult]
    observation: Observation
    reflection: str
    last_sql_error: str | None
    obs_count: int
    final_answer: str
```

`GraphState` 就是 Working Memory，随节点传递，自动随 LangGraph 运行生命周期管理。

---

## Episodic Memory

每次对账任务完成后，将案例写入 SQLite：

```sql
CREATE TABLE episodic_case (
    id          TEXT PRIMARY KEY,
    session_id  TEXT,
    user_query  TEXT,
    sql_used    TEXT,
    result_hash TEXT,
    anomaly     BOOLEAN,
    created_at  DATETIME
);
```

检索时用 BM25 匹配历史问法，找到相似案例后作为 few-shot 注入 plan 节点，帮助 LLM 更快生成正确 SQL。

---

## Semantic Memory

由 reflect 节点在发现规律时主动提炼：

```python
# 当某类 SQL 连续出错 3 次
if error_pattern_count >= 3:
    rule = await llm.extract_rule(error_cases)
    await memory_store.save_semantic_rule(rule)
```

语义规则示例：
```json
{
  "rule": "查询 payments 表时必须加 status='settled' 过滤条件",
  "confidence": 0.92,
  "derived_from": ["session_001", "session_004", "session_009"]
}
```

---

## 记忆提升（Promotion）机制

```
Working Memory
      │ 会话结束，写入
      ▼
Episodic Memory
      │ 相同模式出现 N 次，提炼
      ▼
Semantic Memory
      │ 语义规则置信度衰减 / 被反驳，删除
      ▼
      ╳（遗忘）
```

---

## 记忆检索流

```python
async def enrich_with_memory(query: str) -> MemoryContext:
    # 1. 情节记忆：找最相似历史案例
    episodes = await memory_store.search_episodic(query, top_k=3)
    
    # 2. 语义规则：加载当前业务规则
    rules = await memory_store.get_semantic_rules(domain="payments")
    
    return MemoryContext(episodes=episodes, rules=rules)
```

---

## 持久化存储

```
memory_store/
├── episodic.db      # SQLite，情节案例
├── semantic.db      # SQLite，提炼规则  
└── audit.db         # SQLite，执行审计日志
```

全部使用 SQLite，零外部依赖，部署简单，数据文件可直接备份。
