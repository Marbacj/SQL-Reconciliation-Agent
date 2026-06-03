# SQL 权限管控

> 防止 LLM 生成危险 SQL，保障数据安全

## 为什么需要权限控制

LLM 生成的 SQL 具有不可预测性：可能输出 `DROP TABLE`、`DELETE FROM` 或越权查询敏感字段。在生产环境，一条错误的 SQL 可能造成不可逆损失。

---

## 三层防护体系

```
用户自然语言
      │
      ▼
┌─────────────────────┐
│  Layer 1: 意图过滤   │  关键词黑名单、意图分类
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  Layer 2: AST 静态  │  SQLGlot 解析，禁止 DDL/DML
│  安全检查            │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  Layer 3: 运行时     │  表级 / 字段级白名单
│  权限过滤            │
└──────────┬──────────┘
           │
           ▼
        执行 SQL
```

---

## Layer 2：SQLGlot AST 检查

```python
import sqlglot

FORBIDDEN_STATEMENT_TYPES = {
    sqlglot.exp.Drop, sqlglot.exp.Delete,
    sqlglot.exp.Insert, sqlglot.exp.Update,
    sqlglot.exp.Create, sqlglot.exp.AlterTable,
}

def check_sql_safety(sql: str) -> bool:
    tree = sqlglot.parse_one(sql)
    for node in tree.walk():
        if type(node) in FORBIDDEN_STATEMENT_TYPES:
            raise PermissionError(f"禁止操作: {type(node).__name__}")
    return True
```

只允许 `SELECT`，DDL/DML 在语法树层面直接拦截，不依赖字符串匹配（防绕过）。

---

## Layer 3：表级 / 字段级白名单

```yaml
# permission_config.yaml
roles:
  analyst:
    allow_tables:
      - orders
      - payments
    deny_columns:
      - user_phone
      - id_card_number
  admin:
    allow_tables: "*"
    deny_columns: []
```

Schema Linking 阶段将用户可见 schema 限制在白名单内，LLM 看不到禁止的表/字段，从根源切断越权可能。

---

## 错误分类与重试策略

| 错误类型 | 处理方式 | 重试 |
|---------|---------|------|
| PermissionError (DDL/DML) | 立即终止，返回拒绝提示 | 否 |
| SyntaxError | 将错误上下文反馈给 LLM 重生成 | 最多 3 次 |
| TimeoutError | 终止并提示查询过于复杂 | 否 |
| DataError | 反馈给 observe 节点做合理性判断 | 否 |

---

## 自我修正闭环

```
SQL 执行失败
      │
      ▼ observe 节点捕获错误
      last_sql_error = "..."
      obs_count += 1
      │
      ▼ route: obs_count < MAX_RETRY?
      是 → reflect 节点携带错误重新生成 SQL
      否 → 降级：返回"无法完成，请简化问题"
```

权限错误不进入重试循环，避免 LLM 反复尝试绕过限制。

---

## 审计日志

每条 SQL 执行记录包含：

```json
{
  "session_id": "abc123",
  "sql": "SELECT amount FROM orders WHERE ...",
  "role": "analyst",
  "tables_accessed": ["orders"],
  "execution_time_ms": 42,
  "status": "success",
  "timestamp": "2026-06-03T09:00:00Z"
}
```

审计日志写入 `memory_store/audit.db`，支持按 session / 角色 / 时间范围查询。
