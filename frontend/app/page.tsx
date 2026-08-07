"use client";

import Link from "next/link";
import { useState } from "react";
import { API_BASE, postEquity, type EquityResponse } from "@/lib/api";

const TRAINING = [
  {
    title: "翻前训练器",
    desc: "6-max · 100bb · GTO 范围即时反馈。难度加权聚焦临界手牌，智能模式自动补弱项。",
    href: "/trainer",
    icon: "♠",
    tags: ["RFI / 防守", "GTO 范围", "AI 讲解"],
  },
  {
    title: "翻后训练器",
    desc: "翻牌启发式引擎：range 优势 / MDF / 赔率 / 下注尺度，透明可解释、边界宽容。",
    href: "/trainer/postflop",
    icon: "♣",
    tags: ["c-bet / 防守", "下注尺度", "蒙特卡洛胜率"],
  },
];

const TOOLS: {
  title: string;
  desc: string;
  href: string;
  icon: string;
  badge?: string;
}[] = [
  { title: "范围表", desc: "RFI + 防守 · 13×13 频率网格", href: "/ranges", icon: "▦" },
  { title: "训练进度", desc: "正确率 / 连对 / 错题回顾 · 本地或云端同步", href: "/progress", icon: "📈" },
  {
    title: "截图导入",
    desc: "WePoker 截图 → 观测事实提取（重建 / 剥削建设中）",
    href: "/import",
    icon: "📸",
    badge: "Beta",
  },
];

const COMING = [
  { title: "剥削训练", desc: "对手画像 + node-lock 偏离", phase: "Phase 9", icon: "🧠" },
  { title: "逐人剥削建议", desc: "截图重建 → 偏离标注 → LLM 剥削", phase: "Phase 6 · S2+", icon: "🎯" },
];

export default function Home() {
  return (
    <div className="relative min-h-screen overflow-hidden">
      {/* 背景光晕 */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[520px] bg-gradient-to-b from-emerald-500/10 via-emerald-500/[0.02] to-transparent"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -right-32 -top-24 -z-10 h-[380px] w-[380px] rounded-full bg-teal-500/10 blur-3xl"
      />

      <main className="mx-auto max-w-6xl px-6 py-8">
        {/* Hero */}
        <section className="mb-16 mt-6">
          <div className="inline-flex items-center gap-2 rounded-full border border-emerald-800/50 bg-emerald-950/30 px-3 py-1 text-xs text-emerald-300">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
            翻前 GTO · 翻后启发式 · 实时反馈
          </div>
          <h1 className="mt-5 text-4xl font-black leading-[1.1] tracking-tight sm:text-6xl">
            把每个决策
            <br />
            <span className="bg-gradient-to-r from-emerald-300 via-emerald-400 to-teal-300 bg-clip-text text-transparent">
              练成本能
            </span>
          </h1>
          <p className="mt-5 max-w-xl text-base leading-relaxed text-neutral-400">
            面向进阶牌手的德州扑克决策训练平台：即时对照 GTO 范围、翻后启发式打分、
            蒙特卡洛胜率与进度追踪——每一手都给出可解释的反馈。
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-3">
            <Link
              href="/trainer"
              className="rounded-xl bg-emerald-500 px-5 py-3 text-sm font-semibold text-emerald-950 shadow-lg shadow-emerald-500/20 transition hover:bg-emerald-400"
            >
              开始翻前训练 →
            </Link>
            <Link
              href="/trainer/postflop"
              className="rounded-xl border border-neutral-700 bg-neutral-900/60 px-5 py-3 text-sm font-semibold text-neutral-200 transition hover:border-neutral-600 hover:bg-neutral-800"
            >
              翻后训练
            </Link>
            <Link
              href="/ranges"
              className="px-2 py-3 text-sm font-medium text-neutral-400 transition hover:text-neutral-200"
            >
              浏览范围表 →
            </Link>
          </div>
        </section>

        {/* 训练场 */}
        <SectionTitle kicker="Train">训练场</SectionTitle>
        <section className="mb-14 grid grid-cols-1 gap-4 md:grid-cols-2">
          {TRAINING.map((m) => (
            <Link
              key={m.title}
              href={m.href}
              className="group relative overflow-hidden rounded-2xl border border-neutral-800 bg-neutral-900/50 p-6 transition hover:border-emerald-600/70 hover:bg-neutral-900"
            >
              <div
                aria-hidden
                className="pointer-events-none absolute -right-10 -top-10 h-32 w-32 rounded-full bg-emerald-500/5 blur-2xl transition group-hover:bg-emerald-500/10"
              />
              <div className="flex items-start justify-between">
                <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-emerald-500/10 text-2xl text-emerald-300 ring-1 ring-emerald-500/20">
                  {m.icon}
                </span>
                <span className="rounded-full bg-emerald-900/60 px-2.5 py-0.5 text-xs font-medium text-emerald-300">
                  可用
                </span>
              </div>
              <h3 className="mt-4 text-lg font-bold text-neutral-100">{m.title}</h3>
              <p className="mt-1.5 text-sm leading-relaxed text-neutral-400">{m.desc}</p>
              <div className="mt-4 flex flex-wrap gap-1.5">
                {m.tags.map((t) => (
                  <span
                    key={t}
                    className="rounded-md bg-neutral-800/80 px-2 py-0.5 text-[11px] text-neutral-400"
                  >
                    {t}
                  </span>
                ))}
              </div>
              <span className="mt-5 inline-flex items-center text-sm font-medium text-emerald-400 transition group-hover:translate-x-0.5">
                进入训练 →
              </span>
            </Link>
          ))}
        </section>

        {/* 工具 & 进度 */}
        <SectionTitle kicker="Tools">工具 &amp; 进度</SectionTitle>
        <section className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2">
          {TOOLS.map((m) => (
            <Link
              key={m.title}
              href={m.href}
              className="group flex items-center gap-4 rounded-xl border border-neutral-800 bg-neutral-900/50 p-4 transition hover:border-emerald-700/60 hover:bg-neutral-900"
            >
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-neutral-800 text-xl text-neutral-300">
                {m.icon}
              </span>
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <h3 className="font-semibold text-neutral-100">{m.title}</h3>
                  {m.badge && (
                    <span className="rounded-full bg-emerald-900/60 px-2 py-0.5 text-[10px] font-medium text-emerald-300">
                      {m.badge}
                    </span>
                  )}
                </div>
                <p className="truncate text-sm text-neutral-400">{m.desc}</p>
              </div>
              <span className="ml-auto text-neutral-600 transition group-hover:text-emerald-400">
                →
              </span>
            </Link>
          ))}
        </section>

        <EquityDemo />

        {/* 即将上线 */}
        <SectionTitle kicker="Soon">即将上线</SectionTitle>
        <section className="mb-12 grid grid-cols-1 gap-4 sm:grid-cols-2">
          {COMING.map((m) => (
            <div
              key={m.title}
              className="flex items-center gap-4 rounded-xl border border-dashed border-neutral-800 bg-neutral-900/30 p-4"
            >
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-neutral-800/60 text-xl grayscale">
                {m.icon}
              </span>
              <div className="min-w-0">
                <h3 className="font-semibold text-neutral-300">{m.title}</h3>
                <p className="truncate text-sm text-neutral-500">{m.desc}</p>
              </div>
              <span className="ml-auto rounded-full bg-neutral-800 px-2.5 py-0.5 text-xs text-neutral-400">
                {m.phase}
              </span>
            </div>
          ))}
        </section>

        <footer className="border-t border-neutral-900 pt-6 text-xs text-neutral-600">
          后端 API <code className="text-neutral-500">{API_BASE}</code> · 训练数据存本地，登录后可云端同步。
        </footer>
      </main>
    </div>
  );
}

function SectionTitle({
  kicker,
  children,
}: {
  kicker: string;
  children: React.ReactNode;
}) {
  return (
    <div className="mb-4 flex items-baseline gap-3">
      <h2 className="text-xl font-bold tracking-tight text-neutral-100">{children}</h2>
      <span className="text-[11px] uppercase tracking-[0.2em] text-neutral-600">
        {kicker}
      </span>
      <div className="ml-2 h-px flex-1 bg-gradient-to-r from-neutral-800 to-transparent" />
    </div>
  );
}

function EquityDemo() {
  const [hero, setHero] = useState("As Ad");
  const [range, setRange] = useState("QQ+, AKs, AKo");
  const [board, setBoard] = useState("");
  const [result, setResult] = useState<EquityResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function run() {
    setLoading(true);
    setErr(null);
    setResult(null);
    try {
      const heroCards = hero.trim().split(/\s+/).filter(Boolean);
      const boardCards = board.trim().split(/\s+/).filter(Boolean);
      const res = await postEquity({
        hero: heroCards,
        villain_range: range,
        board: boardCards.length ? boardCards : undefined,
        trials: 20000,
      });
      setResult(res);
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="mb-14 mt-4 rounded-2xl border border-neutral-800 bg-neutral-900/60 p-6">
      <h2 className="text-xl font-semibold">胜率计算器 <span className="text-sm font-normal text-neutral-500">（Monte Carlo · 已接通引擎）</span></h2>
      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Field label="英雄手牌" value={hero} onChange={setHero} placeholder="As Ad" />
        <Field label="对手范围" value={range} onChange={setRange} placeholder="QQ+, AKs" />
        <Field label="公共牌（可选）" value={board} onChange={setBoard} placeholder="Th 7c 2d" />
      </div>
      <button
        onClick={run}
        disabled={loading}
        className="mt-4 rounded-lg bg-emerald-600 px-4 py-2 font-medium text-white transition hover:bg-emerald-500 disabled:opacity-50"
      >
        {loading ? "计算中…" : "计算胜率"}
      </button>

      {err && <p className="mt-4 text-sm text-red-400">错误：{err}</p>}

      {result && (
        <div className="mt-6">
          <div className="flex items-end gap-2">
            <span className="text-4xl font-bold text-emerald-400">
              {(result.equity * 100).toFixed(1)}%
            </span>
            <span className="pb-1 text-neutral-400">equity</span>
          </div>
          <div className="mt-3 h-3 w-full overflow-hidden rounded-full bg-neutral-800">
            <div className="flex h-full">
              <div className="bg-emerald-500" style={{ width: `${result.win * 100}%` }} />
              <div className="bg-amber-500" style={{ width: `${result.tie * 100}%` }} />
              <div className="bg-red-600" style={{ width: `${result.lose * 100}%` }} />
            </div>
          </div>
          <div className="mt-2 flex gap-4 text-sm text-neutral-400">
            <span>胜 {(result.win * 100).toFixed(1)}%</span>
            <span>平 {(result.tie * 100).toFixed(1)}%</span>
            <span>负 {(result.lose * 100).toFixed(1)}%</span>
            <span className="ml-auto text-neutral-600">{result.samples} 样本</span>
          </div>
        </div>
      )}
    </section>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="block">
      <span className="text-sm text-neutral-400">{label}</span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="mt-1 w-full rounded-lg border border-neutral-700 bg-neutral-950 px-3 py-2 text-neutral-100 outline-none focus:border-emerald-500"
      />
    </label>
  );
}
