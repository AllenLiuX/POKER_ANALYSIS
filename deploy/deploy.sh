#!/usr/bin/env bash
# 一键部署：可选拉取最新代码 → 部署后端 → 部署前端。
#
# 用法：
#   deploy/deploy.sh              # 部署前后端
#   deploy/deploy.sh --pull       # 先 git pull 再部署
#   deploy/deploy.sh backend      # 仅后端
#   deploy/deploy.sh frontend     # 仅前端
#   deploy/deploy.sh --pull backend
#
# 配置： 见 deploy/config.env（可选，从 config.env.example 复制）

set -euo pipefail
# shellcheck source=lib/common.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

DO_PULL=0
TARGET="all"
for arg in "$@"; do
  case "$arg" in
    --pull) DO_PULL=1 ;;
    backend|frontend|all) TARGET="$arg" ;;
    -h|--help)
      grep -E '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "未知参数：$arg（用 -h 查看帮助）" ;;
  esac
done

if [ "$DO_PULL" -eq 1 ]; then
  log "拉取最新代码（git pull）…"
  git -C "$REPO_ROOT" pull --ff-only
fi

case "$TARGET" in
  backend)  "$DEPLOY_DIR/backend.sh" ;;
  frontend) "$DEPLOY_DIR/frontend.sh" ;;
  all)      "$DEPLOY_DIR/backend.sh" && "$DEPLOY_DIR/frontend.sh" ;;
esac

ok "==== 部署完成（$TARGET）===="
"$DEPLOY_DIR/status.sh" || true
