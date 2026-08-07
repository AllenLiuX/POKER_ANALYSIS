# 生产部署速查（自有服务器 · pm2）

在单台 Linux 服务器上，用 **pm2** 同时托管 **后端 FastAPI(:8000)** 和 **前端 Next.js(:3000)**。
脚本负责：装依赖 / 前端构建 / pm2 启动或重载 / 健康检查。~15 分钟跑通。

## 目录

```
deploy/
├── ecosystem.config.js   # pm2 进程编排（poker-backend + poker-frontend）
├── start_prod.sh         # 部署/上线：装依赖→前端 build→pm2 起→健康检查（幂等）
├── restart_prod.sh       # 一键：git pull → 重建 → pm2 reload → 健康检查
├── stop.sh / status.sh   # 停止 / 状态
├── config.env.example    # 端口 / API 地址 / worker 配置模板
├── lib/common.sh         # 共享函数（勿直接执行）
└── nginx.conf.example    # 可选：单域名反代（免 CORS + HTTPS）
```

## 你需要

- 一台公网可达的 Linux 服务器（Ubuntu/Debian 推荐，1C2G 起步）
- **Node.js 18+**（推荐 20/22 LTS）、npm、**pm2**（`npm install -g pm2`）
- **Python 3.10+**（`python3-venv`；Ubuntu：`sudo apt install python3-venv python3-pip`）
- `git`、`curl`；AWS 安全组放行 `3000` / `8000`（用 nginx 则只放 `80/443`）

## 一、服务器准备

```bash
# 基础工具（一次性）
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git curl nginx
# Node + pm2（若未装）
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt install -y nodejs
sudo npm install -g pm2

# 拉代码
git clone https://github.com/AllenLiuX/POKER_ANALYSIS.git poker_analysis
cd poker_analysis
```

## 二、配置

```bash
# 1) 部署配置：把 API 地址改成「浏览器能访问到的」后端地址
cp deploy/config.env.example deploy/config.env
nano deploy/config.env
#   NEXT_PUBLIC_API_BASE_URL=http://<你的公网IP>:8000
#   （用 nginx 单域名时写 https://your-domain.com）

# 2) 后端密钥 + CORS
cp backend/.env.example backend/.env
nano backend/.env
#   BACKEND_CORS_ORIGINS=http://<你的公网IP>:3000    # 前端来源，必须放行
#   MODEL_GATEWAY_KEY / OPENAI_API_KEY / SUPABASE_* 按需填（留空则相关功能降级）
```

## 三、一键部署

```bash
./deploy/start_prod.sh          # 装依赖 + 前端 build + pm2 起 + 健康检查
```

看到「后端就绪 / 前端就绪」即成。访问：

- 前端 `http://<公网IP>:3000`　后端 `http://<公网IP>:8000/health`　文档 `/docs`

## 四、开机自启（一次性）

```bash
pm2 startup            # 按输出复制那条 sudo env ... 命令执行一次
pm2 save               # 固化当前进程列表，重启服务器后自动拉起
```

## 五、nginx 反向代理 + HTTPS（可选，推荐）

见 `deploy/nginx.conf.example` 顶部注释。启用后前端只需
`NEXT_PUBLIC_API_BASE_URL=https://your-domain.com`（`/api` 会代理到后端），
免 CORS，并用 certbot 一键签 HTTPS。

## 日常运维

```bash
./deploy/restart_prod.sh          # 更新上线：git pull + 重建 + reload（最常用）
./deploy/restart_prod.sh backend  # 仅后端 / frontend 仅前端
./deploy/status.sh                # pm2 进程 + HTTP 探测
./deploy/stop.sh                  # 停止前后端
pm2 logs poker-backend            # 后端日志（或 logs/backend.*.log）
pm2 logs poker-frontend           # 前端日志
pm2 monit                         # 实时监控面板
```

## 关键注意事项

- **前端环境变量是「构建期」烘焙**：`NEXT_PUBLIC_*` 在 `npm run build` 时写入产物。
  改了 `NEXT_PUBLIC_API_BASE_URL` 必须重跑 `start_prod.sh` / `restart_prod.sh`（会重新 build）。
- **浏览器直连后端**：`NEXT_PUBLIC_API_BASE_URL` 不能填 `127.0.0.1`，要填公网 IP/域名
  （除非用 nginx 反代到同域名）。
- **CORS**：`backend/.env` 的 `BACKEND_CORS_ORIGINS` 必须包含前端来源，否则浏览器请求被拦。
- **后端也在 pm2 里**：`poker-backend` 用 venv 的 uvicorn（`--workers` 由 uvicorn 自己 fork，
  pm2 fork 单实例守护 master）。想改用 systemd 托管后端也可以，二选一即可。

## 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 浏览器 `Failed to fetch` | `NEXT_PUBLIC_API_BASE_URL` 没设或没重新 build | 改 `deploy/config.env` → `./deploy/restart_prod.sh frontend` |
| 浏览器报 CORS | 后端未放行前端来源 | 改 `backend/.env` 的 `BACKEND_CORS_ORIGINS` → `./deploy/restart_prod.sh backend` |
| `pm2: command not found` | 未装 pm2 | `sudo npm install -g pm2` |
| `python3 -m venv` 失败 | 缺 venv 包 | `sudo apt install python3-venv python3-pip` |
| 502 Bad Gateway | uvicorn 没起 / 端口不一致 | `pm2 logs poker-backend` + 核对端口 |
| 端口不通 | 安全组/防火墙没放行 | 放行 3000/8000（或仅 80/443 走 nginx） |
| 改了代码不生效 | 未重建/重载 | `./deploy/restart_prod.sh` |
```
