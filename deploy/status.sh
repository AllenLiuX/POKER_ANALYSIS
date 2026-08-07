#!/usr/bin/env bash
# 查看服务状态：pm2 进程 + HTTP 健康探测。
#
# 用法： ./deploy/status.sh

set -euo pipefail
# shellcheck source=lib/common.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

require_cmd pm2 "安装：npm install -g pm2"

log "==== pm2 进程 ===="
pm2 status

probe() {
  local name="$1" url="$2" code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$url" 2>/dev/null || echo 000)"
  if [ "$code" -ge 200 ] 2>/dev/null && [ "$code" -lt 400 ] 2>/dev/null; then
    ok "$name  $url → $code"
  else
    warn "$name  $url → $code（未就绪？）"
  fi
}

echo
log "==== HTTP 探测 ===="
probe "后端" "http://127.0.0.1:${BACKEND_PORT}/health"
probe "前端" "http://127.0.0.1:${FRONTEND_PORT}/"
echo
log "日志： pm2 logs ${APP_BACKEND} / pm2 logs ${APP_FRONTEND}（或 $LOG_DIR/*.log）"
