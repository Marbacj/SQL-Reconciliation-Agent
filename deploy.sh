#!/bin/bash
# 快速部署脚本
# 用法：
#   ./deploy.sh ui      # 只更新前端（秒级，不重建镜像）
#   ./deploy.sh all     # 全量部署（重建镜像，需要几分钟）
#
# 前置：配置 SSH 免密
#   ssh-keygen -t ed25519 -f ~/.ssh/recon_deploy -N ""
#   ssh-copy-id -i ~/.ssh/recon_deploy.pub root@47.95.229.48
# 然后在 ~/.ssh/config 中加入：
#   Host recon-server
#     HostName 47.95.229.48
#     User root
#     IdentityFile ~/.ssh/recon_deploy
#     ControlMaster auto
#     ControlPath ~/.ssh/cm-%r@%h:%p
#     ControlPersist 60s

SERVER="recon-server"
REMOTE_DIR="/root/SQL-Reconciliation-Agent"

case "$1" in
  ui)
    echo ">>> 同步前端文件..."
    scp apps/ui/index.html $SERVER:$REMOTE_DIR/apps/ui/index.html
    scp apps/ui/landing.html $SERVER:$REMOTE_DIR/apps/ui/landing.html 2>/dev/null || true
    echo "✅ 前端更新完成，访问 https://chatsql.top"
    ;;
  all)
    echo ">>> 同步全部代码..."
    scp -r apps $SERVER:$REMOTE_DIR/
    scp -r recon_v2 $SERVER:$REMOTE_DIR/
    scp deploy/Dockerfile $SERVER:$REMOTE_DIR/deploy/Dockerfile
    scp deploy/docker-compose.yml $SERVER:$REMOTE_DIR/deploy/docker-compose.yml
    echo ">>> 重新构建并启动..."
    ssh $SERVER "cd $REMOTE_DIR/deploy && docker-compose up -d --build app"
    echo "✅ 全量部署完成"
    ;;
  *)
    echo "用法: ./deploy.sh [ui|all]"
    echo "  ui  - 只更新前端 HTML（秒级）"
    echo "  all - 重建后端镜像（分钟级）"
    ;;
esac
