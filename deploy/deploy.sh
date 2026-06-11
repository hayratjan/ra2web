#!/usr/bin/env bash
# ===========================================================================
# ra2web 一键部署脚本(在目标服务器上以 root 运行)
#
# 功能:
#   - 安装依赖(git/python3-venv/pip)
#   - 拉取代码到 /opt/ra2web(已存在则更新)
#   - 创建虚拟环境、安装后端依赖、初始化数据库
#   - 生成 systemd 服务(daphne 单端口同时托管前端+四类后端服务)
#   - 配置 servers.ini 指向本机
#   - 自动放行防火墙端口(ufw/firewalld/iptables 自动识别)
#
# 用法:
#   bash deploy.sh [端口] [服务器IP或域名]
#   例:bash deploy.sh 8899 38.76.165.109
# ===========================================================================
set -euo pipefail

PORT="${1:-8899}"
SERVER_ADDR="${2:-$(curl -s --max-time 10 https://api.ipify.org || hostname -I | awk '{print $1}')}"
REPO_URL="${RA2WEB_REPO_URL:-https://github.com/hayratjan/ra2web.git}"
APP_DIR="/opt/ra2web"
SERVICE_NAME="ra2web"

echo "==> 部署参数: 端口=${PORT} 地址=${SERVER_ADDR} 目录=${APP_DIR}"

# ---------------------------------------------------------------------------
# 1. 系统依赖
# ---------------------------------------------------------------------------
if command -v apt-get >/dev/null 2>&1; then
    # 个别第三方源签名失效不应中断部署
    apt-get update -q || echo "==> apt 源部分更新失败,继续(不影响部署)"
    apt-get install -y -q git python3 python3-venv python3-pip curl
elif command -v yum >/dev/null 2>&1; then
    yum install -y -q git python3 python3-pip curl
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y -q git python3 python3-pip curl
fi

# ---------------------------------------------------------------------------
# 2. 拉取/更新代码
# ---------------------------------------------------------------------------
if [ -d "${APP_DIR}/.git" ]; then
    echo "==> 更新已有代码"
    git -C "${APP_DIR}" fetch origin main
    git -C "${APP_DIR}" reset --hard origin/main
else
    echo "==> 克隆代码"
    git clone --depth 1 "${REPO_URL}" "${APP_DIR}"
fi

# ---------------------------------------------------------------------------
# 3. Python 虚拟环境与依赖
# ---------------------------------------------------------------------------
cd "${APP_DIR}/backend"
if [ ! -d venv ]; then
    python3 -m venv venv
fi
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q -r requirements.txt

# ---------------------------------------------------------------------------
# 4. 环境配置(密钥只生成一次,保存在 /etc/ra2web.env)
# ---------------------------------------------------------------------------
ENV_FILE="/etc/ra2web.env"
if [ ! -f "${ENV_FILE}" ]; then
    SECRET_KEY="$(./venv/bin/python -c 'import secrets;print(secrets.token_urlsafe(48))')"
    cat > "${ENV_FILE}" <<EOF
RA2WEB_SECRET_KEY=${SECRET_KEY}
RA2WEB_DEBUG=0
RA2WEB_ALLOWED_HOSTS=*
RA2WEB_SERVER_NAME=ra2web
EOF
    chmod 600 "${ENV_FILE}"
    echo "==> 已生成 ${ENV_FILE}"
fi

# ---------------------------------------------------------------------------
# 5. 数据库迁移
# ---------------------------------------------------------------------------
set -a; . "${ENV_FILE}"; set +a
./venv/bin/python manage.py migrate --no-input

# ---------------------------------------------------------------------------
# 6. 配置 servers.ini(本机区服放在最前)
# ---------------------------------------------------------------------------
cat > "${APP_DIR}/servers.ini" <<EOF
[local]
label="我的服务器 (${SERVER_ADDR})"
available=yes
gameVersion=0.65.1
wolUrl="ws://${SERVER_ADDR}:${PORT}/wol"
wladderUrl="http://${SERVER_ADDR}:${PORT}/ladder"
wgameresUrl="http://${SERVER_ADDR}:${PORT}/wgameres"
gservUrl="ws://${SERVER_ADDR}:${PORT}/gserv"
wolKeepAliveInGame=yes

[eu1]
label="Europe (EU1)"
available=yes
gameVersion=0.62
wolUrl="wss://wol-eu1.chronodivide.com:443"
wladderUrl="https://wol-eu1.chronodivide.com/ladder"
wgameresUrl="https://wol-eu1.chronodivide.com/wgameres"
gservUrl="wss://gserv-eu1.chronodivide.com:443"
wolKeepAliveInGame=yes

[sea2]
label="South-East Asia (HK)"
available=yes
gameVersion=0.62
wolUrl="wss://wol-sea2.chronodivide.com:443"
wladderUrl="https://wol-sea2.chronodivide.com/ladder"
wgameresUrl="https://wol-sea2.chronodivide.com/wgameres"
gservUrl="wss://gserv-sea2.chronodivide.com:443"
wolKeepAliveInGame=yes
EOF
echo "==> servers.ini 已指向 ${SERVER_ADDR}:${PORT}"

# ---------------------------------------------------------------------------
# 7. systemd 服务
# ---------------------------------------------------------------------------
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=ra2web 联机后端与前端托管(单端口)
After=network.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}/backend
EnvironmentFile=${ENV_FILE}
ExecStart=${APP_DIR}/backend/venv/bin/python -m daphne -b 0.0.0.0 -p ${PORT} ra2web_backend.asgi:application
Restart=always
RestartSec=3
# 资源与安全限制
LimitNOFILE=65536
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}" >/dev/null 2>&1
systemctl restart "${SERVICE_NAME}"
echo "==> systemd 服务 ${SERVICE_NAME} 已启动"

# ---------------------------------------------------------------------------
# 8. 防火墙放行
# ---------------------------------------------------------------------------
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
    ufw allow "${PORT}/tcp" >/dev/null && echo "==> ufw 已放行 ${PORT}/tcp"
elif command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    firewall-cmd --permanent --add-port="${PORT}/tcp" >/dev/null
    firewall-cmd --reload >/dev/null && echo "==> firewalld 已放行 ${PORT}/tcp"
elif command -v iptables >/dev/null 2>&1; then
    iptables -C INPUT -p tcp --dport "${PORT}" -j ACCEPT 2>/dev/null \
        || iptables -I INPUT -p tcp --dport "${PORT}" -j ACCEPT
    echo "==> iptables 已放行 ${PORT}/tcp(如有云安全组请同步放行)"
fi

# ---------------------------------------------------------------------------
# 9. 冒烟验证
# ---------------------------------------------------------------------------
sleep 2
echo "==> 服务状态: $(systemctl is-active ${SERVICE_NAME})"
echo "==> 天梯接口: $(curl -s --max-time 5 http://127.0.0.1:${PORT}/ladder/16640/1v1 || echo 失败)"
echo "==> 首页状态: $(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:${PORT}/)"
echo ""
echo "部署完成!浏览器访问: http://${SERVER_ADDR}:${PORT}"
echo "(注意:如服务器有云厂商安全组,请在控制台同步放行 TCP ${PORT})"
