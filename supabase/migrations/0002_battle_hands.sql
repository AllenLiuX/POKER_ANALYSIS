-- Poker Analysis — 对战「对局记录」云同步：battle_hands + 行级安全（RLS）。
-- 在 Supabase 项目的 SQL Editor 里整段执行即可（依赖 0001_init.sql 已建好 auth.users）。
--
-- 设计：整手记录（双方底牌/公共牌/完整行动线/逐个决策/净收益）以 jsonb 存于 data 列，
-- 另抽几个常查字段成平铺列便于统计。以 (user_id, ts) 唯一，ts=客户端毫秒时间戳，天然唯一，
-- 用于幂等补传与去重。

create table if not exists public.battle_hands (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users (id) on delete cascade,
  ts          bigint not null,                 -- 客户端毫秒时间戳（与前端 RecordedHand.ts 对齐）
  hero_pos    text not null default '',
  hero_net    double precision not null default 0,
  is_problem  boolean not null default false,
  is_big      boolean not null default false,
  data        jsonb not null,                  -- 完整 RecordedHand
  created_at  timestamptz not null default now(),
  unique (user_id, ts)
);

create index if not exists battle_hands_user_ts_idx
  on public.battle_hands (user_id, ts);

-- RLS：每个用户只能读写自己的记录
alter table public.battle_hands enable row level security;

drop policy if exists "battle_hands_select_own" on public.battle_hands;
create policy "battle_hands_select_own"
  on public.battle_hands for select
  using (auth.uid() = user_id);

drop policy if exists "battle_hands_insert_own" on public.battle_hands;
create policy "battle_hands_insert_own"
  on public.battle_hands for insert
  with check (auth.uid() = user_id);

drop policy if exists "battle_hands_delete_own" on public.battle_hands;
create policy "battle_hands_delete_own"
  on public.battle_hands for delete
  using (auth.uid() = user_id);
