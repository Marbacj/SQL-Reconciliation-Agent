## Why

当前项目数据库连接在启动时硬编码，`data/datasources.json` 为空文件，用户无法自助接入自己的数据库。这是从「SQL 对账工具」进化成「通用 Data Agent 平台」的核心阻塞点——没有动态数据源管理，就无法服务任何新用户。

## What Changes

- **新增** 动态数据源注册 API（`POST /api/datasources`），用户粘贴连接串即可接入新库
- **新增** Schema 自动发现与增量向量化（接入新库后自动爬取表结构并写入 RAG 索引）
- **新增** 数据源连通性健康探针（接入时实时检测连通性，失败时返回友好错误）
- **新增** 多租户数据源隔离（每个 user/session 只能访问自己授权的数据源）
- **修改** `apps/api` 中的 `/query` 端点，支持在请求中指定 `datasource_id`
- **修改** `recon_v2/rag/schema_indexer.py`，支持针对指定数据源做增量索引更新
- **修改** 前端 UI，新增「数据源管理」页面（列表 + 新增 + 删除 + 健康状态）

## Capabilities

### New Capabilities

- `datasource-registry`: 数据源的 CRUD 管理，包括注册、列表、删除、连通性探测
- `schema-auto-discovery`: 新数据源接入后自动爬取表结构并触发 RAG 向量化
- `datasource-isolation`: 请求路由时根据 datasource_id 动态切换底层连接，并隔离不同用户的 schema 上下文

### Modified Capabilities

<!-- 无现有 spec 需要修改 -->

## Impact

- **代码**: `apps/api/`（新增路由）、`recon_v2/adapters/`（新增连接池管理）、`recon_v2/rag/schema_indexer.py`（增量索引）、`data/datasources.json`（从空文件升级为持久化存储）
- **API**: 新增 `/api/datasources` REST 端点；`/api/query` 新增 `datasource_id` 参数
- **依赖**: 无新外部依赖（SQLite/MySQL/PG 适配器已存在于 `recon_v2/adapters/`）
- **数据**: `data/datasources.json` 替换为 SQLite 持久化（`data/agents.sqlite` 中新增 datasources 表）
