-- 对手备注/标签：每个用户对每个对手昵称一条，随时可编辑。
-- 档案统计本身由 import_entries 派生，这里只持久化用户手写的备注与标签。
create table if not exists public.opponent_notes (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users (id) on delete cascade,
  alias       text not null,
  note        text not null default '',
  tag         text not null default '',
  updated_at  bigint not null default 0,
  created_at  timestamptz not null default now(),
  unique (user_id, alias)
);

create index if not exists opponent_notes_user_idx on public.opponent_notes (user_id);

alter table public.opponent_notes enable row level security;

drop policy if exists "opponent_notes_select_own" on public.opponent_notes;
create policy "opponent_notes_select_own" on public.opponent_notes
  for select using (auth.uid() = user_id);

drop policy if exists "opponent_notes_insert_own" on public.opponent_notes;
create policy "opponent_notes_insert_own" on public.opponent_notes
  for insert with check (auth.uid() = user_id);

drop policy if exists "opponent_notes_update_own" on public.opponent_notes;
create policy "opponent_notes_update_own" on public.opponent_notes
  for update using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists "opponent_notes_delete_own" on public.opponent_notes;
create policy "opponent_notes_delete_own" on public.opponent_notes
  for delete using (auth.uid() = user_id);
