# SQL Reconciliation Agent v2 — Runbook

## 0. 系统要求

- Python 3.10+
- macOS / Linux
- Docker（可选，仅生产部署需要）

## 1. 本地开发（无外部依赖）

```bash
# 1) 安装核心依赖
python3 -m pip install --user \
  langgraph langchain-core litellm pydantic sqlglot cachetools pytest

# 2) 准备测试数据
python3 -m tests.eval.fixtures.build_test_db --db data/eval_data.sqlite

# 3) 跑测试
python3 -m pytest tests/unit -v

# 4) 跑评测
python3 -m tests.eval.runner --target v2 --db data/eval_data.sqlite

# 5) CLI 体验
python3 apps/cli/demo.py --query "查询昨天所有订单的总数"

# 6) REPL
python3 apps/cli/demo.py
```

## 2. 启动 API 服务

```bash
# 安装 web 依赖
python3 -m pip install --user fastapi uvicorn

# 启动
python3 -m uvicorn apps.api.main:app --host 0.0.0.0 --port 8000

# 测试
curl http://localhost:8000/health
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"query":"查询今天所有订单的总数"}'

# Prometheus
curl http://localhost:8000/metrics
```

## 3. 容器化部署

```bash
cd deploy/
docker compose up -d
docker compose logs -f app
```

服务清单：
| 服务 | 端口 | 用途 |
| --- | --- | --- |
| app | 8000 | FastAPI 主服务 |
| redis | 6379 | LLM Cache |
| qdrant | 6333 | 向量库（Stage 3 完整版用） |
| phoenix | 6006 | OTel UI / Trace 可视化 |

## 4. 环境变量

见 `.env.example`，核心：

| Key | 默认 | 说明 |
| --- | --- | --- |
| `LLM_PROVIDER` | deepseek | LiteLLM provider name |
| `LLM_MODEL` | deepseek-chat | 模型 |
| `LLM_API_KEY` | (空) | API key（**必填**） |
| `REDIS_URL` | redis://localhost:6379/0 | Cache 后端 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | http://localhost:6006 | Phoenix 上报地址 |
| `SQLITE_DB_PATH` | data/recon_v2.sqlite | Memory v2 持久化 |
| `EVAL_DB_PATH` | data/eval_data.sqlite | 评测数据源 |
| `BUDGET_MAX_TOKENS` | 50000 | 单 trace token 上限 |
| `BUDGET_MAX_SECONDS` | 120 | 单 trace 超时 |
| `BUDGET_MAX_STEPS` | 15 | 单 trace 最大步数 |

## 5. 常见故障

### 5.1 LiteLLM 调用失败
- 检查 `LLM_API_KEY` 是否设置
- 检查网络是否可达 provider endpoint
- 系统会自动 retry 3 次（指数退避），仍失败会抛 `RuntimeError`

### 5.2 Redis 不可达
- 自动降级到内存 LRU 缓存
- `/health` 端点会显示当前 cache backend

### 5.3 Qdrant 不可达 / 未启动
- RAG 降级到 BM25-only
- `/health` 显示 `retriever: bm25-only (degraded)`

### 5.4 评测大面积失败
- 用 stub adapter 自检评测脚本：`python3 -m tests.eval.runner --target stub`
- 应返回 100% exec_accuracy

## 6. 数据流图

```
User Query
   ↓
[Route Node] —— rule + LLM —— intent + confidence
   ↓ (conf ≥ 0.6)            ↓ (conf < 0.6 / boundary)
[Plan Node]                  [Clarify Node] → END
   ↓ steps
[Act Node] ←—————┐
   ↓ tool_call    │
[Observe Node]    │ retry
   ↓ → act / reflect
[Reflect Node]
   ↓ submit_skill_review (async)
END
```

## 7. 监控

- **Phoenix UI**: http://localhost:6006 — 全链路 trace 可视化
- **Prometheus**: `curl http://localhost:8000/metrics`
- **Trace by ID**: `curl http://localhost:8000/trace/<trace_id>`
