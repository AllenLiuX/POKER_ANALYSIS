-- Poker Analysis — 截图导入历史云同步：import_entries + 行级安全（RLS）。
-- 在 Supabase 项目的 SQL Editor 里整段执行即可（依赖 0001_init.sql 已建好 auth.users）。
--
-- 设计：每条导入结果（观测事实 + 重建 + 偏离标注 = IngestItem）以 jsonb 存于 data 列，
-- 缩略图（降采样 JPEG 的 data URL）存于 thumb。以 (user_id, entry_id) 唯一，entry_id 为
-- 前端生成的稳定 uuid（比毫秒 ts 更安全，批量解析时不会撞键），用于 upsert 幂等与去重。

create table if not exists public.import_entries (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users (id) on delete cascade,
  entry_id    text not null,                   -- 前端 ImportEntry.id（uuid）
  ts          bigint not null,                 -- 客户端毫秒时间戳
  filename    text not null default '',
  thumb       text,                            -- 降采样缩略图 data URL（可为空）
  data        jsonb not null,                  -- 完整 IngestItem
  created_at  timestamptz not null default now(),
  unique (user_id, entry_id)
);

create index if not exists import_entries_user_ts_idx
  on public.import_entries (user_id, ts);

-- RLS：每个用户只能读写自己的记录
alter table public.import_entries enable row level security;

drop policy if exists "import_entries_select_own" on public.import_entries;
create policy "import_entries_select_own"
  on public.import_entries for select
  using (auth.uid() = user_id);

drop policy if exists "import_entries_insert_own" on public.import_entries;
create policy "import_entries_insert_own"
  on public.import_entries for insert
  with check (auth.uid() = user_id);

drop policy if exists "import_entries_update_own" on public.import_entries;
create policy "import_entries_update_own"
  on public.import_entries for update
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

drop policy if exists "import_entries_delete_own" on public.import_entries;
create policy "import_entries_delete_own"
  on public.import_entries for delete
  using (auth.uid() = user_id);
