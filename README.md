# Poker Analysis — 德州扑克进阶学习平台

面向进阶牌手的**决策训练**平台：通过"决策 → 即时反馈"的刻意练习，把翻前/翻后决策向 GTO / 高 EV 靠拢。

## 核心功能

- **决策训练器**：翻前（GTO 范围）+ 翻后（启发式 → 预计算真 GTO）即时反馈。
- **GTO 引擎**：手牌评估、胜率（equity）、翻前范围、翻后解集。
- **剥削训练**：基于对手画像的 node-lock 剥削（LLM 辅助读牌）。
- **辅助**：交互式范围表、胜率/赔率工具、账号进度、WePoker 截图导入。

> 设计文档见 [`docs/`](./docs)：
> - [`ARCHITECTURE.md`](./docs/ARCHITECTURE.md) — 整体架构与实施
> - [`ALGORITHMS.md`](./docs/ALGORITHMS.md) — 翻后求解、剥削训练、LLM 集成
> - [`SCREENSHOT_IMPORT.md`](./docs/SCREENSHOT_IMPORT.md) — 截图导入（辅助功能）

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | Next.js + TypeScript + Tailwind（Vercel 部署） |
| 后端 | FastAPI（Python） |
| 扑克引擎 | eval7 + 自研范围/启发式 |
| 数据 + 认证 | Supabase（PostgreSQL + Auth + Storage） |
| LLM | model_client 网关（首选）→ OpenAI（env 兜底） |

## 本地开发

### 后端

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # 填入真实密钥（不会进 git）
uvicorn app.main:app --reload --port 8000
# 健康检查: http://127.0.0.1:8000/health
```

### 前端

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
# 打开: http://localhost:3000
```

## 部署到服务器（AWS / 任意 Linux）

一套自带的部署脚本，在单机上跑起 **后端(:8000)** + **前端(:3000)**：

```bash
cp deploy/config.env.example deploy/config.env   # 设 NEXT_PUBLIC_API_BASE_URL 为公网可达地址
cp backend/.env.example backend/.env             # 填密钥（可留空则降级）并配好 CORS
./deploy/deploy.sh                               # 一键部署前后端（含健康检查）
```

更新上线：`./deploy/deploy.sh --pull`；查看状态：`./deploy/status.sh`；停止：`./deploy/stop.sh`。
完整说明（systemd 自启、nginx 反代、故障排查）见 [`deploy/README.md`](./deploy/README.md)。

## 安全

- **所有密钥只存放于 `.env` / `.env.local`（已 `.gitignore`），绝不硬编码、绝不入 git。**
- 提交前请确认无 `.env` 被跟踪：`git status`。
