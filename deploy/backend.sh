#!/usr/bin/env bash
# 部署 / 重启后端（FastAPI + uvicorn）。
#   - 建立/复用 .venv
#   - 安装依赖
#   - 结束占用端口的旧进程
#   - 后台启动 uvicorn，日志写入 $LOG_DIR/backend.log
#   - 轮询 /health 确认启动成功
#
# 用法： deploy/backend.sh
# 配置： 见 deploy/config.env（可选，从 config.env.example 复制）

set -euo pipefail
# shellcheck source=lib/common.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

log "==== 部署后端 ===="
print_config

[ -d "$BACKEND_DIR" ] || die "找不到后端目录：$BACKEND_DIR"
cd "$BACKEND_DIR"

# ---- .env 检查 ----
if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    warn "backend/.env 不存在，从 .env.example 复制（LLM/Supabase 功能会降级，请稍后填入真实密钥）"
    cp .env.example .env
  else
    warn "backend/.env 不存在且无 .env.example，使用纯默认配置"
  fi
fi

# ---- 虚拟环境 ----
if [ ! -d .venv ]; then
  log "创建虚拟环境 .venv …"
  "$PYTHON_BIN" -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate

log "安装依赖（requirements.txt）…"
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt

# ---- 快速自检：能否 import 应用 ----
log "校验应用可加载…"
python -c "from app.main import app" || die "应用导入失败，请检查依赖/代码"

# ---- 结束旧进程并启动 ----
kill_port "$BACKEND_PORT"

LOG_FILE="$LOG_DIR/backend.log"
log "启动 uvicorn（后台），日志：$LOG_FILE"
# 提高文件描述符上限（尽力而为，非致命）。
ulimit -n 65536 2>/dev/null || true

nohup .venv/bin/uvicorn app.main:app \
  --host "$BACKEND_HOST" \
  --port "$BACKEND_PORT" \
  --workers "$BACKEND_WORKERS" \
  > "$LOG_FILE" 2>&1 &

# ---- 健康检查 ----
log "等待后端就绪（/health）…"
if code="$(wait_http "http://127.0.0.1:${BACKEND_PORT}/health")"; then
  ok "后端已就绪：http://${BACKEND_HOST}:${BACKEND_PORT}/health (HTTP $code)"
  ok "接口文档：http://${BACKEND_HOST}:${BACKEND_PORT}/docs"
else
  warn "健康检查未通过（最近状态码 $code）。最近日志："
  tail -n 40 "$LOG_FILE" >&2 || true
  die "后端启动失败"
fi
