#!/usr/bin/env bash
# 停止 pm2 托管的服务。
#
# 用法：
#   ./deploy/stop.sh            # 停止前后端（保留在 pm2 列表，可 start_prod.sh 再拉起）
#   ./deploy/stop.sh backend    # 仅后端
#   ./deploy/stop.sh frontend   # 仅前端
#   ./deploy/stop.sh all --delete   # 停止并从 pm2 列表移除

set -euo pipefail
# shellcheck source=lib/common.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

require_cmd pm2 "安装：npm install -g pm2"

TARGET="${1:-all}"
ACTION="stop"
[ "${2:-}" = "--delete" ] && ACTION="delete"

case "$TARGET" in
  backend)  pm2 "$ACTION" "$APP_BACKEND"  || true ;;
  frontend) pm2 "$ACTION" "$APP_FRONTEND" || true ;;
  all)      pm2 "$ACTION" "$APP_BACKEND" "$APP_FRONTEND" || true ;;
  *) die "未知目标：$TARGET（可选 backend|frontend|all）" ;;
esac
pm2 save >/dev/null 2>&1 || true
if [ "$ACTION" = "delete" ]; then ok "已移除（$TARGET）"; else ok "已停止（$TARGET）"; fi
pm2 status
