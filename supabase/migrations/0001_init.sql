-- Poker Analysis — 初始 schema：训练记录 attempts + 行级安全（RLS）。
-- 在 Supabase 项目的 SQL Editor 里整段执行即可。

create table if not exists public.attempts (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users (id) on delete cascade,
  ts            bigint not null,                 -- 客户端毫秒时间戳（与前端 Attempt.ts 对齐）
  spot          text not null,                   -- RFI / vs_RFI
  position      text not null,                   -- 评分键（vs_RFI 为复合键，如 BB_vs_BTN）
  hero_position text not null,
  opener        text,                            -- 开池者位置（RFI 时为 null）
  hand_class    text not null,                   -- 如 AKo / T9s / 72o
  action        text not null,                   -- 玩家所选
  optimal_action text not null,
  grade         text not null,                   -- optimal / acceptable / mistake
  correct       boolean not null,
  created_at    timestamptz not null default now()
);

create index if not exists attempts_user_ts_idx
  on public.attempts (user_id, ts);

-- RLS：每个用户只能读写自己的记录
alter table public.attempts enable row level security;

drop policy if exists "attempts_select_own" on public.attempts;
create policy "attempts_select_own"
  on public.attempts for select
  using (auth.uid() = user_id);

drop policy if exists "attempts_insert_own" on public.attempts;
create policy "attempts_insert_own"
  on public.attempts for insert
  with check (auth.uid() = user_id);

drop policy if exists "attempts_delete_own" on public.attempts;
create policy "attempts_delete_own"
  on public.attempts for delete
  using (auth.uid() = user_id);
