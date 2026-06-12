## ADDED Requirements

### Requirement: 新数据源接入时自动触发 Schema 索引
系统 SHALL 在新数据源注册成功后，异步后台触发该数据源的 Schema 发现与向量化，无需用户手动操作。

#### Scenario: 注册后自动触发索引
- **WHEN** 数据源注册 API 返回 `201 Created` 后
- **THEN** 系统在后台异步执行 Schema 爬取，`GET /api/datasources/{name}/status` 返回 `{"index_status": "indexing"}`

#### Scenario: 索引完成
- **WHEN** 后台 Schema 索引任务完成
- **THEN** `GET /api/datasources/{name}/status` 返回 `{"index_status": "ready", "table_count": <number>}`

#### Scenario: 索引失败
- **WHEN** 后台索引过程中数据库连接中断
- **THEN** `GET /api/datasources/{name}/status` 返回 `{"index_status": "failed", "error": "<原因>"}`，数据源仍保持已注册状态

### Requirement: 手动触发重新索引
系统 SHALL 提供接口允许用户手动触发指定数据源的 Schema 重新索引（应对表结构变更场景）。

#### Scenario: 手动重新索引
- **WHEN** 用户 POST `/api/datasources/{name}/reindex`
- **THEN** 系统返回 `202 Accepted`，后台异步重新执行 Schema 发现，并覆盖旧索引

### Requirement: Schema 按数据源 Namespace 隔离
系统 SHALL 在向量存储中用 `{datasource_id}::{table_name}` 作为键，确保不同数据源的 Schema 不互相污染。

#### Scenario: 多数据源 Schema 检索不混淆
- **WHEN** 用户对 datasource_id=`db_a` 发起查询，`db_a` 和 `db_b` 都有名为 `orders` 的表
- **THEN** Schema Linking 只返回 `db_a::orders`，不返回 `db_b::orders`
