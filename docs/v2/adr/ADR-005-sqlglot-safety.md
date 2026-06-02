# ADR-005: SQL 安全护栏用 sqlglot AST 而非关键字黑名单

**Status**: Accepted
**Date**: 2026-06-01

## Context

v1 SQL 安全检查是简单关键字黑名单：
```python
DANGEROUS = ["delete", "drop", "update", "alter", "insert"]
if any(kw in sql.lower() for kw in DANGEROUS):
    return False
```

这种实现已知至少 4 种绕过方式：
1. **字符串字面量误杀**：`SELECT name FROM t WHERE name = 'DELETE FROM users'` 被错误拒绝
2. **注释绕过**：`SELECT * FROM t; /* anything */ DROP TABLE x` 部分实现会过
3. **Unicode 同形**：`ＤＲＯＰ TABLE x` (全角字符) 黑名单可能漏
4. **多 statement**：`SELECT 1; DELETE FROM x` 简单实现可能只看第一句

## Decision

用 `sqlglot` 把 SQL 解析为 AST，做两步检查：

### Step 1: Verb 白名单
- 解析所有 statement（`sqlglot.parse`，不是 `parse_one`）
- 每个根节点必须是 `Select` 或 `With`
- 任一不符合 → 拒绝

### Step 2: 子树扫描
- 遍历每个 statement 的所有子节点（`stmt.walk()`）
- 任一节点是 `Delete/Update/Insert/Drop/Alter/Create/TruncateTable/Command` → 拒绝
- 关键点：**字符串字面量是 `Literal` 节点而非 expression node，不会误杀**

### Step 3: 解析失败 = 不安全
- 宁可误杀也不放过：parse 抛异常 → 直接 reject

### Step 4: EXPLAIN 预校验
- 通过安全检查后，先执行 `EXPLAIN QUERY PLAN` 验证 SQL 在目标 DB 可执行
- 失败原因（表不存在等）作为错误返回 Agent，让 Agent 自我修复

### Step 5: LIMIT Guard
- SELECT 没有 LIMIT 时自动追加 `LIMIT 1000`
- 防止全表扫描误炸数据库

## Consequences

**正向**：
- 4 种已知攻击全部拦截（在单测中验证）
- 字符串字面量含 DELETE 不会误杀（已加入 Golden Set 边界 case）
- 多语句 + 注释组合攻击天然防御（AST 看的是结构而非文本）
- 跨方言：sqlglot 支持 MySQL/PG/SQLite 等多方言

**负向**：
- 增加一个依赖（sqlglot ~ 3MB）
- 单次 SQL 解析增加 1-5ms 开销（可忽略）
- 极个别 SQL 方言特性 sqlglot 解析失败 → 误拒（罕见）

## Alternatives Considered

1. **黑名单 + 正则**：已证明不可靠
2. **白名单 + 正则**：维护成本高，易遗漏 SQL 语法
3. **手写 SQL parser**：重新发明轮子
4. **数据库账号只读权限**：业界推荐做法，但**应用层防护与数据库层防护是 defense-in-depth**，两者都要做
