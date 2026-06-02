## ADDED Requirements

### Requirement: sqlglot AST 解析
系统 SHALL 使用 sqlglot 将待执行 SQL 解析为 AST，无法解析时拒绝执行。

#### Scenario: 不合法 SQL 拒绝
- **WHEN** sql = "SELEC * FROM"
- **THEN** sqlglot.parse_one 抛异常，is_safe 返回 (False, "parse error: ...")

### Requirement: Verb 白名单校验
SQL 根节点 verb MUST 在白名单 `{SELECT, WITH}` 内，其他一律拒绝。

#### Scenario: DELETE 被拦截
- **WHEN** sql = "DELETE FROM users WHERE id=1"
- **THEN** is_safe 返回 (False, "verb DELETE not allowed")

#### Scenario: SELECT 通过
- **WHEN** sql = "SELECT * FROM orders LIMIT 10"
- **THEN** is_safe 返回 (True, "ok")

### Requirement: 危险节点子树扫描
AST 子树 MUST 不含 `Delete / Update / Insert / Drop / Alter / Create / TruncateTable` 节点，否则拒绝。

#### Scenario: 字符串字面量中含 DELETE 关键字不误杀
- **WHEN** sql = "SELECT name FROM t WHERE name = 'DELETE FROM users'"
- **THEN** is_safe 返回 (True, "ok")，因 DELETE 是 Literal 不是 expression node

#### Scenario: 注释绕过攻击拦截
- **WHEN** sql = "SELECT * FROM t; /* DROP TABLE x */ DROP TABLE x"
- **THEN** sqlglot 解析两个 statement，第二个含 Drop 节点，is_safe 返回 False

### Requirement: EXPLAIN 预校验
对通过安全检查的 SQL，SQLAdapter SHALL 先执行 EXPLAIN 检查 SQL 是否在目标数据源可执行，再真正运行。

#### Scenario: EXPLAIN 失败终止
- **WHEN** EXPLAIN 报错（如表不存在）
- **THEN** 返回错误信息给 Agent，不执行实际查询
