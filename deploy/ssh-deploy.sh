#!/usr/bin/env bash
# 从本机通过 SSH 更新生产服务器(需设置 RA2WEB_SSH_PASSWORD 或配置密钥)
set -euo pipefail

HOST="${RA2WEB_HOST:-38.76.165.109}"
PORT="${RA2WEB_SSH_PORT:-56567}"
USER="${RA2WEB_SSH_USER:-root}"
APP_DIR="/opt/ra2web"
NGINX_PORT="8899"
DAPHNE_PORT="8898"

REMOTE_SCRIPT=$(cat <<'EOS'
set -euo pipefail
APP_DIR="/opt/ra2web"
PORT="8899"
DAPHNE_PORT="8898"
NGINX_VHOST_DIR="/www/server/panel/vhost/nginx"

cd "${APP_DIR}"
git fetch origin main
git reset --hard origin/main

cd backend
./venv/bin/pip install -q -r requirements.txt
set -a; . /etc/ra2web.env; set +a
./venv/bin/python manage.py migrate --no-input

if command -v nginx >/dev/null 2>&1 && [ -d "${NGINX_VHOST_DIR}" ]; then
  sed "s|/opt/ra2web|${APP_DIR}|g; s|8899|${PORT}|g; s|8898|${DAPHNE_PORT}|g" \
    "${APP_DIR}/deploy/nginx/ra2web_8899.conf" \
    > "${NGINX_VHOST_DIR}/ra2web_${PORT}.conf"
  nginx -t
  systemctl reload nginx
fi

systemctl restart ra2web
sleep 2
echo "==> ra2web: $(systemctl is-active ra2web)"
curl -sf -o /dev/null -w "==> 首页 HTTP %{http_code}\n" "http://127.0.0.1:${PORT}/"
curl -sf "http://127.0.0.1:${PORT}/lib/local-trans.js?v=4" | tail -3
EOS
)

SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=15 -p "${PORT}")

# 支持 Cursor Secrets / GitHub Actions 等多种变量名
SSH_PASSWORD="${RA2WEB_SSH_PASSWORD:-${DEPLOY_PASSWORD:-${SSH_PASSWORD:-${SERVER_PASSWORD:-}}}}"

if [ -z "${SSH_PASSWORD}" ]; then
  echo "错误: 未找到 SSH 密码环境变量。" >&2
  echo "请在 Cursor Cloud Agent Secrets 中添加 RA2WEB_SSH_PASSWORD,然后重新启动 Agent。" >&2
  exit 1
fi

sshpass -p "${SSH_PASSWORD}" ssh "${SSH_OPTS[@]}" "${USER}@${HOST}" bash -s <<< "${REMOTE_SCRIPT}"

echo "==> 部署完成: http://${HOST}:${NGINX_PORT}/"
