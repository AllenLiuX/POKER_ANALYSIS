"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  getTrainerNext,
  getTrainerSpots,
  postTrainerAnswer,
  postTrainerCoach,
  type SpotIndexEntry,
  type TrainerAnswer,
  type TrainerScenario,
} from "@/lib/api";
import ActionBar from "@/components/ActionBar";
import FeedbackPanel, { type CoachState } from "@/components/FeedbackPanel";
import PlayingCard from "@/components/PlayingCard";
import PokerTable from "@/components/PokerTable";

const POSITION_ORDER = ["UTG", "MP", "CO", "BTN", "SB", "BB"];
const SPOT_TABS: { id: string; label: string }[] = [
  { id: "RFI", label: "开池 (RFI)" },
  { id: "vs_RFI", label: "防守 (面对开池)" },
];

function matchupLabel(pos: string): string {
  return pos.replace("_vs_", " vs ");
}

interface Stats {
  total: number;
  correct: number;
  streak: number;
  bestStreak: number;
}

const ZERO: Stats = { total: 0, correct: 0, streak: 0, bestStreak: 0 };

export default function TrainerPage() {
  const [spots, setSpots] = useState<SpotIndexEntry[]>([]);
  const [spotFilter, setSpotFilter] = useState("RFI");
  const [posFilter, setPosFilter] = useState("随机");
  const [scenario, setScenario] = useState<TrainerScenario | null>(null);
  const [answer, setAnswer] = useState<TrainerAnswer | null>(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [stats, setStats] = useState<Stats>(ZERO);
  const [coach, setCoach] = useState<CoachState>({
    text: null,
    loading: false,
    error: null,
  });

  const loadNext = useCallback(async () => {
    setLoading(true);
    setErr(null);
    setAnswer(null);
    setCoach({ text: null, loading: false, error: null });
    try {
      const scen = await getTrainerNext({
        spot: spotFilter,
        position: posFilter === "随机" ? undefined : posFilter,
      });
      setScenario(scen);
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setLoading(false);
    }
  }, [spotFilter, posFilter]);

  useEffect(() => {
    getTrainerSpots().then(setSpots).catch(() => {});
  }, []);

  useEffect(() => {
    loadNext();
  }, [loadNext]);

  // 当前训练类型下可选的位置/对局
  const choices = useMemo(() => {
    const forSpot = spots.filter((s) => s.spot === spotFilter);
    const positions =
      spotFilter === "RFI"
        ? POSITION_ORDER.filter((p) => forSpot.some((s) => s.position === p))
        : forSpot.map((s) => s.position);
    return ["随机", ...positions];
  }, [spots, spotFilter]);

  function selectSpot(id: string) {
    setSpotFilter(id);
    setPosFilter("随机");
  }

  const act = useCallback(
    async (action: string) => {
      if (!scenario || answer || submitting) return;
      setSubmitting(true);
      setErr(null);
      try {
        const res = await postTrainerAnswer({
          format: scenario.format,
          spot: scenario.spot,
          position: scenario.position,
          hero: scenario.hero,
          action,
          scenario_id: scenario.id,
        });
        setAnswer(res);
        setStats((s) => {
          const correct = res.score.correct;
          const streak = correct ? s.streak + 1 : 0;
          return {
            total: s.total + 1,
            correct: s.correct + (correct ? 1 : 0),
            streak,
            bestStreak: Math.max(s.bestStreak, streak),
          };
        });
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
      const res = await postTrainerCoach({
        format: scenario.format,
        spot: scenario.spot,
        position: scenario.position,
        hero: scenario.hero,
        action: answer.score.chosen,
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

  // 键盘快捷键：f/c/r 出招，Enter/空格 下一手
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (!scenario) return;
      const k = e.key.toLowerCase();
      if (!answer) {
        const map: Record<string, string> = { f: "fold", c: "call", r: "raise" };
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
          翻前训练器{" "}
          <span className="text-sm font-normal text-neutral-500">
            6-max · 100bb · RFI
          </span>
        </h1>
      </div>

      {/* 统计条 */}
      <div className="mb-5 flex items-center gap-4 rounded-xl border border-neutral-800 bg-neutral-900/60 px-4 py-3 text-sm">
        <Stat label="正确率" value={`${acc}%`} sub={`${stats.correct}/${stats.total}`} />
        <Stat label="连对" value={String(stats.streak)} />
        <Stat label="最佳连对" value={String(stats.bestStreak)} />
        <button
          onClick={() => setStats(ZERO)}
          className="ml-auto rounded-lg bg-neutral-800 px-3 py-1.5 text-xs text-neutral-400 hover:bg-neutral-700"
        >
          重置统计
        </button>
      </div>

      {/* 训练类型 */}
      <div className="mb-3 flex gap-2">
        {SPOT_TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => selectSpot(t.id)}
            className={`rounded-lg px-4 py-1.5 text-sm font-medium transition ${
              t.id === spotFilter
                ? "bg-neutral-100 text-neutral-900"
                : "bg-neutral-800 text-neutral-300 hover:bg-neutral-700"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* 位置 / 对局过滤 */}
      <div className="mb-5 flex flex-wrap gap-2">
        {choices.map((p) => (
          <button
            key={p}
            onClick={() => setPosFilter(p)}
            className={`rounded-lg px-3 py-1 text-xs font-medium transition ${
              p === posFilter
                ? "bg-emerald-600 text-white"
                : "bg-neutral-800 text-neutral-300 hover:bg-neutral-700"
            }`}
          >
            {p === "随机" ? "随机" : matchupLabel(p)}
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
          <PokerTable scenario={scenario} />

          {/* 你的手牌 + 出题 */}
          <div className="mt-4 flex flex-col items-center gap-3">
            <div className="flex items-center gap-3">
              {scenario.hero.map((c) => (
                <PlayingCard key={c} card={c} size="lg" />
              ))}
              <div className="text-left">
                <div className="text-2xl font-bold text-neutral-100">
                  {scenario.hero_class}
                </div>
                <div className="text-xs text-neutral-500">
                  {scenario.opener_position
                    ? `你在 ${scenario.hero_position} 防守 ${scenario.opener_position} 的开池`
                    : `你在 ${scenario.hero_position} 首先行动`}
                </div>
              </div>
            </div>
            <p className="text-center text-sm text-neutral-400">{scenario.prompt}</p>
          </div>

          {/* 出招 / 反馈 */}
          <div className="mt-6">
            {!answer ? (
              <ActionBar
                actions={scenario.available_actions}
                labels={scenario.action_labels}
                disabled={submitting || loading}
                onAct={act}
              />
            ) : (
              <FeedbackPanel
                answer={answer}
                coach={coach}
                onRequestCoach={requestCoach}
                onNext={loadNext}
              />
            )}
          </div>

          {!answer && (
            <p className="mt-4 text-center text-xs text-neutral-600">
              快捷键：F 弃牌 · C 跟注 · R 加注 · 反馈后按 Enter 下一手
            </p>
          )}
        </>
      )}

      {loading && !scenario && (
        <p className="text-center text-neutral-500">发牌中…</p>
      )}
    </main>
  );
}

function Stat({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-neutral-500">
        {label}
      </div>
      <div className="text-lg font-bold text-neutral-100">
        {value}
        {sub && <span className="ml-1 text-xs font-normal text-neutral-500">{sub}</span>}
      </div>
    </div>
  );
}
