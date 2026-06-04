FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 配置 pip 使用阿里云镜像（国内服务器加速）
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ && \
    pip config set global.trusted-host mirrors.aliyun.com

# 先复制依赖文件层（缓存友好）
COPY pyproject.toml /app/

RUN pip install --upgrade pip && \
    pip install \
      "langgraph>=0.2.0" \
      "langchain-core>=0.3.0" \
      "litellm>=1.40.0" \
      "pydantic>=2.0" \
      "sqlglot>=23.0" \
      "cachetools>=5.3" \
      "fastapi>=0.110" \
      "uvicorn>=0.27" \
      "python-multipart>=0.0.9" \
      "qdrant-client>=1.8.0" \
      "redis>=5.0.0" \
      "opentelemetry-sdk>=1.24.0" \
      "opentelemetry-exporter-otlp-proto-grpc>=1.24.0" \
      "curl_cffi>=0.6.0"

# 应用代码
COPY recon_v2 /app/recon_v2
COPY apps /app/apps
COPY tests /app/tests
COPY knowledge_base /app/knowledge_base
COPY data /app/data
COPY .env.example /app/.env.example

# 确保 data 目录存在
RUN mkdir -p /app/data

# 构建统一测试数据库（enterprise + leetcode 合并）
RUN python3 data/merge_unified_db.py

ENV PYTHONPATH=/app
EXPOSE 8000

# 启动：先初始化数据库，再启动 API
CMD ["sh", "-c", "python tests/eval/fixtures/build_test_db.py && uvicorn apps.api.main:app --host 0.0.0.0 --port 8000"]
