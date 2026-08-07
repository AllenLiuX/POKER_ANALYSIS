"use client";

import { useEffect, useState } from "react";
import {
  API_BASE,
  getHealth,
  postEquity,
  type EquityResponse,
  type HealthResponse,
} from "@/lib/api";

const MODULES = [
  { title: "翻前训练器", desc: "GTO 范围即时反馈", phase: "Phase 2", ready: false },
  { title: "翻后训练器", desc: "启发式 → 预计算真 GTO", phase: "Phase 5", ready: false },
  { title: "范围表", desc: "13×13 交互网格", phase: "Phase 3", ready: false },
  { title: "胜率 / 赔率工具", desc: "Monte Carlo equity", phase: "Phase 0", ready: true },
  { title: "剥削训练", desc: "对手画像 + node-lock", phase: "Phase 9", ready: false },
  { title: "截图导入", desc: "WePoker 牌谱 → 逐人剥削", phase: "Phase 6", ready: false },
];

export default function Home() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthErr, setHealthErr] = useState<string | null>(null);

  useEffect(() => {
    getHealth().then(setHealth).catch((e) => setHealthErr(String(e)));
  }, []);

  return (
    <main className="mx-auto max-w-5xl px-6 py-12">
      <header className="mb-10">
        <div className="flex items-center gap-3">
          <span className="text-3xl">♠️</span>
          <h1 className="text-3xl font-bold tracking-tight">
            Poker Analysis
          </h1>
          <HealthBadge health={health} error={healthErr} />
        </div>
        <p className="mt-2 text-neutral-400">
          面向进阶牌手的 GTO 决策训练与剥削分析平台 · 后端 <code className="text-neutral-500">{API_BASE}</code>
        </p>
      </header>

      <section className="mb-12 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {MODULES.map((m) => (
          <div
            key={m.title}
            className="rounded-xl border border-neutral-800 bg-neutral-900/60 p-4"
          >
            <div className="flex items-center justify-between">
              <h3 className="font-semibold">{m.title}</h3>
              <span
                className={`rounded-full px-2 py-0.5 text-xs ${
                  m.ready
                    ? "bg-emerald-900/60 text-emerald-300"
                    : "bg-neutral-800 text-neutral-400"
                }`}
              >
                {m.ready ? "可用" : m.phase}
              </span>
            </div>
            <p className="mt-1 text-sm text-neutral-400">{m.desc}</p>
          </div>
        ))}
      </section>

      <EquityDemo />
    </main>
  );
}

function HealthBadge({
  health,
  error,
}: {
  health: HealthResponse | null;
  error: string | null;
}) {
  if (error) {
    return (
      <span className="rounded-full bg-red-900/60 px-3 py-1 text-xs text-red-300">
        后端未连接
      </span>
    );
  }
  if (!health) {
    return (
      <span className="rounded-full bg-neutral-800 px-3 py-1 text-xs text-neutral-400">
        连接中…
      </span>
    );
  }
  return (
    <span className="rounded-full bg-emerald-900/60 px-3 py-1 text-xs text-emerald-300">
      后端 v{health.version} · 在线
    </span>
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
    <section className="rounded-2xl border border-neutral-800 bg-neutral-900/60 p-6">
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
