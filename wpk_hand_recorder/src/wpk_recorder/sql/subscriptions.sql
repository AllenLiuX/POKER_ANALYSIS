-- Manual subscriptions. No payment provider yet: an admin writes one row per
-- user. A missing row is Free. Pro with a null expiry does not expire.
-- The browser cannot write this table. Admin changes go through admin_set_plan.

create table if not exists wpk.subscriptions (
    user_id uuid primary key references wpk.profiles (id) on delete cascade,
    plan text not null default 'free' check (plan in ('free', 'pro')),
    expires_at timestamptz,
    note text,
    granted_by uuid,
    granted_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists wpk.subscription_events (
    id bigint generated always as identity primary key,
    user_id uuid not null references wpk.profiles (id) on delete cascade,
    plan text not null,
    expires_at timestamptz,
    note text,
    granted_by uuid,
    created_at timestamptz not null default now()
);

create index if not exists idx_wpk_subscriptions_plan on wpk.subscriptions (plan);
create index if not exists idx_wpk_subscription_events_user
    on wpk.subscription_events (user_id, created_at desc);

alter table wpk.subscriptions enable row level security;
alter table wpk.subscription_events enable row level security;

create or replace function wpk.effective_plan(uid uuid)
returns text
language sql
stable
security definer
set search_path = wpk
as $$
    select case
        when exists (
            select 1
            from wpk.subscriptions s
            where s.user_id = uid
              and s.plan = 'pro'
              and (s.expires_at is null or s.expires_at > now())
        ) then 'pro'
        else 'free'
    end;
$$;

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
    sub wpk.subscriptions%rowtype;
    active_pro boolean;
begin
    if auth.uid() is null then
        raise exception 'not authenticated';
    end if;
    select * into profile from wpk.profiles where id = auth.uid();
    select * into sub from wpk.subscriptions where user_id = auth.uid();
    active_pro := wpk.effective_plan(auth.uid()) = 'pro';
    select count(*) into hand_count
    from wpk.hands h
    where wpk.owns_hand(h.hand_id);
    return jsonb_build_object(
        'user_id', auth.uid(),
        'email', coalesce(profile.email, auth.jwt() ->> 'email'),
        'is_admin', coalesce(profile.is_admin, false),
        'hands', hand_count,
        'plan', coalesce(sub.plan, 'free'),
        'plan_expires_at', sub.expires_at,
        'entitled', coalesce(profile.is_admin, false) or active_pro
    );
end;
$$;

create or replace function public.admin_dashboard()
returns jsonb
language plpgsql
stable
security definer
set search_path = wpk, public
as $$
declare
    caller uuid := auth.uid();
    cutoff_30 text;
    cutoff_today text;
begin
    if caller is null or not exists (
        select 1 from wpk.profiles p where p.id = caller and p.is_admin
    ) then
        raise exception '需要管理员权限';
    end if;

    cutoff_30 := to_char(
        (now() at time zone 'utc') - interval '30 days',
        'YYYY-MM-DD"T"HH24:MI:SS'
    );
    cutoff_today := to_char(
        date_trunc('day', now() at time zone 'utc'),
        'YYYY-MM-DD"T"HH24:MI:SS'
    );

    return jsonb_build_object(
        'generated_at', now(),
        'stats', jsonb_build_object(
            'users', (select count(*) from wpk.profiles),
            'pro_users', (
                select count(*)
                from wpk.profiles p
                where wpk.effective_plan(p.id) = 'pro'
            ),
            'hands', (select count(*) from wpk.hands),
            'hands_30d', (
                select count(*) from wpk.hands h
                where h.played_at >= cutoff_30
            ),
            'hands_today', (
                select count(*) from wpk.hands h
                where h.played_at >= cutoff_today
            )
        ),
        'plan_breakdown', (
            select coalesce(
                jsonb_agg(jsonb_build_object('key', t.key, 'count', t.n) order by t.n desc),
                '[]'::jsonb
            )
            from (
                select
                    case when wpk.effective_plan(p.id) = 'pro' then 'pro' else 'free' end as key,
                    count(*)::int as n
                from wpk.profiles p
                group by 1
            ) t
        ),
        'quality_breakdown', (
            select coalesce(
                jsonb_agg(jsonb_build_object('key', t.key, 'count', t.n) order by t.n desc),
                '[]'::jsonb
            )
            from (
                select h.quality_status as key, count(*)::int as n
                from wpk.hands h
                group by h.quality_status
            ) t
        ),
        'mode_breakdown', (
            select coalesce(
                jsonb_agg(jsonb_build_object('key', t.key, 'count', t.n) order by t.n desc),
                '[]'::jsonb
            )
            from (
                select h.game_mode as key, count(*)::int as n
                from wpk.hands h
                group by h.game_mode
            ) t
        ),
        'top_users', (
            select coalesce(jsonb_agg(to_jsonb(t) order by t.hands_30d desc), '[]'::jsonb)
            from (
                select
                    p.id as user_id,
                    p.email,
                    nullif(btrim(coalesce(
                        u.raw_user_meta_data ->> 'display_name',
                        u.raw_user_meta_data ->> 'full_name',
                        u.raw_user_meta_data ->> 'name',
                        ''
                    )), '') as display_name,
                    p.is_admin,
                    coalesce(s.plan, 'free') as plan,
                    (wpk.effective_plan(p.id) = 'pro') as active,
                    s.expires_at as plan_expires_at,
                    c.hands_30d
                from wpk.profiles p
                join (
                    select owner_id, count(*)::int as hands_30d
                    from (
                        select p2.id as owner_id, h.hand_id
                        from wpk.profiles p2
                        join wpk.hands h
                          on h.owner_id = p2.id
                          or (
                              h.owner_id is null
                              and lower(h.owner_email) = lower(coalesce(p2.email, ''))
                          )
                        where h.played_at >= cutoff_30
                    ) matched
                    group by owner_id
                ) c on c.owner_id = p.id
                left join auth.users u on u.id = p.id
                left join wpk.subscriptions s on s.user_id = p.id
                order by c.hands_30d desc, p.created_at desc
                limit 10
            ) t
        ),
        'users', (
            select coalesce(jsonb_agg(to_jsonb(t) order by t.created_at desc), '[]'::jsonb)
            from (
                select
                    p.id as user_id,
                    p.email,
                    nullif(btrim(coalesce(
                        u.raw_user_meta_data ->> 'display_name',
                        u.raw_user_meta_data ->> 'full_name',
                        u.raw_user_meta_data ->> 'name',
                        ''
                    )), '') as display_name,
                    p.is_admin,
                    coalesce(s.plan, 'free') as plan,
                    (wpk.effective_plan(p.id) = 'pro') as active,
                    s.expires_at as plan_expires_at,
                    s.note as plan_note,
                    coalesce(c.hands, 0) as hands,
                    coalesce(c.hands_30d, 0) as hands_30d,
                    p.created_at,
                    u.last_sign_in_at,
                    c.last_hand_at
                from wpk.profiles p
                left join auth.users u on u.id = p.id
                left join wpk.subscriptions s on s.user_id = p.id
                left join (
                    select
                        p2.id as owner_id,
                        count(*)::int as hands,
                        count(*) filter (where h.played_at >= cutoff_30)::int as hands_30d,
                        max(h.played_at) as last_hand_at
                    from wpk.profiles p2
                    join wpk.hands h
                      on h.owner_id = p2.id
                      or (
                          h.owner_id is null
                          and lower(h.owner_email) = lower(coalesce(p2.email, ''))
                      )
                    group by p2.id
                ) c on c.owner_id = p.id
                order by p.created_at desc
                limit 50
            ) t
        ),
        'unclaimed', (
            select coalesce(jsonb_agg(to_jsonb(t) order by t.hands desc), '[]'::jsonb)
            from (
                select h.owner_email, count(*)::int as hands
                from wpk.hands h
                where h.owner_id is null
                  and not exists (
                      select 1
                      from wpk.profiles p
                      where lower(coalesce(p.email, '')) = lower(h.owner_email)
                  )
                group by h.owner_email
                order by count(*) desc
            ) t
        )
    );
end;
$$;

create or replace function public.admin_set_plan(
    target_user_id uuid,
    new_plan text,
    new_expires_at timestamptz default null,
    new_note text default null
)
returns jsonb
language plpgsql
security definer
set search_path = wpk, public
as $$
declare
    caller uuid := auth.uid();
    expires_value timestamptz;
    clean_note text;
begin
    if caller is null or not exists (
        select 1 from wpk.profiles p where p.id = caller and p.is_admin
    ) then
        raise exception '需要管理员权限';
    end if;
    if new_plan not in ('free', 'pro') then
        raise exception '套餐必须是 free 或 pro';
    end if;
    if not exists (select 1 from wpk.profiles p where p.id = target_user_id) then
        raise exception '用户不存在';
    end if;

    expires_value := case when new_plan = 'pro' then new_expires_at else null end;
    clean_note := nullif(left(btrim(coalesce(new_note, '')), 200), '');

    insert into wpk.subscriptions (
        user_id, plan, expires_at, note, granted_by, granted_at, updated_at
    )
    values (
        target_user_id, new_plan, expires_value, clean_note, caller, now(), now()
    )
    on conflict (user_id) do update
        set plan = excluded.plan,
            expires_at = excluded.expires_at,
            note = excluded.note,
            granted_by = excluded.granted_by,
            granted_at = excluded.granted_at,
            updated_at = now();

    insert into wpk.subscription_events (user_id, plan, expires_at, note, granted_by)
    values (target_user_id, new_plan, expires_value, clean_note, caller);

    return jsonb_build_object(
        'user_id', target_user_id,
        'plan', new_plan,
        'active', wpk.effective_plan(target_user_id) = 'pro',
        'expires_at', expires_value,
        'note', clean_note
    );
end;
$$;

revoke all on function wpk.effective_plan(uuid) from public, anon, authenticated;
revoke all on function public.wpk_me() from public, anon;
revoke all on function public.admin_dashboard() from public, anon;
revoke all on function public.admin_set_plan(uuid, text, timestamptz, text) from public, anon;

grant execute on function public.wpk_me() to authenticated;
grant execute on function public.admin_dashboard() to authenticated;
grant execute on function public.admin_set_plan(uuid, text, timestamptz, text) to authenticated;
