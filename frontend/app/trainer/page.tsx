"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Target } from "lucide-react";
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
import PokerTable, { scenarioToTableView } from "@/components/PokerTable";
import {
  loadAttempts,
  pickAdaptiveTarget,
  recordAttempt,
  summarize,
  type Attempt,
  type Grade,
} from "@/lib/progress";
import { pushAttempt } from "@/lib/cloud";

const POSITION_ORDER = ["UTG", "MP", "CO", "BTN", "SB", "BB"];
const SPOT_TABS: { id: string; label: string }[] = [
  { id: "RFI", label: "开池 (RFI)" },
  { id: "vs_RFI", label: "防守 (面对开池)" },
];
const DIFFICULTY_TABS: { id: string; label: string; hint: string }[] = [
  { id: "easy", label: "轻松", hint: "自然随机发牌" },
  { id: "standard", label: "标准", hint: "偏向临界手牌" },
  { id: "hard", label: "进阶", hint: "专攻混合/边界手牌" },
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
  const [difficulty, setDifficulty] = useState("standard");
  const [adaptive, setAdaptive] = useState(false);
  const [scenario, setScenario] = useState<TrainerScenario | null>(null);
  const [answer, setAnswer] = useState<TrainerAnswer | null>(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [stats, setStats] = useState<Stats>(ZERO);
  const [lifetime, setLifetime] = useState({ total: 0, accuracy: 0 });
  const [coach, setCoach] = useState<CoachState>({
    text: null,
    loading: false,
    error: null,
  });

  useEffect(() => {
    const s = summarize(loadAttempts());
    setLifetime({ total: s.total, accuracy: s.accuracy });
  }, []);

  const loadNext = useCallback(async () => {
    setLoading(true);
    setErr(null);
    setAnswer(null);
    setCoach({ text: null, loading: false, error: null });
    try {
      let target: { spot?: string; position?: string } = {
        spot: spotFilter,
        position: posFilter === "随机" ? undefined : posFilter,
      };
      if (adaptive && spots.length) {
        // 智能模式：跨所有 spot 按弱项加权选题，忽略手动过滤
        const t = pickAdaptiveTarget(
          loadAttempts(),
          spots.map((s) => ({ spot: s.spot, position: s.position })),
        );
        if (t) target = { spot: t.spot, position: t.position };
      }
      const scen = await getTrainerNext({ ...target, difficulty });
      setScenario(scen);
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setLoading(false);
    }
  }, [spotFilter, posFilter, difficulty, adaptive, spots]);

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
        const attempt: Attempt = {
          ts: Date.now(),
          spot: scenario.spot,
          position: scenario.position,
          heroPosition: scenario.hero_position,
          opener: scenario.opener_position,
          handClass: scenario.hero_class,
          action: res.score.chosen,
          optimalAction: res.score.optimal_action,
          grade: res.score.grade as Grade,
          correct: res.score.correct,
        };
        const all = recordAttempt(attempt);
        const sum = summarize(all);
        setLifetime({ total: sum.total, accuracy: sum.accuracy });
        // 登录后顺带同步到云端（未登录/未启用则静默跳过）
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

  // 出招快捷键交给 ActionBar；此处只在有反馈时用 Enter/空格进入下一手。
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

  const acc = stats.total ? Math.round((stats.correct / stats.total) * 100) : 0;

  const shortcutHint = useMemo(() => {
    if (!scenario) return "";
    const cap: Record<string, string> = {
      fold: "F 弃牌",
      call: "C 跟注",
      raise: "R 加注",
      allin: "A 全下",
    };
    const parts = scenario.available_actions.map(
      (a) => cap[a] ?? `${a} ${scenario.action_labels[a] ?? a}`,
    );
    return `快捷键：${parts.join(" · ")} · 反馈后按 Enter 下一手`;
  }, [scenario]);

  return (
    <main className="mx-auto max-w-2xl px-6 py-10">
      <div className="mb-6 flex items-baseline gap-3">
        <h1 className="text-2xl font-bold">
          翻前训练器{" "}
          <span className="text-sm font-normal text-neutral-500">
            6-max · 100bb
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
        <button
          onClick={() => setStats(ZERO)}
          className="ml-auto rounded-lg bg-neutral-800 px-3 py-1.5 text-xs text-neutral-400 hover:bg-neutral-700"
        >
          重置本次
        </button>
      </div>

      {/* 训练设置：类型 / 难度 / 模式 收进一张卡，位置在下一行 */}
      <div className="mb-5 rounded-xl border border-neutral-800 bg-neutral-900/40 px-4 py-3">
        <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
          <Segmented
            label="类型"
            options={SPOT_TABS}
            value={spotFilter}
            onChange={selectSpot}
            disabled={adaptive}
          />
          <Segmented
            label="难度"
            options={DIFFICULTY_TABS}
            value={difficulty}
            onChange={setDifficulty}
          />
          <div className="flex flex-col gap-1">
            <span className="text-[10px] uppercase tracking-wider text-neutral-500">
              模式
            </span>
            <button
              onClick={() => setAdaptive((v) => !v)}
              title="按你的历史弱项与练习不足处自动挑选题目"
              className={`flex items-center gap-1 rounded-md px-3 py-1 text-xs font-medium transition ${
                adaptive
                  ? "bg-violet-600 text-white"
                  : "bg-neutral-800 text-neutral-300 hover:bg-neutral-700"
              }`}
            >
              <Target className="size-3.5" />
              智能训练{adaptive ? "：开" : ""}
            </button>
          </div>
        </div>

        {/* 位置 / 对局 */}
        <div className="mt-3 border-t border-neutral-800/70 pt-3">
          {adaptive ? (
            <p className="text-xs text-violet-400">
              智能模式：按你的历史弱项与练习不足处自动挑选题目（已忽略手动过滤）。
            </p>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <span className="mr-1 text-[10px] uppercase tracking-wider text-neutral-500">
                {spotFilter === "RFI" ? "位置" : "对局"}
              </span>
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
          <PokerTable view={scenarioToTableView(scenario)} />

          {/* 你的手牌 + 出题 */}
          <div className="mt-4 flex flex-col items-center gap-3">
            <div className="flex items-center gap-3">
              {scenario.hero.map((c) => (
                <PlayingCard key={c} card={c} size="lg" />
              ))}
              <div className="text-left">
                <div className="flex items-center gap-2">
                  <span className="text-2xl font-bold text-neutral-100">
                    {scenario.hero_class}
                  </span>
                  {scenario.is_critical && (
                    <span
                      title="GTO 在这手牌上混合多个动作——正是最值得练的临界决策"
                      className="rounded-full bg-amber-500/20 px-2 py-0.5 text-[10px] font-medium text-amber-300 ring-1 ring-amber-500/40"
                    >
                      临界牌
                    </span>
                  )}
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
            <p className="mt-4 text-center text-xs text-neutral-600">{shortcutHint}</p>
          )}
        </>
      )}

      {loading && !scenario && (
        <p className="text-center text-neutral-500">发牌中…</p>
      )}
    </main>
  );
}

function Segmented({
  label,
  options,
  value,
  onChange,
  disabled,
}: {
  label: string;
  options: { id: string; label: string; hint?: string }[];
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className={`flex flex-col gap-1 ${disabled ? "pointer-events-none opacity-40" : ""}`}>
      <span className="text-[10px] uppercase tracking-wider text-neutral-500">
        {label}
      </span>
      <div className="flex gap-1 rounded-lg bg-neutral-900 p-1">
        {options.map((o) => (
          <button
            key={o.id}
            onClick={() => onChange(o.id)}
            title={o.hint}
            className={`rounded-md px-3 py-1 text-xs font-medium transition ${
              o.id === value
                ? "bg-neutral-100 text-neutral-900"
                : "text-neutral-400 hover:text-neutral-200"
            }`}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
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
