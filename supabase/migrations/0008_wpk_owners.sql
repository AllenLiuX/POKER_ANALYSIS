-- Hands belong to a user. Historical rows stay with the admin email until that
-- account signs up, then the trigger copies their auth id onto those rows.
-- Direct table access stays closed. The browser uses the public functions below,
-- which enforce the same ownership rule as RLS.

alter table wpk.hands add column if not exists owner_email text not null default 'allenliux01@gmail.com';
alter table wpk.hands add column if not exists owner_id uuid;

create index if not exists idx_wpk_hands_owner_email on wpk.hands (owner_email);
create index if not exists idx_wpk_hands_owner_id on wpk.hands (owner_id);

create table if not exists wpk.admin_emails (
    email text primary key
);

insert into wpk.admin_emails (email)
values ('allenliux01@gmail.com')
on conflict (email) do nothing;

create table if not exists wpk.profiles (
    id uuid primary key references auth.users (id) on delete cascade,
    email text,
    is_admin boolean not null default false,
    created_at timestamptz not null default now()
);

create or replace function wpk.owns_hand(hid text)
returns boolean
language sql
stable
security definer
set search_path = wpk
as $$
    select exists (
        select 1
        from wpk.hands h
        where h.hand_id = hid
          and (
              h.owner_id = auth.uid()
              or (
                  h.owner_id is null
                  and lower(h.owner_email) = lower(coalesce(auth.jwt() ->> 'email', ''))
              )
          )
    );
$$;

create or replace function wpk.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = wpk
as $$
declare
    admin_flag boolean;
begin
    select exists (
        select 1
        from wpk.admin_emails e
        where lower(e.email) = lower(coalesce(new.email, ''))
    )
    into admin_flag;

    insert into wpk.profiles (id, email, is_admin)
    values (new.id, new.email, admin_flag)
    on conflict (id) do update
        set email = excluded.email,
            is_admin = wpk.profiles.is_admin or excluded.is_admin;

    if admin_flag then
        update wpk.hands
        set owner_id = new.id
        where owner_id is null
          and lower(owner_email) = lower(coalesce(new.email, ''));
    end if;
    return new;
end;
$$;

drop trigger if exists wpk_on_auth_user_created on auth.users;
create trigger wpk_on_auth_user_created
    after insert on auth.users
    for each row execute function wpk.handle_new_user();

insert into wpk.profiles (id, email, is_admin)
select u.id,
       u.email,
       exists (
           select 1
           from wpk.admin_emails e
           where lower(e.email) = lower(coalesce(u.email, ''))
       )
from auth.users u
on conflict (id) do update
    set email = excluded.email,
        is_admin = wpk.profiles.is_admin or excluded.is_admin;

update wpk.hands h
set owner_id = p.id
from wpk.profiles p
where h.owner_id is null
  and p.is_admin
  and lower(p.email) = lower(h.owner_email);

alter table wpk.hands enable row level security;
alter table wpk.hand_players enable row level security;
alter table wpk.actions enable row level security;
alter table wpk.decision_snapshots enable row level security;
alter table wpk.opportunities enable row level security;
alter table wpk.board_cards enable row level security;
alter table wpk.results enable row level security;
alter table wpk.hand_squid_players enable row level security;
alter table wpk.squid_awards enable row level security;
alter table wpk.showdown_observations enable row level security;
alter table wpk.decision_states enable row level security;
alter table wpk.strategy_evaluations enable row level security;
alter table wpk.inference_contexts enable row level security;
alter table wpk.inference_runs enable row level security;
alter table wpk.players enable row level security;
alter table wpk.profiles enable row level security;
alter table wpk.admin_emails enable row level security;

drop policy if exists hands_owner_select on wpk.hands;
create policy hands_owner_select on wpk.hands
    for select to authenticated
    using (wpk.owns_hand(hand_id));

drop policy if exists profiles_select_own on wpk.profiles;
create policy profiles_select_own on wpk.profiles
    for select to authenticated
    using (id = auth.uid());

create or replace function public.wpk_me()
returns jsonb
language plpgsql
stable
security definer
set search_path = wpk, public
as $$
declare
    profile wpk.profiles%rowtype;
    hand_count bigint;
begin
    if auth.uid() is null then
        raise exception 'not authenticated';
    end if;
    select * into profile from wpk.profiles where id = auth.uid();
    select count(*) into hand_count
    from wpk.hands h
    where wpk.owns_hand(h.hand_id);
    return jsonb_build_object(
        'user_id', auth.uid(),
        'email', coalesce(profile.email, auth.jwt() ->> 'email'),
        'is_admin', coalesce(profile.is_admin, false),
        'hands', hand_count
    );
end;
$$;

create or replace function public.my_hands(lim integer default 50)
returns table (
    hand_id text,
    hand_number bigint,
    played_at text,
    game_mode text,
    status text,
    pot double precision,
    board_json text,
    quality_status text
)
language sql
stable
security definer
set search_path = wpk, public
as $$
    select h.hand_id, h.hand_number, h.played_at, h.game_mode, h.status,
           h.pot, h.board_json, h.quality_status
    from wpk.hands h
    where wpk.owns_hand(h.hand_id)
    order by h.played_at desc nulls last
    limit least(greatest(coalesce(lim, 50), 1), 200);
$$;

create or replace function public.my_opponents(lim integer default 50)
returns table (
    user_id text,
    alias text,
    hands bigint
)
language sql
stable
security definer
set search_path = wpk, public
as $$
    select hp.user_id,
           max(hp.alias) as alias,
           count(distinct hp.hand_id) as hands
    from wpk.hand_players hp
    join wpk.hands h on h.hand_id = hp.hand_id
    where hp.is_hero = 0
      and hp.user_id is not null
      and wpk.owns_hand(h.hand_id)
    group by hp.user_id
    order by count(distinct hp.hand_id) desc
    limit least(greatest(coalesce(lim, 50), 1), 200);
$$;

create or replace function public.admin_overview()
returns jsonb
language plpgsql
stable
security definer
set search_path = wpk, public
as $$
declare
    caller uuid := auth.uid();
begin
    if caller is null or not exists (
        select 1 from wpk.profiles p where p.id = caller and p.is_admin
    ) then
        raise exception 'not admin';
    end if;
    return jsonb_build_object(
        'hands', (select count(*) from wpk.hands),
        'profiles', (
            select coalesce(jsonb_agg(to_jsonb(t)), '[]'::jsonb)
            from (
                select p.id, p.email, p.is_admin, p.created_at,
                       (
                           select count(*)
                           from wpk.hands h
                           where h.owner_id = p.id
                              or (
                                  h.owner_id is null
                                  and lower(h.owner_email) = lower(coalesce(p.email, ''))
                              )
                       ) as hands
                from wpk.profiles p
                order by p.created_at desc
            ) t
        ),
        'by_owner', (
            select coalesce(jsonb_agg(to_jsonb(t)), '[]'::jsonb)
            from (
                select h.owner_email, count(*) as hands
                from wpk.hands h
                group by h.owner_email
                order by count(*) desc
            ) t
        )
    );
end;
$$;

revoke all on function public.wpk_me() from public;
revoke all on function public.my_hands(integer) from public;
revoke all on function public.my_opponents(integer) from public;
revoke all on function public.admin_overview() from public;
grant execute on function public.wpk_me() to authenticated;
grant execute on function public.my_hands(integer) to authenticated;
grant execute on function public.my_opponents(integer) to authenticated;
grant execute on function public.admin_overview() to authenticated;
