-- Phase A：服务端权威的逐对手聚合画像（可加计数器 + 幂等增量更新）。
--
-- 设计：
--   opponents            规范对手身份（按 user+昵称唯一；预留 aliases/merged_into 供合并）
--   opponent_stats       每对手一行的滚动聚合（hand_count/net/counters jsonb）
--   opponent_hand_index  幂等守卫 + 回滚依据（存每手对该对手的 delta；同手只计一次）
--   opponent_reports     逐对手 LLM 剥削报告（按样本显著增长再生，前端做门控）
--
-- 合并数学：counters 全为数值叶子，用 jsonb_deep_add 逐字段相加、jsonb_deep_scale(-1) 相减，
-- 因此增量 apply / 回滚 retract 天然可交换、幂等、可重建。
-- 写入统一走 SECURITY DEFINER 的 RPC（内部按 auth.uid() 隔离），读取走 RLS。

-- ---------- 通用 JSONB 数值合并 ----------
create or replace function public.jsonb_deep_add(a jsonb, b jsonb)
returns jsonb language plpgsql immutable as $$
declare
  result jsonb;
  k text;
begin
  if a is null then return b; end if;
  if b is null then return a; end if;
  if jsonb_typeof(a) = 'number' and jsonb_typeof(b) = 'number' then
    return to_jsonb((a::numeric) + (b::numeric));
  end if;
  if jsonb_typeof(a) = 'object' and jsonb_typeof(b) = 'object' then
    result := a;
    for k in select jsonb_object_keys(b) loop
      result := jsonb_set(result, array[k], public.jsonb_deep_add(a -> k, b -> k), true);
    end loop;
    return result;
  end if;
  return b;  -- 类型不一致：以 b 覆盖（固定 schema 下不会触发）
end;
$$;

create or replace function public.jsonb_deep_scale(a jsonb, f numeric)
returns jsonb language plpgsql immutable as $$
declare
  result jsonb;
  k text;
begin
  if a is null then return null; end if;
  if jsonb_typeof(a) = 'number' then return to_jsonb((a::numeric) * f); end if;
  if jsonb_typeof(a) = 'object' then
    result := '{}'::jsonb;
    for k in select jsonb_object_keys(a) loop
      result := jsonb_set(result, array[k], public.jsonb_deep_scale(a -> k, f), true);
    end loop;
    return result;
  end if;
  return a;
end;
$$;

-- ---------- 表 ----------
create table if not exists public.opponents (
  user_id      uuid not null references auth.users (id) on delete cascade,
  opponent_id  uuid not null default gen_random_uuid(),
  primary_alias text not null,
  aliases      text[] not null default '{}',
  merged_into  uuid,
  created_at   timestamptz not null default now(),
  updated_at   bigint not null default 0,
  primary key (user_id, opponent_id),
  unique (user_id, primary_alias)
);
create index if not exists opponents_user_idx on public.opponents (user_id);

create table if not exists public.opponent_stats (
  user_id      uuid not null,
  opponent_id  uuid not null,
  hand_count   int not null default 0,
  net          double precision not null default 0,
  counters     jsonb not null default '{}',
  agg_version  int not null default 1,
  updated_at   bigint not null default 0,
  primary key (user_id, opponent_id)
);
create index if not exists opponent_stats_user_idx on public.opponent_stats (user_id);

create table if not exists public.opponent_hand_index (
  user_id      uuid not null,
  opponent_id  uuid not null,
  hand_id      text not null,
  net          double precision not null default 0,
  counters     jsonb not null default '{}',
  created_at   timestamptz not null default now(),
  primary key (user_id, opponent_id, hand_id)
);

create table if not exists public.opponent_reports (
  user_id             uuid not null,
  opponent_id         uuid not null,
  report              text not null,
  model               text,
  based_on_hand_count int not null default 0,
  stats_snapshot      jsonb,
  created_at          timestamptz not null default now(),
  primary key (user_id, opponent_id)
);

-- ---------- RLS（读取；写入由 definer RPC 完成）----------
alter table public.opponents           enable row level security;
alter table public.opponent_stats      enable row level security;
alter table public.opponent_hand_index enable row level security;
alter table public.opponent_reports    enable row level security;

drop policy if exists "opponents_all_own" on public.opponents;
create policy "opponents_all_own" on public.opponents
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists "opponent_stats_all_own" on public.opponent_stats;
create policy "opponent_stats_all_own" on public.opponent_stats
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists "opponent_hand_index_all_own" on public.opponent_hand_index;
create policy "opponent_hand_index_all_own" on public.opponent_hand_index
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists "opponent_reports_all_own" on public.opponent_reports;
create policy "opponent_reports_all_own" on public.opponent_reports
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- ---------- 幂等增量合并 RPC ----------
-- 一手的逐对手贡献数组 p_players: [{alias, is_hero, net, counters}]。
-- 只聚合非英雄；无 hand_id 直接跳过（无法幂等去重）。同一对手同一手只计一次。
create or replace function public.apply_hand_contributions(p_hand_id text, p_players jsonb)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_uid uuid := auth.uid();
  rec jsonb;
  v_alias text;
  v_net numeric;
  v_counters jsonb;
  v_opp uuid;
  v_rows int;
  v_now bigint := (extract(epoch from now()) * 1000)::bigint;
begin
  if v_uid is null then raise exception 'not authenticated'; end if;
  if p_hand_id is null or length(trim(p_hand_id)) = 0 then return; end if;
  if p_players is null or jsonb_typeof(p_players) <> 'array' then return; end if;

  for rec in select value from jsonb_array_elements(p_players) as value loop
    if coalesce((rec ->> 'is_hero')::bool, false) then continue; end if;
    v_alias := trim(coalesce(rec ->> 'alias', ''));
    if v_alias = '' then continue; end if;
    v_net := coalesce((rec ->> 'net')::numeric, 0);
    v_counters := coalesce(rec -> 'counters', '{}'::jsonb);

    insert into opponents (user_id, primary_alias, updated_at)
      values (v_uid, v_alias, v_now)
      on conflict (user_id, primary_alias) do nothing;
    select opponent_id into v_opp
      from opponents where user_id = v_uid and primary_alias = v_alias;

    -- 幂等守卫：新插入返回 1；已存在（同手同对手）→ 跳过
    insert into opponent_hand_index (user_id, opponent_id, hand_id, net, counters)
      values (v_uid, v_opp, p_hand_id, v_net, v_counters)
      on conflict (user_id, opponent_id, hand_id) do nothing;
    get diagnostics v_rows = row_count;
    if v_rows = 0 then continue; end if;

    insert into opponent_stats (user_id, opponent_id, hand_count, net, counters, updated_at)
      values (v_uid, v_opp, 1, v_net, v_counters, v_now)
      on conflict (user_id, opponent_id) do update set
        hand_count = opponent_stats.hand_count + 1,
        net        = opponent_stats.net + excluded.net,
        counters   = public.jsonb_deep_add(opponent_stats.counters, excluded.counters),
        updated_at = v_now;
  end loop;
end;
$$;

-- 回滚一手（编辑/删除前调用）：按 index 里存的 delta 从聚合中扣除，再删除 index 行。
create or replace function public.retract_hand(p_hand_id text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_uid uuid := auth.uid();
  r record;
  v_now bigint := (extract(epoch from now()) * 1000)::bigint;
begin
  if v_uid is null then raise exception 'not authenticated'; end if;
  if p_hand_id is null then return; end if;
  for r in select opponent_id, net, counters from opponent_hand_index
           where user_id = v_uid and hand_id = p_hand_id loop
    update opponent_stats set
      hand_count = greatest(0, hand_count - 1),
      net        = net - r.net,
      counters   = public.jsonb_deep_add(counters, public.jsonb_deep_scale(r.counters, -1)),
      updated_at = v_now
    where user_id = v_uid and opponent_id = r.opponent_id;
  end loop;
  delete from opponent_hand_index where user_id = v_uid and hand_id = p_hand_id;
end;
$$;

grant execute on function public.apply_hand_contributions(text, jsonb) to authenticated;
grant execute on function public.retract_hand(text) to authenticated;
