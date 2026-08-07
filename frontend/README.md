# Frontend — Next.js

德州扑克进阶学习平台前端（App Router + TypeScript + Tailwind）。

## 运行

```bash
npm install
cp .env.local.example .env.local   # 指向后端 API
npm run dev
# 打开 http://localhost:3000
```

首页会调用后端 `/health` 显示连接状态，并内置一个已接通引擎的**胜率计算器**（`POST /api/equity`）。

## 结构

```
app/
├── layout.tsx      # 根布局
├── globals.css     # Tailwind
└── page.tsx        # 首页：模块概览 + 胜率计算器 demo
lib/
└── api.ts          # 后端调用封装（NEXT_PUBLIC_API_BASE_URL）
```

## 部署

Vercel：设置 `NEXT_PUBLIC_API_BASE_URL` 指向线上后端。
