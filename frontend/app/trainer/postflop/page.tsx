"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Lightbulb, Sparkles } from "lucide-react";
import {
  getPostflopNext,
  getPostflopMatchups,
  postPostflopAnswer,
  postPostflopCoach,
  type PostflopAnswer,
  type PostflopMatchup,
  type PostflopScenario,
} from "@/lib/api";
import ActionBar from "@/components/ActionBar";
import { type CoachState } from "@/components/FeedbackPanel";
import PlayingCard from "@/components/PlayingCard";
import PokerTable, { type TableView } from "@/components/PokerTable";
import {
  loadAttempts,
  recordAttempt,
  summarize,
  type Attempt,
  type Grade,
} from "@/lib/progress";
import { pushAttempt } from "@/lib/cloud";

const ROLE_TABS = [
  { id: "", label: "随机" },
  { id: "pfr", label: "持续下注 (我是加注方)" },
  { id: "caller", label: "防守 (面对下注)" },
];

const GRADE_STYLE: Record<string, { ring: string; badge: string }> = {
  optimal: { ring: "border-emerald-500/60 bg-emerald-950/40", badge: "bg-emerald-500 text-emerald-950" },
  acceptable: { ring: "border-amber-500/60 bg-amber-950/30", badge: "bg-amber-500 text-amber-950" },
  mistake: { ring: "border-red-500/60 bg-red-950/30", badge: "bg-red-500 text-red-950" },
};

interface Stats {
  total: number;
  correct: number;
  streak: number;
}

const STREET_CN: Record<string, string> = {
  flop: "翻牌",
  turn: "转牌",
  river: "河牌",
};

function postflopTableView(scenario: PostflopScenario): TableView {
  const street = STREET_CN[scenario.street] ?? "翻牌";
  const bet = scenario.bet_bb ?? 0;
  return {
    seats: [
      {
        position: scenario.villain_position,
        status: bet > 0 ? "raiser" : "waiting",
        slot: "UTG",
        hideCards: true,
        isButton: scenario.villain_position === "BTN",
        label: bet > 0 ? `下注 ${bet}bb` : "已过牌",
      },
      {
        position: scenario.hero_position,
        status: "hero",
        slot: "BTN",
        cards: scenario.hero,
        isButton: scenario.hero_position === "BTN",
        label: "YOU",
      },
    ],
    board: scenario.board,
    potBB: scenario.pot_bb,
    toCallBB: bet > 0 ? bet : undefined,
    streetLabel: `${street} · ${scenario.texture.descriptor}`,
  };
}

export default function PostflopTrainerPage() {
  const [role, setRole] = useState("");
  const [matchup, setMatchup] = useState("");
  const [matchups, setMatchups] = useState<PostflopMatchup[]>([]);
  const [scenario, setScenario] = useState<PostflopScenario | null>(null);
  const [answer, setAnswer] = useState<PostflopAnswer | null>(null);
  const [coach, setCoach] = useState<CoachState>({
    text: null,
    loading: false,
    error: null,
  });
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [stats, setStats] = useState<Stats>({ total: 0, correct: 0, streak: 0 });
  const [lifetime, setLifetime] = useState({ total: 0, accuracy: 0 });

  useEffect(() => {
    const s = summarize(loadAttempts());
    setLifetime({ total: s.total, accuracy: s.accuracy });
    getPostflopMatchups()
      .then(setMatchups)
      .catch(() => {});
  }, []);

  const loadNext = useCallback(async () => {
    setLoading(true);
    setErr(null);
    setAnswer(null);
    setCoach({ text: null, loading: false, error: null });
    try {
      const scen = await getPostflopNext({ role: role || undefined, matchup: matchup || undefined });
      setScenario(scen);
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setLoading(false);
    }
  }, [role, matchup]);

  useEffect(() => {
    loadNext();
  }, [loadNext]);

  const act = useCallback(
    async (action: string, size?: string) => {
      if (!scenario || answer || submitting) return;
      setSubmitting(true);
      setErr(null);
      try {
        const res = await postPostflopAnswer({
          role: scenario.role,
          hero: scenario.hero,
          board: scenario.board,
          villain_range: scenario.villain_range,
          pot_bb: scenario.pot_bb,
          bet_bb: scenario.bet_bb,
          action,
          size,
          scenario_id: scenario.id,
        });
        setAnswer(res);
        setStats((s) => ({
          total: s.total + 1,
          correct: s.correct + (res.score.correct ? 1 : 0),
          streak: res.score.correct ? s.streak + 1 : 0,
        }));
        const attempt: Attempt = {
          ts: Date.now(),
          spot: scenario.role === "pfr" ? "postflop_cbet" : "postflop_defense",
          position: scenario.hero_position,
          heroPosition: scenario.hero_position,
          opener: scenario.villain_position,
          handClass: scenario.hero_class,
          action: res.score.chosen,
          optimalAction: res.score.recommended,
          grade: res.score.grade as Grade,
          correct: res.score.correct,
        };
        const all = recordAttempt(attempt);
        const sum = summarize(all);
        setLifetime({ total: sum.total, accuracy: sum.accuracy });
        pushAttempt(attempt).catch(() => {});
      } catch (e) {
        setErr(String(e instanceof Error ? e.message : e));
      } finally {
        setSubmitting(false);
      }
    },
    [scenario, answer, submitting],
  );

  const requestCoach = useCallback(async () => {
    if (!scenario || !answer || coach.loading || coach.text) return;
    setCoach({ text: null, loading: true, error: null });
    try {
      const res = await postPostflopCoach({
        role: scenario.role,
        hero: scenario.hero,
        board: scenario.board,
        villain_range: scenario.villain_range,
        pot_bb: scenario.pot_bb,
        bet_bb: scenario.bet_bb,
        hero_position: scenario.hero_position,
        villain_position: scenario.villain_position,
        action: answer.score.chosen,
        size: answer.score.size ?? undefined,
      });
      setCoach({ text: res.coaching, loading: false, error: null });
    } catch (e) {
      setCoach({
        text: null,
        loading: false,
        error: String(e instanceof Error ? e.message : e),
      });
    }
  }, [scenario, answer, coach.loading, coach.text]);

  // 出招快捷键由 ActionBar 自己处理；这里只在已有反馈时用 Enter/空格进入下一手。
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (!scenario || !answer) return;
      const k = e.key.toLowerCase();
      if (k === "enter" || k === " ") {
        e.preventDefault();
        loadNext();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [scenario, answer, loadNext]);

  const sizeMap = useMemo(() => {
    if (!scenario) return undefined;
    const m: Record<string, { id: string; label: string; amount_bb?: number }[]> = {};
    if (scenario.bet_sizes?.length) m.bet = scenario.bet_sizes;
    if (scenario.raise_sizes?.length) m.raise = scenario.raise_sizes;
    return Object.keys(m).length ? m : undefined;
  }, [scenario]);

  const shortcutHint = useMemo(() => {
    if (!scenario) return "";
    const cap: Record<string, string> = {
      fold: "F 弃牌",
      call: "C 跟注",
      check: "C 过牌",
      bet: "B 下注",
      raise: "R 加注",
    };
    const parts = scenario.available_actions.map((a) => {
      const base = cap[a] ?? a;
      return sizeMap?.[a]?.length ? `${base}(选尺度)` : base;
    });
    return `快捷键：${parts.join(" · ")} · 反馈后按 Enter 下一手`;
  }, [scenario, sizeMap]);

  const acc = stats.total ? Math.round((stats.correct / stats.total) * 100) : 0;

  return (
    <main className="mx-auto max-w-2xl px-6 py-10">
      <div className="mb-6 flex items-baseline gap-3">
        <h1 className="text-2xl font-bold">
          翻后训练器{" "}
          <span
            title="翻后为透明启发式引擎（range 优势 / MDF / 赔率 / bluff-to-value），非精确 GTO 求解；用于建立直觉，边界处允许合理区间。"
            className="cursor-help text-sm font-normal text-neutral-500 underline decoration-dotted underline-offset-2"
          >
            翻牌 · 启发式 ⓘ
          </span>
        </h1>
      </div>

      {/* 统计条 */}
      <div className="mb-5 flex items-center gap-4 rounded-xl border border-neutral-800 bg-neutral-900/60 px-4 py-3 text-sm">
        <Stat label="本次正确率" value={`${acc}%`} sub={`${stats.correct}/${stats.total}`} />
        <Stat label="连对" value={String(stats.streak)} />
        <Stat
          label="累计手数"
          value={String(lifetime.total)}
          sub={`${Math.round(lifetime.accuracy * 100)}%`}
        />
        <Link
          href="/progress"
          className="ml-auto rounded-lg bg-neutral-800 px-3 py-1.5 text-xs text-neutral-400 hover:bg-neutral-700"
        >
          进度详情 →
        </Link>
      </div>

      {/* 角色 */}
      <div className="mb-5 rounded-xl border border-neutral-800 bg-neutral-900/40 px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="mr-1 text-[10px] uppercase tracking-wider text-neutral-500">
            角色
          </span>
          {ROLE_TABS.map((t) => (
            <button
              key={t.id || "any"}
              onClick={() => setRole(t.id)}
              className={`rounded-lg px-3 py-1.5 text-sm font-medium transition ${
                t.id === role
                  ? "bg-neutral-100 text-neutral-900"
                  : "bg-neutral-800 text-neutral-300 hover:bg-neutral-700"
              }`}
            >
              {t.label}
            </button>
          ))}
          {matchups.length > 0 && (
            <label className="ml-auto flex items-center gap-2 text-xs text-neutral-500">
              对位
              <select
                value={matchup}
                onChange={(e) => setMatchup(e.target.value)}
                className="rounded-lg border border-neutral-700 bg-neutral-800 px-2 py-1.5 text-sm text-neutral-200 focus:border-neutral-500 focus:outline-none"
              >
                <option value="">随机对位</option>
                {matchups.map((m) => (
                  <option key={m.matchup} value={m.matchup}>
                    {m.label}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
      </div>

      {err && (
        <p className="mb-4 rounded-lg bg-red-950/50 px-4 py-2 text-sm text-red-300">
          错误：{err}
        </p>
      )}

      {scenario && (
        <>
          <PokerTable view={postflopTableView(scenario)} />

          {/* 你的手牌 */}
          <div className="mt-4 flex flex-col items-center gap-3">
            <div className="flex items-center gap-3">
              {scenario.hero.map((c) => (
                <PlayingCard key={c} card={c} size="lg" />
              ))}
              <div className="text-left">
                <div className="text-2xl font-bold text-neutral-100">{scenario.hero_class}</div>
                <div className="text-xs text-neutral-500">
                  你在 {scenario.hero_position}{" "}
                  {scenario.role === "pfr" ? "持续下注" : "防守"} vs {scenario.villain_position}
                </div>
              </div>
            </div>
            <p className="text-center text-sm text-neutral-400">{scenario.prompt}</p>
          </div>

          <div className="mt-6">
            {!answer ? (
              <ActionBar
                actions={scenario.available_actions}
                labels={scenario.action_labels}
                sizes={sizeMap}
                disabled={submitting || loading}
                onAct={act}
              />
            ) : (
              <PostflopFeedback
                answer={answer}
                coach={coach}
                onRequestCoach={requestCoach}
                onNext={loadNext}
              />
            )}
          </div>

          {!answer && (
            <p className="mt-4 text-center text-xs text-neutral-600">{shortcutHint}</p>
          )}
        </>
      )}

      {loading && !scenario && <p className="text-center text-neutral-500">发牌中…</p>}
    </main>
  );
}

function PostflopFeedback({
  answer,
  coach,
  onRequestCoach,
  onNext,
}: {
  answer: PostflopAnswer;
  coach: CoachState;
  onRequestCoach: () => void;
  onNext: () => void;
}) {
  const { score, feedback, recommendation: rec, equity } = answer;
  const style = GRADE_STYLE[score.grade] ?? GRADE_STYLE.mistake;
  const isDefense = rec.spot === "defense";

  return (
    <div className={`rounded-2xl border p-5 ${style.ring}`}>
      <div className="flex items-center gap-3">
        <span className={`rounded-full px-3 py-1 text-sm font-bold ${style.badge}`}>
          {feedback.headline}
        </span>
        <span className="text-sm text-neutral-400">
          {answer.hand.made_label}
          {answer.hand.draw_label ? ` + ${answer.hand.draw_label}` : ""}
        </span>
      </div>

      <p className="mt-3 text-sm leading-relaxed text-neutral-200">
        {feedback.explanation}
      </p>
      {feedback.tip && (
        <p className="mt-1 flex items-start gap-1.5 text-xs text-neutral-400">
          <Lightbulb className="mt-0.5 size-3.5 shrink-0 text-amber-300/80" />
          {feedback.tip}
        </p>
      )}

      {/* 胜率条 */}
      <div className="mt-4">
        <div className="mb-1 flex justify-between text-xs text-neutral-500">
          <span>估算胜率 vs 对手范围</span>
          <span>{Math.round(equity * 100)}%</span>
        </div>
        <div className="relative h-3 w-full overflow-hidden rounded-full bg-neutral-800">
          <div
            className="h-full bg-sky-500"
            style={{ width: `${Math.min(100, equity * 100)}%` }}
          />
          {isDefense && rec.required_equity != null && (
            <div
              className="absolute top-0 h-full w-0.5 bg-amber-300"
              style={{ left: `${Math.min(100, rec.required_equity * 100)}%` }}
              title={`底池赔率需 ${Math.round((rec.required_equity ?? 0) * 100)}%`}
            />
          )}
        </div>
        {isDefense && (
          <div className="mt-1 flex gap-4 text-[11px] text-neutral-500">
            <span>
              需要胜率 <span className="text-amber-300">{Math.round((rec.required_equity ?? 0) * 100)}%</span>
            </span>
            <span>
              MDF <span className="text-neutral-300">{Math.round((rec.mdf ?? 0) * 100)}%</span>
            </span>
          </div>
        )}
      </div>

      <div className="mt-3 flex flex-wrap gap-2 text-xs">
        <span className="rounded bg-neutral-800 px-2 py-1 text-neutral-300">
          你选：{labelOf(score.chosen)}
          {score.size_label ? ` · ${score.size_label}` : ""}
        </span>
        <span className="rounded bg-emerald-900/60 px-2 py-1 text-emerald-300">
          建议：{labelOf(score.recommended)}
          {score.recommended_size_label ? ` · ${score.recommended_size_label}` : ""}
        </span>
        {score.size_ok === false && (
          <span className="rounded bg-amber-900/50 px-2 py-1 text-amber-300">
            尺度可更优
          </span>
        )}
        {rec.size_advice && (
          <span className="rounded bg-neutral-800 px-2 py-1 text-neutral-400">
            尺度建议：{rec.size_advice}
          </span>
        )}
      </div>

      {/* AI 深度教练（可选，点击才调 LLM） */}
      <div className="mt-4 border-t border-neutral-800 pt-4">
        {coach.text ? (
          <div className="rounded-xl bg-neutral-900/70 p-3">
            <div className="mb-1 flex items-center gap-2 text-xs font-medium text-violet-300">
              <span>AI 教练</span>
              <span className="text-neutral-600">讲解为什么这样打</span>
            </div>
            <p className="whitespace-pre-line text-sm leading-relaxed text-neutral-200">
              {coach.text}
            </p>
          </div>
        ) : (
          <button
            onClick={onRequestCoach}
            disabled={coach.loading}
            className="flex w-full items-center justify-center gap-1.5 rounded-xl border border-violet-700/60 bg-violet-950/30 py-2.5 text-sm font-medium text-violet-200 transition hover:bg-violet-900/40 disabled:opacity-50"
          >
            {coach.loading ? (
              "AI 教练思考中…"
            ) : (
              <>
                <Sparkles className="size-4" />
                AI 深度讲解：为什么这样打？
              </>
            )}
          </button>
        )}
        {coach.error && (
          <p className="mt-2 text-xs text-red-400">教练暂时不可用：{coach.error}</p>
        )}
      </div>

      <button
        onClick={onNext}
        className="mt-4 w-full rounded-xl bg-emerald-600 py-3 font-semibold text-white transition hover:bg-emerald-500"
      >
        下一手 →
      </button>
    </div>
  );
}

const LABELS: Record<string, string> = {
  fold: "弃牌",
  call: "跟注",
  raise: "加注",
  check: "过牌",
  bet: "下注",
};
function labelOf(a: string): string {
  return LABELS[a] ?? a;
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-neutral-500">{label}</div>
      <div className="text-lg font-bold text-neutral-100">
        {value}
        {sub && <span className="ml-1 text-xs font-normal text-neutral-500">{sub}</span>}
      </div>
    </div>
  );
}
