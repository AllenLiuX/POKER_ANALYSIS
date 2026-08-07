#!/usr/bin/env bash
# 查看前后端运行状态（端口监听 + HTTP 探测）。
#
# 用法： deploy/status.sh

set -euo pipefail
# shellcheck source=lib/common.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

check() {
  local name="$1" port="$2" path="$3"
  local pids code
  pids="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR>1{print $2}' | sort -u | tr '\n' ' ')"
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:${port}${path}" 2>/dev/null || echo 000)"
  if [ -n "$pids" ]; then
    ok "$name 运行中（端口 $port，PID: ${pids}），HTTP ${path} → $code"
  else
    warn "$name 未运行（端口 $port 无监听），HTTP ${path} → $code"
  fi
}

log "==== 服务状态 ===="
check "后端" "$BACKEND_PORT" "/health"
check "前端" "$FRONTEND_PORT" "/"
log "日志目录：$LOG_DIR"
