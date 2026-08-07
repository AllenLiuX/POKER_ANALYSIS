#!/usr/bin/env bash
# 部署 / 重启前端（Next.js）。
#   - 依据 config.env 写入 .env.local（NEXT_PUBLIC_* 在构建时烘焙，必须先写后 build）
#   - npm ci 安装依赖
#   - npm run build 生产构建
#   - 结束占用端口的旧进程
#   - 后台启动 next start，日志写入 $LOG_DIR/frontend.log
#   - 轮询首页确认启动成功
#
# 用法： deploy/frontend.sh
# 配置： 见 deploy/config.env（可选，从 config.env.example 复制）

set -euo pipefail
# shellcheck source=lib/common.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

log "==== 部署前端 ===="
print_config

[ -d "$FRONTEND_DIR" ] || die "找不到前端目录：$FRONTEND_DIR"
cd "$FRONTEND_DIR"

# ---- 在 .env.local 中 upsert 一个 KEY=VALUE（不破坏其它行）----
upsert_env() {
  local key="$1" val="$2" file=".env.local"
  touch "$file"
  local tmp
  tmp="$(mktemp)"
  grep -v -E "^${key}=" "$file" > "$tmp" 2>/dev/null || true
  echo "${key}=${val}" >> "$tmp"
  mv "$tmp" "$file"
}

# 若无 .env.local，先从示例复制（保留 Supabase 等占位）。
if [ ! -f .env.local ] && [ -f .env.local.example ]; then
  cp .env.local.example .env.local
fi

log "写入构建期环境变量（.env.local）：NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}"
upsert_env "NEXT_PUBLIC_API_BASE_URL" "$NEXT_PUBLIC_API_BASE_URL"

# 仅当在环境/config.env 中显式提供时才写入 Supabase 变量（否则保留 .env.local 原值）。
[ -n "${NEXT_PUBLIC_SUPABASE_URL:-}" ]             && upsert_env "NEXT_PUBLIC_SUPABASE_URL" "$NEXT_PUBLIC_SUPABASE_URL"
[ -n "${NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY:-}" ] && upsert_env "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY" "$NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY"
[ -n "${NEXT_PUBLIC_SUPABASE_ANON_KEY:-}" ]        && upsert_env "NEXT_PUBLIC_SUPABASE_ANON_KEY" "$NEXT_PUBLIC_SUPABASE_ANON_KEY"

# ---- 安装依赖 ----
if [ -f package-lock.json ]; then
  log "安装依赖（npm ci）…"
  npm ci
else
  log "安装依赖（npm install）…"
  npm install
fi

# ---- 生产构建 ----
log "生产构建（npm run build）…"
npm run build

# ---- 结束旧进程并启动 ----
kill_port "$FRONTEND_PORT"

LOG_FILE="$LOG_DIR/frontend.log"
log "启动 next start（后台），日志：$LOG_FILE"
ulimit -n 65536 2>/dev/null || true

nohup npm run start -- -p "$FRONTEND_PORT" -H "$FRONTEND_HOST" \
  > "$LOG_FILE" 2>&1 &

# ---- 健康检查 ----
log "等待前端就绪…"
if code="$(wait_http "http://127.0.0.1:${FRONTEND_PORT}/")"; then
  ok "前端已就绪：http://${FRONTEND_HOST}:${FRONTEND_PORT}/ (HTTP $code)"
else
  warn "健康检查未通过（最近状态码 $code）。最近日志："
  tail -n 40 "$LOG_FILE" >&2 || true
  die "前端启动失败"
fi
