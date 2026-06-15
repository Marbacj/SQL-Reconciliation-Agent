#!/bin/zsh
# ─────────────────────────────────────────────
#  SQL Reconciliation Agent — 本地启动脚本
#  用法:
#    ./start.sh          启动服务（前台，Ctrl+C 停止）
#    ./start.sh restart  停止旧进程并重新启动
#    ./start.sh stop     仅停止当前运行的服务
# ─────────────────────────────────────────────

set -e

PORT=8000
APP="apps.api.main:app"
PID_FILE=".recon_server.pid"

# ── 颜色输出 ──
green()  { echo "\033[32m$*\033[0m"; }
yellow() { echo "\033[33m$*\033[0m"; }
red()    { echo "\033[31m$*\033[0m"; }

# ── 切换到项目根目录 ──
cd "$(dirname "$0")"

# ── 停止旧进程 ──
stop_server() {
  if [[ -f "$PID_FILE" ]]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
      yellow "停止旧进程 (PID $OLD_PID)..."
      kill "$OLD_PID"
      sleep 1
    fi
    rm -f "$PID_FILE"
  fi

  # 兜底：找同端口进程一并清掉
  PIDS=$(lsof -ti :$PORT 2>/dev/null || true)
  if [[ -n "$PIDS" ]]; then
    yellow "清理占用 :$PORT 的进程: $PIDS"
    echo "$PIDS" | xargs kill -9 2>/dev/null || true
    sleep 1
  fi

  # 杀掉持有 Milvus Lite fcntl 锁的进程（删文件不够，必须杀进程）
  find ./data -name "LOCK" -type f 2>/dev/null | while read lf; do
    LOCK_PIDS=$(lsof -t "$lf" 2>/dev/null || true)
    if [[ -n "$LOCK_PIDS" ]]; then
      yellow "清除持有 Milvus 锁的进程: $LOCK_PIDS ($lf)"
      echo "$LOCK_PIDS" | xargs kill -9 2>/dev/null || true
      sleep 1
    fi
    rm -f "$lf"
  done
}

# ── 加载 .env ──
load_env() {
  if [[ -f ".env" ]]; then
    set -o allexport
    source .env
    set +o allexport
    green "已加载 .env"
  fi
}

# ── 子命令处理 ──
case "${1:-start}" in
  stop)
    stop_server
    green "服务已停止。"
    exit 0
    ;;
  restart)
    stop_server
    ;;
esac

# ── 启动 ──
load_env
stop_server  # 确保端口干净

green "启动 SQL Reconciliation Agent..."
echo "  地址: http://localhost:$PORT"
echo "  控制台: http://localhost:$PORT/ui/index.html"
echo "  文档: http://localhost:$PORT/docs.html"
echo "  按 Ctrl+C 停止"
echo ""

# 记录 PID（后台启动时用）
PYTHON="${0%/*}/venv/bin/python"
[ -x "$PYTHON" ] || PYTHON=python3

# --reload 与 Milvus Lite (gRPC+fcntl) 不兼容：
#   reloader 父进程 import app → 初始化 Milvus → 获取 fcntl 锁
#   fork 出 worker 子进程 → 再次初始化 Milvus → 锁冲突崩溃
# 默认不加 --reload；开发热重载用 ./start.sh dev
if [[ "${1:-start}" == "dev" ]]; then
  yellow "开发模式（热重载，仅监听源码目录）"
  RELOAD_FLAGS="--reload --reload-dir apps --reload-dir recon_v2"
else
  RELOAD_FLAGS=""
fi

"$PYTHON" -m uvicorn "$APP" \
  --host 0.0.0.0 \
  --port "$PORT" \
  $RELOAD_FLAGS \
  --log-level info &

SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"

# 等待服务就绪
for i in $(seq 1 10); do
  sleep 1
  if curl -s "http://localhost:$PORT/health" > /dev/null 2>&1; then
    green "✓ 服务已就绪 (PID $SERVER_PID)"
    break
  fi
  echo "  等待服务启动... ($i/10)"
done

# 前台等待（Ctrl+C 触发清理）
trap "stop_server; green '服务已停止。'; exit 0" INT TERM

wait "$SERVER_PID"
