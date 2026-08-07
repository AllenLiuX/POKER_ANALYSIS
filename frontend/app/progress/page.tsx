"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  clearProgress,
  loadAttempts,
  positionKey,
  summarize,
  type Attempt,
  type Bucket,
  type Summary,
} from "@/lib/progress";
import {
  clearCloudAttempts,
  fetchCloudAttempts,
  syncLocalToCloud,
} from "@/lib/cloud";
import {
  postTrainerCoach,
  postTrainerReview,
  type ReviewRequestBody,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";

const PREFLOP_SPOTS = new Set(["RFI", "vs_RFI"]);

/** 把起手类别还原成一副代表手牌（供逐手 AI 讲解重放）。 */
function handClassToCards(hc: string): string[] | null {
  if (/^([AKQJT98765432])\1$/.test(hc)) return [`${hc[0]}h`, `${hc[0]}d`];
  const m = hc.match(/^([AKQJT98765432])([AKQJT98765432])([so])$/);
  if (!m) return null;
  const [, r1, r2, suit] = m;
  return suit === "s" ? [`${r1}h`, `${r2}h`] : [`${r1}h`, `${r2}d`];
}

function toReviewBody(summary: Summary): ReviewRequestBody {
  return {
    total: summary.total,
    accuracy: summary.accuracy,
    current_streak: summary.currentStreak,
    best_streak: summary.bestStreak,
    by_grade: summary.byGrade,
    by_spot: Object.entries(summary.bySpot).map(([key, b]) => ({
      key,
      total: b.total,
      correct: b.correct,
    })),
    by_position: Object.entries(summary.byPosition).map(([key, b]) => ({
      key,
      total: b.total,
      correct: b.correct,
    })),
    mistakes: summary.recentMistakes.map((m) => ({
      spot: m.spot,
      position: m.position,
      hero_position: m.heroPosition,
      opener: m.opener,
      hand_class: m.handClass,
      action: m.action,
      optimal_action: m.optimalAction,
    })),
  };
}

const ACTION_LABEL: Record<string, string> = {
  fold: "弃牌",
  call: "跟注",
  raise: "加注",
  allin: "全下",
};
const SPOT_LABEL: Record<string, string> = {
  RFI: "开池 (RFI)",
  vs_RFI: "防守 (面对开池)",
};

function pct(n: number): string {
  return `${Math.round(n * 100)}%`;
}

function accColor(a: number): string {
  if (a >= 0.85) return "text-emerald-400";
  if (a >= 0.7) return "text-amber-400";
  return "text-red-400";
}

export default function ProgressPage() {
  const { enabled, user, signOut } = useAuth();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [busy, setBusy] = useState(false);
  const [review, setReview] = useState<{
    text: string | null;
    loading: boolean;
    error: string | null;
  }>({ text: null, loading: false, error: null });
  const cloud = enabled && !!user;

  const runReview = useCallback(async () => {
    if (!summary || summary.total === 0) return;
    setReview({ text: null, loading: true, error: null });
    try {
      const res = await postTrainerReview(toReviewBody(summary));
      if (!res.report || !res.report.trim()) {
        setReview({ text: null, loading: false, error: "复盘生成为空，请重试" });
      } else {
        setReview({ text: res.report, loading: false, error: null });
      }
    } catch (e) {
      setReview({
        text: null,
        loading: false,
        error: String(e instanceof Error ? e.message : e),
      });
    }
  }, [summary]);

  const refresh = useCallback(async () => {
    setReview({ text: null, loading: false, error: null });
    if (cloud) {
      // 打开进度页时自动对账：补传任何本地有、云端还没有的手牌（含离线时写失败的）。
      await syncLocalToCloud(loadAttempts());
      setSummary(summarize(await fetchCloudAttempts()));
    } else {
      setSummary(summarize(loadAttempts()));
    }
  }, [cloud]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function handleClear() {
    if (!confirm("确定清空记录？此操作不可撤销。")) return;
    setBusy(true);
    if (cloud) await clearCloudAttempts();
    else clearProgress();
    await refresh();
    setBusy(false);
  }

  if (!summary) return null;

  const empty = summary.total === 0;

  return (
    <main className="mx-auto max-w-3xl px-6 py-10">
      <div className="mb-6 flex items-center gap-3">
        <h1 className="text-2xl font-bold">我的进度</h1>
        <span
          className={`rounded-full px-2 py-0.5 text-xs ${
            cloud
              ? "bg-emerald-900/60 text-emerald-300"
              : "bg-neutral-800 text-neutral-400"
          }`}
        >
          {cloud ? `云端 · ${user?.email}` : "本地模式"}
        </span>
        {!empty && (
          <button
            onClick={handleClear}
            disabled={busy}
            className="ml-auto rounded-lg bg-neutral-800 px-3 py-1.5 text-xs text-neutral-400 hover:bg-red-900/60 hover:text-red-300 disabled:opacity-50"
          >
            清空进度
          </button>
        )}
      </div>

      {/* 账户 / 同步操作条 */}
      {enabled && (
        <div className="mb-5 flex flex-wrap items-center gap-3 rounded-xl border border-neutral-800 bg-neutral-900/60 px-4 py-2.5 text-sm">
          {cloud ? (
            <>
              <span className="flex items-center gap-1.5 text-neutral-400">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                每手自动云端同步 · 跨设备可见
              </span>
              <button
                onClick={() => signOut()}
                className="ml-auto rounded-lg bg-neutral-800 px-3 py-1.5 text-xs text-neutral-400 hover:bg-neutral-700"
              >
                退出登录
              </button>
            </>
          ) : (
            <>
              <span className="text-neutral-400">登录后可跨设备同步进度</span>
              <Link
                href="/login"
                className="ml-auto rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-500"
              >
                登录 / 注册
              </Link>
            </>
          )}
        </div>
      )}

      {empty ? (
        <div className="rounded-2xl border border-neutral-800 bg-neutral-900/60 p-10 text-center">
          <p className="text-neutral-400">还没有练习记录。</p>
          <Link
            href="/trainer"
            className="mt-4 inline-block rounded-xl bg-emerald-600 px-5 py-2.5 font-medium text-white transition hover:bg-emerald-500"
          >
            去练一把 →
          </Link>
          <p className="mt-4 text-xs text-neutral-600">
            {enabled
              ? "登录后进度将云端同步，可跨设备访问。"
              : "进度保存在本机浏览器（localStorage）。配置 Supabase 后可开启登录云同步。"}
          </p>
        </div>
      ) : (
        <>
          {/* 概览 */}
          <section className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <BigStat label="累计手数" value={String(summary.total)} />
            <BigStat
              label="总正确率"
              value={pct(summary.accuracy)}
              valueClass={accColor(summary.accuracy)}
            />
            <BigStat label="当前连对" value={String(summary.currentStreak)} />
            <BigStat label="最佳连对" value={String(summary.bestStreak)} />
          </section>

          {/* 评级分布 */}
          <section className="mb-6 rounded-2xl border border-neutral-800 bg-neutral-900/60 p-5">
            <h2 className="mb-3 text-sm font-semibold text-neutral-300">评级分布</h2>
            <GradeBar summary={summary} />
          </section>

          {/* 分训练类型 / 位置 */}
          <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2">
            <BucketTable
              title="按训练类型"
              buckets={summary.bySpot}
              labelFn={(k) => SPOT_LABEL[k] ?? k}
            />
            <BucketTable
              title="按位置 / 对局"
              buckets={summary.byPosition}
              labelFn={(k) => k}
            />
          </div>

          {/* AI 复盘报告 */}
          <section className="mb-6 rounded-2xl border border-violet-800/40 bg-violet-950/20 p-5">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <h2 className="text-sm font-semibold text-violet-200">🧠 AI 复盘报告</h2>
              <span className="text-xs text-neutral-500">
                基于你近期 {summary.total} 手 · 分析漏洞与倾向，给出训练建议
              </span>
              {review.text && (
                <button
                  onClick={runReview}
                  disabled={review.loading}
                  className="ml-auto rounded-lg border border-violet-700/50 px-2.5 py-1 text-xs text-violet-300 transition hover:bg-violet-900/40 disabled:opacity-50"
                >
                  重新生成
                </button>
              )}
            </div>

            {review.text ? (
              <div className="whitespace-pre-line rounded-xl bg-neutral-950/60 p-4 text-sm leading-relaxed text-neutral-200">
                {review.text}
              </div>
            ) : (
              <button
                onClick={runReview}
                disabled={review.loading}
                className="w-full rounded-xl border border-violet-700/50 bg-violet-900/30 py-3 text-sm font-medium text-violet-100 transition hover:bg-violet-900/50 disabled:opacity-50"
              >
                {review.loading
                  ? "AI 教练正在复盘你的记录…"
                  : "生成我的复盘报告：我的问题在哪、该怎么练？"}
              </button>
            )}
            {review.error && (
              <p className="mt-2 text-xs text-red-400">复盘暂时不可用：{review.error}</p>
            )}
          </section>

          {/* 错题回顾 */}
          <section className="rounded-2xl border border-neutral-800 bg-neutral-900/60 p-5">
            <h2 className="mb-1 text-sm font-semibold text-neutral-300">
              错题回顾{" "}
              <span className="font-normal text-neutral-500">
                最近 {summary.recentMistakes.length} 手
              </span>
            </h2>
            {summary.recentMistakes.length === 0 ? (
              <p className="text-sm text-neutral-500">暂无错题，保持！</p>
            ) : (
              <>
                <p className="mb-2 text-xs text-neutral-600">点击任意一手展开详情与逐手讲解</p>
                <ul className="divide-y divide-neutral-800">
                  {summary.recentMistakes.map((m, i) => (
                    <MistakeItem key={`${m.ts}-${i}`} m={m} />
                  ))}
                </ul>
              </>
            )}
          </section>

          <p className="mt-6 text-center text-xs text-neutral-600">
            {cloud
              ? "进度已云端同步。"
              : enabled
                ? "本地模式；登录后可云端同步跨设备。"
                : "进度保存在本机浏览器（localStorage）。配置 Supabase 后可开启登录云同步。"}
          </p>
        </>
      )}
    </main>
  );
}

function BigStat({
  label,
  value,
  valueClass,
}: {
  label: string;
  value: string;
  valueClass?: string;
}) {
  return (
    <div className="rounded-xl border border-neutral-800 bg-neutral-900/60 p-4">
      <div className="text-[10px] uppercase tracking-wider text-neutral-500">
        {label}
      </div>
      <div className={`mt-1 text-2xl font-bold ${valueClass ?? "text-neutral-100"}`}>
        {value}
      </div>
    </div>
  );
}

function GradeBar({ summary }: { summary: Summary }) {
  const { byGrade, total } = summary;
  const segs: { key: string; label: string; color: string; n: number }[] = [
    { key: "optimal", label: "最优", color: "#10b981", n: byGrade.optimal },
    { key: "acceptable", label: "可接受", color: "#f59e0b", n: byGrade.acceptable },
    { key: "mistake", label: "偏离", color: "#dc2626", n: byGrade.mistake },
  ];
  return (
    <>
      <div className="flex h-4 w-full overflow-hidden rounded-full bg-neutral-800">
        {segs.map((s) =>
          s.n > 0 ? (
            <div
              key={s.key}
              style={{ width: `${(s.n / total) * 100}%`, backgroundColor: s.color }}
              title={`${s.label} ${s.n}`}
            />
          ) : null,
        )}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {segs.map((s) => (
          <span key={s.key} className="flex items-center gap-1.5">
            <span
              className="inline-block h-2.5 w-2.5 rounded-sm"
              style={{ backgroundColor: s.color }}
            />
            <span className="text-neutral-300">
              {s.label} {s.n}（{total ? pct(s.n / total) : "0%"}）
            </span>
          </span>
        ))}
      </div>
    </>
  );
}

function BucketTable({
  title,
  buckets,
  labelFn,
}: {
  title: string;
  buckets: Record<string, Bucket>;
  labelFn: (k: string) => string;
}) {
  const rows = Object.entries(buckets).sort((a, b) => b[1].total - a[1].total);
  return (
    <section className="rounded-2xl border border-neutral-800 bg-neutral-900/60 p-5">
      <h2 className="mb-3 text-sm font-semibold text-neutral-300">{title}</h2>
      <ul className="space-y-2">
        {rows.map(([k, b]) => {
          const a = b.total ? b.correct / b.total : 0;
          return (
            <li key={k} className="flex items-center gap-3 text-sm">
              <span className="w-28 shrink-0 truncate text-neutral-300">
                {labelFn(k)}
              </span>
              <div className="h-2 flex-1 overflow-hidden rounded-full bg-neutral-800">
                <div
                  className="h-full bg-emerald-500"
                  style={{ width: `${a * 100}%` }}
                />
              </div>
              <span className={`w-20 shrink-0 text-right ${accColor(a)}`}>
                {pct(a)}
                <span className="ml-1 text-xs text-neutral-600">
                  {b.correct}/{b.total}
                </span>
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

const GRADE_LABEL: Record<string, { label: string; cls: string }> = {
  optimal: { label: "最优", cls: "bg-emerald-500/15 text-emerald-300" },
  acceptable: { label: "可接受", cls: "bg-amber-500/15 text-amber-300" },
  mistake: { label: "偏离", cls: "bg-red-500/15 text-red-300" },
};

function fmtTime(ts: number): string {
  try {
    return new Date(ts).toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}

function MistakeItem({ m }: { m: Attempt }) {
  const [open, setOpen] = useState(false);
  const [coach, setCoach] = useState<{
    text: string | null;
    loading: boolean;
    error: string | null;
  }>({ text: null, loading: false, error: null });

  const isPreflop = PREFLOP_SPOTS.has(m.spot);

  const requestCoach = useCallback(async () => {
    if (coach.loading || coach.text) return;
    const cards = handClassToCards(m.handClass);
    if (!cards) {
      setCoach({ text: null, loading: false, error: "无法还原该手牌" });
      return;
    }
    setCoach({ text: null, loading: true, error: null });
    try {
      const res = await postTrainerCoach({
        format: "6max_100bb",
        spot: m.spot,
        position: m.position,
        hero: cards,
        action: m.action,
      });
      setCoach({ text: res.coaching, loading: false, error: null });
    } catch (e) {
      setCoach({
        text: null,
        loading: false,
        error: String(e instanceof Error ? e.message : e),
      });
    }
  }, [coach.loading, coach.text, m]);

  const g = GRADE_LABEL[m.grade] ?? GRADE_LABEL.mistake;

  return (
    <li className="py-1">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 py-1.5 text-left text-sm transition hover:opacity-80"
      >
        <span className="w-14 shrink-0 font-bold text-neutral-100">{m.handClass}</span>
        <span className="w-32 shrink-0 truncate text-neutral-400">{positionKey(m)}</span>
        <span className="min-w-0 flex-1 truncate text-neutral-500">
          你选 <span className="text-red-400">{ACTION_LABEL[m.action] ?? m.action}</span>
          {" → 应 "}
          <span className="text-emerald-400">
            {ACTION_LABEL[m.optimalAction] ?? m.optimalAction}
          </span>
        </span>
        <span className={`shrink-0 text-neutral-600 transition ${open ? "rotate-90" : ""}`}>
          ›
        </span>
      </button>

      {open && (
        <div className="mb-2 ml-1 rounded-xl border border-neutral-800 bg-neutral-950/50 p-3">
          <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-neutral-400">
            <span>
              训练类型：<span className="text-neutral-300">{SPOT_LABEL[m.spot] ?? m.spot}</span>
            </span>
            <span>
              位置：<span className="text-neutral-300">{positionKey(m)}</span>
            </span>
            <span className={`rounded px-1.5 py-0.5 ${g.cls}`}>{g.label}</span>
            <span className="text-neutral-600">{fmtTime(m.ts)}</span>
          </div>

          {isPreflop ? (
            coach.text ? (
              <div className="rounded-lg bg-neutral-900/70 p-3">
                <div className="mb-1 text-xs font-medium text-violet-300">AI 讲解</div>
                <p className="whitespace-pre-line text-sm leading-relaxed text-neutral-200">
                  {coach.text}
                </p>
              </div>
            ) : (
              <button
                onClick={requestCoach}
                disabled={coach.loading}
                className="w-full rounded-lg border border-violet-700/50 bg-violet-950/30 py-2 text-xs font-medium text-violet-200 transition hover:bg-violet-900/40 disabled:opacity-50"
              >
                {coach.loading ? "AI 教练思考中…" : "🧠 为什么应该这样打？"}
              </button>
            )
          ) : (
            <p className="text-xs text-neutral-500">翻后逐手讲解开发中，可在翻后训练器内即时查看讲解。</p>
          )}
          {coach.error && (
            <p className="mt-2 text-xs text-red-400">讲解暂时不可用：{coach.error}</p>
          )}
        </div>
      )}
    </li>
  );
}
