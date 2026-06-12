# Cursor Cloud Agent 说明

## 部署到生产服务器 (38.76.165.109:8899)

在 [Cloud Agents → Secrets](https://cursor.com/dashboard/cloud-agents) 添加：

| 变量名 | 说明 |
|--------|------|
| `RA2WEB_SSH_PASSWORD` | 服务器 root 密码 (SSH 端口 56567) |

要求：
- Secret 名称必须**完全一致**（区分大小写）
- 作用范围包含本仓库 `hayratjan/ra2web`
- 添加后需**重新启动 Cloud Agent**（继续当前会话不会自动注入）

部署命令：

```bash
bash deploy/ssh-deploy.sh
```

## 手动部署（无需 Agent Secret）

在服务器上执行：

```bash
cd /opt/ra2web && git fetch origin main && git reset --hard origin/main
cp deploy/nginx/ra2web_8899.conf /www/server/panel/vhost/nginx/ra2web_8899.conf
nginx -t && systemctl reload nginx && systemctl restart ra2web
```
