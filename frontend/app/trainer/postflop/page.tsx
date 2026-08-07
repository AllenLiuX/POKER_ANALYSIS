"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  getPostflopNext,
  postPostflopAnswer,
  type PostflopAnswer,
  type PostflopScenario,
} from "@/lib/api";
import ActionBar from "@/components/ActionBar";
import PlayingCard from "@/components/PlayingCard";
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

export default function PostflopTrainerPage() {
  const [role, setRole] = useState("");
  const [scenario, setScenario] = useState<PostflopScenario | null>(null);
  const [answer, setAnswer] = useState<PostflopAnswer | null>(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [stats, setStats] = useState<Stats>({ total: 0, correct: 0, streak: 0 });
  const [lifetime, setLifetime] = useState({ total: 0, accuracy: 0 });

  useEffect(() => {
    const s = summarize(loadAttempts());
    setLifetime({ total: s.total, accuracy: s.accuracy });
  }, []);

  const loadNext = useCallback(async () => {
    setLoading(true);
    setErr(null);
    setAnswer(null);
    try {
      const scen = await getPostflopNext({ role: role || undefined });
      setScenario(scen);
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setLoading(false);
    }
  }, [role]);

  useEffect(() => {
    loadNext();
  }, [loadNext]);

  const act = useCallback(
    async (action: string) => {
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

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (!scenario) return;
      const k = e.key.toLowerCase();
      if (!answer) {
        const map: Record<string, string> = {
          f: "fold",
          c: scenario.role === "pfr" ? "check" : "call",
          b: "bet",
          r: "raise",
        };
        const a = map[k];
        if (a && scenario.available_actions.includes(a)) {
          e.preventDefault();
          act(a);
        }
      } else if (k === "enter" || k === " ") {
        e.preventDefault();
        loadNext();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [scenario, answer, act, loadNext]);

  const acc = stats.total ? Math.round((stats.correct / stats.total) * 100) : 0;

  return (
    <main className="mx-auto max-w-2xl px-6 py-10">
      <div className="mb-6 flex items-center gap-3">
        <Link href="/" className="text-sm text-neutral-400 hover:text-neutral-200">
          ← 返回
        </Link>
        <h1 className="text-2xl font-bold">
          翻后训练器{" "}
          <span className="text-sm font-normal text-neutral-500">翻牌 · 启发式</span>
        </h1>
        <Link
          href="/trainer"
          className="ml-auto text-sm text-emerald-400 hover:text-emerald-300"
        >
          翻前训练 →
        </Link>
      </div>

      <div className="mb-4 rounded-lg border border-amber-800/50 bg-amber-950/20 px-4 py-2 text-xs text-amber-300/90">
        翻后为<strong>透明启发式引擎</strong>（range 优势 / MDF / 赔率 / bluff-to-value），
        非精确 GTO 求解；用于建立直觉，边界处允许合理区间。
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
      <div className="mb-5 flex flex-wrap gap-2">
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
      </div>

      {err && (
        <p className="mb-4 rounded-lg bg-red-950/50 px-4 py-2 text-sm text-red-300">
          错误：{err}
        </p>
      )}

      {scenario && (
        <>
          {/* 牌桌信息 */}
          <div className="rounded-2xl border border-neutral-800 bg-gradient-to-b from-emerald-950/30 to-neutral-950 p-5">
            <div className="mb-3 flex items-center justify-between text-xs text-neutral-400">
              <span>
                {scenario.hero_position} <span className="text-neutral-600">vs</span>{" "}
                {scenario.villain_position} · 单加注底池
              </span>
              <span>
                底池 <span className="font-semibold text-neutral-200">{scenario.pot_bb}bb</span>
                {scenario.bet_bb ? (
                  <>
                    {" · "}对手下注{" "}
                    <span className="font-semibold text-red-300">{scenario.bet_bb}bb</span>
                  </>
                ) : null}
              </span>
            </div>

            {/* 翻牌 */}
            <div className="mb-1 text-center text-[11px] uppercase tracking-widest text-neutral-500">
              翻牌 · {scenario.texture.descriptor}
            </div>
            <div className="flex justify-center gap-2">
              {scenario.board.map((c) => (
                <PlayingCard key={c} card={c} size="lg" />
              ))}
            </div>

            {/* 英雄手牌 */}
            <div className="mt-5 flex items-center justify-center gap-3">
              {scenario.hero.map((c) => (
                <PlayingCard key={c} card={c} size="md" />
              ))}
              <span className="text-sm text-neutral-400">你的手牌</span>
            </div>
          </div>

          <p className="mt-4 text-center text-sm text-neutral-400">{scenario.prompt}</p>

          <div className="mt-6">
            {!answer ? (
              <ActionBar
                actions={scenario.available_actions}
                labels={scenario.action_labels}
                disabled={submitting || loading}
                onAct={act}
              />
            ) : (
              <PostflopFeedback answer={answer} onNext={loadNext} />
            )}
          </div>

          {!answer && (
            <p className="mt-4 text-center text-xs text-neutral-600">
              {scenario.role === "pfr"
                ? "快捷键：C 过牌 · B 下注"
                : "快捷键：F 弃牌 · C 跟注 · R 加注"}{" "}
              · 反馈后按 Enter 下一手
            </p>
          )}
        </>
      )}

      {loading && !scenario && <p className="text-center text-neutral-500">发牌中…</p>}
    </main>
  );
}

function PostflopFeedback({
  answer,
  onNext,
}: {
  answer: PostflopAnswer;
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
        <span className="ml-auto rounded bg-neutral-800 px-2 py-0.5 text-[10px] text-neutral-400">
          启发式近似
        </span>
      </div>

      <p className="mt-3 text-sm leading-relaxed text-neutral-200">
        {feedback.explanation}
      </p>
      {feedback.tip && <p className="mt-1 text-xs text-neutral-400">💡 {feedback.tip}</p>}

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
        </span>
        <span className="rounded bg-emerald-900/60 px-2 py-1 text-emerald-300">
          建议：{labelOf(score.recommended)}
        </span>
        {rec.size_advice && (
          <span className="rounded bg-neutral-800 px-2 py-1 text-neutral-400">
            尺度：{rec.size_advice}
          </span>
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
