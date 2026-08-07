"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  clearProgress,
  loadAttempts,
  positionKey,
  summarize,
  type Attempt,
  type Bucket,
  type Summary,
} from "@/lib/progress";

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
  const [summary, setSummary] = useState<Summary | null>(null);

  function refresh() {
    setSummary(summarize(loadAttempts()));
  }

  useEffect(() => {
    refresh();
  }, []);

  if (!summary) return null;

  const empty = summary.total === 0;

  return (
    <main className="mx-auto max-w-3xl px-6 py-10">
      <div className="mb-6 flex items-center gap-3">
        <Link href="/trainer" className="text-sm text-neutral-400 hover:text-neutral-200">
          ← 训练器
        </Link>
        <h1 className="text-2xl font-bold">我的进度</h1>
        {!empty && (
          <button
            onClick={() => {
              if (confirm("确定清空本地进度记录？此操作不可撤销。")) {
                clearProgress();
                refresh();
              }
            }}
            className="ml-auto rounded-lg bg-neutral-800 px-3 py-1.5 text-xs text-neutral-400 hover:bg-red-900/60 hover:text-red-300"
          >
            清空进度
          </button>
        )}
      </div>

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
            进度保存在本机浏览器（localStorage）。登录云同步将在接入 Supabase 后开放。
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

          {/* 错题回顾 */}
          <section className="rounded-2xl border border-neutral-800 bg-neutral-900/60 p-5">
            <h2 className="mb-3 text-sm font-semibold text-neutral-300">
              错题回顾{" "}
              <span className="font-normal text-neutral-500">
                最近 {summary.recentMistakes.length} 手
              </span>
            </h2>
            {summary.recentMistakes.length === 0 ? (
              <p className="text-sm text-neutral-500">暂无错题，保持！</p>
            ) : (
              <ul className="divide-y divide-neutral-800">
                {summary.recentMistakes.map((m, i) => (
                  <MistakeRow key={`${m.ts}-${i}`} m={m} />
                ))}
              </ul>
            )}
          </section>

          <p className="mt-6 text-center text-xs text-neutral-600">
            进度保存在本机浏览器（localStorage）。登录云同步将在接入 Supabase 后开放。
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

function MistakeRow({ m }: { m: Attempt }) {
  return (
    <li className="flex items-center gap-3 py-2 text-sm">
      <span className="w-14 font-bold text-neutral-100">{m.handClass}</span>
      <span className="w-32 shrink-0 truncate text-neutral-400">
        {positionKey(m)}
      </span>
      <span className="text-neutral-500">
        你选 <span className="text-red-400">{ACTION_LABEL[m.action] ?? m.action}</span>
        {" → 应 "}
        <span className="text-emerald-400">
          {ACTION_LABEL[m.optimalAction] ?? m.optimalAction}
        </span>
      </span>
    </li>
  );
}
