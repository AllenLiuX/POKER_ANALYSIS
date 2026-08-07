#!/usr/bin/env bash
# 部署脚本共享函数库。被 deploy/*.sh source。
# 不要直接执行本文件。

set -euo pipefail

# ---- 路径解析 ----
# DEPLOY_DIR = deploy/ 目录；REPO_ROOT = 仓库根目录。
DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$DEPLOY_DIR/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
FRONTEND_DIR="$REPO_ROOT/frontend"

# ---- 默认配置（可被 deploy/config.env 覆盖）----
: "${BACKEND_HOST:=0.0.0.0}"
: "${BACKEND_PORT:=8000}"
: "${BACKEND_WORKERS:=2}"
: "${FRONTEND_HOST:=0.0.0.0}"
: "${FRONTEND_PORT:=3000}"
# 前端构建时会把 NEXT_PUBLIC_* 烘焙进产物；浏览器需要能直连该地址。
# 生产环境请改成公网 IP 或域名，例如 http://<你的公网IP>:8000
: "${NEXT_PUBLIC_API_BASE_URL:=http://127.0.0.1:${BACKEND_PORT}}"
: "${PYTHON_BIN:=python3}"
: "${LOG_DIR:=$REPO_ROOT/logs}"
# 健康检查最长等待秒数
: "${HEALTH_TIMEOUT:=60}"

# pm2 应用名（与 ecosystem.config.js 一致）
APP_BACKEND="poker-backend"
APP_FRONTEND="poker-frontend"
ECOSYSTEM="$DEPLOY_DIR/ecosystem.config.js"

# 若存在 deploy/config.env 则加载（覆盖上面的默认值）。
if [ -f "$DEPLOY_DIR/config.env" ]; then
  # shellcheck disable=SC1091
  set -a; . "$DEPLOY_DIR/config.env"; set +a
fi

mkdir -p "$LOG_DIR"

# ---- 日志输出 ----
_ts() { date '+%Y-%m-%d %H:%M:%S'; }
log()  { printf '\033[0;36m[%s]\033[0m %s\n' "$(_ts)" "$*"; }
ok()   { printf '\033[0;32m[%s] ✓ %s\033[0m\n' "$(_ts)" "$*"; }
warn() { printf '\033[0;33m[%s] ! %s\033[0m\n' "$(_ts)" "$*" >&2; }
die()  { printf '\033[0;31m[%s] ✗ %s\033[0m\n' "$(_ts)" "$*" >&2; exit 1; }

# 确认命令存在，否则给出安装提示并退出。
require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "缺少命令：$1。${2:-}"
}

# ---- 端口占用清理 ----
# 杀掉监听指定端口的进程（跨 macOS/Linux 便携，避免 xargs -r 兼容问题）。
kill_port() {
  local port="$1"
  local pids
  pids="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR>1{print $2}' | sort -u)"
  if [ -n "$pids" ]; then
    log "端口 $port 被占用（PID: $(echo "$pids" | tr '\n' ' ')），正在结束…"
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
    sleep 1
  fi
}

# ---- HTTP 健康检查 ----
# wait_http <url> [timeout_seconds]
# 轮询直到返回 2xx/3xx，或超时返回非 0。
wait_http() {
  local url="$1"
  local timeout="${2:-$HEALTH_TIMEOUT}"
  local elapsed=0 code
  while [ "$elapsed" -lt "$timeout" ]; do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$url" 2>/dev/null || echo 000)"
    if [ "$code" -ge 200 ] 2>/dev/null && [ "$code" -lt 400 ] 2>/dev/null; then
      echo "$code"
      return 0
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
  echo "${code:-000}"
  return 1
}

# 打印当前生效配置摘要。
print_config() {
  log "仓库根目录 : $REPO_ROOT"
  log "后端       : ${BACKEND_HOST}:${BACKEND_PORT} (workers=${BACKEND_WORKERS})"
  log "前端       : ${FRONTEND_HOST}:${FRONTEND_PORT}"
  log "API 地址   : ${NEXT_PUBLIC_API_BASE_URL}"
  log "日志目录   : ${LOG_DIR}"
}
