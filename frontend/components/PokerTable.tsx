import type { TrainerScenario, TrainerSeat } from "@/lib/api";
import PlayingCard from "./PlayingCard";

// 固定几何：动作顺时针流动（UTG→MP→CO→BTN→SB→BB），BTN 在底部靠近视角。
const SEAT_SLOT: Record<string, { left: string; top: string }> = {
  UTG: { left: "50%", top: "6%" },
  MP: { left: "87%", top: "27%" },
  CO: { left: "87%", top: "72%" },
  BTN: { left: "50%", top: "88%" },
  SB: { left: "13%", top: "72%" },
  BB: { left: "13%", top: "27%" },
};

function SeatChip({ seat, hero }: { seat: TrainerSeat; hero: string[] }) {
  const slot = SEAT_SLOT[seat.position] ?? { left: "50%", top: "50%" };
  const base =
    "absolute -translate-x-1/2 -translate-y-1/2 flex flex-col items-center gap-1";
  return (
    <div className={base} style={{ left: slot.left, top: slot.top }}>
      {seat.status === "hero" && (
        <div className="flex gap-1">
          {hero.map((c) => (
            <PlayingCard key={c} card={c} size="sm" />
          ))}
        </div>
      )}
      <div
        className={`flex h-12 w-12 items-center justify-center rounded-full border-2 text-xs font-bold transition ${
          seat.status === "hero"
            ? "border-emerald-400 bg-emerald-500/20 text-emerald-200 ring-2 ring-emerald-400/50"
            : seat.status === "folded"
              ? "border-neutral-800 bg-neutral-900 text-neutral-600"
              : "border-neutral-600 bg-neutral-800 text-neutral-300"
        }`}
      >
        {seat.position}
      </div>
      <span
        className={`text-[10px] ${
          seat.status === "hero"
            ? "text-emerald-300"
            : seat.status === "folded"
              ? "text-neutral-600"
              : "text-neutral-500"
        }`}
      >
        {seat.status === "hero"
          ? "YOU"
          : seat.status === "folded"
            ? "fold"
            : seat.is_blind
              ? seat.position === "SB"
                ? "SB 0.5"
                : "BB 1"
              : "待行动"}
      </span>
      {seat.position === "BTN" && (
        <span className="absolute -right-4 top-0 flex h-5 w-5 items-center justify-center rounded-full bg-white text-[10px] font-bold text-neutral-900">
          D
        </span>
      )}
    </div>
  );
}

export default function PokerTable({ scenario }: { scenario: TrainerScenario }) {
  return (
    <div className="relative mx-auto aspect-[4/3] w-full max-w-xl">
      {/* 桌面 */}
      <div className="absolute inset-[12%] rounded-[45%] border-4 border-neutral-700 bg-emerald-950/70 shadow-inner" />
      <div className="absolute inset-[15%] rounded-[45%] border border-emerald-800/60" />
      {/* 底池 */}
      <div className="absolute left-1/2 top-[42%] -translate-x-1/2 -translate-y-1/2 text-center">
        <div className="text-[10px] uppercase tracking-wider text-emerald-400/70">
          底池
        </div>
        <div className="text-lg font-bold text-emerald-200">
          {scenario.pot_bb} bb
        </div>
        <div className="text-[10px] text-neutral-500">
          有效筹码 {scenario.effective_stack_bb}bb
        </div>
      </div>
      {/* 座位 */}
      {scenario.seats.map((seat) => (
        <SeatChip key={seat.position} seat={seat} hero={scenario.hero} />
      ))}
    </div>
  );
}
