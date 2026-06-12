## 1. API 层 - 数据源 CRUD 路由

- [x] 1.1 在 `apps/api/` 中新建 `datasources.py` 路由文件，注册 `router = APIRouter(prefix="/api/datasources")`
- [x] 1.2 实现 `POST /api/datasources`：接收 `DataSourceConfig`，调用 `DataSourceRegistry.register()`，返回 `201`
- [x] 1.3 实现 `GET /api/datasources`：调用 `registry.list_all()`，密码字段脱敏后返回
- [x] 1.4 实现 `DELETE /api/datasources/{name}`：调用 `registry.unregister()`，处理 404
- [x] 1.5 实现 `GET /api/datasources/{name}/health`：调用 `build_adapter(cfg).test_connection()`，返回延迟或错误信息
- [x] 1.6 实现 `GET /api/datasources/{name}/status`：返回 `index_status`（indexing/ready/failed）和 `table_count`
- [x] 1.7 在主 `app.py` / `main.py` 中 include 新路由

## 2. DataSourceRegistry 能力补齐

- [x] 2.1 在 `DataSourceRegistry` 中添加 `list_all()` 方法，返回所有 `DataSourceEntry` 列表
- [x] 2.2 为 `DataSourceEntry` 添加 `index_status: str`（pending/indexing/ready/failed）和 `table_count: int` 字段
- [x] 2.3 在各 SQLAdapter 基类（`adapters/base.py`）添加 `test_connection() -> dict` 抽象方法
- [x] 2.4 在 `SQLiteAdapter`/`MySQLAdapter`/`PostgreSQLAdapter` 中实现 `test_connection()`（执行 `SELECT 1`，测量延迟）

## 3. Schema 自动发现与增量索引

- [x] 3.1 修改 `rag/schema_indexer.py`，`build_index()` 接受可选 `datasource_id` 参数
- [x] 3.2 向量化写入时 key 改为 `{datasource_id}::{table_name}`（或默认 `default::{table_name}`）
- [x] 3.3 新增 `index_datasource(datasource_id: str)` 异步函数，用于后台任务调用
- [x] 3.4 在 `POST /api/datasources` 注册成功后，用 `asyncio.create_task(index_datasource(name))` 触发后台索引
- [x] 3.5 索引过程中更新 `DataSourceEntry.index_status`（indexing → ready/failed）
- [x] 3.6 实现 `POST /api/datasources/{name}/reindex`：重置 index_status 并重新触发后台索引

## 4. 查询路由 datasource_id 支持

- [x] 4.1 修改 `GraphState`（`orchestration/state.py`），新增 `datasource_id: Optional[str] = None`
- [x] 4.2 修改 `/api/query` 请求模型（`QueryRequest`），新增可选 `datasource_id: Optional[str]`
- [x] 4.3 在请求处理入口将 `datasource_id` 写入 `GraphState`
- [x] 4.4 修改 `act.py`（或 `sql_runner.py`），在获取 adapter 时优先使用 `state.datasource_id` 对应的 adapter，fallback 到默认
- [x] 4.5 修改 `schema_indexer.py` 检索逻辑，按 `datasource_id` 过滤 namespace，确保 Schema Linking 隔离

## 5. 前端 UI - 数据源管理页

- [x] 5.1 在前端新增「数据源」导航入口
- [x] 5.2 实现数据源列表页：展示 name/type/status/健康度，支持删除操作
- [x] 5.3 实现「添加数据源」表单：type 下拉切换 SQLite/MySQL/PG 字段，提交后显示索引进度
- [x] 5.4 实现查询框数据源选择器：下拉选择已注册且 index_status=ready 的数据源

## 6. 测试与验证

- [ ] 6.1 为 `datasources.py` 路由写集成测试（注册/列表/删除/健康探测 happy + error path）
- [ ] 6.2 为 `index_datasource()` 写单元测试（验证 namespace 隔离 `db_a::orders` vs `db_b::orders`）
- [ ] 6.3 写并发隔离测试：同时发起两个绑定不同 `datasource_id` 的查询，验证 Schema Linking 不混淆
- [x] 6.4 手动 E2E 验证：注册一个新 SQLite 数据库 → 等待索引完成 → 发起自然语言查询 → 验证 SQL 使用正确表名
