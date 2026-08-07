#!/usr/bin/env bash
# =============================================================================
# 一键拉取最新代码 + 重新构建 + pm2 重载 + 健康检查。
#
# 用法（仓库根或 deploy/ 下都行）：
#   ./deploy/restart_prod.sh            # 前后端
#   ./deploy/restart_prod.sh backend    # 仅后端
#   ./deploy/restart_prod.sh frontend   # 仅前端
#
# 做的事：
#   1. git pull --ff-only         （有冲突立即退出，不会污染工作区）
#   2. 交给 start_prod.sh：装依赖 / 前端重建 / pm2 startOrReload / 轮询 /health
#
# 注意：前端 NEXT_PUBLIC_* 是构建期变量，改地址后重跑本脚本即可重新 build。
# =============================================================================
set -euo pipefail
# shellcheck source=lib/common.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

TARGET="${1:-all}"

log "→ [1/2] git pull --ff-only"
git -C "$REPO_ROOT" pull --ff-only

log "→ [2/2] 重新构建并重载（start_prod.sh $TARGET）"
exec "$DEPLOY_DIR/start_prod.sh" "$TARGET"
