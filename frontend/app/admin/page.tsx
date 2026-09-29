"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { fetchAdminOverview, fetchWpkMe, type AdminOverview, type WpkMe } from "@/lib/account";

export default function AdminPage() {
  const { enabled, loading, user } = useAuth();
  const [me, setMe] = useState<WpkMe | null>(null);
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    fetchWpkMe()
      .then(async (profile) => {
        setMe(profile);
        if (!profile.is_admin) return;
        setOverview(await fetchAdminOverview());
      })
      .catch((err: Error) => setError(err.message));
  }, [user]);

  if (!enabled) {
    return <main className="mx-auto max-w-xl px-6 py-16 text-sm text-neutral-300">云端账号未配置。</main>;
  }
  if (loading) {
    return <main className="mx-auto max-w-xl px-6 py-16 text-sm text-neutral-400">正在确认登录状态…</main>;
  }
  if (!user) {
    return (
      <main className="mx-auto max-w-xl px-6 py-16 text-sm text-neutral-300">
        <Link href="/login" className="text-emerald-300">登录</Link>
        后才能进入后台。
      </main>
    );
  }
  if (error) {
    return <main className="mx-auto max-w-xl px-6 py-16 text-sm text-red-300">{error}</main>;
  }
  if (!me) {
    return <main className="mx-auto max-w-xl px-6 py-16 text-sm text-neutral-400">读取账号…</main>;
  }
  if (!me.is_admin) {
    return (
      <main className="mx-auto max-w-xl px-6 py-16 text-sm text-neutral-300">
        {me.email} 不是管理员。每位用户只能查看自己的手牌。
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <h1 className="text-2xl font-bold">后台</h1>
      <p className="mt-2 text-sm text-neutral-400">
        已登录管理员 {me.email}。历史牌局在对应管理员账号注册后自动归到该账号。
      </p>
      <div className="mt-6 grid gap-3 sm:grid-cols-2">
        <Stat label="云端手牌" value={overview ? String(overview.hands) : "…"} />
        <Stat label="注册用户" value={overview ? String(overview.profiles.length) : "…"} />
      </div>

      <section className="mt-8">
        <h2 className="text-sm font-medium text-neutral-300">按归属</h2>
        <ul className="mt-3 divide-y divide-neutral-800 rounded-2xl border border-neutral-800">
          {(overview?.by_owner || []).map((row) => (
            <li key={row.owner_email} className="flex items-center justify-between px-4 py-3 text-sm">
              <span>{row.owner_email}</span>
              <span className="text-neutral-400">{row.hands} 手</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="mt-8">
        <h2 className="text-sm font-medium text-neutral-300">用户</h2>
        <ul className="mt-3 divide-y divide-neutral-800 rounded-2xl border border-neutral-800">
          {(overview?.profiles || []).map((profile) => (
            <li key={profile.id} className="flex items-center justify-between px-4 py-3 text-sm">
              <span>
                {profile.email}
                {profile.is_admin && (
                  <span className="ml-2 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] text-emerald-300">
                    管理员
                  </span>
                )}
              </span>
              <span className="text-neutral-400">{profile.hands} 手</span>
            </li>
          ))}
          {overview && overview.profiles.length === 0 && (
            <li className="px-4 py-3 text-sm text-neutral-500">还没有人注册。管理员邮箱注册后会出现在这里。</li>
          )}
        </ul>
      </section>
    </main>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-neutral-800 bg-neutral-900/40 p-4">
      <div className="text-xs text-neutral-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
    </div>
  );
}
