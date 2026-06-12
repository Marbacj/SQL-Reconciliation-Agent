## Context

项目已有 `recon_v2/adapters/` 模块，包含完整的 SQLite/MySQL/PostgreSQL 适配器和 `DataSourceRegistry` 单例（基于 JSON 持久化）。`DataSourceConfig` 和 `DataSourceEntry` 模型已定义。现有问题：

1. `DataSourceRegistry` 只能在 Python 代码内使用，没有暴露 REST API
2. `data/datasources.json` 默认为空，无 UI 入口让用户自助接入
3. `schema_indexer.py` 的向量化是全量的，没有针对单个数据源的增量触发机制
4. 请求路由（`/query`）不支持 `datasource_id` 参数，固定使用启动时的默认库

## Goals / Non-Goals

**Goals:**
- 暴露 `/api/datasources` REST 端点（CRUD + 健康探测），复用已有 `DataSourceRegistry`
- 在接入新数据源时自动触发 Schema 增量向量化（异步后台任务）
- `/api/query` 支持 `datasource_id` 参数，按请求动态切换底层连接
- 前端新增「数据源管理」页面

**Non-Goals:**
- 不做连接池管理（每次查询新建连接，适配器已自带 timeout 控制）
- 不做密码加密存储（当前阶段 JSON 明文，生产环境接 Secret Manager 是后续事项）
- 不做跨数据源 JOIN 查询（单次 query 绑定一个 datasource_id）
- 不做数据源权限 RBAC（多租户隔离是独立 change）

## Decisions

### D1：复用 DataSourceRegistry，不重建数据模型

**选择**：直接在 `DataSourceRegistry.get_instance()` 上层包装 FastAPI 路由，不引入新的 ORM 层。

**理由**：`DataSourceConfig` Pydantic 模型已定义且完整，`_save/_load` 持久化逻辑可用。重建会导致两套模型不同步。

**否决方案**：引入 SQLAlchemy 管理 datasources 表 → 增加依赖复杂度，短期无必要。

---

### D2：Schema 索引增量化：按 datasource_id 分 namespace

**选择**：`schema_indexer.py` 在 `build_index()` 时接受可选的 `datasource_id` 参数，写入 RAG 时用 `{datasource_id}::{table_name}` 作为 key，查询时按 datasource_id 过滤。

**理由**：向量库当前用 JSON 文件存储（`data/schema_index.json`），namespace 前缀是最低成本的隔离方式，不需要改底层存储结构。

**否决方案**：每个数据源一个独立的向量库文件 → 文件数量随数据源增加，管理复杂。

---

### D3：schema 自动发现用异步后台任务

**选择**：POST /datasources 注册成功后，用 `asyncio.create_task()` 后台触发 `index_datasource(datasource_id)`，API 立即返回 `202 Accepted`，前端轮询 `/datasources/{id}/status` 查看索引进度。

**理由**：大型数据库（100+ 张表）索引可能耗时 30s+，同步阻塞会导致 API 超时。

**否决方案**：同步等待索引完成再返回 → 超时风险，体验差。

---

### D4：请求路由中的数据源切换

**选择**：`GraphState` 新增 `datasource_id: Optional[str]` 字段，Route 节点读取后写入 Context，Act 节点从 Context 获取 adapter 时优先查 `datasource_id` 对应的 adapter，fallback 到默认库。

**理由**：最小化改动范围，不需要改动中间的 Plan/Observe 节点。

## Risks / Trade-offs

- **[密码明文存储]** `datasources.json` 存储明文密码 → 接受，短期演示场景，生产环境文档中注明需接 Secret Manager
- **[并发索引冲突]** 多个数据源同时触发后台索引可能写入 schema JSON 文件冲突 → 用文件写锁（`threading.Lock`）序列化写操作
- **[健康探针误判]** 数据库网络抖动可能触发误报 → 健康探针设置 3s 超时 + 重试 1 次，降低误报率
- **[索引陈旧]** 数据库表结构变更后索引不会自动更新 → 提供手动触发重新索引的 API（`POST /datasources/{id}/reindex`），告知用户
