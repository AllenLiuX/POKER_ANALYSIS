#!/usr/bin/env bash
# =============================================================================
# DNS 就绪后一键上线 poker.aico-music.com：
#   1) 校验 DNS 已指向本机
#   2) 注入 :80 acme vhost → reload → certbot webroot 签证书
#   3) 注入 :443 单域名反代块 → nginx -t 校验 → reload
#   4) 前端切到 https://poker.aico-music.com 并重新 build（pm2 reload）
#
# 安全：改共享的 /etc/nginx/nginx.conf（同机还有 aico / study / fomo2）前都会备份，
#       注入后 nginx -t 不过立即回滚，绝不 reload 坏配置。幂等，可重复运行。
#
# 用法： sudo ./deploy/enable_nginx_https.sh
# =============================================================================
set -euo pipefail

DOMAIN="poker.aico-music.com"
BACKEND_PORT="8010"
FRONTEND_PORT="3010"
SERVER_IP="52.53.204.234"
NGINX_CONF="/etc/nginx/nginx.conf"
WEBROOT="/var/lib/letsencrypt"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

log(){ printf '\033[1;34m→ %s\033[0m\n' "$*"; }
ok(){  printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
warn(){ printf '\033[1;33m! %s\033[0m\n' "$*"; }
die(){ printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" = "0" ] || die "请用 sudo 运行（要改 /etc/nginx 和跑 certbot）"

# --- 在 nginx.conf 的 http{} 末尾（最后一个 } 前）插入带标记的块；幂等 ---
inject_block() {  # $1=marker  $2=block-content
  local marker="$1" content="$2"
  if grep -q "# >>> ${marker} >>>" "$NGINX_CONF"; then
    log "nginx 块 [$marker] 已存在，跳过注入"; return 0
  fi
  cp "$NGINX_CONF" "${NGINX_CONF}.bak.$(date +%Y%m%d_%H%M%S)"
  MARKER="$marker" CONTENT="$content" python3 - "$NGINX_CONF" <<'PY'
import os, sys
path = sys.argv[1]
marker, content = os.environ["MARKER"], os.environ["CONTENT"]
lines = open(path).read().splitlines()
# 找最后一个仅含 '}' 的行 = http{} 的收尾
idx = max(i for i, l in enumerate(lines) if l.strip() == "}")
block = [f"    # >>> {marker} >>>", *content.splitlines(), f"    # <<< {marker} <<<", ""]
lines[idx:idx] = block
open(path, "w").write("\n".join(lines) + "\n")
PY
  log "已注入 nginx 块 [$marker]"
}

# --- nginx -t，不过就回滚到最近备份 ---
test_or_rollback() {
  if nginx -t 2>/tmp/poker_nginx_test.log; then
    ok "nginx -t 通过"
  else
    warn "nginx -t 失败，回滚："; cat /tmp/poker_nginx_test.log
    local last_bak; last_bak="$(ls -t ${NGINX_CONF}.bak.* 2>/dev/null | head -n1 || true)"
    [ -n "$last_bak" ] && cp "$last_bak" "$NGINX_CONF" && warn "已回滚到 $last_bak"
    die "nginx 配置有误，已回滚，未 reload"
  fi
}

# ---------------------------------------------------------------------------
# 0. DNS 校验
# ---------------------------------------------------------------------------
log "校验 DNS：$DOMAIN"
# 本机解析器(VPC DNS)可能有旧的否定缓存；查不到就回退公共 DNS。
# certbot 验证走公网权威解析，只要公共 DNS 能看到即可签发。
resolved="$(dig +short "$DOMAIN" A | tail -n1 || true)"
if [ -z "$resolved" ]; then
  warn "本机解析器暂无记录（可能否定缓存），回退查 8.8.8.8 …"
  resolved="$(dig +short @8.8.8.8 "$DOMAIN" A | tail -n1 || true)"
fi
[ -n "$resolved" ] || die "DNS 未生效：$DOMAIN 无 A 记录。请先在 aico-music.com 加 A 记录：poker → $SERVER_IP"
[ "$resolved" = "$SERVER_IP" ] || warn "解析到 $resolved（期望 $SERVER_IP）；若刚改可能还在传播"
ok "DNS：$DOMAIN → $resolved（权威已生效）"

# ---------------------------------------------------------------------------
# 1. :80 acme vhost → reload → 签证书
# ---------------------------------------------------------------------------
if [ ! -d "/etc/letsencrypt/live/$DOMAIN" ]; then
  inject_block "poker acme80" "server {
        listen 80;
        listen [::]:80;
        server_name $DOMAIN;
        location /.well-known/acme-challenge/ { root $WEBROOT; }
        location / { return 301 https://\$host\$request_uri; }
    }"
  test_or_rollback
  systemctl reload nginx; ok "nginx 已 reload（:80 acme 就绪）"

  log "certbot webroot 签证书 …"
  certbot certonly --webroot -w "$WEBROOT" -d "$DOMAIN" \
    --non-interactive --agree-tos --keep-until-expiring \
    || die "证书签发失败，检查 DNS 是否已全网生效 + 80 端口可达"
  ok "证书已签：/etc/letsencrypt/live/$DOMAIN/"
else
  ok "证书已存在：/etc/letsencrypt/live/$DOMAIN/（跳过签发）"
fi

# ---------------------------------------------------------------------------
# 2. :443 单域名反代块
# ---------------------------------------------------------------------------
inject_block "poker https" "server {
        listen 443 ssl http2;
        listen [::]:443 ssl http2;
        server_name $DOMAIN;

        ssl_certificate     /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
        ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;
        ssl_protocols       TLSv1.2 TLSv1.3;
        ssl_ciphers         HIGH:!aNULL:!MD5;
        ssl_prefer_server_ciphers on;

        add_header Strict-Transport-Security \"max-age=31536000; includeSubDomains\" always;
        add_header X-Content-Type-Options \"nosniff\" always;

        client_max_body_size 20M;
        proxy_buffer_size 32k;
        proxy_buffers 8 32k;
        proxy_busy_buffers_size 64k;

        location /api/ {
            proxy_pass http://127.0.0.1:$BACKEND_PORT;
            proxy_http_version 1.1;
            proxy_set_header Connection \"\";
            proxy_buffering off;
            proxy_read_timeout 300s;
            proxy_send_timeout 300s;
            proxy_set_header Host              \$host;
            proxy_set_header X-Real-IP         \$remote_addr;
            proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
        location = /health       { proxy_pass http://127.0.0.1:$BACKEND_PORT/health; access_log off; }
        location /docs           { proxy_pass http://127.0.0.1:$BACKEND_PORT/docs; }
        location = /openapi.json { proxy_pass http://127.0.0.1:$BACKEND_PORT/openapi.json; }

        location / {
            proxy_pass http://127.0.0.1:$FRONTEND_PORT;
            proxy_http_version 1.1;
            proxy_set_header Upgrade           \$http_upgrade;
            proxy_set_header Connection         \"upgrade\";
            proxy_set_header Host              \$host;
            proxy_set_header X-Real-IP         \$remote_addr;
            proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
            proxy_read_timeout 300s;
            proxy_send_timeout 300s;
            proxy_buffering off;
        }
        location /_next/static/ {
            proxy_pass http://127.0.0.1:$FRONTEND_PORT;
            proxy_set_header Host \$host;
            add_header Cache-Control \"public, max-age=31536000, immutable\";
        }
    }"
test_or_rollback
systemctl reload nginx; ok "nginx 已 reload（:443 poker 就绪）"

# ---------------------------------------------------------------------------
# 3. 前端切到 https 域名并重构建（用仓库自带的部署脚本，以 config.env 为准）
# ---------------------------------------------------------------------------
log "重构建前端（NEXT_PUBLIC_API_BASE_URL=https://$DOMAIN）…"
DEPLOY_USER="${SUDO_USER:-ec2-user}"
sudo -iu "$DEPLOY_USER" bash -lc "cd '$REPO_ROOT' && ./deploy/start_prod.sh frontend"

# ---------------------------------------------------------------------------
# 4. 自检
# ---------------------------------------------------------------------------
echo
log "自检："
curl -s -o /dev/null -w "  https://$DOMAIN/health        → %{http_code}\n" "https://$DOMAIN/health" || true
curl -s -o /dev/null -w "  https://$DOMAIN/ (前端)       → %{http_code}\n" "https://$DOMAIN/" || true
echo
ok "完成：用浏览器打开  https://$DOMAIN"
