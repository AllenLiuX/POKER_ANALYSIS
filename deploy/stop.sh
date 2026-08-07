#!/usr/bin/env bash
# 停止前端 / 后端服务（按端口结束进程）。
#
# 用法：
#   deploy/stop.sh            # 同时停止前后端
#   deploy/stop.sh backend    # 仅后端
#   deploy/stop.sh frontend   # 仅前端

set -euo pipefail
# shellcheck source=lib/common.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

TARGET="${1:-all}"

case "$TARGET" in
  backend)  kill_port "$BACKEND_PORT";  ok "已停止后端（端口 $BACKEND_PORT）" ;;
  frontend) kill_port "$FRONTEND_PORT"; ok "已停止前端（端口 $FRONTEND_PORT）" ;;
  all)
    kill_port "$FRONTEND_PORT"; kill_port "$BACKEND_PORT"
    ok "已停止前后端（端口 $FRONTEND_PORT / $BACKEND_PORT）" ;;
  *) die "未知参数：$TARGET（可选：backend | frontend | all）" ;;
esac
