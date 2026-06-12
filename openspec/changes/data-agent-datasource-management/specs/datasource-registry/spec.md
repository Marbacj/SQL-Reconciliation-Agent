## ADDED Requirements

### Requirement: 数据源注册
系统 SHALL 提供 REST API，允许用户通过提交连接配置注册新数据源，注册成功后该数据源可在后续查询中使用。

#### Scenario: 成功注册 SQLite 数据源
- **WHEN** 用户 POST `/api/datasources` 提交 `{"name": "my_db", "type": "sqlite", "db_path": "/data/test.db"}`
- **THEN** 系统返回 `201 Created`，响应体包含 `{"id": "my_db", "status": "registered"}`，且数据源持久化到存储

#### Scenario: 成功注册 MySQL 数据源
- **WHEN** 用户 POST `/api/datasources` 提交包含 host/port/user/password/database 的 MySQL 配置
- **THEN** 系统返回 `201 Created`，数据源持久化，后续可通过 `datasource_id` 引用

#### Scenario: 注册重名数据源
- **WHEN** 用户 POST `/api/datasources` 使用已存在的 name
- **THEN** 系统返回 `409 Conflict`，响应体包含 `{"error": "datasource already exists"}`

#### Scenario: 注册缺少必要字段
- **WHEN** 用户 POST SQLite 数据源但未提供 `db_path`
- **THEN** 系统返回 `422 Unprocessable Entity`，包含字段级错误说明

### Requirement: 数据源列表查询
系统 SHALL 提供 API 返回当前已注册的所有数据源列表（不含密码字段）。

#### Scenario: 查询数据源列表
- **WHEN** 用户 GET `/api/datasources`
- **THEN** 系统返回 `200 OK`，响应体为数组，每项包含 `id/type/description/enabled` 字段，密码字段 MUST 被隐藏（`"***"`）

### Requirement: 数据源删除
系统 SHALL 允许用户通过 name 删除已注册的数据源。

#### Scenario: 成功删除数据源
- **WHEN** 用户 DELETE `/api/datasources/{name}`，该数据源存在
- **THEN** 系统返回 `204 No Content`，数据源从持久化存储中移除

#### Scenario: 删除不存在的数据源
- **WHEN** 用户 DELETE `/api/datasources/{name}`，该数据源不存在
- **THEN** 系统返回 `404 Not Found`

### Requirement: 数据源连通性健康探测
系统 SHALL 提供健康探测接口，对指定数据源执行轻量连接测试并返回结果。

#### Scenario: 数据源连接正常
- **WHEN** 用户 GET `/api/datasources/{name}/health`，数据库连接可达
- **THEN** 系统在 5s 内返回 `{"status": "ok", "latency_ms": <number>}`

#### Scenario: 数据源连接失败
- **WHEN** 用户 GET `/api/datasources/{name}/health`，数据库不可达或认证失败
- **THEN** 系统返回 `{"status": "error", "message": "<友好错误描述>"}`（HTTP 200，业务层错误）
