"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import { useAuth } from "@/lib/auth";
import {
  fetchMyHands,
  fetchMyOpponents,
  type OwnedHand,
  type OwnedOpponent,
} from "@/lib/account";

export default function MyHandsPage() {
  const { enabled, loading, user } = useAuth();
  const [hands, setHands] = useState<OwnedHand[]>([]);
  const [opponents, setOpponents] = useState<OwnedOpponent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!user) return;
    setBusy(true);
    Promise.all([fetchMyHands(50), fetchMyOpponents(30)])
      .then(([nextHands, nextOpponents]) => {
        setHands(nextHands || []);
        setOpponents(nextOpponents || []);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  }, [user]);

  if (!enabled) {
    return <Notice>云端账号未配置，手牌仍只在本机记录器里。</Notice>;
  }
  if (loading) return <Notice>正在确认登录状态…</Notice>;
  if (!user) {
    return (
      <Notice>
        登录后只能看到自己名下的手牌和对手汇总。
        <Link href="/login" className="mt-4 inline-block text-emerald-300">
          去登录
        </Link>
      </Notice>
    );
  }

  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <h1 className="text-2xl font-bold">我的手牌</h1>
      <p className="mt-2 text-sm text-neutral-400">
        {user.email} 的云端记录。其他账号的牌局不会出现在这里。
      </p>
      {error && <p className="mt-4 text-sm text-red-300">{error}</p>}
      {busy && <p className="mt-4 text-sm text-neutral-500">读取中…</p>}

      <section className="mt-8">
        <h2 className="text-sm font-medium text-neutral-300">最近手牌</h2>
        <div className="mt-3 overflow-hidden rounded-2xl border border-neutral-800">
          {hands.length === 0 ? (
            <p className="p-4 text-sm text-neutral-500">还没有归到这个账号的手牌。</p>
          ) : (
            <table className="w-full text-left text-sm">
              <thead className="bg-neutral-900 text-neutral-400">
                <tr>
                  <th className="px-4 py-2 font-medium">时间</th>
                  <th className="px-4 py-2 font-medium">模式</th>
                  <th className="px-4 py-2 font-medium">公共牌</th>
                  <th className="px-4 py-2 font-medium">底池</th>
                </tr>
              </thead>
              <tbody>
                {hands.map((hand) => (
                  <tr key={hand.hand_id} className="border-t border-neutral-800">
                    <td className="px-4 py-2 text-neutral-200">{hand.played_at || "—"}</td>
                    <td className="px-4 py-2">{hand.game_mode}</td>
                    <td className="px-4 py-2 text-neutral-400">{hand.board_json}</td>
                    <td className="px-4 py-2">{hand.pot ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>

      <section className="mt-8">
        <h2 className="text-sm font-medium text-neutral-300">对手汇总</h2>
        <div className="mt-3 overflow-hidden rounded-2xl border border-neutral-800">
          {opponents.length === 0 ? (
            <p className="p-4 text-sm text-neutral-500">暂无对手样本。</p>
          ) : (
            <table className="w-full text-left text-sm">
              <thead className="bg-neutral-900 text-neutral-400">
                <tr>
                  <th className="px-4 py-2 font-medium">对手</th>
                  <th className="px-4 py-2 font-medium">同桌手数</th>
                </tr>
              </thead>
              <tbody>
                {opponents.map((opponent) => (
                  <tr key={opponent.user_id} className="border-t border-neutral-800">
                    <td className="px-4 py-2">{opponent.alias || opponent.user_id}</td>
                    <td className="px-4 py-2">{opponent.hands}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>
    </main>
  );
}

function Notice({ children }: { children: ReactNode }) {
  return (
    <main className="mx-auto max-w-xl px-6 py-16 text-sm text-neutral-300">
      {children}
    </main>
  );
}
