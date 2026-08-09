import { Lightbulb, Sparkles } from "lucide-react";
import type { TrainerAnswer } from "@/lib/api";

const GRADE_STYLE: Record<string, { ring: string; badge: string; text: string }> = {
  optimal: {
    ring: "border-emerald-500/60 bg-emerald-950/40",
    badge: "bg-emerald-500 text-emerald-950",
    text: "text-emerald-300",
  },
  acceptable: {
    ring: "border-amber-500/60 bg-amber-950/30",
    badge: "bg-amber-500 text-amber-950",
    text: "text-amber-300",
  },
  mistake: {
    ring: "border-red-500/60 bg-red-950/30",
    badge: "bg-red-500 text-red-950",
    text: "text-red-300",
  },
};

const ACTION_COLOR: Record<string, string> = {
  fold: "#525252",
  call: "#0284c7",
  raise: "#dc2626",
  allin: "#d97706",
};

const ACTION_LABEL: Record<string, string> = {
  fold: "弃牌",
  call: "跟注",
  raise: "加注",
  allin: "全下",
};

const ACTION_ORDER = ["fold", "call", "raise", "allin"];

export interface CoachState {
  text: string | null;
  loading: boolean;
  error: string | null;
}

export default function FeedbackPanel({
  answer,
  coach,
  onRequestCoach,
  onNext,
}: {
  answer: TrainerAnswer;
  coach: CoachState;
  onRequestCoach: () => void;
  onNext: () => void;
}) {
  const { score, feedback } = answer;
  const style = GRADE_STYLE[score.grade] ?? GRADE_STYLE.mistake;
  const acts = ACTION_ORDER.filter((a) => a in score.frequencies);

  return (
    <div className={`rounded-2xl border p-5 ${style.ring}`}>
      <div className="flex items-center gap-3">
        <span
          className={`rounded-full px-3 py-1 text-sm font-bold ${style.badge}`}
        >
          {feedback.headline}
        </span>
        <span className="text-sm text-neutral-400">
          {answer.hand_class}
          {" · "}
          {answer.opener_position
            ? `${answer.hero_position} vs ${answer.opener_position}`
            : answer.hero_position}
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

      {/* GTO 频率条 */}
      <div className="mt-4">
        <div className="mb-1 text-xs text-neutral-500">GTO 策略频率</div>
        <div className="flex h-4 w-full overflow-hidden rounded-full bg-neutral-800">
          {acts.map((a) => {
            const f = score.frequencies[a] ?? 0;
            if (f <= 0) return null;
            return (
              <div
                key={a}
                style={{ width: `${f * 100}%`, backgroundColor: ACTION_COLOR[a] }}
                title={`${ACTION_LABEL[a] ?? a} ${(f * 100).toFixed(0)}%`}
              />
            );
          })}
        </div>
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
          {acts.map((a) => {
            const f = score.frequencies[a] ?? 0;
            const isChosen = a === score.chosen;
            const isOptimal = a === score.optimal_action;
            return (
              <span key={a} className="flex items-center gap-1.5">
                <span
                  className="inline-block h-2.5 w-2.5 rounded-sm"
                  style={{ backgroundColor: ACTION_COLOR[a] }}
                />
                <span className="text-neutral-300">
                  {ACTION_LABEL[a] ?? a} {(f * 100).toFixed(0)}%
                </span>
                {isChosen && (
                  <span className="rounded bg-neutral-700 px-1 text-[10px] text-neutral-200">
                    你选
                  </span>
                )}
                {isOptimal && !isChosen && (
                  <span className="rounded bg-emerald-900 px-1 text-[10px] text-emerald-300">
                    最高频
                  </span>
                )}
              </span>
            );
          })}
        </div>
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
