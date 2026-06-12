## ADDED Requirements

### Requirement: 查询请求绑定指定数据源
系统 SHALL 允许 `/api/query` 请求通过 `datasource_id` 字段指定目标数据源，Agent 在该数据源上执行所有 SQL。

#### Scenario: 查询时指定数据源
- **WHEN** 用户 POST `/api/query` 包含 `{"question": "...", "datasource_id": "my_db"}`
- **THEN** Agent 使用 `my_db` 对应的 adapter 执行 SQL，不影响其他用户或其他查询的数据源绑定

#### Scenario: 未指定数据源时 fallback 到默认库
- **WHEN** 用户 POST `/api/query` 不包含 `datasource_id`
- **THEN** Agent 使用系统默认数据源（启动时配置），行为与当前一致

#### Scenario: 指定不存在的数据源
- **WHEN** 用户 POST `/api/query` 提交的 `datasource_id` 不在注册表中
- **THEN** 系统返回 `400 Bad Request`，包含 `{"error": "datasource not found: <id>"}`

### Requirement: 数据源上下文请求隔离
系统 SHALL 确保不同请求的数据源上下文互不干扰，Schema Linking 只在当前请求绑定的数据源范围内检索。

#### Scenario: 并发请求数据源隔离
- **WHEN** 请求 A 绑定 `datasource_id=db_a`，请求 B 同时绑定 `datasource_id=db_b`
- **THEN** 请求 A 的 Schema 检索结果 MUST 只包含 `db_a` 的表，请求 B MUST 只包含 `db_b` 的表，两者 MUST NOT 混合
