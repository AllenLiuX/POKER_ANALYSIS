import { Spade } from "lucide-react";
import type { TrainerScenario } from "@/lib/api";
import PlayingCard from "./PlayingCard";

// 固定几何：动作顺时针流动（UTG→MP→CO→BTN→SB→BB），BTN 在底部靠近视角。
const SEAT_SLOT: Record<string, { left: string; top: string }> = {
  UTG: { left: "50%", top: "8%" },
  MP: { left: "88%", top: "30%" },
  CO: { left: "88%", top: "72%" },
  BTN: { left: "50%", top: "90%" },
  SB: { left: "12%", top: "72%" },
  BB: { left: "12%", top: "30%" },
};

export type SeatStatus = "hero" | "villain" | "raiser" | "folded" | "waiting";

export interface TableSeat {
  position: string;
  status: SeatStatus;
  /** SEAT_SLOT 定位键，默认用 position。单挑时可强制英雄在底部、对手在顶部。 */
  slot?: string;
  /** 明牌（英雄始终可见；对手仅摊牌时可见） */
  cards?: string[];
  /** 显示两张牌背（未摊牌的对手） */
  hideCards?: boolean;
  /** 座位下方主标签，如 YOU / 加注 2.5bb / fold */
  label?: string;
  /** 可选筹码量，显示为 xxbb 一行 */
  stackBB?: number;
  isButton?: boolean;
}

export interface TableView {
  seats: TableSeat[];
  /** 公共牌（居中显示） */
  board?: string[];
  potBB: number;
  /** 街道标签，如「翻牌 · 干燥」；翻前不传 */
  streetLabel?: string;
  /** 待跟注额 */
  toCallBB?: number;
  /** 有效筹码，仅翻前中心显示 */
  effectiveStackBB?: number;
}

const CHIP_STYLE: Record<SeatStatus, string> = {
  hero: "border-emerald-400 bg-emerald-500/20 text-emerald-200 ring-2 ring-emerald-400/50",
  villain: "border-sky-400 bg-sky-500/20 text-sky-200 ring-2 ring-sky-400/40",
  raiser: "border-red-400 bg-red-500/20 text-red-200 ring-2 ring-red-400/50",
  folded: "border-neutral-800 bg-neutral-900 text-neutral-600",
  waiting: "border-neutral-600 bg-neutral-800 text-neutral-300",
};

const LABEL_STYLE: Record<SeatStatus, string> = {
  hero: "text-emerald-300",
  villain: "text-sky-300",
  raiser: "text-red-300",
  folded: "text-neutral-600",
  waiting: "text-neutral-500",
};

function CardBack() {
  return (
    <div className="inline-flex h-9 w-7 items-center justify-center rounded-md border border-sky-800 bg-gradient-to-br from-sky-900 to-indigo-950 text-sky-500 shadow-md">
      <Spade className="size-3.5" fill="currentColor" strokeWidth={0} />
    </div>
  );
}

function SeatChip({ seat }: { seat: TableSeat }) {
  const slot = SEAT_SLOT[seat.slot ?? seat.position] ?? { left: "50%", top: "50%" };
  const showBack = seat.hideCards && !seat.cards?.length;
  return (
    <div
      className="absolute flex -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-1"
      style={{ left: slot.left, top: slot.top }}
    >
      {(seat.cards?.length || showBack) && (
        <div className="flex gap-1">
          {seat.cards?.length
            ? seat.cards.map((c) => <PlayingCard key={c} card={c} size="sm" />)
            : (
              <>
                <CardBack />
                <CardBack />
              </>
            )}
        </div>
      )}
      <div className="relative">
        <div
          className={`flex h-12 w-12 items-center justify-center rounded-full border-2 text-xs font-bold transition ${CHIP_STYLE[seat.status]}`}
        >
          {seat.position}
        </div>
        {seat.isButton && (
          <span className="absolute -right-4 top-0 flex h-5 w-5 items-center justify-center rounded-full bg-white text-[10px] font-bold text-neutral-900">
            D
          </span>
        )}
      </div>
      {seat.label && <span className={`text-[10px] ${LABEL_STYLE[seat.status]}`}>{seat.label}</span>}
      {seat.stackBB != null && (
        <span className="text-[10px] text-neutral-600">{seat.stackBB}bb</span>
      )}
    </div>
  );
}

export default function PokerTable({ view }: { view: TableView }) {
  return (
    <div className="relative mx-auto aspect-[4/3] w-full max-w-xl">
      {/* 桌面 */}
      <div className="absolute inset-[12%] rounded-[45%] border-4 border-neutral-700 bg-emerald-950/70 shadow-inner" />
      <div className="absolute inset-[15%] rounded-[45%] border border-emerald-800/60" />

      {/* 中心：街道 + 公共牌 + 底池 */}
      <div className="absolute left-1/2 top-1/2 flex -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-1.5">
        {view.streetLabel && (
          <div className="text-[10px] uppercase tracking-widest text-emerald-400/70">
            {view.streetLabel}
          </div>
        )}
        {view.board?.length ? (
          <div className="flex gap-1.5">
            {view.board.map((c) => (
              <PlayingCard key={c} card={c} size="md" />
            ))}
          </div>
        ) : null}
        <div className="text-center">
          <div className="text-lg font-bold text-emerald-200">{view.potBB} bb</div>
          {view.toCallBB != null && view.toCallBB > 0 ? (
            <div className="text-[10px] text-red-300/90">待跟 {view.toCallBB}bb</div>
          ) : view.effectiveStackBB != null ? (
            <div className="text-[10px] text-neutral-500">有效筹码 {view.effectiveStackBB}bb</div>
          ) : (
            <div className="text-[10px] uppercase tracking-wider text-emerald-400/60">底池</div>
          )}
        </div>
      </div>

      {/* 座位 */}
      {view.seats.map((seat) => (
        <SeatChip key={`${seat.position}-${seat.slot ?? ""}`} seat={seat} />
      ))}
    </div>
  );
}

// ---------- 适配器：翻前 scenario → TableView ----------
export function scenarioToTableView(scenario: TrainerScenario): TableView {
  const openSize = scenario.facing?.open_size_bb ?? 2.5;
  const seats: TableSeat[] = scenario.seats.map((s) => {
    let label: string;
    if (s.status === "hero") label = "YOU";
    else if (s.status === "raiser") label = `加注 ${openSize}bb`;
    else if (s.status === "folded") label = "fold";
    else if (s.is_blind) label = s.position === "SB" ? "SB 0.5" : "BB 1";
    else label = "待行动";
    return {
      position: s.position,
      status: s.status,
      cards: s.status === "hero" ? scenario.hero : undefined,
      label,
      isButton: s.position === "BTN",
    };
  });
  return {
    seats,
    potBB: scenario.pot_bb,
    effectiveStackBB: scenario.effective_stack_bb,
  };
}
