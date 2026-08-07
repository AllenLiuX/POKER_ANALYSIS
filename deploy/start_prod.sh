#!/usr/bin/env bash
# 生产部署 / 上线（pm2 托管前后端）。幂等，可反复运行。
#   后端：建/复用 .venv → 装依赖 → import 自检
#   前端：写 .env.local(NEXT_PUBLIC_*) → npm ci → npm run build
#   统一：pm2 startOrReload ecosystem → pm2 save → 轮询健康检查
#
# 用法：
#   ./deploy/start_prod.sh            # 前后端
#   ./deploy/start_prod.sh backend    # 仅后端
#   ./deploy/start_prod.sh frontend   # 仅前端
#
# 配置：deploy/config.env（从 config.env.example 复制）

set -euo pipefail
# shellcheck source=lib/common.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

TARGET="${1:-all}"
case "$TARGET" in backend|frontend|all) ;; *) die "未知目标：$TARGET（可选 backend|frontend|all）";; esac

require_cmd pm2 "安装：npm install -g pm2"
log "==== 部署（$TARGET）===="
print_config

setup_backend() {
  require_cmd "$PYTHON_BIN"
  [ -d "$BACKEND_DIR" ] || die "找不到后端目录：$BACKEND_DIR"
  cd "$BACKEND_DIR"

  if [ ! -f .env ]; then
    if [ -f .env.example ]; then
      warn "backend/.env 不存在，从 .env.example 复制（记得填 CORS / 密钥）"
      cp .env.example .env
    else
      warn "backend/.env 不存在且无示例，使用默认配置"
    fi
  fi

  if [ ! -d .venv ]; then
    log "创建虚拟环境 .venv …"
    "$PYTHON_BIN" -m venv .venv
  fi
  log "安装/更新后端依赖 …"
  ./.venv/bin/python -m pip install -q --upgrade pip
  ./.venv/bin/python -m pip install -q -r requirements.txt

  log "校验应用可加载 …"
  ./.venv/bin/python -c "from app.main import app" || die "后端导入失败，检查依赖/代码"
}

setup_frontend() {
  require_cmd node "安装 Node.js 18+（推荐 20/22 LTS）"
  require_cmd npm
  [ -d "$FRONTEND_DIR" ] || die "找不到前端目录：$FRONTEND_DIR"
  cd "$FRONTEND_DIR"

  # 在 .env.local 中 upsert 单个 KEY=VALUE（保留其它行）。
  upsert_env() {
    local key="$1" val="$2" file=".env.local" tmp
    touch "$file"; tmp="$(mktemp)"
    grep -v -E "^${key}=" "$file" > "$tmp" 2>/dev/null || true
    echo "${key}=${val}" >> "$tmp"; mv "$tmp" "$file"
  }

  [ ! -f .env.local ] && [ -f .env.local.example ] && cp .env.local.example .env.local

  # NEXT_PUBLIC_* 在 build 时烘焙，必须先写后 build。
  log "写入构建期变量：NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}"
  upsert_env "NEXT_PUBLIC_API_BASE_URL" "$NEXT_PUBLIC_API_BASE_URL"
  [ -n "${NEXT_PUBLIC_SUPABASE_URL:-}" ]             && upsert_env "NEXT_PUBLIC_SUPABASE_URL" "$NEXT_PUBLIC_SUPABASE_URL"
  [ -n "${NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY:-}" ] && upsert_env "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY" "$NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY"
  [ -n "${NEXT_PUBLIC_SUPABASE_ANON_KEY:-}" ]        && upsert_env "NEXT_PUBLIC_SUPABASE_ANON_KEY" "$NEXT_PUBLIC_SUPABASE_ANON_KEY"

  if [ -f package-lock.json ]; then
    log "安装前端依赖（npm ci）…"; npm ci
  else
    log "安装前端依赖（npm install）…"; npm install
  fi
  log "生产构建（npm run build）…"; npm run build
}

# ---- 执行 setup ----
case "$TARGET" in
  backend)  setup_backend ;;
  frontend) setup_frontend ;;
  all)      setup_backend; setup_frontend ;;
esac

# ---- pm2 启动/重载 ----
ONLY=""
[ "$TARGET" = "backend" ]  && ONLY="--only $APP_BACKEND"
[ "$TARGET" = "frontend" ] && ONLY="--only $APP_FRONTEND"

log "pm2 startOrReload（$TARGET）…"
# shellcheck disable=SC2086
pm2 startOrReload "$ECOSYSTEM" --update-env $ONLY
pm2 save >/dev/null 2>&1 || true

# ---- 健康检查 ----
rc=0
if [ "$TARGET" = "backend" ] || [ "$TARGET" = "all" ]; then
  log "等待后端 /health …"
  if code="$(wait_http "http://127.0.0.1:${BACKEND_PORT}/health")"; then
    ok "后端就绪 (HTTP $code) → http://${BACKEND_HOST}:${BACKEND_PORT}/health"
  else
    warn "后端健康检查失败（$code）："; pm2 logs "$APP_BACKEND" --lines 40 --nostream || true; rc=1
  fi
fi
if [ "$TARGET" = "frontend" ] || [ "$TARGET" = "all" ]; then
  log "等待前端 …"
  if code="$(wait_http "http://127.0.0.1:${FRONTEND_PORT}/")"; then
    ok "前端就绪 (HTTP $code) → http://${FRONTEND_HOST}:${FRONTEND_PORT}/"
  else
    warn "前端健康检查失败（$code）："; pm2 logs "$APP_FRONTEND" --lines 40 --nostream || true; rc=1
  fi
fi

echo
pm2 status
echo
log "开机自启（首次执行一次，按提示复制 sudo 命令）： pm2 startup  然后  pm2 save"
[ "$rc" -eq 0 ] && ok "==== 部署完成（$TARGET）====" || die "部署过程中健康检查未通过，见上方日志"
