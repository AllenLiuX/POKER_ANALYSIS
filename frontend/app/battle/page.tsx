"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Sparkles } from "lucide-react";
import ActionBar from "@/components/ActionBar";
import PlayingCard from "@/components/PlayingCard";
import PokerTable, { type TableView } from "@/components/PokerTable";
import Markdown from "@/components/Markdown";
import {
  streamAnalyzeProblemHands,
  streamExplainHand,
  actionLineFromHistory,
  battleAct,
  clearHands,
  handActionLine,
  loadBattleMatchups,
  loadHands,
  mergeHands,
  recordHand,
  newBattle,
  type BattleMatchup,
  type BattleState,
  type DecisionGrade,
  type RecordedHand,
} from "@/lib/battle";
import {
  clearCloudHands,
  fetchCloudHands,
  pushHand,
  syncLocalHandsToCloud,
} from "@/lib/cloud";

const SIDE_TABS = [
  { id: "", label: "随机" },
  { id: "opener", label: "我开池" },
  { id: "defender", label: "我防守" },
];

type PauseMode = "off" | "mistake" | "action";
const PAUSE_TABS: { id: PauseMode; label: string; hint: string }[] = [
  { id: "off", label: "关闭", hint: "连续打，不暂停" },
  { id: "mistake", label: "偏离时", hint: "只在非最优决策后暂停讲解" },
  { id: "action", label: "每一步", hint: "每次行动后都暂停讲解" },
];
const PAUSE_KEY = "poker_battle_pause_v1";

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

/** 英雄本手是否有过任何行动（用于跳过对手翻前直接弃牌、英雄零决策的 walk）。 */
function heroActed(s: BattleState): boolean {
  return (s.grades?.length ?? 0) > 0 || (s.history ?? []).some((e) => e.actor === "hero");
}

interface Session {
  hands: number;
  netBB: number;
  mistakes: number;
  // 招法级统计（仅计入有范围/引擎判分的决策）
  moves: number;
  optimal: number;
  acceptable: number;
  badMoves: number;
}

export default function BattlePage() {
  const [matchupSel, setMatchupSel] = useState("");
  const [sideSel, setSideSel] = useState("");
  const [matchups, setMatchups] = useState<BattleMatchup[]>([]);
  const [state, setState] = useState<BattleState | null>(null);
  const [loading, setLoading] = useState(false);
  const [acting, setActing] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [session, setSession] = useState<Session>({
    hands: 0,
    netBB: 0,
    mistakes: 0,
    moves: 0,
    optimal: 0,
    acceptable: 0,
    badMoves: 0,
  });
  const [hands, setHands] = useState<RecordedHand[]>([]);
  const [recorded, setRecorded] = useState(false);
  const [pauseMode, setPauseMode] = useState<PauseMode>("mistake");
  // 暂停讲解：非 null 时挡住动作区，展示该决策的快速讲解，点「继续」放行
  const [feedback, setFeedback] = useState<DecisionGrade | null>(null);

  useEffect(() => {
    try {
      const v = localStorage.getItem(PAUSE_KEY);
      if (v === "off" || v === "mistake" || v === "action") setPauseMode(v);
    } catch {
      /* ignore */
    }
  }, []);

  const changePauseMode = useCallback((m: PauseMode) => {
    setPauseMode(m);
    try {
      localStorage.setItem(PAUSE_KEY, m);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    loadBattleMatchups()
      .then(setMatchups)
      .catch(() => {});
  }, []);

  const resolveHeroPos = useCallback((): string | undefined => {
    if (!matchupSel) return undefined; // 随机对位 → 后端随机对位与位置
    const m = matchups.find((x) => x.matchup === matchupSel);
    if (!m) return undefined;
    if (sideSel === "opener") return m.opener;
    if (sideSel === "defender") return m.defender;
    return undefined; // 指定对位、随机一侧
  }, [matchupSel, sideSel, matchups]);

  useEffect(() => {
    setHands(loadHands());
    // 云端合并：补传本地记录 + 拉回云端记录（未登录/未启用则静默 no-op）
    (async () => {
      try {
        await syncLocalHandsToCloud(loadHands());
        const cloud = await fetchCloudHands();
        if (cloud.length) setHands(mergeHands(cloud));
      } catch {
        /* ignore */
      }
    })();
  }, []);

  const problems = useMemo(() => hands.filter((h) => h.is_problem), [hands]);

  const finalize = useCallback((s: BattleState) => {
    if (!s.complete || !s.result) return;
    const graded = s.grades.filter((g) => g.grade !== "ungraded");
    const opt = graded.filter((g) => g.grade === "optimal").length;
    const acc = graded.filter((g) => g.grade === "acceptable").length;
    const bad = graded.filter((g) => g.grade === "mistake").length;
    setSession((prev) => ({
      hands: prev.hands + 1,
      netBB: prev.netBB + s.result!.hero_net,
      mistakes: prev.mistakes + s.result!.review.mistakes,
      moves: prev.moves + graded.length,
      optimal: prev.optimal + opt,
      acceptable: prev.acceptable + acc,
      badMoves: prev.badMoves + bad,
    }));
    const list = recordHand(s);
    setHands(list);
    // 默认上传到云端（未登录/未启用则静默跳过）
    const newest = list[list.length - 1];
    if (newest) pushHand(newest).catch(() => {});
    setRecorded(true);
  }, []);

  const deal = useCallback(async () => {
    setLoading(true);
    setErr(null);
    setRecorded(false);
    setFeedback(null);
    try {
      let s = await newBattle({ matchup: matchupSel || undefined, hero_pos: resolveHeroPos() });
      // 跳过英雄「完全不用行动」的手（对手翻前直接弃牌的 walk）：无决策不值得训练，自动重发。
      let guard = 0;
      while (s.complete && !heroActed(s) && guard < 25) {
        s = await newBattle({ matchup: matchupSel || undefined, hero_pos: resolveHeroPos() });
        guard++;
      }
      setState(s);
      if (s.complete && heroActed(s)) finalize(s);
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setLoading(false);
    }
  }, [matchupSel, resolveHeroPos, finalize]);

  useEffect(() => {
    deal();
  }, [deal]);

  const act = useCallback(
    async (action: string, size?: string) => {
      if (!state || state.complete || acting || feedback) return;
      setActing(true);
      setErr(null);
      const gradesBefore = state.grades.length;
      try {
        const next = await battleAct({
          deal_seed: state.deal_seed,
          matchup: state.matchup,
          hero_pos: state.hero_pos,
          history: state.history,
          action,
          size,
        });
        setState(next);
        if (next.complete && !recorded) finalize(next);
        // 本次英雄决策对应的判分（act 后新增的第一条 hero grade）
        const g = next.grades[gradesBefore] ?? next.grades[next.grades.length - 1];
        if (g && g.grade !== "ungraded") {
          const pause =
            pauseMode === "action" || (pauseMode === "mistake" && g.grade !== "optimal");
          if (pause) setFeedback(g);
        }
      } catch (e) {
        setErr(String(e instanceof Error ? e.message : e));
      } finally {
        setActing(false);
      }
    },
    [state, acting, recorded, feedback, pauseMode, finalize],
  );

  const resume = useCallback(() => setFeedback(null), []);

  useEffect(() => {
    if (!feedback) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        setFeedback(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [feedback]);

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

  const onClearHands = useCallback(() => {
    clearHands();
    setHands([]);
    clearCloudHands().catch(() => {});
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
      <div className="mb-5 flex flex-wrap items-center gap-x-5 gap-y-2 rounded-xl border border-neutral-800 bg-neutral-900/60 px-4 py-3 text-sm">
        <Stat label="本次手数" value={String(session.hands)} />
        <Stat label="净收益" value={`${session.netBB >= 0 ? "+" : ""}${session.netBB.toFixed(1)}bb`} valueClass={netColor} />
        <Stat
          label="决策命中率"
          value={session.moves ? `${Math.round((session.optimal / session.moves) * 100)}%` : "—"}
          sub={`${session.optimal}/${session.moves}`}
        />
        <div>
          <div className="text-[10px] uppercase tracking-wider text-neutral-500">招法</div>
          <div className="flex items-center gap-2 text-lg font-bold">
            <span className="text-emerald-300">{session.optimal}</span>
            <span className="text-sm text-neutral-600">/</span>
            <span className="text-amber-300">{session.acceptable}</span>
            <span className="text-sm text-neutral-600">/</span>
            <span className="text-red-300">{session.badMoves}</span>
          </div>
          <div className="text-[10px] text-neutral-600">最优 / 可接受 / 偏离</div>
        </div>
        <Stat label="问题手" value={String(problems.length)} />
        <Link href="#hands" className="ml-auto rounded-lg bg-neutral-800 px-3 py-1.5 text-xs text-neutral-400 hover:bg-neutral-700">
          对局记录 & 复盘 ↓
        </Link>
      </div>

      {/* 对位 & 位置选择 */}
      <div className="mb-3 rounded-xl border border-neutral-800 bg-neutral-900/40 px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="mr-1 text-[10px] uppercase tracking-wider text-neutral-500">对位</span>
          <select
            value={matchupSel}
            onChange={(e) => setMatchupSel(e.target.value)}
            className="rounded-lg border border-neutral-700 bg-neutral-800 px-2 py-1.5 text-sm text-neutral-200 focus:border-neutral-500 focus:outline-none"
          >
            <option value="">随机对位</option>
            {matchups.map((m) => (
              <option key={m.matchup} value={m.matchup}>
                {m.label}
              </option>
            ))}
          </select>
          {matchupSel && (
            <div className="flex items-center gap-1.5">
              {SIDE_TABS.map((t) => (
                <button
                  key={t.id || "any"}
                  onClick={() => setSideSel(t.id)}
                  className={`rounded-lg px-2.5 py-1.5 text-xs font-medium transition ${
                    t.id === sideSel ? "bg-neutral-100 text-neutral-900" : "bg-neutral-800 text-neutral-300 hover:bg-neutral-700"
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>
          )}
          <span className="ml-auto text-[11px] text-neutral-600">下一手生效</span>
        </div>
      </div>

      {/* 讲解暂停控制（类 GTO Wizard：pause after） */}
      <div className="mb-5 rounded-xl border border-neutral-800 bg-neutral-900/40 px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="mr-1 text-[10px] uppercase tracking-wider text-neutral-500">讲解暂停</span>
          {PAUSE_TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => changePauseMode(t.id)}
              title={t.hint}
              className={`rounded-lg px-3 py-1.5 text-sm font-medium transition ${
                t.id === pauseMode ? "bg-neutral-100 text-neutral-900" : "bg-neutral-800 text-neutral-300 hover:bg-neutral-700"
              }`}
            >
              {t.label}
            </button>
          ))}
          <span className="ml-auto text-[11px] text-neutral-600">
            {PAUSE_TABS.find((t) => t.id === pauseMode)?.hint}
          </span>
        </div>
      </div>

      {err && <p className="mb-4 rounded-lg bg-red-950/50 px-4 py-2 text-sm text-red-300">错误：{err}</p>}

      {state && (
        <>
          <ActionLineBar state={state} />
          <PokerTable view={battleTableView(state)} />

          {/* 你的手牌 */}
          <div className="mt-4 flex flex-col items-center gap-3">
            <div className="flex items-center gap-3">
              {state.hero.map((c) => (
                <PlayingCard key={c} card={c} size="lg" />
              ))}
              <div className="text-left">
                <div className="text-2xl font-bold text-neutral-100">{state.hero_class}</div>
                <div className="text-xs text-neutral-500">
                  你在 {state.hero_pos} · {state.matchup_label} · 有效筹码 {state.hero_stack_bb}bb
                </div>
              </div>
            </div>
            <p className="text-center text-sm text-neutral-400">{state.message}</p>
          </div>

          {/* 即时判分提示（仅「关闭」暂停时，作为轻量提示） */}
          {pauseMode === "off" &&
            !feedback &&
            !state.complete &&
            lastGrade &&
            lastGrade.grade !== "optimal" &&
            lastGrade.grade !== "ungraded" && <GradeHint grade={lastGrade} />}

          {/* 动作区 */}
          <div className="mt-5">
            {feedback ? (
              <QuickCoach grade={feedback} onContinue={resume} complete={state.complete} />
            ) : !state.complete && state.to_act === "hero" ? (
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
        </>
      )}

      {loading && !state && <p className="text-center text-neutral-500">发牌中…</p>}

      {/* 对局记录 & AI 复盘 */}
      <HandsPanel hands={hands} problems={problems} onClear={onClearHands} />
    </main>
  );
}

function streetCn(s: string): string {
  return { preflop: "翻前", flop: "翻牌", turn: "转牌", river: "河牌" }[s] ?? s;
}

function battleTableView(state: BattleState): TableView {
  const villainActed =
    state.villain_last && state.villain_last.street === state.street
      ? state.villain_last.label
      : undefined;
  return {
    seats: [
      {
        position: state.villain_pos,
        status: state.result ? "villain" : state.to_call_bb > 0 ? "raiser" : "waiting",
        slot: "UTG",
        isButton: state.villain_pos === "BTN",
        cards: state.result ? state.result.villain : undefined,
        hideCards: !state.result,
        stackBB: state.villain_stack_bb,
        label: state.result ? state.result.villain_class : villainActed,
      },
      {
        position: state.hero_pos,
        status: "hero",
        slot: "BTN",
        isButton: state.hero_pos === "BTN",
        cards: state.hero,
        stackBB: state.hero_stack_bb,
        label: "YOU",
      },
    ],
    board: state.board,
    potBB: state.pot_bb,
    toCallBB: !state.complete ? state.to_call_bb : undefined,
    streetLabel: streetCn(state.street),
  };
}

function ActionLineBar({ state }: { state: BattleState }) {
  const lines = actionLineFromHistory(state.history);
  if (!lines.length) return null;
  return (
    <div className="mb-3 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-xl border border-neutral-800 bg-neutral-900/40 px-3 py-2 text-[11px]">
      {lines.map((ln, i) => (
        <span key={ln.street} className="flex items-center gap-2">
          {i > 0 && <span className="text-neutral-700">›</span>}
          <span className="rounded bg-neutral-800 px-1.5 py-0.5 font-medium text-neutral-300">{ln.street}</span>
          <span className="text-neutral-400">{ln.text}</span>
        </span>
      ))}
    </div>
  );
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

const FREQ_ORDER = ["raise", "call", "check", "bet", "fold"];
const FREQ_COLOR: Record<string, string> = {
  raise: "bg-orange-500",
  bet: "bg-orange-500",
  call: "bg-emerald-500",
  check: "bg-sky-500",
  fold: "bg-neutral-500",
};

function pct(x: number): string {
  return `${Math.round(x * 100)}%`;
}

// 翻前：GTO 范围表频率条（权威）；翻后：胜率 vs 需要胜率（启发式）
function QuickCoach({
  grade,
  onContinue,
  complete,
}: {
  grade: DecisionGrade;
  onContinue: () => void;
  complete?: boolean;
}) {
  const style = GRADE_STYLE[grade.grade] ?? GRADE_STYLE.mistake;
  const isPreflop = grade.street === "preflop";
  const freqs = grade.frequencies;
  const rec = grade.recommendation;
  const chosen = grade.action;
  const optimal = grade.optimal_action;

  const freqRows = freqs
    ? FREQ_ORDER.filter((k) => k in freqs).map((k) => ({ action: k, freq: freqs[k] ?? 0 }))
    : [];

  const eq = grade.equity ?? null;
  const required = rec?.required_equity ?? null;

  return (
    <div className={`rounded-2xl border p-4 ${style.ring}`}>
      {/* 头部：评级 + 场景 + 牌 */}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className={`rounded-full px-2.5 py-0.5 text-xs font-bold ${style.badge}`}>{GRADE_CN[grade.grade]}</span>
        <span className="text-sm text-neutral-400">{grade.spot_label}</span>
        <span className="text-sm font-semibold text-neutral-100">{grade.hand_class ?? grade.made_label}</span>
        {grade.draw_label && <span className="text-xs text-sky-300">{grade.draw_label}</span>}
        {grade.made_label && grade.hand_class && (
          <span className="text-xs text-neutral-500">· {grade.made_label}</span>
        )}
      </div>

      {/* 你选 vs 建议 */}
      <div className="mt-2 text-sm">
        你选 <span className="font-semibold">{ACT_CN[chosen] ?? chosen}</span>
        {optimal && optimal !== chosen && (
          <>
            {" "}· 建议 <span className="font-semibold text-emerald-300">{ACT_CN[optimal] ?? optimal}</span>
          </>
        )}
        {rec?.accept && rec.accept.length > 1 && (
          <span className="ml-1 text-xs text-neutral-500">
            （可接受：{rec.accept.map((a) => ACT_CN[a] ?? a).join(" / ")}）
          </span>
        )}
      </div>

      {/* 翻前：范围表频率 */}
      {isPreflop && freqRows.length > 0 && (
        <div className="mt-3 space-y-1.5">
          <div className="text-[11px] uppercase tracking-wider text-neutral-500">GTO 范围频率</div>
          {freqRows.map((r) => (
            <div key={r.action} className="flex items-center gap-2">
              <span className={`w-10 shrink-0 text-xs ${r.action === chosen ? "font-semibold text-neutral-100" : "text-neutral-400"}`}>
                {ACT_CN[r.action] ?? r.action}
              </span>
              <div className="relative h-3 flex-1 overflow-hidden rounded-full bg-neutral-800">
                <div
                  className={`h-full rounded-full ${FREQ_COLOR[r.action] ?? "bg-neutral-500"} ${r.action === chosen ? "" : "opacity-60"}`}
                  style={{ width: pct(r.freq) }}
                />
              </div>
              <span className={`w-10 shrink-0 text-right text-xs ${r.action === chosen ? "text-neutral-100" : "text-neutral-500"}`}>
                {pct(r.freq)}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* 翻后：胜率 vs 需要胜率 */}
      {!isPreflop && eq != null && (
        <div className="mt-3">
          <div className="mb-1 flex items-center justify-between text-[11px] uppercase tracking-wider text-neutral-500">
            <span>胜率 vs 对手范围</span>
            <span className="text-neutral-300">{pct(eq)}</span>
          </div>
          <div className="relative h-3 overflow-hidden rounded-full bg-neutral-800">
            <div className="h-full rounded-full bg-emerald-500" style={{ width: pct(Math.min(1, eq)) }} />
            {required != null && (
              <div
                className="absolute top-0 h-full w-0.5 bg-amber-300"
                style={{ left: pct(Math.min(1, required)) }}
                title={`需要胜率 ${pct(required)}`}
              />
            )}
          </div>
          {required != null && (
            <div className="mt-1 text-[11px] text-neutral-500">
              底池赔率需要 <span className="text-amber-300">{pct(required)}</span>
              {rec?.mdf != null && <> · MDF {pct(rec.mdf)}</>}
            </div>
          )}
          {(rec?.range_label || rec?.nut_label) && (
            <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-neutral-400">
              {rec?.range_label && <span className="rounded bg-neutral-800 px-1.5 py-0.5">{rec.range_label}</span>}
              {rec?.nut_label && <span className="rounded bg-neutral-800 px-1.5 py-0.5">{rec.nut_label}</span>}
            </div>
          )}
        </div>
      )}

      {/* 依据（为什么） */}
      {grade.reasons && grade.reasons.length > 0 && (
        <ul className="mt-3 space-y-1 text-xs text-neutral-400">
          {grade.reasons.slice(0, 3).map((r, i) => (
            <li key={i} className="flex gap-1.5">
              <span className="text-neutral-600">·</span>
              <span>{r}</span>
            </li>
          ))}
        </ul>
      )}

      <button
        onClick={onContinue}
        className="mt-4 w-full rounded-xl bg-emerald-600 py-3 font-semibold text-white transition hover:bg-emerald-500"
      >
        {complete ? "查看结果 →" : "继续 →"} <span className="text-xs font-normal opacity-70">(Enter)</span>
      </button>
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

interface AnalyzeState {
  loading: boolean;
  text: string | null;
  error: string | null;
}
const EMPTY_ANALYZE: AnalyzeState = { loading: false, text: null, error: null };

function HandsPanel({
  hands,
  problems,
  onClear,
}: {
  hands: RecordedHand[];
  problems: RecordedHand[];
  onClear: () => void;
}) {
  const [onlyProblems, setOnlyProblems] = useState(false);
  const [bulk, setBulk] = useState<AnalyzeState>(EMPTY_ANALYZE);
  // 单手复盘：一次只展开一手
  const [openTs, setOpenTs] = useState<number | null>(null);
  const [explain, setExplain] = useState<AnalyzeState>(EMPTY_ANALYZE);

  const list = useMemo(
    () => [...(onlyProblems ? problems : hands)].reverse(),
    [onlyProblems, problems, hands],
  );

  const runBulk = useCallback(async () => {
    if (bulk.loading || problems.length === 0) return;
    setBulk({ loading: true, text: null, error: null });
    try {
      const full = await streamAnalyzeProblemHands(problems, (partial) =>
        setBulk({ loading: true, text: partial, error: null }),
      );
      setBulk({ loading: false, text: full, error: null });
    } catch (e) {
      setBulk({ loading: false, text: null, error: String(e instanceof Error ? e.message : e) });
    }
  }, [bulk.loading, problems]);

  const askHand = useCallback(
    async (hand: RecordedHand) => {
      if (openTs === hand.ts) {
        setOpenTs(null);
        return;
      }
      setOpenTs(hand.ts);
      setExplain({ loading: true, text: null, error: null });
      try {
        const full = await streamExplainHand(hand, (partial) =>
          setExplain({ loading: true, text: partial, error: null }),
        );
        setExplain({ loading: false, text: full, error: null });
      } catch (e) {
        setExplain({ loading: false, text: null, error: String(e instanceof Error ? e.message : e) });
      }
    },
    [openTs],
  );

  return (
    <section id="hands" className="mt-8 rounded-2xl border border-neutral-800 bg-neutral-900/40 p-5">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-bold">
          我的对局记录 <span className="text-sm font-normal text-neutral-500">({hands.length})</span>
        </h2>
        {hands.length > 0 && (
          <button onClick={onClear} className="text-xs text-neutral-500 hover:text-red-300">
            清空
          </button>
        )}
      </div>

      <p className="mt-1 text-sm text-neutral-500">
        每一手都会自动存下来（本地保存）。任意一手都能单独问 AI「这手打得对不对、为什么」；标为问题的手还能一次性批量复盘找系统漏洞。
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button
          onClick={runBulk}
          disabled={bulk.loading || problems.length === 0}
          className="flex flex-1 items-center justify-center gap-1.5 rounded-xl border border-violet-700/60 bg-violet-950/30 py-2.5 text-sm font-medium text-violet-200 transition hover:bg-violet-900/40 disabled:opacity-40"
        >
          {bulk.loading ? (
            "AI 复盘中…"
          ) : (
            <>
              <Sparkles className="size-4" />
              批量复盘 {problems.length} 手问题牌
            </>
          )}
        </button>
        <button
          onClick={() => setOnlyProblems((v) => !v)}
          className={`rounded-xl px-4 py-2.5 text-sm transition ${
            onlyProblems ? "bg-amber-600 text-white" : "bg-neutral-800 text-neutral-300 hover:bg-neutral-700"
          }`}
        >
          {onlyProblems ? "只看问题手：开" : "只看问题手"}
        </button>
      </div>

      {bulk.error && <p className="mt-3 text-xs text-red-400">复盘不可用：{bulk.error}</p>}
      {bulk.text && (
        <div className="mt-4 rounded-xl bg-neutral-950/70 p-4">
          <div className="mb-2 text-xs font-medium text-violet-300">AI 批量复盘报告</div>
          <Markdown>{bulk.text}</Markdown>
        </div>
      )}

      {list.length === 0 ? (
        <p className="mt-4 text-sm text-neutral-600">
          {onlyProblems ? "还没有问题手。" : "还没有对局记录，去上面打几手吧。"}
        </p>
      ) : (
        <ul className="mt-4 space-y-2">
          {list.map((h) => {
            const mistakes = h.decisions.filter((d) => d.grade === "mistake").length;
            const actionLines = handActionLine(h);
            const isOpen = openTs === h.ts;
            return (
              <li key={h.ts} className="rounded-xl border border-neutral-800 bg-neutral-950/50 p-3 text-xs">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-neutral-300">
                    {h.hero_pos} {h.hero_glyphs.join(" ")}
                    {h.board_glyphs.length ? (
                      <span className="text-neutral-500"> · {h.board_glyphs.join(" ")}</span>
                    ) : null}
                    {h.villain_glyphs.length ? (
                      <span className="text-neutral-600"> · 对手 {h.villain_glyphs.join(" ")}</span>
                    ) : null}
                  </span>
                  <span className={`shrink-0 ${h.hero_net >= 0 ? "text-emerald-300" : "text-red-300"}`}>
                    {h.hero_net >= 0 ? "+" : ""}
                    {h.hero_net.toFixed(1)}bb
                    {h.is_problem && (
                      <span
                        className={`ml-1 rounded px-1 py-0.5 text-[10px] ${
                          h.is_big ? "bg-red-900/60 text-red-200" : "bg-amber-900/60 text-amber-200"
                        }`}
                      >
                        {h.is_big ? "大" : "问题"}
                      </span>
                    )}
                  </span>
                </div>

                {actionLines.length > 0 && (
                  <div className="mt-1.5 space-y-0.5">
                    {actionLines.map((ln) => (
                      <div key={ln.street} className="flex gap-2 text-[11px] leading-relaxed">
                        <span className="shrink-0 text-neutral-600">{ln.street}</span>
                        <span className="text-neutral-400">{ln.text}</span>
                      </div>
                    ))}
                  </div>
                )}

                <div className="mt-1.5 flex items-center justify-between gap-2">
                  <span className="text-neutral-500">
                    {h.decisions.length} 个决策
                    {mistakes > 0 ? <span className="text-red-400"> · {mistakes} 偏离</span> : null}
                  </span>
                  <button
                    onClick={() => askHand(h)}
                    className={`flex shrink-0 items-center gap-1 rounded-lg px-2.5 py-1 text-[11px] font-medium transition ${
                      isOpen
                        ? "bg-violet-700 text-white"
                        : "border border-violet-700/60 bg-violet-950/30 text-violet-200 hover:bg-violet-900/40"
                    }`}
                  >
                    {isOpen ? "收起" : (<><Sparkles className="size-3.5" /> 问 AI 这手</>)}
                  </button>
                </div>

                {isOpen && (
                  <div className="mt-2 rounded-lg bg-neutral-900/70 p-3">
                    {explain.error ? (
                      <p className="text-xs text-red-400">复盘不可用：{explain.error}</p>
                    ) : explain.text ? (
                      <Markdown>{explain.text}</Markdown>
                    ) : (
                      <p className="text-xs text-neutral-500">AI 正在复盘这手…</p>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
