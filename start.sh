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
    echo "$PIDS" | xargs kill 2>/dev/null || true
    sleep 1
  fi
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
"$PYTHON" -m uvicorn "$APP" \
  --host 0.0.0.0 \
  --port "$PORT" \
  --reload \
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
