# 可观测性与评测体系

> 如何知道 Agent 在线上表现如何，以及哪里出了问题

## 为什么需要这套体系

SQL Agent 的"正确"比传统服务更难定义——同一个问题可以有多种等价 SQL 写法，结果集匹配也存在浮点误差、列顺序差异等噪音。单纯看"有没有报错"远远不够，需要一套从**评测 → 追踪 → 反馈**形成闭环的完整体系。

---

## 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                     触发入口                             │
│        用户 Query / CI 每次 Prompt 变更 / 手动运行       │
├────────────────┬────────────────┬───────────────────────┤
│   评测层        │   可观测层      │   反馈闭环             │
│  tests/eval/   │  TraceLogger   │  POST /feedback        │
│  Golden Set    │  Range Guard   │  episodic.json         │
│  4 维 Metric   │  JSONL + HTML  │  → semantic 规则提炼   │
├────────────────┴────────────────┴───────────────────────┤
│                     持久化层                             │
│  eval_data.sqlite · sessions.sqlite · reports/           │
└─────────────────────────────────────────────────────────┘
```

---

## 一、评测层（离线批量）

### Golden Set

`tests/eval/golden_set.jsonl` 是核心基准，目前覆盖 **50 个案例**，横跨 6 类意图：

| 意图类型 | 案例数 | 典型问题 |
|---------|--------|---------|
| `simple_query` | 10 | "昨天总订单数" |
| `multi_table_join` | 10 | "支付和订单金额不一致的记录" |
| `time_window_recon` | 10 | "对账今天的订单与支付" |
| `nested_diff` | 10 | "按渠道统计退款率差异" |
| `trend_analysis` | 5 | "近 30 天日订单量趋势" |
| `reject / clarify` | 5 | 安全用例 / 模糊用例 |

每条 case 的结构：

```jsonl
{
  "id": "mj-007",
  "query": "支付和订单金额不一致的记录",
  "intent_label": "multi_table_join",
  "difficulty": "hard",
  "expected_sql": "SELECT o.id, o.amount, p.amount FROM orders o JOIN payments p ...",
  "expected_result_summary": "订单/支付金额不一致",
  "tags": ["diff"]
}
```

### 四维评测指标

| 指标 | 含义 | 计算方式 |
|------|------|---------|
| **Exec-Accuracy** | SQL 执行结果与参考结果一致 | result_set hash 比对（含 6 种宽松匹配策略）|
| **Semantic-Match** | 自然语言答案语义等价 | 关键词覆盖 / LLM-as-Judge（可注入）|
| **Avg Latency** | 平均执行延迟 | ms，含 LLM + SQL 执行 |
| **P95 Latency** | 尾部延迟 | 第 95 百分位，衡量稳定性 |

**宽松匹配策略**（按优先级）：

```
1. 精确匹配 hash 相等
2. 两者都返回空集（ok:both_empty）
3. 候选多返回了状态列，截取前 N 列匹配（ok:extra_cols_trimmed）
4. 列顺序不同但值集合相同（ok:col_reorder）
5. 主键集合一致（ok:id_set_match）
6. 单值数值误差 < 0.1%（ok:numeric_approx）
```

### 运行方式

```bash
# 跑全量 50 条，输出 Markdown 报告
python -m tests.eval.runner --target v2 --db data/eval_data.sqlite

# 只跑前 10 条快速 smoke test
python -m tests.eval.runner --target v2 --limit 10

# 对比 v1 vs v2
python -m tests.eval.runner --target v2 --compare v1

# 使用 LeetCode 数据集
python -m tests.eval.runner --target v2 \
  --db data/unified_test.db \
  --golden tests/eval/unified_golden.jsonl
```

### 质量门禁

当 Exec-Accuracy 低于阈值时，CI 应返回非零退出码阻断部署：

```python
# runner.py 末尾加入
if agg["exec_accuracy"] < 0.75:
    print(f"[GATE] EA={agg['exec_accuracy']:.2%} < 75%，拦截部署")
    sys.exit(1)
```

### 意图维度拆解（建议补充）

当前报告只输出汇总指标，加上按意图分组可以精准定位短板：

```
=== Intent Breakdown ===
  simple_query              90.00% (10 cases)
  multi_table_join          80.00% (10 cases)
  time_window_recon         70.00% (10 cases)  ← 重点优化目标
  nested_diff               60.00% (10 cases)  ← 重点优化目标
```

---

## 二、可观测层（运行时追踪）

### TraceLogger

每次 Agent 执行自动生成两份文件：

```
memory/traces/
├── trace-{session_id}.jsonl   # 机器可读，用于自动化分析
└── trace-{session_id}.html    # 人类可读，用于问题排查
```

**追踪的事件类型**：

| 事件 | 触发时机 | 关键字段 |
|------|---------|---------|
| `session_start` | 会话开始 | agent_name, config |
| `llm_request` | LLM 调用前 | model, messages, token_count |
| `llm_response` | LLM 返回后 | content, usage.total_tokens |
| `tool_call` | SQL 执行前 | tool_name, sql, db_path |
| `tool_result` | SQL 执行后 | status, rows_count, duration_ms |
| `range_guard_triggered` | 合理性校验拦截 | check_type, value, action |
| `circuit_breaker` | 熔断器触发 | tool_name, state |
| `session_end` | 会话结束 | duration, total_tokens, error_count |

**事件结构示例**：

```json
{
  "ts": "2026-06-11T14:00:00.000Z",
  "session_id": "s-20260611-a3f2",
  "step": 2,
  "event": "tool_call",
  "payload": {
    "tool_name": "SQLRunner",
    "sql": "SELECT SUM(amount) FROM payments WHERE channel='wechat'",
    "db_path": "data/recon_v2.sqlite"
  }
}
```

### Range Guard（数据合理性校验）

在 `observe.py` 节点内置四类合理性断言，拦截 LLM 生成的逻辑正确但业务异常的结果：

```python
checks = [
    # 总金额不应为负数
    ("negative_amount",  amount < 0),
    # 比率不应超过 100%
    ("ratio_over_100",   ratio > 1.0),
    # COUNT 不应超过百万（通常是 JOIN 爆炸）
    ("count_explosion",  row_count > 1_000_000),
    # 对账查询不应返回空集（可能是时间窗口错误）
    ("empty_recon_result", is_recon_query and len(rows) == 0),
]
```

拦截后写入 TraceLogger，前端可展示告警标记：

```json
{
  "event": "range_guard_triggered",
  "payload": {
    "check": "empty_recon_result",
    "query": "对账昨天的订单与支付",
    "action": "warn_user"
  }
}
```

### 关键追踪维度

除通用事件外，以下维度对 SQL Agent 尤为重要：

| 维度 | 埋点位置 | 用途 |
|------|---------|------|
| Schema Linking 命中率 | `rag/schema_indexer.py` | 定位 SQL 生成错误根源 |
| Plan 选择路径 | `orchestration/plan.py` | 识别意图路由错误 |
| 并行任务数分布 | `parallel_act` 节点 | 评估并行策略有效性 |
| 澄清触发率 | `orchestration/clarify.py` | 衡量理解置信度分布 |
| Self-Correction 重试次数 | `act.py` 错误回溯节点 | 定位高频失败的 SQL 模式 |

---

## 三、线上查询问题获取

线上真实 query 是最宝贵的评测数据来源，有三条获取路径：

### 路径 1：sessions.sqlite（全量，当下可用）

每次对话记录都存在 `data/sessions.sqlite`，messages 字段里含用户原始问题：

```bash
sqlite3 data/sessions.sqlite "
  SELECT
    json_extract(value, '$.query') as query,
    datetime(s.updated/1000, 'unixepoch', 'localtime') as time
  FROM sessions s, json_each(json(s.messages))
  WHERE json_extract(value, '$.role') = 'user'
  ORDER BY s.updated DESC LIMIT 100;
"
```

### 路径 2：episodic.json 差评 case（质量最高）

用户差评（thumbsdown）自动写入 `memory_store/episodic.json`，`outcome=0` 的条目是最有价值的改进样本：

```bash
python - << 'EOF'
import json, pathlib
cases = json.loads(pathlib.Path("memory_store/episodic.json").read_text())
bad = [c for c in cases if c.get("outcome", 1) == 0]
for c in bad:
    print(json.dumps({
        "id": f"online-{c['trace_id'][:8]}",
        "query": c["query"],
        "intent_label": c.get("intent", "unknown"),
        "difficulty": "hard",
        "expected_sql": c.get("sql", ""),  # 需人工修正
        "expected_result_summary": "待标注",
        "tags": ["online_fail"]
    }))
EOF
```

### 路径 3：query_log 表（推荐落地，全量可查）

在 `sessions.sqlite` 补充一张 `query_log` 表，永久记录每次请求：

```sql
CREATE TABLE IF NOT EXISTS query_log (
    trace_id   TEXT PRIMARY KEY,
    tenant_id  TEXT,
    query      TEXT,
    intent     TEXT,
    sql        TEXT,
    latency_ms REAL,
    status     TEXT,      -- 'ok' | 'clarify' | 'reject' | 'error'
    ts         INTEGER    -- epoch ms
);
```

通过状态字段可以快速筛选问题 case：

```bash
# 查所有执行错误的 query
sqlite3 data/sessions.sqlite \
  "SELECT query, latency_ms FROM query_log WHERE status='error' ORDER BY ts DESC LIMIT 20"
```

---

## 四、反馈闭环

```
用户点 ❌（差评）
    │
    ▼ POST /feedback  { trace_id, query, sql, correct: false }
    │
    ├─ 立即：写入 episodic.json（outcome=0, user_flag=1）
    │
    └─ 异步：调用 SkillReviewer
               │
               ▼ 分析失败根因（LLM 分析）
               │
               ▼ 提炼语义规则（如"GMV 对账必须 JOIN refunds 表"）
               │
               ▼ 写入 semantic.json（confidence > 0.7 才生效）
               │
               ▼ 下次同类问题 → plan 节点自动注入该规则
```

**反馈接口响应**：

```json
{
  "status": "ok",
  "trace_id": "s-20260611-a3f2",
  "importance": 0.82,
  "promoted": false,
  "message": "反馈已记录，已触发后台评审"
}
```

---

## 五、评测迭代流程

每次 Prompt 改动或新功能上线，建议执行以下流程：

```
1. 运行全量 Golden Set（50 条）
   python -m tests.eval.runner --target v2

2. 查看意图维度报告
   重点关注 exec_accuracy < 70% 的意图类型

3. 收集失败案例（runner 报告 reason 列）
   按类型分组：语法错误 / 幻觉表名 / 语义偏差 / REJECT 误判

4. 修复最高频失败类型（每轮只改一类）
   经验值：修复一类高频错误通常提升 10~15 个百分点

5. 回归全量，验证无副作用

6. 从线上差评中取 5~10 条补充 Golden Set
   python scripts/export_bad_cases.py >> tests/eval/golden_set.jsonl

7. 重复直到 EA ≥ 目标值
```

---

## 六、Markdown 报告示例

每次 runner 执行后生成 `reports/{target}_{timestamp}.md`：

```markdown
# Eval Report - target: v2

## Coverage
- Total: **50**
- By intent: `{'simple_query': 10, 'multi_table_join': 10, ...}`

## Aggregated Metrics
| Metric          | Value     |
| --------------- | --------- |
| Exec-Accuracy   | **84.00%** |
| Semantic-Match  | **88.00%** |
| Avg Latency (ms) | 1243.5   |
| P95 Latency (ms) | 3891.2   |
| Total tokens    | 48320     |
| Total cost (USD) | 0.0289   |

## Per-case Detail
| case_id | exec | sem | latency_ms | reason |
| ------- | ---- | --- | ---------- | ------ |
| mj-007  | 1    | 1   | 1521.3     | ok     |
| nd-004  | 0    | 0   | 892.1      | expected failed but candidate ok |
```

---

## 相关文档

- [系统架构](./01-architecture.md) — DAG 节点与数据流
- [记忆系统](./04-memory.md) — Episodic / Semantic 持久化
- [自进化机制](./06-self-evolution.md) — 从失败中提炼规则
- [TraceLogger 详细配置](../observability-guide.md) — 事件类型完整参考
