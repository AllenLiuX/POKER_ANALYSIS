"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import ActionBar from "@/components/ActionBar";
import PlayingCard from "@/components/PlayingCard";
import {
  analyzeProblemHands,
  battleAct,
  clearProblemHands,
  loadProblemHands,
  maybeRecordProblemHand,
  newBattle,
  type BattleState,
  type DecisionGrade,
  type ProblemHand,
} from "@/lib/battle";

const POS_TABS = [
  { id: "", label: "随机位置" },
  { id: "BTN", label: "按钮位 (BTN)" },
  { id: "BB", label: "大盲 (BB)" },
];

const GRADE_STYLE: Record<string, { ring: string; badge: string; text: string }> = {
  optimal: { ring: "border-emerald-500/60 bg-emerald-950/40", badge: "bg-emerald-500 text-emerald-950", text: "text-emerald-300" },
  acceptable: { ring: "border-amber-500/60 bg-amber-950/30", badge: "bg-amber-500 text-amber-950", text: "text-amber-300" },
  mistake: { ring: "border-red-500/60 bg-red-950/30", badge: "bg-red-500 text-red-950", text: "text-red-300" },
  ungraded: { ring: "border-neutral-700 bg-neutral-900/40", badge: "bg-neutral-700 text-neutral-200", text: "text-neutral-400" },
};

const GRADE_CN: Record<string, string> = {
  optimal: "最优",
  acceptable: "可接受",
  mistake: "偏离",
  ungraded: "无判分",
};
const ACT_CN: Record<string, string> = { fold: "弃牌", call: "跟注", check: "过牌", bet: "下注", raise: "加注" };

interface Session {
  hands: number;
  netBB: number;
  mistakes: number;
}

export default function BattlePage() {
  const [heroPos, setHeroPos] = useState("");
  const [state, setState] = useState<BattleState | null>(null);
  const [loading, setLoading] = useState(false);
  const [acting, setActing] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [session, setSession] = useState<Session>({ hands: 0, netBB: 0, mistakes: 0 });
  const [problems, setProblems] = useState<ProblemHand[]>([]);
  const [recorded, setRecorded] = useState(false);
  const [analyze, setAnalyze] = useState<{ loading: boolean; text: string | null; error: string | null }>({
    loading: false,
    text: null,
    error: null,
  });

  useEffect(() => {
    setProblems(loadProblemHands());
  }, []);

  const finalize = useCallback((s: BattleState) => {
    if (!s.complete || !s.result) return;
    setSession((prev) => ({
      hands: prev.hands + 1,
      netBB: prev.netBB + s.result!.hero_net,
      mistakes: prev.mistakes + s.result!.review.mistakes,
    }));
    if (s.result.review.is_problem) {
      setProblems(maybeRecordProblemHand(s));
    }
    setRecorded(true);
  }, []);

  const deal = useCallback(async () => {
    setLoading(true);
    setErr(null);
    setRecorded(false);
    try {
      const s = await newBattle({ hero_pos: (heroPos || undefined) as "BTN" | "BB" | undefined });
      setState(s);
      if (s.complete) finalize(s);
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setLoading(false);
    }
  }, [heroPos, finalize]);

  useEffect(() => {
    deal();
  }, [deal]);

  const act = useCallback(
    async (action: string, size?: string) => {
      if (!state || state.complete || acting) return;
      setActing(true);
      setErr(null);
      try {
        const next = await battleAct({
          deal_seed: state.deal_seed,
          hero_pos: state.hero_pos,
          history: state.history,
          action,
          size,
        });
        setState(next);
        if (next.complete && !recorded) finalize(next);
      } catch (e) {
        setErr(String(e instanceof Error ? e.message : e));
      } finally {
        setActing(false);
      }
    },
    [state, acting, recorded, finalize],
  );

  const sizeMap = useMemo(() => {
    if (!state) return undefined;
    const m: Record<string, { id: string; label: string; amount_bb?: number }[]> = {};
    if (state.bet_sizes?.length) m.bet = state.bet_sizes;
    if (state.raise_sizes?.length) m.raise = state.raise_sizes;
    return Object.keys(m).length ? m : undefined;
  }, [state]);

  // 本手最近一次英雄判分（用于即时提示）
  const lastGrade: DecisionGrade | null = useMemo(() => {
    if (!state || !state.grades.length) return null;
    return state.grades[state.grades.length - 1];
  }, [state]);

  const runAnalyze = useCallback(async () => {
    if (analyze.loading || problems.length === 0) return;
    setAnalyze({ loading: true, text: null, error: null });
    try {
      const res = await analyzeProblemHands(problems);
      setAnalyze({ loading: false, text: res.report, error: null });
    } catch (e) {
      setAnalyze({ loading: false, text: null, error: String(e instanceof Error ? e.message : e) });
    }
  }, [analyze.loading, problems]);

  const onClearProblems = useCallback(() => {
    clearProblemHands();
    setProblems([]);
    setAnalyze({ loading: false, text: null, error: null });
  }, []);

  const netColor = session.netBB >= 0 ? "text-emerald-300" : "text-red-300";

  return (
    <main className="mx-auto max-w-2xl px-6 py-10">
      <div className="mb-5 flex items-baseline gap-3">
        <h1 className="text-2xl font-bold">
          模拟对战{" "}
          <span
            title="和 AI 单挑（HU）：翻前用范围表、翻后用启发式引擎驱动对手与判分。用于把训练迁移到真实对局，并沉淀你的问题手供 AI 复盘。"
            className="cursor-help text-sm font-normal text-neutral-500 underline decoration-dotted underline-offset-2"
          >
            HU · 从翻前打起 ⓘ
          </span>
        </h1>
      </div>

      {/* 会话统计 */}
      <div className="mb-5 flex items-center gap-5 rounded-xl border border-neutral-800 bg-neutral-900/60 px-4 py-3 text-sm">
        <Stat label="本次手数" value={String(session.hands)} />
        <Stat label="净收益" value={`${session.netBB >= 0 ? "+" : ""}${session.netBB.toFixed(1)}bb`} valueClass={netColor} />
        <Stat label="大问题手" value={String(problems.length)} sub={`偏离 ${session.mistakes}`} />
        <Link href="#problems" className="ml-auto rounded-lg bg-neutral-800 px-3 py-1.5 text-xs text-neutral-400 hover:bg-neutral-700">
          问题手 & 复盘 ↓
        </Link>
      </div>

      {/* 位置选择 */}
      <div className="mb-5 rounded-xl border border-neutral-800 bg-neutral-900/40 px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="mr-1 text-[10px] uppercase tracking-wider text-neutral-500">我的位置</span>
          {POS_TABS.map((t) => (
            <button
              key={t.id || "any"}
              onClick={() => setHeroPos(t.id)}
              className={`rounded-lg px-3 py-1.5 text-sm font-medium transition ${
                t.id === heroPos ? "bg-neutral-100 text-neutral-900" : "bg-neutral-800 text-neutral-300 hover:bg-neutral-700"
              }`}
            >
              {t.label}
            </button>
          ))}
          <span className="ml-auto text-[11px] text-neutral-600">下一手生效</span>
        </div>
      </div>

      {err && <p className="mb-4 rounded-lg bg-red-950/50 px-4 py-2 text-sm text-red-300">错误：{err}</p>}

      {state && (
        <div className="rounded-2xl border border-neutral-800 bg-gradient-to-b from-emerald-950/30 to-neutral-950 p-5">
          {/* 对手 */}
          <SeatRow
            label={`对手 · ${state.villain_pos}`}
            stack={state.villain_stack_bb}
            lastAction={state.villain_last && state.villain_last.street === state.street ? state.villain_last.label : null}
          >
            {state.result ? (
              state.result.villain.map((c) => <PlayingCard key={c} card={c} size="md" />)
            ) : (
              <>
                <CardBack />
                <CardBack />
              </>
            )}
            {state.result && (
              <span className="ml-2 text-xs text-neutral-400">{state.result.villain_class}</span>
            )}
          </SeatRow>

          {/* 底池 & 公共牌 */}
          <div className="my-4 flex flex-col items-center gap-2">
            <div className="text-[11px] uppercase tracking-widest text-neutral-500">
              {streetCn(state.street)} · 底池 <span className="font-semibold text-neutral-200">{state.pot_bb}bb</span>
              {state.to_call_bb > 0 && !state.complete && (
                <>
                  {" · 待跟 "}
                  <span className="font-semibold text-red-300">{state.to_call_bb}bb</span>
                </>
              )}
            </div>
            <div className="flex min-h-[3.5rem] items-center justify-center gap-2">
              {state.board.length ? (
                state.board.map((c) => <PlayingCard key={c} card={c} size="lg" />)
              ) : (
                <span className="text-sm text-neutral-600">翻牌前</span>
              )}
            </div>
          </div>

          {/* 英雄 */}
          <SeatRow label={`你 · ${state.hero_pos}`} stack={state.hero_stack_bb} hero>
            {state.hero.map((c) => <PlayingCard key={c} card={c} size="md" />)}
            <span className="ml-2 text-xs text-neutral-400">{state.hero_class}</span>
          </SeatRow>

          <p className="mt-4 text-center text-sm text-neutral-400">{state.message}</p>

          {/* 即时判分提示（进行中） */}
          {!state.complete && lastGrade && lastGrade.grade !== "optimal" && lastGrade.grade !== "ungraded" && (
            <GradeHint grade={lastGrade} />
          )}

          {/* 动作区 */}
          <div className="mt-5">
            {!state.complete && state.to_act === "hero" ? (
              <ActionBar
                actions={state.available_actions}
                labels={state.action_labels}
                sizes={sizeMap}
                disabled={acting || loading}
                onAct={act}
              />
            ) : state.complete && state.result ? (
              <ResultPanel state={state} onNext={deal} />
            ) : (
              <p className="text-center text-sm text-neutral-500">对手行动中…</p>
            )}
          </div>
        </div>
      )}

      {loading && !state && <p className="text-center text-neutral-500">发牌中…</p>}

      {/* 问题手 & AI 复盘 */}
      <ProblemsPanel
        problems={problems}
        analyze={analyze}
        onAnalyze={runAnalyze}
        onClear={onClearProblems}
      />
    </main>
  );
}

function streetCn(s: string): string {
  return { preflop: "翻前", flop: "翻牌", turn: "转牌", river: "河牌" }[s] ?? s;
}

function Stat({ label, value, sub, valueClass }: { label: string; value: string; sub?: string; valueClass?: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-neutral-500">{label}</div>
      <div className={`text-lg font-bold ${valueClass ?? "text-neutral-100"}`}>
        {value}
        {sub && <span className="ml-1 text-xs font-normal text-neutral-500">{sub}</span>}
      </div>
    </div>
  );
}

function SeatRow({
  label,
  stack,
  lastAction,
  hero,
  children,
}: {
  label: string;
  stack: number;
  lastAction?: string | null;
  hero?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={`flex items-center gap-3 ${hero ? "justify-center" : "justify-center"}`}>
      <div className="flex min-w-[120px] flex-col items-end text-right">
        <span className="text-xs font-medium text-neutral-300">{label}</span>
        <span className="text-[11px] text-neutral-500">{stack}bb</span>
        {lastAction && (
          <span className="mt-0.5 rounded bg-red-900/50 px-1.5 py-0.5 text-[10px] text-red-300">{lastAction}</span>
        )}
      </div>
      <div className="flex items-center gap-2">{children}</div>
      <div className="min-w-[120px]" />
    </div>
  );
}

function CardBack() {
  return (
    <div className="inline-flex h-14 w-10 items-center justify-center rounded-md border border-sky-800 bg-gradient-to-br from-sky-900 to-indigo-950 text-lg text-sky-500 shadow-md">
      ♠
    </div>
  );
}

function GradeHint({ grade }: { grade: DecisionGrade }) {
  const style = GRADE_STYLE[grade.grade] ?? GRADE_STYLE.mistake;
  const reason = grade.reasons?.[0];
  return (
    <div className={`mt-4 rounded-xl border px-4 py-2.5 text-sm ${style.ring}`}>
      <span className={`mr-2 rounded-full px-2 py-0.5 text-xs font-bold ${style.badge}`}>{GRADE_CN[grade.grade]}</span>
      你选<span className="font-medium">{ACT_CN[grade.action] ?? grade.action}</span>
      {grade.optimal_action && grade.optimal_action !== grade.action && (
        <>
          ，建议<span className="font-medium text-emerald-300">{ACT_CN[grade.optimal_action] ?? grade.optimal_action}</span>
        </>
      )}
      {reason && <span className="ml-1 text-neutral-400">— {reason}</span>}
    </div>
  );
}

function ResultPanel({ state, onNext }: { state: BattleState; onNext: () => void }) {
  const r = state.result!;
  const win = r.winner === "hero";
  const split = r.winner === "split";
  const color = split ? "text-neutral-200" : win ? "text-emerald-300" : "text-red-300";
  const graded = state.grades.filter((g) => g.grade === "mistake" || g.grade === "acceptable");
  return (
    <div className="rounded-2xl border border-neutral-800 bg-neutral-950/60 p-4">
      <div className="flex items-center justify-between">
        <span className={`text-lg font-bold ${color}`}>
          {split ? "平分底池" : win ? "你赢了" : "你输了"} {r.hero_net >= 0 ? "+" : ""}
          {r.hero_net.toFixed(1)}bb
        </span>
        {r.review.is_problem && (
          <span
            className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
              r.review.is_big ? "bg-red-900/70 text-red-200" : "bg-amber-900/60 text-amber-200"
            }`}
          >
            {r.review.is_big ? "已记为大问题手" : "已记为问题手"}
          </span>
        )}
      </div>

      {graded.length > 0 && (
        <div className="mt-3 space-y-1.5">
          {graded.map((g, i) => (
            <div key={i} className="flex flex-wrap items-center gap-x-2 text-xs">
              <span className={`rounded px-1.5 py-0.5 font-semibold ${GRADE_STYLE[g.grade]?.badge ?? ""}`}>
                {GRADE_CN[g.grade]}
              </span>
              <span className="text-neutral-400">{g.spot_label}</span>
              <span className="text-neutral-300">{g.hand_class ?? g.made_label}</span>
              <span className="text-neutral-500">
                选{ACT_CN[g.action] ?? g.action}
                {g.optimal_action && g.optimal_action !== g.action ? ` · 应${ACT_CN[g.optimal_action] ?? g.optimal_action}` : ""}
              </span>
            </div>
          ))}
        </div>
      )}

      <button
        onClick={onNext}
        className="mt-4 w-full rounded-xl bg-emerald-600 py-3 font-semibold text-white transition hover:bg-emerald-500"
      >
        下一手 →
      </button>
    </div>
  );
}

function ProblemsPanel({
  problems,
  analyze,
  onAnalyze,
  onClear,
}: {
  problems: ProblemHand[];
  analyze: { loading: boolean; text: string | null; error: string | null };
  onAnalyze: () => void;
  onClear: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <section id="problems" className="mt-8 rounded-2xl border border-neutral-800 bg-neutral-900/40 p-5">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-bold">
          我的问题手 <span className="text-sm font-normal text-neutral-500">({problems.length})</span>
        </h2>
        {problems.length > 0 && (
          <button onClick={onClear} className="text-xs text-neutral-500 hover:text-red-300">
            清空
          </button>
        )}
      </div>

      <p className="mt-1 text-sm text-neutral-500">
        对战中被标记为「有问题」的手会自动沉淀在这里（本地保存）。攒够几手后用 AI 一次性复盘，找出系统性漏洞。
      </p>

      <div className="mt-4 flex gap-3">
        <button
          onClick={onAnalyze}
          disabled={analyze.loading || problems.length === 0}
          className="flex-1 rounded-xl border border-violet-700/60 bg-violet-950/30 py-2.5 text-sm font-medium text-violet-200 transition hover:bg-violet-900/40 disabled:opacity-40"
        >
          {analyze.loading ? "AI 复盘中…" : `🧠 AI 分析这 ${problems.length} 手问题牌`}
        </button>
        {problems.length > 0 && (
          <button
            onClick={() => setOpen((o) => !o)}
            className="rounded-xl bg-neutral-800 px-4 py-2.5 text-sm text-neutral-300 hover:bg-neutral-700"
          >
            {open ? "收起明细" : "查看明细"}
          </button>
        )}
      </div>

      {analyze.error && <p className="mt-3 text-xs text-red-400">复盘不可用：{analyze.error}</p>}
      {analyze.text && (
        <div className="mt-4 rounded-xl bg-neutral-950/70 p-4">
          <div className="mb-2 text-xs font-medium text-violet-300">AI 复盘报告</div>
          <p className="whitespace-pre-line text-sm leading-relaxed text-neutral-200">{analyze.text}</p>
        </div>
      )}

      {open && problems.length > 0 && (
        <ul className="mt-4 space-y-2">
          {[...problems].reverse().map((h) => (
            <li key={h.ts} className="rounded-xl border border-neutral-800 bg-neutral-950/50 p-3 text-xs">
              <div className="flex items-center justify-between">
                <span className="text-neutral-300">
                  {h.hero_pos} {h.hero_glyphs.join(" ")}
                  {h.board_glyphs.length ? <span className="text-neutral-500"> · {h.board_glyphs.join(" ")}</span> : null}
                </span>
                <span className={h.hero_net >= 0 ? "text-emerald-300" : "text-red-300"}>
                  {h.hero_net >= 0 ? "+" : ""}
                  {h.hero_net.toFixed(1)}bb
                  {h.is_big && <span className="ml-1 rounded bg-red-900/60 px-1 py-0.5 text-[10px] text-red-200">大</span>}
                </span>
              </div>
              <div className="mt-1 space-y-0.5 text-neutral-500">
                {h.decisions
                  .filter((d) => d.grade === "mistake")
                  .map((d, i) => (
                    <div key={i}>
                      {d.spot_label}·{d.hand_class ?? d.made_label}：选{ACT_CN[d.action] ?? d.action}
                      {d.optimal_action ? ` · 应${ACT_CN[d.optimal_action] ?? d.optimal_action}` : ""}
                    </div>
                  ))}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
