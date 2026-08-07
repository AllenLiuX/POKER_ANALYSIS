# 部署指南（AWS / 任意 Linux 服务器）

一套用于在单台服务器上部署本项目的脚本：**后端 FastAPI（:8000）** + **前端 Next.js（:3000）**。
脚本会自动结束旧进程、（重）安装依赖、后台启动服务并做健康检查。

## 目录

```
deploy/
├── config.env.example      # 部署配置模板（复制为 config.env 使用）
├── deploy.sh               # 一键部署（可 --pull 先拉代码）
├── backend.sh              # 仅部署/重启后端
├── frontend.sh             # 仅部署/重启前端
├── stop.sh                 # 停止服务
├── status.sh               # 查看状态
├── lib/common.sh           # 共享函数（勿直接执行）
├── systemd/                # 可选：开机自启 + 崩溃重启的 systemd 单元模板
└── nginx/                  # 可选：反向代理示例（收敛到单域名，免 CORS）
```

## 前置条件（服务器上）

- **Python 3.10+**（`python3 -m venv` 可用；Ubuntu 需 `sudo apt install python3-venv`）
- **Node.js 18+**（推荐 20/22 LTS）与 npm
- `git`、`curl`、`lsof`
- AWS 安全组放行端口：前端 `3000`、后端 `8000`（若用 nginx 则放行 `80/443` 即可）

## 快速开始

```bash
# 1) 克隆代码
git clone https://github.com/AllenLiuX/POKER_ANALYSIS.git poker_analysis
cd poker_analysis

# 2) 部署配置：把 API 地址改成「浏览器能访问到的」后端地址
cp deploy/config.env.example deploy/config.env
#   编辑 deploy/config.env，至少设置：
#   NEXT_PUBLIC_API_BASE_URL=http://<你的公网IP>:8000

# 3) 后端密钥（LLM / Supabase，可留空则功能降级）
cp backend/.env.example backend/.env
#   按需填入 MODEL_GATEWAY_KEY / OPENAI_API_KEY / SUPABASE_* 等
#   并把前端来源加入 CORS：
#   BACKEND_CORS_ORIGINS=http://<你的公网IP>:3000

# 4) 一键部署（前后端）
./deploy/deploy.sh
```

部署完成后：

- 前端：`http://<公网IP>:3000`
- 后端健康检查：`http://<公网IP>:8000/health`
- 接口文档：`http://<公网IP>:8000/docs`

## 常用命令

```bash
./deploy/deploy.sh            # 部署前后端
./deploy/deploy.sh --pull     # 先 git pull 再部署（更新上线常用）
./deploy/deploy.sh backend    # 仅后端
./deploy/deploy.sh frontend   # 仅前端
./deploy/status.sh            # 查看运行状态
./deploy/stop.sh              # 停止前后端
tail -f logs/backend.log      # 后端日志
tail -f logs/frontend.log     # 前端日志
```

## 关键注意事项

- **前端环境变量在「构建时」烘焙**：`NEXT_PUBLIC_API_BASE_URL` 等 `NEXT_PUBLIC_*`
  会在 `npm run build` 时写入产物。改了地址必须重新跑 `deploy/frontend.sh`（会重新 build）。
- **浏览器直连后端**：默认前端在客户端直接 `fetch` 后端 API，所以该地址不能填
  `127.0.0.1`，要填服务器公网 IP 或域名（除非用下面的 nginx 反代到同域名）。
- **CORS**：后端 `backend/.env` 里的 `BACKEND_CORS_ORIGINS` 必须包含前端来源，
  否则浏览器请求会被拦截。

## 生产加固（可选，推荐）

### A. systemd —— 开机自启 + 崩溃自动重启
先手动跑一次 `deploy/backend.sh` 和 `deploy/frontend.sh`（生成 `.venv` 与 `.next`），
再按 `deploy/systemd/*.service` 顶部注释安装单元文件。之后用
`systemctl restart poker-backend poker-frontend` 管理。

### B. nginx 反向代理 —— 单域名、免 CORS、支持 HTTPS
见 `deploy/nginx/poker.conf` 顶部注释。启用后前端只需：
`NEXT_PUBLIC_API_BASE_URL=https://your-domain.com`（`/api` 会被代理到后端）。

## 故障排查

| 现象 | 排查 |
|------|------|
| 健康检查失败 | 看 `logs/backend.log` / `logs/frontend.log` 末尾报错 |
| 前端能开但调用后端失败 | 检查 `NEXT_PUBLIC_API_BASE_URL` 是否为公网可达地址、后端 CORS 是否放行 |
| `python3 -m venv` 失败 | `sudo apt install python3-venv python3-pip` |
| 端口未生效 | 确认 AWS 安全组 / 防火墙已放行对应端口 |
| 改了后端代码不生效 | 重跑 `deploy/backend.sh`（或 `systemctl restart poker-backend`） |
```
