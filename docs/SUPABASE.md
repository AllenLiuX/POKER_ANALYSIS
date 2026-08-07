# Supabase 接入指南（账户 + 云同步）

训练进度默认存在浏览器本地（localStorage），**不配置 Supabase 也能完整使用**。
配置后即解锁：邮箱登录/注册、进度云端同步、跨设备访问。前端代码已做好降级——
凭证缺失时全站按本地模式运行，无需改任何代码。

## 1. 新建项目

1. 打开 <https://supabase.com> → New project（选离你近的区域）。
2. 记下数据库密码（用于控制台，前端用不到）。

## 2. 建表 + 打开 RLS

进入项目 → SQL Editor → 新建 query，粘贴并运行仓库里的：

```
supabase/migrations/0001_init.sql
```

它会创建 `public.attempts` 表并开启行级安全（每个用户只能读写自己的记录）。

## 3. 邮箱登录设置

- Authentication → Providers → Email：确保 **Email** 已启用。
- 开发期建议临时关闭邮箱确认：Authentication → Sign In / Providers →
  关闭 "Confirm email"，这样注册后可直接登录（上线前再打开）。

## 4. 填前端环境变量

Project Settings → API 里复制：

- **Project URL** → `NEXT_PUBLIC_SUPABASE_URL`
- **anon public** key → `NEXT_PUBLIC_SUPABASE_ANON_KEY`

写入 `frontend/.env.local`：

```env
NEXT_PUBLIC_SUPABASE_URL=https://xxxx.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJhbGciOi...
```

> 只用 anon key（受 RLS 保护）。service_role key 是后端专用密钥，
> 千万不要放进前端或提交到仓库。

## 5. 重启前端

```bash
cd frontend && npm run dev
```

- 首页右上角出现「登录」。
- `/login` 可注册/登录；登录后 `/progress` 顶部显示「云端 · 邮箱」。
- 之前的本地记录可在 `/progress` 用「上传本地 N 手到云端」一次性迁移。

## 数据模型

前端 `Attempt`（`frontend/lib/progress.ts`）与表 `public.attempts` 字段一一对应，
云同步逻辑在 `frontend/lib/cloud.ts`，登录态在 `frontend/lib/auth.tsx`。
